"""Datos fiscales del receptor guardados en la base (una sola fila).

Si la tabla está vacía o no hay base, se usan los del entorno. Si la base está
configurada pero falla, NO se usa el entorno: se podría facturar con un RFC viejo.
"""
import dataclasses
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column

from . import db
from .config import DatosFiscales
from .db import Base

log = logging.getLogger("facturacion.perfil")

CAMPOS = tuple(f.name for f in dataclasses.fields(DatosFiscales))


class PerfilNoDisponible(Exception):
    def __init__(self, mensaje="No se pudo leer el perfil fiscal; intenta en un momento."):
        super().__init__(mensaje)


def _ahora():
    return datetime.now(timezone.utc)


class PerfilFiscal(Base):
    __tablename__ = "perfil_fiscal"
    __table_args__ = (CheckConstraint("id = 1", name="perfil_fiscal_una_fila"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rfc: Mapped[str] = mapped_column(String(13), default="")
    cp: Mapped[str] = mapped_column(String(5), default="")
    razon_social: Mapped[str] = mapped_column(String(300), default="")
    calle: Mapped[str] = mapped_column(String(200), default="")
    num_ext: Mapped[str] = mapped_column(String(50), default="")
    num_int: Mapped[str] = mapped_column(String(50), default="")
    referencia: Mapped[str] = mapped_column(String(200), default="")
    estado: Mapped[str] = mapped_column(String(100), default="")
    municipio: Mapped[str] = mapped_column(String(100), default="")
    colonia: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    regimen_texto: Mapped[str] = mapped_column(String(200), default="")
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora,
                                                  onupdate=_ahora)

    def a_datos(self):
        return DatosFiscales(**{c: getattr(self, c) or "" for c in CAMPOS})


def _fila():
    """La fila guardada, o None si la tabla está vacía. Lanza PerfilNoDisponible si la base falla."""
    try:
        with db.nueva_sesion() as s:
            return s.get(PerfilFiscal, 1)
    except SQLAlchemyError as e:
        # Solo el tipo: el mensaje puede traer la cadena de conexión.
        log.warning("No se pudo leer el perfil fiscal: %s", type(e).__name__)
        raise PerfilNoDisponible() from e


def obtener_datos():
    if not db.configurada():
        return DatosFiscales.desde_env()
    fila = _fila()
    return fila.a_datos() if fila else DatosFiscales.desde_env()


def origen_actual():
    if not db.configurada():
        return "entorno"
    return "base" if _fila() else "entorno"


# ------------------------------------------------------------------ API

class PerfilEntrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    rfc: str = Field(..., pattern=r"^[A-ZÑ&0-9]{12,13}$")
    cp: str = Field(..., pattern=r"^\d{5}$")
    razon_social: str = Field(..., min_length=1, max_length=300)
    calle: str = Field(..., min_length=1, max_length=200)
    num_ext: str = Field(..., min_length=1, max_length=50)
    num_int: str = Field("", max_length=50)
    referencia: str = Field("", max_length=200)
    estado: str = Field(..., min_length=1, max_length=100)
    municipio: str = Field(..., min_length=1, max_length=100)
    colonia: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    regimen_texto: str = Field("General de Ley Personas Morales", min_length=1, max_length=200)

    @field_validator("rfc", mode="before")
    @classmethod
    def _rfc_limpio(cls, v):
        return "".join(str(v).split()).upper() if isinstance(v, str) else v


router = APIRouter(tags=["perfil"])


def _respuesta(datos, origen):
    return {"origen": origen, "datos": dataclasses.asdict(datos), "faltantes": datos.faltantes()}


@router.get("/perfil")
def ver_perfil():
    """Datos fiscales vigentes y de dónde salen ("base" o "entorno")."""
    try:
        return _respuesta(obtener_datos(), origen_actual())
    except PerfilNoDisponible as e:
        raise HTTPException(503, str(e))


@router.put("/perfil")
def guardar_perfil(body: PerfilEntrada):
    """Guarda los datos fiscales; aplican a las facturas que se creen después."""
    if not db.configurada():
        raise HTTPException(503, "Configura DATABASE_URL para editar los datos desde la web.")
    campos = body.model_dump()
    campos["email"] = str(campos["email"])
    try:
        with db.nueva_sesion() as s:
            s.merge(PerfilFiscal(id=1, **campos))
            s.commit()
    except SQLAlchemyError as e:
        log.warning("No se pudo guardar el perfil fiscal: %s", type(e).__name__)
        raise HTTPException(503, "No se pudo guardar el perfil fiscal; intenta en un momento.")
    log.info("Perfil fiscal actualizado.")
    return _respuesta(DatosFiscales(**campos), "base")
