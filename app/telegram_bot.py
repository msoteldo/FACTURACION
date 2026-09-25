"""Bot de Telegram (webhook) para facturar tickets desde el celular.

Flujo:
  1. Mandas la foto del ticket  -> el bot lee TC, TR, total y forma de pago (Gemini).
  2. Tocas G01 o G03            -> si la forma de pago no se leyó, la pregunta.
  3. El portal se llena solo    -> te llega la captura con [Facturar] [Cancelar].
  4. Te avisa el resultado (la factura llega a tu correo).

Comandos: /tc, /tr (corregir), /facturar TC TR (sin foto), /consultar TC, /cancelar, /estado.

Se habla con la Bot API por HTTP con `requests` (sin SDK). Solo responde a los chats de
TELEGRAM_ALLOWED_CHAT_IDS. El token nunca se escribe en logs ni en mensajes de error.
"""
import hashlib
import html
import itertools
import json
import logging
import re
import threading
from collections import deque

import requests

from . import extraccion
from .portal_walmart import SolicitudConsulta, SolicitudFactura

log = logging.getLogger("facturacion.telegram")

USOS = {"G01": "🛒 G01 Mercancías (barra)", "G03": "🧾 G03 Gastos generales"}
FORMAS = {"04": "Crédito", "28": "Débito", "05": "Monedero"}
ETIQUETAS = {"facturar": "✅ Facturar", "cancelar": "✖ Cancelar", "continuar": "Continuar",
             "cerrar": "Cerrar", "reintentar": "🔄 Reintentar"}
MAX_TEXTO, MAX_PIE = 4000, 1000


def _e(texto):
    return html.escape(str(texto))


def _corta(texto, limite):
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


class TelegramError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token):
        self._base = f"https://api.telegram.org/bot{token}"
        self._archivos = f"https://api.telegram.org/file/bot{token}"

    def llamar(self, metodo, files=None, **params):
        try:
            if files:
                r = requests.post(f"{self._base}/{metodo}", data=params, files=files, timeout=60)
            else:
                r = requests.post(f"{self._base}/{metodo}", json=params, timeout=30)
        except requests.RequestException as e:
            # El mensaje de la excepción trae la URL con el token: no se propaga.
            raise TelegramError(f"{metodo}: {type(e).__name__}") from None
        datos = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not datos.get("ok"):
            raise TelegramError(f"{metodo}: {datos.get('description', r.status_code)}")
        return datos["result"]

    def descargar(self, file_id):
        ruta = self.llamar("getFile", file_id=file_id)["file_path"]
        try:
            r = requests.get(f"{self._archivos}/{ruta}", timeout=60)
        except requests.RequestException as e:
            raise TelegramError(f"descarga: {type(e).__name__}") from None
        if r.status_code != 200:
            raise TelegramError(f"descarga: HTTP {r.status_code}")
        return r.content


def _teclado(filas):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in fila] for fila in filas]}


class BotFacturacion:
    def __init__(self, ajustes, gestor, api=None):
        self.ajustes = ajustes
        self.gestor = gestor
        self.api = api or TelegramAPI(ajustes.telegram_token)
        # Telegram solo acepta [A-Za-z0-9_-] en el secreto; el que genera Render puede traer
        # "+/=". Se usa su SHA-256 en hex: vale cualquier valor configurado.
        self.secreto = hashlib.sha256(ajustes.telegram_secreto.encode()).hexdigest()
        self.pendientes = {}          # chat -> ticket leído que aún no se manda a facturar
        self.trabajo_de_chat = {}     # chat -> id del último trabajo lanzado
        self._ids = itertools.count(1)
        self._vistos = deque(maxlen=500)  # update_id ya procesados (Telegram reintenta)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ envío

    def enviar(self, chat, texto, teclado=None):
        params = {"chat_id": chat, "text": _corta(texto, MAX_TEXTO), "parse_mode": "HTML",
                  "disable_web_page_preview": True}
        if teclado:
            params["reply_markup"] = teclado
        try:
            return self.api.llamar("sendMessage", **params)
        except TelegramError as e:
            log.warning("No se pudo enviar mensaje: %s", e)

    def enviar_captura(self, chat, trabajo, nombre, pie, teclado=None):
        ruta = trabajo.dir / nombre if nombre else None
        if not ruta or not ruta.exists():
            return self.enviar(chat, pie, teclado)
        params = {"chat_id": chat, "caption": _corta(pie, MAX_PIE), "parse_mode": "HTML"}
        if teclado:
            params["reply_markup"] = json.dumps(teclado)  # multipart: va como texto JSON
        # Las capturas de página completa pueden ser muy altas para sendPhoto: se reintenta
        # como documento.
        for metodo, campo in (("sendPhoto", "photo"), ("sendDocument", "document")):
            try:
                with open(ruta, "rb") as f:
                    return self.api.llamar(metodo, files={campo: (nombre, f, "image/png")}, **params)
            except TelegramError as e:
                log.warning("%s falló: %s", metodo, e)
        return self.enviar(chat, pie, teclado)

    # ------------------------------------------------------------------ entrada

    def procesar(self, update):
        """Punto de entrada del webhook (se corre en un hilo aparte)."""
        uid = update.get("update_id")
        with self._lock:
            if uid in self._vistos:
                return
            self._vistos.append(uid)
        try:
            if "callback_query" in update:
                self._boton(update["callback_query"])
            elif "message" in update:
                self._mensaje(update["message"])
        except Exception:
            log.exception("Error procesando update de Telegram")

    def autorizado(self, chat):
        return chat in self.ajustes.telegram_chats

    def _mensaje(self, msg):
        chat = msg["chat"]["id"]
        texto = (msg.get("text") or msg.get("caption") or "").strip()
        comando, _, args = texto.partition(" ")
        comando = comando.split("@")[0].lower()

        if comando == "/start" and not self.autorizado(chat):
            return self.enviar(chat, f"Este bot es privado. Tu chat id es <code>{chat}</code>; "
                                     "pide que lo agreguen a TELEGRAM_ALLOWED_CHAT_IDS.")
        if not self.autorizado(chat):
            return  # silencio para desconocidos

        if comando in ("/start", "/ayuda", "/help"):
            return self.enviar(chat, AYUDA)
        foto = self._file_id_imagen(msg)
        if foto:
            return self._leer_foto(chat, foto)
        if comando in ("/tc", "/tr"):
            return self._corregir(chat, comando[1:], args)
        if comando == "/facturar":
            partes = args.split()
            if len(partes) != 2:
                return self.enviar(chat, "Uso: <code>/facturar TC TR</code>")
            return self._nuevo_pendiente(chat, {"tc_numero_ticket": partes[0],
                                                "tr_numero_transaccion": partes[1]})
        if comando == "/consultar":
            return self._consultar(chat, args.strip())
        if comando == "/cancelar":
            return self._cancelar(chat)
        if comando == "/estado":
            return self._estado(chat)
        self.enviar(chat, "Mándame la <b>foto del ticket</b>, o usa /ayuda.")

    @staticmethod
    def _file_id_imagen(msg):
        if msg.get("photo"):
            return msg["photo"][-1]["file_id"]  # la resolución más alta
        doc = msg.get("document") or {}
        if (doc.get("mime_type") or "").startswith("image/"):
            return doc["file_id"]
        return None

    # ------------------------------------------------------------------ ticket pendiente

    def _leer_foto(self, chat, file_id):
        self.enviar(chat, "📷 Leyendo el ticket…")
        try:
            imagen = self.api.descargar(file_id)
            if len(imagen) > self.ajustes.max_bytes_imagen:
                return self.enviar(chat, "La imagen es demasiado grande.")
            ticket = extraccion.leer_ticket(self.ajustes.gemini_api_key, self.ajustes.gemini_modelo,
                                            imagen, "image/jpeg")
        except TelegramError as e:
            return self.enviar(chat, f"No pude descargar la foto ({_e(e)}).")
        except extraccion.ExtraccionError as e:
            return self.enviar(chat, f"❌ No pude leer el ticket: {_e(e.mensaje)}")
        self._nuevo_pendiente(chat, ticket)

    def _nuevo_pendiente(self, chat, ticket):
        p = {
            "id": next(self._ids),
            "tc": re.sub(r"\s", "", str(ticket.get("tc_numero_ticket") or "")),
            "tr": re.sub(r"\s", "", str(ticket.get("tr_numero_transaccion") or "")),
            "forma_pago": ticket.get("forma_pago_sat_sugerida"),
            "ticket": ticket,
        }
        self.pendientes[chat] = p
        self._mostrar_pendiente(chat, p)

    def _mostrar_pendiente(self, chat, p):
        t = p["ticket"]
        lineas = ["<b>Ticket leído</b>"]
        for etiqueta, valor in (("Comercio", t.get("comercio")), ("Fecha", t.get("fecha")),
                                ("Total", f"${t['total']:,.2f}" if isinstance(t.get("total"), (int, float)) else None)):
            if valor:
                lineas.append(f"{etiqueta}: {_e(valor)}")
        conf = lambda k: "" if t.get(k) in (None, "alta") else f" ⚠️ confianza {t.get(k)}"
        lineas.append(f"TC: <code>{_e(p['tc'] or '—')}</code>{conf('confianza_tc')}")
        lineas.append(f"TR: <code>{_e(p['tr'] or '—')}</code>{conf('confianza_tr')}")
        forma = FORMAS.get(p["forma_pago"], "sin leer (te la pregunto)")
        lineas.append(f"Forma de pago: {_e(forma)}")
        for adv in t.get("advertencias") or []:
            lineas.append(f"⚠️ {_e(adv)}")
        if t.get("requiere_revision"):
            lineas.append("\nRevisa TC y TR contra el papel. Corrige con <code>/tc 123…</code> o "
                          "<code>/tr 123…</code>.")
        lineas.append("\n¿Para qué es la compra?")
        pid = p["id"]
        self.enviar(chat, "\n".join(lineas), _teclado([
            [(USOS["G01"], f"u:{pid}:G01")],
            [(USOS["G03"], f"u:{pid}:G03")],
            [("💳 Cambiar forma de pago", f"fp:{pid}"), ("🗑 Descartar", f"x:{pid}")],
        ]))

    def _corregir(self, chat, campo, valor):
        p = self.pendientes.get(chat)
        valor = re.sub(r"\s", "", valor)
        if not p:
            return self.enviar(chat, "No hay un ticket pendiente. Manda la foto primero.")
        if not valor.isdigit():
            return self.enviar(chat, f"Uso: <code>/{campo} 123456…</code> (solo dígitos)")
        p[campo] = valor
        p["ticket"]["requiere_revision"] = False
        p["id"] = next(self._ids)  # invalida los botones del mensaje anterior
        self._mostrar_pendiente(chat, p)

    def _pedir_forma(self, chat, p):
        self.enviar(chat, "¿Con qué se pagó?", _teclado(
            [[(nombre, f"f:{p['id']}:{codigo}") for codigo, nombre in FORMAS.items()]]))

    def _lanzar(self, chat, p):
        if not re.fullmatch(r"\d{10,30}", p["tc"]) or not re.fullmatch(r"\d{1,10}", p["tr"]):
            return self.enviar(chat, "Falta el TC o el TR (o no son solo dígitos). Corrígelos con "
                                     "<code>/tc</code> y <code>/tr</code>.")
        faltan = self.gestor.datos.faltantes()
        if faltan:
            return self.enviar(chat, f"Faltan datos fiscales en el servidor: {_e(faltan)}")
        sol = SolicitudFactura(tc=p["tc"], tr=p["tr"], uso_cfdi=p["uso_cfdi"],
                               forma_pago=p["forma_pago"], metodo_entrega="descarga")
        try:
            t = self.gestor.crear("factura", sol, origen={"chat": chat})
        except ValueError as e:
            return self.enviar(chat, f"⚠️ {_e(e)}")
        self.pendientes.pop(chat, None)
        self.trabajo_de_chat[chat] = t.id
        self.enviar(chat, f"⏳ Llenando el portal ({_e(p['uso_cfdi'])}, {_e(FORMAS[p['forma_pago']])}). "
                          "Te aviso cuando necesite tu confirmación (≈1 min).")

    # ------------------------------------------------------------------ botones

    def _boton(self, cb):
        chat = cb["message"]["chat"]["id"]
        datos = cb.get("data", "")
        aviso = None
        try:
            if not self.autorizado(chat):
                aviso = "No autorizado."
            elif datos.startswith("r:"):
                aviso = self._boton_respuesta(datos)
            else:
                aviso = self._boton_pendiente(chat, datos)
            if aviso is None:  # acción aceptada: se quitan los botones para evitar dobles clics
                try:
                    self.api.llamar("editMessageReplyMarkup", chat_id=chat,
                                    message_id=cb["message"]["message_id"],
                                    reply_markup={"inline_keyboard": []})
                except TelegramError:
                    pass
        finally:
            try:
                self.api.llamar("answerCallbackQuery", callback_query_id=cb["id"],
                                text=aviso or "", show_alert=bool(aviso))
            except TelegramError:
                pass

    def _boton_pendiente(self, chat, datos):
        tipo, _, resto = datos.partition(":")
        pid, _, valor = resto.partition(":")
        p = self.pendientes.get(chat)
        if not p or str(p["id"]) != pid:
            return "Ese ticket ya no está pendiente."
        if tipo == "x":
            self.pendientes.pop(chat, None)
            self.enviar(chat, "🗑 Ticket descartado.")
        elif tipo == "fp":
            self._pedir_forma(chat, p)
        elif tipo == "u" and valor in USOS:
            p["uso_cfdi"] = valor
            if p.get("forma_pago") in FORMAS:
                self._lanzar(chat, p)
            else:
                self._pedir_forma(chat, p)
        elif tipo == "f" and valor in FORMAS:
            p["forma_pago"] = valor
            if p.get("uso_cfdi"):
                self._lanzar(chat, p)
            else:
                p["id"] = next(self._ids)
                self._mostrar_pendiente(chat, p)
        else:
            return "Opción no válida."
        return None

    def _boton_respuesta(self, datos):
        _, trabajo_id, pregunta_id, respuesta = (datos.split(":", 3) + ["", "", ""])[:4]
        try:
            self.gestor.responder(trabajo_id, int(pregunta_id), respuesta)
        except KeyError:
            return "Ese trabajo ya no existe (¿se reinició el servicio?)."
        except ValueError as e:
            return _corta(str(e), 190)
        return None

    # ------------------------------------------------------------------ otros comandos

    def _consultar(self, chat, numero):
        if not re.fullmatch(r"[A-Za-z0-9-]{1,40}", numero):
            return self.enviar(chat, "Uso: <code>/consultar TC</code>")
        try:
            t = self.gestor.crear("consulta", SolicitudConsulta(tc=numero), origen={"chat": chat})
        except ValueError as e:
            return self.enviar(chat, f"⚠️ {_e(e)}")
        self.trabajo_de_chat[chat] = t.id
        self.enviar(chat, "🔎 Consultando en el portal…")

    def _cancelar(self, chat):
        if self.pendientes.pop(chat, None):
            return self.enviar(chat, "🗑 Ticket pendiente descartado.")
        t = self.gestor.obtener(self.trabajo_de_chat.get(chat, ""))
        if not t or t.estado not in ("en_cola", "ejecutando", "esperando_respuesta"):
            return self.enviar(chat, "No hay nada en curso.")
        if t.facturado:
            return self.enviar(chat, "Ya se dio clic en Facturar; no se puede cancelar.")
        self.gestor.cancelar(t.id)
        self.enviar(chat, "Cancelando…")

    def _estado(self, chat):
        t = self.gestor.obtener(self.trabajo_de_chat.get(chat, ""))
        if not t:
            return self.enviar(chat, "No hay trabajos recientes.")
        self.enviar(chat, f"Trabajo <code>{t.id}</code>: {_e(t.estado)}"
                          + (f"\n{_e(_corta(t.error, 500))}" if t.error else ""))

    # ------------------------------------------------------------------ avisos del gestor

    def _chat_de(self, t):
        return (t.origen or {}).get("chat")

    def on_pregunta(self, t):
        chat = self._chat_de(t)
        p = t.pregunta
        if chat is None or not p:
            return
        lineas = []
        if p["tipo"] == "confirmar_facturar":
            d = p.get("datos") or {}
            lineas.append("<b>¿Facturar?</b> Esto ya no se puede deshacer.")
            for etiqueta, clave in (("TC", "tc"), ("TR", "tr"), ("RFC", "rfc"),
                                    ("Razón social", "razon_social"), ("Uso CFDI", "uso_cfdi_portal"),
                                    ("Forma de pago", "forma_pago")):
                if d.get(clave):
                    lineas.append(f"{etiqueta}: {_e(d[clave])}")
            for m in d.get("mensajes_del_portal") or []:
                lineas.append(f"ℹ️ Portal: {_e(_corta(m, 200))}")
        elif p["tipo"] == "revisar_direccion":
            lineas.append("<b>Tus datos fiscales no coinciden con el portal:</b>")
            for campo, v in (p.get("datos") or {}).items():
                lineas.append(f"{_e(campo)}: tuyo «{_e(v.get('esperado'))}» / portal «{_e(v.get('en_portal'))}»")
            lineas.append("¿Continuar con lo que muestra el portal?")
        elif p["tipo"] == "captcha":
            lineas.append("🧩 <b>El portal pidió un captcha.</b> No lo resuelvo; puedes reintentar o "
                          "cancelar y facturar este ticket a mano.")
        else:
            lineas.append(f"<b>Mensaje del portal:</b>\n{_e(_corta(p['texto'], 600))}")
        botones = [[(ETIQUETAS.get(o, o), f"r:{t.id}:{p['id']}:{o}") for o in p["opciones"]]]
        seg = p["expira_en_s"]
        lineas.append(f"\n⏱ Tienes {seg // 60} min para responder." if seg >= 60
                      else f"\n⏱ Tienes {seg} s para responder.")
        self.enviar_captura(chat, t, p.get("captura"), "\n".join(lineas), _teclado(botones))

    def on_fin(self, t):
        chat = self._chat_de(t)
        if chat is None:
            return
        if t.estado == "completado" and t.tipo == "factura":
            self.enviar_captura(chat, t, "11_resultado_final.png",
                                "✅ <b>Factura generada.</b> Walmart la envía a tu correo (PDF y XML).")
        elif t.estado == "completado":
            texto = _corta((t.resultado or {}).get("texto_visible", ""), 600)
            self.enviar_captura(chat, t, t.capturas[-1] if t.capturas else None,
                                f"🔎 Resultado de la consulta:\n{_e(texto)}")
        elif t.estado == "cancelado":
            self.enviar(chat, f"✖ Cancelado. {_e(t.error or '')}\nNo se facturó nada.")
        else:
            extra = "\n⚠️ Ya se había dado clic en Facturar: revisa tu correo." if t.facturado else ""
            self.enviar_captura(chat, t, t.capturas[-1] if t.capturas else None,
                                f"❌ <b>Error</b>: {_e(_corta(t.error or '', 700))}{extra}")

    # ------------------------------------------------------------------ webhook

    def configurar_webhook(self):
        if not self.ajustes.url_publica:
            raise TelegramError("Falta PUBLIC_URL (o RENDER_EXTERNAL_URL) para registrar el webhook.")
        return self.api.llamar(
            "setWebhook", url=f"{self.ajustes.url_publica}/telegram/webhook",
            secret_token=self.secreto,
            allowed_updates=["message", "callback_query"])


AYUDA = """<b>Facturación de tickets</b>
📷 Mándame la <b>foto del ticket</b> y sigue los botones.

/tc 123… — corregir el TC leído
/tr 123… — corregir el TR leído
/facturar TC TR — facturar sin foto
/consultar TC — buscar un ticket ya facturado
/estado — ver el último trabajo
/cancelar — cancelar lo pendiente

El clic final en <b>Facturar</b> siempre te lo pregunto a ti."""
