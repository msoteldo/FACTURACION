"""Datos fiscales del receptor guardados en la base (una sola fila).

Si la tabla está vacía o no hay base, se usan los del entorno. Si la base está
configurada pero falla, NO se usa el entorno: se podría facturar con un RFC viejo.
"""
import dataclasses
import logging
from datetime import datetime, timezone

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
