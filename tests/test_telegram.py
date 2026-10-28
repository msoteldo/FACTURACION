"""Bot de Telegram con una Bot API simulada, de punta a punta contra el portal simulado."""
import dataclasses
import itertools
import json
import time

from fastapi.testclient import TestClient

from app import extraccion, main
from app.telegram_bot import BotFacturacion

CHAT = 111
TC = "9906477777777777777777"


class APIFalsa:
    def __init__(self):
        self.llamadas = []
        self._ids = itertools.count(1)

    def llamar(self, metodo, files=None, **params):
        if "reply_markup" in params and isinstance(params["reply_markup"], str):
            params["reply_markup"] = json.loads(params["reply_markup"])
        if files:
            params["_archivo"] = next(iter(files.values()))[0]
        self.llamadas.append((metodo, params, bool(files)))
        return {"message_id": next(self._ids)}

    def descargar(self, file_id):
        return b"foto"

    def buscar(self, condicion, timeout=60):
        fin = time.time() + timeout
        while time.time() < fin:
            for metodo, params, _ in list(self.llamadas):
                if condicion(metodo, params):
                    return params
            time.sleep(0.3)
        raise AssertionError(f"no llegó el mensaje esperado; llamadas: {[m for m, *_ in self.llamadas]}")


def texto(params):
    return params.get("text") or params.get("caption") or ""


def botones(params):
    return [b["callback_data"] for fila in params.get("reply_markup", {}).get("inline_keyboard", [])
            for b in fila]


def nuevo_bot():
    ajustes = dataclasses.replace(main.ajustes, telegram_token="x", telegram_secreto="s",
                                  telegram_chats=frozenset({CHAT}))
    api = APIFalsa()
    bot = BotFacturacion(ajustes, main.gestor, api=api)
    main.gestor.oyentes.append(bot)
    return bot, api


_updates = itertools.count(1)


def foto(chat=CHAT):
    return {"update_id": next(_updates),
            "message": {"chat": {"id": chat}, "photo": [{"file_id": "chica"}, {"file_id": "grande"}]}}


def boton(data, chat=CHAT):
    return {"update_id": next(_updates),
            "callback_query": {"id": "cb", "data": data,
                               "message": {"chat": {"id": chat}, "message_id": 1}}}


def ticket_leido(monkeypatch, forma="28", tc=TC):
    monkeypatch.setattr(extraccion, "leer_ticket", lambda *a, **k: {
        "comercio": "Sam's Club", "total": 1417.86, "tc_numero_ticket": tc,
        "tr_numero_transaccion": "03388", "forma_pago_sat_sugerida": forma,
        "confianza_tc": "alta", "confianza_tr": "alta", "advertencias": [],
        "requiere_revision": False})


def test_flujo_foto_a_factura(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch)
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        assert TC in texto(resumen) and "Débito" in texto(resumen)
        assert "¿Qué quieres hacer?" in texto(resumen)
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p))
        uso_g01 = next(b for b in botones(usos) if b.endswith(":G01"))

        # Forma de pago leída del ticket: con elegir G01 ya arranca.
        bot.procesar(boton(uso_g01))
        foto_datos = api.buscar(lambda m, p: m == "sendPhoto" and "datos fiscales" in texto(p))
        assert foto_datos["_archivo"] == "04_direccion_llenada.png"
        pregunta = api.buscar(lambda m, p: m == "sendMessage" and "¿Facturar?" in texto(p))
        t = texto(pregunta)
        assert "débito" in t and "¿Confirmas que tus datos fiscales" in t  # mensaje del portal
        # Datos leídos del portal ya llenado (valores de prueba de conftest).
        for esperado in ("RFC: CPA010101AB1", "Razón social: CLUB DE PADEL DE PRUEBA",
                         "Colonia: Centro", "Código postal: 01000", "Correo: facturas@example.com",
                         "Régimen fiscal: 601 - General de Ley Personas Morales",
                         "Uso CFDI: G01 - Adquisición de mercancías"):
            assert esperado in t, esperado
        assert "Entrega: 📧 por correo a facturas@example.com" in t
        facturar = next(b for b in botones(pregunta) if b.endswith(":facturar"))

        bot.procesar(boton(facturar))
        fin = api.buscar(lambda m, p: "Factura generada" in texto(p))
        assert fin is not None
        # Los botones usados se quitan para evitar dobles clics.
        assert any(m == "editMessageReplyMarkup" for m, *_ in api.llamadas)
    finally:
        main.gestor.oyentes.remove(bot)


def test_pide_forma_de_pago_si_no_se_leyo_y_se_puede_cancelar(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch, forma=None, tc="9906488888888888888888")
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p))
        bot.procesar(boton(next(b for b in botones(usos) if b.endswith(":G03"))))
        formas = api.buscar(lambda m, p: "¿Con qué se pagó?" in texto(p))
        bot.procesar(boton(next(b for b in botones(formas) if b.endswith(":04"))))
        pregunta = api.buscar(lambda m, p: "¿Facturar?" in texto(p))
        assert "crédito" in texto(pregunta) and "30 s" in texto(pregunta)
        bot.procesar(boton(next(b for b in botones(pregunta) if b.endswith(":cancelar"))))
        assert api.buscar(lambda m, p: "No se facturó nada" in texto(p))
    finally:
        main.gestor.oyentes.remove(bot)


def test_botones_viejos_y_correccion_de_tc(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch)
        bot.procesar(foto())
        viejo = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        api.llamadas.clear()
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/tc 1234567890123"}})
        nuevo = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        assert "1234567890123" in texto(nuevo)
        # El botón del mensaje anterior ya no sirve.
        bot.procesar(boton(botones(viejo)[0]))
        aviso = api.buscar(lambda m, p: m == "answerCallbackQuery" and p.get("text"))
        assert "ya no está pendiente" in aviso["text"]
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/cancelar"}})
        assert api.buscar(lambda m, p: "descartado" in texto(p))
    finally:
        main.gestor.oyentes.remove(bot)


def test_chat_no_autorizado(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch)
        bot.procesar(foto(chat=999))
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": 999}, "text": "/facturar 1234567890 1"}})
        assert api.llamadas == []  # silencio total
        bot.procesar({"update_id": next(_updates), "message": {"chat": {"id": 999}, "text": "/start"}})
        assert "999" in texto(api.llamadas[-1][1]) and "privado" in texto(api.llamadas[-1][1])
        # Un update repetido (Telegram reintenta) se ignora.
        u = foto()
        bot.procesar(u)
        n = len(api.llamadas)
        bot.procesar(u)
        assert len(api.llamadas) == n
    finally:
        main.gestor.oyentes.remove(bot)


def test_webhook_exige_secreto(monkeypatch):
    bot, api = nuevo_bot()
    main.gestor.oyentes.remove(bot)
    monkeypatch.setattr(main, "bot", bot)
    monkeypatch.setattr(main, "ajustes", bot.ajustes)
    c = TestClient(main.app)
    upd = {"update_id": next(_updates), "message": {"chat": {"id": CHAT}, "text": "/ayuda"}}
    assert c.post("/telegram/webhook", json=upd).status_code == 401
    assert c.post("/telegram/webhook", json=upd,
                  headers={"X-Telegram-Bot-Api-Secret-Token": "otro"}).status_code == 401
    # El secreto crudo tampoco sirve: Telegram manda su SHA-256 (ver BotFacturacion.secreto).
    assert c.post("/telegram/webhook", json=upd,
                  headers={"X-Telegram-Bot-Api-Secret-Token": "s"}).status_code == 401
    r = c.post("/telegram/webhook", json=upd,
               headers={"X-Telegram-Bot-Api-Secret-Token": bot.secreto})
    assert r.status_code == 200
    assert api.buscar(lambda m, p: "Facturación de tickets" in texto(p))


def test_registro_del_webhook():
    bot, api = nuevo_bot()
    main.gestor.oyentes.remove(bot)
    bot.ajustes = dataclasses.replace(bot.ajustes, url_publica="https://ejemplo.onrender.com")
    bot.configurar_webhook()
    metodo, params, _ = api.llamadas[-1]
    assert metodo == "setWebhook"
    assert params["url"] == "https://ejemplo.onrender.com/telegram/webhook"
    # Secreto en formato que Telegram acepta aunque el configurado traiga "+/=".
    assert params["secret_token"] == bot.secreto and bot.secreto.isalnum() and len(bot.secreto) == 64


def test_comando_facturar_va_directo_a_uso():
    bot, api = nuevo_bot()
    try:
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/facturar 9906412341234123412341 03388"}})
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        assert "¿Para qué es la compra?" in texto(resumen)
        assert not any(b.endswith(":con") for b in botones(resumen))
    finally:
        main.gestor.oyentes.remove(bot)


def test_efectivo_como_opcion_de_forma_de_pago(monkeypatch):
    bot, api = nuevo_bot()
    try:
        # Gemini leyó "efectivo": ya no hay que preguntar la forma de pago.
        ticket_leido(monkeypatch, forma=extraccion.FORMA_PAGO_SAT["efectivo"],
                     tc="5506400000000000000002")
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        assert "Efectivo" in texto(resumen)
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p))
        bot.procesar(boton(next(b for b in botones(usos) if b.endswith(":G03"))))
        pregunta = api.buscar(lambda m, p: "¿Facturar?" in texto(p))
        assert "La define el ticket" in texto(pregunta)
        bot.procesar(boton(next(b for b in botones(pregunta) if b.endswith(":cancelar"))))
        api.buscar(lambda m, p: "No se facturó nada" in texto(p))
        # Y en el menú manual de forma de pago aparece Efectivo.
        bot._pedir_forma(CHAT, {"id": 0})
        menu = api.llamadas[-1][1]
        assert any(b.endswith(":01") for b in botones(menu))
    finally:
        main.gestor.oyentes.remove(bot)


def test_ticket_oxxo_de_punta_a_punta(monkeypatch):
    bot, api = nuevo_bot()
    try:
        monkeypatch.setattr(extraccion, "leer_ticket", lambda *a, **k: {
            "comercio": "OXXO", "comercio_clave": "oxxo", "fecha": "2026-10-09",
            "oxxo_folio_venta": "1154010", "oxxo_id_venta": "10M0N50RCS1", "total": 270.0,
            "forma_de_pago": "debito", "confianza_id_venta": "media",
            "advertencias": ["El 4o carácter del ID podría ser O o 0"], "requiere_revision": True})
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket de OXXO leído" in texto(p))
        assert "10M0N50RCS1" in texto(resumen) and "confianza media" in texto(resumen)
        assert not any(b.endswith(":con") for b in botones(resumen))  # sin consulta para OXXO

        # Corrección del ID ambiguo (0 -> O) y del folio.
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/id 10mon50rcs1"}})
        corregido = api.buscar(lambda m, p: "<code>10MON50RCS1</code>" in texto(p))
        bot.procesar(boton(next(b for b in botones(corregido) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p) and "OXXO" not in texto(p))
        assert not any(b.startswith("fp:") for b in botones(usos))  # OXXO no pide forma de pago
        bot.procesar(boton(next(b for b in botones(usos) if b.endswith(":G01"))))

        foto_datos = api.buscar(lambda m, p: m == "sendPhoto" and "datos fiscales" in texto(p))
        assert foto_datos["_archivo"] == "04_listo_para_continuar.png"
        pregunta = api.buscar(lambda m, p: "¿Facturar?" in texto(p))
        t = texto(pregunta)
        assert "Ticket OXXO" in t and "10MON50RCS1" in t and "09/10/2026" in t
        assert "RFC: CPA010101AB1" in t and "Uso CFDI: Adquisición de mercancias" in t
        bot.procesar(boton(next(b for b in botones(pregunta) if b.endswith(":facturar"))))
        assert api.buscar(lambda m, p: "Factura de OXXO generada" in texto(p))
    finally:
        main.gestor.oyentes.remove(bot)


def test_ticket_heb_de_punta_a_punta(monkeypatch):
    from datetime import date
    bot, api = nuevo_bot()
    try:
        fecha = date.today().replace(day=1).isoformat()
        monkeypatch.setattr(extraccion, "leer_ticket", lambda *a, **k: {
            "comercio": "H-E-B", "comercio_clave": "heb", "fecha": fecha,
            "heb_sucursal": "2950", "heb_ticket": "800599", "total": 345.7,
            "confianza_tc": "media", "advertencias": [], "requiere_revision": True})
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket de HEB leído" in texto(p))
        assert "800599" in texto(resumen) and "2950" in texto(resumen) and "confianza media" in texto(resumen)
        assert not any(b.endswith(":con") for b in botones(resumen))  # sin consulta para HEB

        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/ticket 800544"}})
        corregido = api.buscar(lambda m, p: "<code>800544</code>" in texto(p))
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/tc 123"}})  # no aplica a HEB
        api.buscar(lambda m, p: "no aplica" in texto(p))
        bot.procesar(boton(next(b for b in botones(corregido) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p) and "HEB" not in texto(p))
        assert not any(b.startswith("fp:") for b in botones(usos))  # HEB no pide forma de pago
        bot.procesar(boton(next(b for b in botones(usos) if b.endswith(":G01"))))

        foto_datos = api.buscar(lambda m, p: m == "sendPhoto" and "datos fiscales" in texto(p))
        assert foto_datos["_archivo"] == "05_listo_para_facturar.png"
        pregunta = api.buscar(lambda m, p: "¿Facturar?" in texto(p))
        t = texto(pregunta)
        assert "Ticket HEB" in t and "800544" in t and "2950" in t
        assert "RFC: CPA010101AB1" in t and "Uso CFDI: G01 - Adquisición de mercancías." in t
        assert "Entrega: 📧 por correo a facturas@example.com" in t
        bot.procesar(boton(next(b for b in botones(pregunta) if b.endswith(":facturar"))))
        fin = api.buscar(lambda m, p: "Factura de HEB generada" in texto(p))
        assert "JA1933771" in texto(fin)
    finally:
        main.gestor.oyentes.remove(bot)


def test_bot_usa_los_datos_vigentes(monkeypatch):
    datos = dataclasses.replace(main.gestor.obtener_datos(), razon_social="CLUB DE PADEL SA DE CV")
    monkeypatch.setattr(main.gestor, "obtener_datos", lambda: datos)
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch, tc="9906466666666666666666")
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p))
        bot.procesar(boton(next(b for b in botones(usos) if b.endswith(":G01"))))
        t = main.gestor.obtener(bot.trabajo_de_chat[CHAT])
        assert t.datos.razon_social == "CLUB DE PADEL SA DE CV"
        main.gestor.cancelar(t.id)
    finally:
        main.gestor.oyentes.remove(bot)


def test_bot_avisa_si_no_hay_perfil(monkeypatch):
    from app.perfil import PerfilNoDisponible

    def falla():
        raise PerfilNoDisponible()
    monkeypatch.setattr(main.gestor, "obtener_datos", falla)
    bot, api = nuevo_bot()
    try:
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/facturar 9906499999999999999999 03388"}})
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":G01"))))
        formas = api.buscar(lambda m, p: "¿Con qué se pagó?" in texto(p))
        bot.procesar(boton(next(b for b in botones(formas) if b.endswith(":28"))))
        assert api.buscar(lambda m, p: "perfil fiscal" in texto(p))
    finally:
        main.gestor.oyentes.remove(bot)


def test_resumen_sin_consulta_y_comando_consultar_no_crea_trabajos(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch, tc="9906400000000000000401")
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        assert not any(b.endswith(":con") for b in botones(resumen))
        assert "Consultar" not in texto(resumen)
        antes = set(main.gestor.trabajos)
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/consultar 9906400000000000000401"}})
        time.sleep(1)
        assert set(main.gestor.trabajos) == antes
    finally:
        main.gestor.oyentes.remove(bot)
