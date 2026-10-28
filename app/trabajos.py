"""Trabajos de facturación en segundo plano.

Cada trabajo corre en su propio hilo con su propio Chromium (la API síncrona de
Playwright debe usarse siempre desde el mismo hilo). Cuando el flujo necesita a un
humano, el hilo se bloquea en `preguntar()` y el trabajo queda en estado
"esperando_respuesta" hasta que llegue POST /facturas/{id}/respuesta.

El estado vive en memoria: correr con UN solo worker de uvicorn. Si Render reinicia
o duerme el servicio, los trabajos en curso se pierden (sin haber facturado nada,
salvo que el clic final ya se hubiera dado).
"""
import json
import logging
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests
from playwright.sync_api import sync_playwright

from .portal_heb import FlujoHeb, SolicitudHeb
from .portal_oxxo import FlujoOxxo, SolicitudOxxo
from .portal_walmart import FlujoCancelado, FlujoError, FlujoWalmart

log = logging.getLogger("facturacion.trabajos")

ACTIVOS = {"en_cola", "ejecutando", "esperando_respuesta"}


class TrabajoDuplicado(ValueError):
    """Ya hay un trabajo activo del mismo tipo para ese TC."""
    def __init__(self, mensaje, trabajo_id):
        super().__init__(mensaje)
        self.trabajo_id = trabajo_id


class DatosIncompletos(Exception):
    """Faltan datos fiscales obligatorios; no se puede facturar."""
    def __init__(self, faltantes):
        super().__init__(f"Faltan datos fiscales: {faltantes}")
        self.faltantes = faltantes


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Trabajo:
    def __init__(self, tipo, solicitud, directorio):
        self.id = uuid.uuid4().hex[:12]
        self.tipo = tipo                  # "factura" | "inspeccion"
        self.solicitud = solicitud
        self.dir = directorio / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.estado = "en_cola"
        self.pregunta = None
        self.eventos = []
        self.capturas = []
        self.archivos = []
        self.resultado = None
        self.error = None
        self.creado = _ahora()
        self.actualizado = self.creado
        self.creado_ts = time.time()
        self.facturado = False            # True en cuanto se dio el clic irreversible
        self.origen = None                # quién lo pidió (p. ej. {"chat": id} de Telegram)
        self.datos = None                 # DatosFiscales vigentes al crearlo; no cambian después
        self._respuesta = None
        self._hay_respuesta = threading.Event()
        self._cancelar = threading.Event()
        self._contador_preguntas = 0

    def a_dict(self):
        return {
            "id": self.id,
            "tipo": self.tipo,
            "estado": self.estado,
            "pregunta": self.pregunta,
            "eventos": self.eventos[-50:],
            "capturas": self.capturas,
            "archivos": self.archivos,
            "resultado": self.resultado,
            "error": self.error,
            "facturado": self.facturado,
            "creado": self.creado,
            "actualizado": self.actualizado,
        }


class InteraccionTrabajo:
    """Implementa la interfaz que espera FlujoWalmart, sobre un Trabajo."""

    def __init__(self, gestor, trabajo):
        self.gestor = gestor
        self.t = trabajo

    def evento(self, mensaje):
        self.t.eventos.append({"hora": _ahora(), "mensaje": mensaje})
        self.t.actualizado = _ahora()

    def marcar_facturado(self):
        self.t.facturado = True

    def captura(self, page, nombre):
        nombre = re.sub(r"[^\w.-]", "_", nombre) + ".png"
        try:
            page.screenshot(path=str(self.t.dir / nombre), full_page=True)
        except Exception as e:
            self.evento(f"No se pudo capturar pantalla '{nombre}': {type(e).__name__}")
            return None
        if nombre not in self.t.capturas:
            self.t.capturas.append(nombre)
        return nombre

    def preguntar(self, tipo, texto, opciones, captura=None, datos=None):
        if self.t._cancelar.is_set():
            raise FlujoCancelado("Cancelado.")
        self.t._contador_preguntas += 1
        self.t._hay_respuesta.clear()
        self.t._respuesta = None
        self.t.pregunta = {
            "id": self.t._contador_preguntas,
            "tipo": tipo,
            "texto": texto,
            "opciones": opciones,
            "captura": captura,
            "datos": datos,
            "expira_en_s": self.gestor.ajustes.timeout_respuesta_s,
            # Hora absoluta: la web calcula lo que falta aunque se recargue la página.
            "expira_en": (datetime.now(timezone.utc) + timedelta(
                seconds=self.gestor.ajustes.timeout_respuesta_s)).isoformat(timespec="seconds"),
        }
        # Queda en el historial para poder diagnosticar después (p. ej. el texto de un modal).
        self.evento(f"Pregunta '{tipo}' #{self.t._contador_preguntas}: {texto[:500]}")
        self.t.estado = "esperando_respuesta"
        self.t.actualizado = _ahora()
        self.gestor.notificar(self.t)

        respondio = self.t._hay_respuesta.wait(self.gestor.ajustes.timeout_respuesta_s)
        self.t.pregunta = None
        self.t.estado = "ejecutando"
        self.t.actualizado = _ahora()
        if self.t._cancelar.is_set():
            raise FlujoCancelado("Cancelado por el usuario.")
        if not respondio:
            raise FlujoCancelado("Nadie respondió a tiempo; se canceló sin facturar.")
        self.evento(f"Respuesta a '{tipo}': {self.t._respuesta}")
        return self.t._respuesta


class GestorTrabajos:
    def __init__(self, ajustes, obtener_datos):
        self.ajustes = ajustes
        # Se llama al crear cada trabajo: así un cambio en los datos fiscales (p. ej. desde
        # la web) aplica a la siguiente factura sin reiniciar. Puede lanzar PerfilNoDisponible.
        self.obtener_datos = obtener_datos
        # Objetos con on_pregunta(t) y on_fin(t) (p. ej. el bot de Telegram).
        self.oyentes = []
        self.trabajos = {}
        self._lock = threading.Lock()
        self._navegadores = threading.BoundedSemaphore(ajustes.max_navegadores)
        ajustes.dir_trabajos.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- API pública

    def crear(self, tipo, solicitud=None, origen=None):
        self.limpiar_viejos()
        datos = self.obtener_datos()
        if tipo == "factura" and datos.faltantes():
            raise DatosIncompletos(datos.faltantes())
        with self._lock:
            if solicitud is not None:
                for t in self.trabajos.values():
                    # Dos trabajos del mismo tipo para el mismo ticket a la vez no tienen sentido.
                    if (t.tipo == tipo and t.estado in ACTIVOS and t.solicitud is not None
                            and t.solicitud.tc == solicitud.tc):
                        raise TrabajoDuplicado(
                            f"Ya hay un trabajo de tipo '{tipo}' activo para ese TC: {t.id} "
                            f"(estado: {t.estado}). Revísalo con GET /facturas/{t.id} "
                            f"o cancélalo con POST /facturas/{t.id}/cancelar.", t.id)
            t = Trabajo(tipo, solicitud, self.ajustes.dir_trabajos)
            t.origen = origen  # antes de arrancar el hilo, para que los oyentes lo vean
            t.datos = datos
            self.trabajos[t.id] = t
        threading.Thread(target=self._correr, args=(t,), daemon=True, name=f"trabajo-{t.id}").start()
        return t

    def obtener(self, trabajo_id):
        return self.trabajos.get(trabajo_id)

    def responder(self, trabajo_id, pregunta_id, respuesta):
        t = self.trabajos.get(trabajo_id)
        if t is None:
            raise KeyError(trabajo_id)
        p = t.pregunta
        if t.estado != "esperando_respuesta" or p is None:
            raise ValueError("El trabajo no está esperando una respuesta.")
        if p["id"] != pregunta_id:
            raise ValueError(f"La pregunta vigente es la #{p['id']}, no la #{pregunta_id}.")
        if respuesta not in p["opciones"]:
            raise ValueError(f"Respuesta no válida; opciones: {p['opciones']}")
        t._respuesta = respuesta
        t._hay_respuesta.set()

    def cancelar(self, trabajo_id):
        t = self.trabajos.get(trabajo_id)
        if t is None:
            raise KeyError(trabajo_id)
        t._cancelar.set()
        t._hay_respuesta.set()
        if t.estado == "en_cola":
            t.estado = "cancelado"

    def _avisar_oyentes(self, metodo, t):
        for oyente in list(self.oyentes):
            try:
                getattr(oyente, metodo)(t)
            except Exception:
                log.exception("Falló el oyente %s.%s del trabajo %s", type(oyente).__name__, metodo, t.id)

    def notificar(self, t):
        """Avisa que un trabajo necesita a un humano: oyentes internos (bot de Telegram) y,
        opcionalmente, un webhook externo."""
        self._avisar_oyentes("on_pregunta", t)
        if not self.ajustes.webhook_url:
            return
        cuerpo = {"id": t.id, "estado": t.estado,
                  "pregunta": {k: t.pregunta[k] for k in ("id", "tipo", "texto", "opciones")}}
        try:
            requests.post(self.ajustes.webhook_url, json=cuerpo, timeout=10)
        except requests.RequestException as e:
            log.warning("No se pudo notificar al webhook: %s", type(e).__name__)

    def limpiar_viejos(self):
        limite = time.time() - self.ajustes.ttl_trabajos_s
        with self._lock:
            viejos = [i for i, t in self.trabajos.items()
                      if t.estado not in ACTIVOS and t.creado_ts < limite]
            for i in viejos:
                shutil.rmtree(self.trabajos.pop(i).dir, ignore_errors=True)

    # ------------------------------------------------------------- ejecución

    def _lanzar(self, p):
        opciones = {"headless": self.ajustes.headless,
                    "args": ["--disable-dev-shm-usage", "--disable-gpu"]}
        if self.ajustes.navegador_ejecutable:
            opciones["executable_path"] = self.ajustes.navegador_ejecutable
        elif self.ajustes.navegador_canal:
            opciones["channel"] = self.ajustes.navegador_canal
        return p.chromium.launch(**opciones)

    def _ejecutar_flujo(self, t, flujo, page):
        url = self.ajustes.walmart_url
        if t.tipo == "inspeccion":
            t.resultado = flujo.inspeccionar(url)
        elif isinstance(t.solicitud, SolicitudOxxo):
            t.resultado = flujo.ejecutar(self.ajustes.oxxo_url, t.solicitud)
        elif isinstance(t.solicitud, SolicitudHeb):
            t.resultado = flujo.ejecutar(self.ajustes.heb_url, t.solicitud)
        else:
            t.resultado = flujo.ejecutar(url, t.solicitud)
            page.wait_for_timeout(2000)

    def _correr(self, t):
        ui = InteraccionTrabajo(self, t)
        with self._navegadores:
            if t._cancelar.is_set():
                t.estado = "cancelado"
                return
            t.estado = "ejecutando"
            t.actualizado = _ahora()
            try:
                with sync_playwright() as p:
                    browser = self._lanzar(p)
                    try:
                        ctx = browser.new_context(
                            accept_downloads=True,
                            locale="es-MX",
                            timezone_id="America/Mexico_City",
                            viewport={"width": 1366, "height": 900},
                        )
                        page = ctx.new_page()
                        page.set_default_timeout(30000)

                        def guardar_descarga(d):
                            nombre = re.sub(r"[^\w.-]", "_", d.suggested_filename)
                            d.save_as(str(t.dir / nombre))
                            if nombre not in t.archivos:
                                t.archivos.append(nombre)
                            ui.evento(f"Descarga guardada: {nombre}")

                        page.on("download", guardar_descarga)
                        # Si el portal abre el PDF/XML en otra pestaña, también se captura.
                        ctx.on("page", lambda p: p.on("download", guardar_descarga))
                        if isinstance(t.solicitud, SolicitudOxxo):
                            flujo = FlujoOxxo(page, t.datos, ui, self.ajustes.oxxo_usuario,
                                              self.ajustes.oxxo_password)
                        elif isinstance(t.solicitud, SolicitudHeb):
                            flujo = FlujoHeb(page, t.datos, ui)
                        else:
                            flujo = FlujoWalmart(page, t.datos, ui)
                        try:
                            self._ejecutar_flujo(t, flujo, page)
                        except (FlujoCancelado, FlujoError):
                            raise
                        except Exception:
                            ui.captura(page, "error_inesperado")  # para ver qué había en pantalla
                            raise
                        t.estado = "completado"
                    finally:
                        browser.close()
            except FlujoCancelado as e:
                t.estado = "cancelado"
                t.error = str(e)
            except FlujoError as e:
                t.estado = "error"
                t.error = str(e)
            except Exception as e:
                log.exception("Error inesperado en trabajo %s", t.id)
                t.estado = "error"
                t.error = f"Error inesperado: {type(e).__name__}: {e}"[:2000]
            finally:
                t.pregunta = None
                t.actualizado = _ahora()
                (t.dir / "estado.json").write_text(
                    json.dumps(t.a_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                log.info("Trabajo %s terminó: %s", t.id, t.estado)
                self._avisar_oyentes("on_fin", t)
