"""Base de datos del perfil fiscal: URL, origen de los datos y migración."""
import dataclasses
import os

import alembic.command
import alembic.config
import pytest

from app import db
from app.config import DatosFiscales
from app.db import normalizar_url
from app.perfil import PerfilFiscal, PerfilNoDisponible, obtener_datos, origen_actual


def test_normaliza_urls():
    assert normalizar_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert (normalizar_url("postgresql://u:p@h/db?sslmode=require")
            == "postgresql+psycopg://u:p@h/db?sslmode=require")
    assert normalizar_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"
    assert normalizar_url("sqlite:///x.db") == "sqlite:///x.db"


def test_sin_base_usa_entorno():
    assert not db.configurada()
    assert obtener_datos() == DatosFiscales.desde_env()
    assert origen_actual() == "entorno"


def test_tabla_vacia_usa_entorno(base_sqlite):
    assert obtener_datos().rfc == "CPA010101AB1"
    assert origen_actual() == "entorno"


def test_fila_guardada_gana(base_sqlite):
    with db.nueva_sesion() as s:
        campos = {**dataclasses.asdict(DatosFiscales.desde_env()), "rfc": "XAXX010101000"}
        s.add(PerfilFiscal(id=1, **campos))
        s.commit()
    assert obtener_datos().rfc == "XAXX010101000"
    assert origen_actual() == "base"


def test_base_caida_no_usa_entorno():
    db.iniciar("postgresql+psycopg://x:y@127.0.0.1:1/nada?connect_timeout=1")
    try:
        with pytest.raises(PerfilNoDisponible):
            obtener_datos()
    finally:
        db.cerrar()


def _migrar_y_comparar(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = alembic.config.Config("alembic.ini")
    alembic.command.upgrade(cfg, "head")
    alembic.command.check(cfg)


def test_migracion_coincide_con_modelo(tmp_path, monkeypatch):
    _migrar_y_comparar(monkeypatch, f"sqlite:///{tmp_path}/m.db")


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="sin Postgres de prueba")
def test_migracion_en_postgres(monkeypatch):
    _migrar_y_comparar(monkeypatch, os.environ["TEST_DATABASE_URL"])


def test_postgres_con_timeout_de_conexion(monkeypatch):
    capturado = {}

    def falso(url, **kwargs):
        capturado.update(kwargs)
        return type("Motor", (), {"dispose": lambda self: None})()
    monkeypatch.setattr(db, "create_engine", falso)
    db.iniciar("postgresql://u:p@h/db")
    db.cerrar()
    assert capturado["connect_args"]["connect_timeout"] == 10
