"""API de facturación automática de tickets (receptor CFDI 4.0).

Flujo típico desde un frontend (web, bot de Telegram, etc.):
  1. POST /tickets/extraer         foto -> TC, TR, total, forma de pago sugerida...
  2. POST /facturas                inicia la automatización del portal (en segundo plano)
  3. GET  /facturas/{id}           consultar (polling) hasta estado "esperando_respuesta"
  4. POST /facturas/{id}/respuesta responder la pregunta vigente (modal, captcha,
                                   o la confirmación final "facturar")
  5. GET  /facturas/{id}/archivos/{nombre}  bajar XML/PDF o capturas de pantalla

Para un ticket ya facturado: POST /consultas y luego los pasos 3 y 5.

Todas las rutas (salvo /salud) requieren el header X-API-Key.
"""
import logging
from contextlib import asynccontextmanager
import secrets
import threading
import time
from pathlib import Path
from datetime import date
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Security, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, EmailStr, Field
from starlette.concurrency import run_in_threadpool

from . import db, extraccion, perfil, sesion
from .config import Ajustes
from .perfil import PerfilNoDisponible, obtener_datos
from .portal_heb import SolicitudHeb
from .portal_oxxo import SolicitudOxxo
from .portal_walmart import SolicitudConsulta, SolicitudFactura
from .telegram_bot import BotFacturacion, TelegramError
from .trabajos import DatosIncompletos, GestorTrabajos, TrabajoDuplicado

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ajustes = Ajustes.desde_env()
if ajustes.database_url:
    db.iniciar(ajustes.database_url)
gestor = GestorTrabajos(ajustes, obtener_datos)

bot = None
if ajustes.telegram_token:
    bot = BotFacturacion(ajustes, gestor)
    gestor.oyentes.append(bot)


def registrar_webhook_telegram():
    """Registra el webhook al arrancar (idempotente); en segundo plano para no frenar el arranque."""
    if not bot or not ajustes.telegram_secreto or not ajustes.url_publica:
        return

    def registrar():
        try:
            bot.configurar_webhook()
            logging.getLogger("facturacion").info("Webhook de Telegram registrado.")
        except TelegramError as e:
            logging.getLogger("facturacion").warning("No se pudo registrar el webhook: %s", e)

    threading.Thread(target=registrar, daemon=True).start()


@asynccontextmanager
async def lifespan(_app):
    registrar_webhook_telegram()
    yield


app = FastAPI(
    title="Facturación de tickets",
    description="Extrae datos de tickets y automatiza el portal de facturación de Sam's/Walmart. "
                "El clic final en 'Facturar' siempre requiere confirmación humana.",
    version="0.1.0",
    lifespan=lifespan,
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

ESTATICOS = Path(__file__).parent / "static"
HEADERS_SEGURIDAD = {
    # El JS de la webapp vive en archivos propios: nada inline. frame-ancestors evita que otro
    # sitio meta la app en un iframe y engañe con el clic irreversible en "Facturar".
    "Content-Security-Policy": "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}


@app.middleware("http")
async def headers_de_seguridad(request: Request, call_next):
    respuesta = await call_next(request)
    for nombre, valor in HEADERS_SEGURIDAD.items():
        respuesta.headers.setdefault(nombre, valor)
    return respuesta




def requiere_api_key(request: Request, clave: Optional[str] = Security(api_key_header)):
    """Acepta el header X-API-Key (curl, bot) o la cookie de la webapp."""
    if not ajustes.api_key:
        raise HTTPException(503, "El servidor no tiene API_KEY configurada; se rechazan todas las solicitudes.")
    clave = sesion.limpiar_clave(clave)
    if clave and secrets.compare_digest(clave.encode(), ajustes.api_key.encode()):
        return
    if sesion.token_valido(request.cookies.get(sesion.COOKIE), ajustes.api_key, time.time()):
        # Un formulario de otro sitio no puede mandar este header: segunda barrera contra CSRF
        # (la primera es SameSite=Strict).
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "fetch":
            raise HTTPException(403, "Falta el header X-Requested-With: fetch.")
        return
    raise HTTPException(401, "X-API-Key inválida o ausente.")


protegido = [Depends(requiere_api_key)]
app.include_router(perfil.router, dependencies=protegido)
app.include_router(sesion.crear_router(lambda: ajustes.api_key))


# ------------------------------------------------------------------ modelos

class NuevaFactura(BaseModel):
    comercio: Literal["sams", "walmart"] = "sams"
    tc: str = Field(..., pattern=r"^\d{10,30}$", description="TC# del ticket (solo dígitos)")
    tr: str = Field(..., pattern=r"^\d{1,10}$", description="TR# del ticket (solo dígitos)")
    uso_cfdi: Literal["G01", "G03"] = Field(
        ..., description="G01 = Adquisición de mercancías (reventa en barra/snack); G03 = Gastos en general")
    forma_pago: Literal["01", "04", "28", "05"] = Field(
        ..., description="01 = Efectivo, 04 = Tarjeta de crédito, 28 = Tarjeta de débito, "
                         "05 = Monedero electrónico (si el portal no pide forma de pago, se ignora)")
    metodo_entrega: Literal["email", "descarga"] = "email"
    correo_alterno: Optional[EmailStr] = None


class NuevaFacturaOxxo(BaseModel):
    fecha: date = Field(..., description="Fecha de venta del ticket (AAAA-MM-DD)")
    folio: str = Field(..., pattern=r"^\d{1,9}$", description="Fol_Vta del ticket")
    id_venta: str = Field(..., pattern=r"^[A-Za-z0-9]{1,12}$", description="ID= del ticket")
    total: float = Field(..., gt=0, description="Total del ticket")
    uso_cfdi: Literal["G01", "G03"]


class NuevaFacturaHeb(BaseModel):
    sucursal: str = Field(..., pattern=r"^\d{1,6}$", description="Código de sucursal (p. ej. 2950)")
    ticket: str = Field(..., pattern=r"^\d{1,12}$", description="Número de ticket")
    fecha: date = Field(..., description="Fecha de compra (AAAA-MM-DD)")
    total: float = Field(..., gt=0, description="Venta total del ticket")
    uso_cfdi: Literal["G01", "G03"]


class NuevaConsulta(BaseModel):
    numero: str = Field(..., pattern=r"^[A-Za-z0-9-]{1,40}$",
                        description="Número de ticket (TC#) o folio de la factura")


class Respuesta(BaseModel):
    pregunta_id: int = Field(..., description="El 'id' de la pregunta vigente (evita responder una pregunta vieja)")
    respuesta: str = Field(..., description="Una de las 'opciones' de la pregunta")


# ------------------------------------------------------------------ rutas

def _crear(tipo, solicitud=None):
    """Crea un trabajo y traduce los errores de creación a respuestas HTTP."""
    try:
        return gestor.crear(tipo, solicitud).a_dict()
    except PerfilNoDisponible as e:
        raise HTTPException(503, str(e))
    except DatosIncompletos as e:
        raise HTTPException(503, f"Faltan datos fiscales: {e.faltantes}")
    except TrabajoDuplicado as e:
        raise HTTPException(409, {"error": str(e), "trabajo_id": e.trabajo_id})


@app.get("/salud")
def salud():
    """Health check para Render (sin autenticación, sin datos sensibles)."""
    return {"ok": True}


@app.get("/sesion", dependencies=protegido)
def ver_sesion():
    """La webapp lo usa para saber si sigue con sesión (no toca la base)."""
    return {"ok": True}


@app.get("/configuracion", dependencies=protegido)
def configuracion():
    """Qué falta configurar, sin revelar valores."""
    try:
        faltantes = gestor.obtener_datos().faltantes()
    except PerfilNoDisponible as e:
        raise HTTPException(503, str(e))
    return {
        "datos_fiscales_faltantes": faltantes,
        "gemini_configurado": bool(ajustes.gemini_api_key),
        "gemini_modelo": ajustes.gemini_modelo,
        "headless": ajustes.headless,
        "navegador_canal": ajustes.navegador_canal or "chrome-headless-shell",
    }


@app.post("/tickets/extraer", dependencies=protegido)
async def extraer_ticket(foto: UploadFile = File(...)):
    tipo = foto.content_type or ""
    if not tipo.startswith("image/"):
        raise HTTPException(415, "Sube una imagen (image/jpeg, image/png, image/webp...).")
    datos = await foto.read(ajustes.max_bytes_imagen + 1)
    if len(datos) > ajustes.max_bytes_imagen:
        raise HTTPException(413, f"La imagen pesa más de {ajustes.max_bytes_imagen // (1024 * 1024)} MB.")
    try:
        # requests es bloqueante: se corre en el threadpool para no frenar el event loop.
        return await run_in_threadpool(
            extraccion.leer_ticket, ajustes.gemini_api_key, ajustes.gemini_modelo, datos, tipo)
    except extraccion.ExtraccionError as e:
        raise HTTPException(e.status_http, {"error": e.mensaje, "detalle": e.detalle})


@app.post("/facturas", status_code=202, dependencies=protegido)
def crear_factura(body: NuevaFactura):
    solicitud = SolicitudFactura(
        tc=body.tc, tr=body.tr, uso_cfdi=body.uso_cfdi, forma_pago=body.forma_pago,
        metodo_entrega=body.metodo_entrega,
        correo_alterno=str(body.correo_alterno) if body.correo_alterno else None,
    )
    return _crear("factura", solicitud)


@app.post("/facturas/oxxo", status_code=202, dependencies=protegido)
def crear_factura_oxxo(body: NuevaFacturaOxxo):
    """Factura un ticket de OXXO. Se detiene a confirmar ANTES de 'Continuar', que en OXXO
    puede generar la factura de inmediato. El avance se ve con GET /facturas/{id}."""
    sol = SolicitudOxxo(fecha=body.fecha, folio=body.folio, id_venta=body.id_venta.upper(),
                        total=round(body.total, 2), uso_cfdi=body.uso_cfdi)
    return _crear("factura", sol)


@app.post("/facturas/heb", status_code=202, dependencies=protegido)
def crear_factura_heb(body: NuevaFacturaHeb):
    """Factura un ticket de HEB. Se detiene a confirmar ANTES de 'Generar factura', que emite
    la factura de inmediato. El avance se ve con GET /facturas/{id}."""
    sol = SolicitudHeb(sucursal=str(int(body.sucursal)), ticket=body.ticket, fecha=body.fecha,
                       total=round(body.total, 2), uso_cfdi=body.uso_cfdi)
    return _crear("factura", sol)


@app.post("/consultas", status_code=202, dependencies=protegido)
def crear_consulta(body: NuevaConsulta):
    """Busca un ticket YA facturado en "Consulta o reenvía tu factura" y descarga el
    XML/PDF si el portal lo ofrece. No factura ni reenvía nada. El avance y los archivos
    se ven igual que una factura: GET /facturas/{id}."""
    return _crear("consulta", SolicitudConsulta(tc=body.numero))


@app.post("/diagnostico/portal", status_code=202, dependencies=protegido)
def diagnosticar_portal():
    """Abre el portal en modo headless y lista campos/botones (equivale a --inspect).
    Sirve para comprobar desde Render que el portal no se comporta distinto en headless."""
    return _crear("inspeccion")


def _trabajo(trabajo_id):
    t = gestor.obtener(trabajo_id)
    if t is None:
        raise HTTPException(404, "Trabajo no encontrado (¿el servicio se reinició o ya expiró?).")
    return t


@app.get("/facturas/{trabajo_id}", dependencies=protegido)
def ver_factura(trabajo_id: str):
    return _trabajo(trabajo_id).a_dict()


@app.post("/facturas/{trabajo_id}/respuesta", dependencies=protegido)
def responder(trabajo_id: str, body: Respuesta):
    _trabajo(trabajo_id)
    try:
        gestor.responder(trabajo_id, body.pregunta_id, body.respuesta)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/facturas/{trabajo_id}/cancelar", dependencies=protegido)
def cancelar(trabajo_id: str):
    t = _trabajo(trabajo_id)
    if t.facturado:
        raise HTTPException(409, "Ya se dio clic en 'Facturar'; no se puede cancelar.")
    gestor.cancelar(trabajo_id)
    return {"ok": True}


@app.get("/facturas/{trabajo_id}/archivos/{nombre}", dependencies=protegido)
def descargar(trabajo_id: str, nombre: str):
    t = _trabajo(trabajo_id)
    if nombre not in t.archivos and nombre not in t.capturas:
        raise HTTPException(404, "Archivo no encontrado.")
    return FileResponse(t.dir / nombre, filename=nombre)


# ------------------------------------------------------------------ Telegram

@app.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request,
                           x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    """Recibe los mensajes del bot. Telegram manda el secreto registrado con setWebhook;
    sin él (o sin bot configurado) se rechaza."""
    if not bot or not ajustes.telegram_secreto:
        raise HTTPException(404)
    if not x_telegram_bot_api_secret_token or not secrets.compare_digest(
            x_telegram_bot_api_secret_token.encode(), bot.secreto.encode()):
        raise HTTPException(401)
    update = await request.json()
    # Se responde de inmediato: leer la foto o llenar el portal tarda más de lo que
    # Telegram espera, y si no recibe 200 reenvía el mismo mensaje.
    threading.Thread(target=bot.procesar, args=(update,), daemon=True).start()
    return {"ok": True}


@app.post("/telegram/configurar", dependencies=protegido)
def telegram_configurar():
    """Registra (o re-registra) el webhook del bot en Telegram."""
    if not bot:
        raise HTTPException(503, "Falta TELEGRAM_BOT_TOKEN.")
    if not ajustes.telegram_secreto:
        raise HTTPException(503, "Falta TELEGRAM_WEBHOOK_SECRET.")
    try:
        bot.configurar_webhook()
        info = bot.api.llamar("getWebhookInfo")
    except TelegramError as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "url": info.get("url"), "pendientes": info.get("pending_update_count"),
            "ultimo_error": info.get("last_error_message"),
            "chats_autorizados": len(ajustes.telegram_chats)}


# ------------------------------------------------------------------ webapp
# Al final: el montaje en "/" atiende solo lo que ninguna ruta anterior atendió.
app.mount("/", StaticFiles(directory=ESTATICOS, html=True), name="webapp")
