"""API de facturación automática de tickets (receptor CFDI 4.0).

Flujo típico desde un frontend (web, bot de Telegram, etc.):
  1. POST /tickets/extraer         foto -> TC, TR, total, forma de pago sugerida...
  2. POST /facturas                inicia la automatización del portal (en segundo plano)
  3. GET  /facturas/{id}           consultar (polling) hasta estado "esperando_respuesta"
  4. POST /facturas/{id}/respuesta responder la pregunta vigente (modal, captcha,
                                   o la confirmación final "facturar")
  5. GET  /facturas/{id}/archivos/{nombre}  bajar XML/PDF o capturas de pantalla

Todas las rutas (salvo /salud) requieren el header X-API-Key.
"""
import logging
import secrets
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Security, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, EmailStr, Field
from starlette.concurrency import run_in_threadpool

from . import extraccion
from .config import Ajustes, DatosFiscales
from .portal_walmart import SolicitudFactura
from .trabajos import GestorTrabajos

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ajustes = Ajustes.desde_env()
datos_fiscales = DatosFiscales.desde_env()
gestor = GestorTrabajos(ajustes, datos_fiscales)

app = FastAPI(
    title="Facturación de tickets",
    description="Extrae datos de tickets y automatiza el portal de facturación de Sam's/Walmart. "
                "El clic final en 'Facturar' siempre requiere confirmación humana.",
    version="0.1.0",
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def requiere_api_key(clave: Optional[str] = Security(api_key_header)):
    if not ajustes.api_key:
        raise HTTPException(503, "El servidor no tiene API_KEY configurada; se rechazan todas las solicitudes.")
    # Tolera espacios o comillas que se cuelan al copiar y pegar la clave.
    clave = (clave or "").strip().strip("\"'")
    if not clave or not secrets.compare_digest(clave.encode(), ajustes.api_key.encode()):
        raise HTTPException(401, "X-API-Key inválida o ausente.")


protegido = [Depends(requiere_api_key)]


# ------------------------------------------------------------------ modelos

class NuevaFactura(BaseModel):
    comercio: Literal["sams", "walmart"] = "sams"
    tc: str = Field(..., pattern=r"^\d{10,30}$", description="TC# del ticket (solo dígitos)")
    tr: str = Field(..., pattern=r"^\d{1,10}$", description="TR# del ticket (solo dígitos)")
    uso_cfdi: Literal["G01", "G03"] = Field(
        ..., description="G01 = Adquisición de mercancías (reventa en barra/snack); G03 = Gastos en general")
    forma_pago: Literal["04", "28", "05"] = Field(
        ..., description="04 = Tarjeta de crédito, 28 = Tarjeta de débito, 05 = Monedero electrónico")
    metodo_entrega: Literal["email", "descarga"] = "descarga"
    correo_alterno: Optional[EmailStr] = None


class Respuesta(BaseModel):
    pregunta_id: int = Field(..., description="El 'id' de la pregunta vigente (evita responder una pregunta vieja)")
    respuesta: str = Field(..., description="Una de las 'opciones' de la pregunta")


# ------------------------------------------------------------------ rutas

@app.get("/salud")
def salud():
    """Health check para Render (sin autenticación, sin datos sensibles)."""
    return {"ok": True}


@app.get("/configuracion", dependencies=protegido)
def configuracion():
    """Qué falta configurar, sin revelar valores."""
    return {
        "datos_fiscales_faltantes": datos_fiscales.faltantes(),
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
    faltan = datos_fiscales.faltantes()
    if faltan:
        raise HTTPException(503, f"Faltan datos fiscales en las variables de entorno: {faltan}")
    solicitud = SolicitudFactura(
        tc=body.tc, tr=body.tr, uso_cfdi=body.uso_cfdi, forma_pago=body.forma_pago,
        metodo_entrega=body.metodo_entrega,
        correo_alterno=str(body.correo_alterno) if body.correo_alterno else None,
    )
    try:
        t = gestor.crear("factura", solicitud)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return t.a_dict()


@app.post("/diagnostico/portal", status_code=202, dependencies=protegido)
def diagnosticar_portal():
    """Abre el portal en modo headless y lista campos/botones (equivale a --inspect).
    Sirve para comprobar desde Render que el portal no se comporta distinto en headless."""
    return gestor.crear("inspeccion").a_dict()


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
