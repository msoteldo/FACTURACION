"""Conexión a Postgres (Neon en producción, SQLite en pruebas) con SQLAlchemy.

Sin DATABASE_URL el servicio funciona como antes: los datos fiscales salen del entorno
y solo la edición desde la web queda desactivada.
"""
import re

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_motor = None
_sesiones = None


class Base(DeclarativeBase):
    pass


def normalizar_url(url):
    """Render y Neon entregan postgres:// o postgresql://; SQLAlchemy necesita el driver."""
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url.strip())


def iniciar(url):
    global _motor, _sesiones
    cerrar()
    url = normalizar_url(url)
    opciones = {"pool_pre_ping": True, "pool_recycle": 300}
    if url.startswith("sqlite"):
        # Los trabajos corren en hilos: la misma conexión puede usarse desde otro hilo.
        opciones["connect_args"] = {"check_same_thread": False}
    # Neon suspende la base tras unos minutos sin uso y corta las conexiones abiertas;
    # pool_pre_ping las detecta antes de usarlas.
    _motor = create_engine(url, **opciones)
    _sesiones = sessionmaker(_motor, expire_on_commit=False)


def cerrar():
    global _motor, _sesiones
    if _motor is not None:
        _motor.dispose()
    _motor = _sesiones = None


def configurada():
    return _motor is not None


def motor():
    return _motor


def nueva_sesion() -> Session:
    if _sesiones is None:
        raise RuntimeError("La base de datos no está configurada (falta DATABASE_URL).")
    return _sesiones()
