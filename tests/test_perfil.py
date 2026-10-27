"""Datos fiscales editables: se leen al crear cada trabajo y se ven/editan con /perfil."""
import dataclasses
import logging

import pytest

from app import db, main
from app.config import DatosFiscales
from app.perfil import PerfilFiscal, PerfilNoDisponible, obtener_datos
from tests.test_api import H, client, nueva

FACTURA = {"tc": "9906455555555555555555", "tr": "03388", "uso_cfdi": "G01", "forma_pago": "28",
           "metodo_entrega": "descarga"}


def guardar_fila(**cambios):
    """Upsert de la fila 1 directo en la base (solo para pruebas)."""
    campos = {**dataclasses.asdict(DatosFiscales.desde_env()), **cambios}
    with db.nueva_sesion() as s:
        s.merge(PerfilFiscal(id=1, **campos))
        s.commit()


def lanza(error):
    def f():
        raise error
    return f


def test_trabajo_conserva_sus_datos(base_sqlite):
    t = nueva(tc="9906433333333333333333")
    guardar_fila(rfc="XAXX010101000")
    assert main.gestor.obtener(t["id"]).datos.rfc == "CPA010101AB1"
    assert obtener_datos().rfc == "XAXX010101000"
    client.post(f"/facturas/{t['id']}/cancelar", headers=H)


def test_base_caida_responde_503_sin_crear_trabajo(monkeypatch):
    monkeypatch.setattr(main.gestor, "obtener_datos", lanza(PerfilNoDisponible()))
    antes = set(main.gestor.trabajos)
    r = client.post("/facturas", headers=H, json=FACTURA)
    assert r.status_code == 503 and "perfil fiscal" in r.json()["detail"]
    assert set(main.gestor.trabajos) == antes


def test_datos_incompletos_responde_503_sin_crear_trabajo(monkeypatch):
    incompletos = dataclasses.replace(DatosFiscales.desde_env(), rfc="")
    monkeypatch.setattr(main.gestor, "obtener_datos", lambda: incompletos)
    antes = set(main.gestor.trabajos)
    r = client.post("/facturas", headers=H, json=FACTURA)
    assert r.status_code == 503 and "RFC" in r.json()["detail"]
    assert set(main.gestor.trabajos) == antes


def test_duplicado_trae_trabajo_id():
    t = nueva(tc="9906444444444444444444")
    r = client.post("/facturas", headers=H, json={**FACTURA, "tc": "9906444444444444444444"})
    assert r.status_code == 409
    assert r.json()["detail"]["trabajo_id"] == t["id"]
    assert t["id"] in r.json()["detail"]["error"]
    client.post(f"/facturas/{t['id']}/cancelar", headers=H)


PERFIL = {"rfc": "CPA010101AB1", "cp": "01000", "razon_social": "CLUB DE PADEL DE PRUEBA",
          "calle": "Calle Falsa", "num_ext": "123", "num_int": "", "referencia": "",
          "estado": "Ciudad de México", "municipio": "Benito Juárez", "colonia": "Centro",
          "email": "facturas@example.com", "regimen_texto": "General de Ley Personas Morales"}


def test_get_perfil_desde_entorno(base_sqlite):
    r = client.get("/perfil", headers=H).json()
    assert r["origen"] == "entorno" and r["datos"]["rfc"] == "CPA010101AB1" and r["faltantes"] == []


def test_put_normaliza_y_guarda(base_sqlite):
    r = client.put("/perfil", headers=H, json={**PERFIL, "rfc": " xaxx 010101000 "})
    assert r.status_code == 200 and r.json()["origen"] == "base"
    assert client.get("/perfil", headers=H).json()["datos"]["rfc"] == "XAXX010101000"
    assert obtener_datos().rfc == "XAXX010101000"


@pytest.mark.parametrize("cambio", [{"cp": "1234"}, {"rfc": "ABC"}, {"email": "no"}, {"calle": "  "}])
def test_put_invalido_422(base_sqlite, cambio):
    assert client.put("/perfil", headers=H, json={**PERFIL, **cambio}).status_code == 422


def test_put_sin_base_503():
    r = client.put("/perfil", headers=H, json=PERFIL)
    assert r.status_code == 503 and "DATABASE_URL" in r.json()["detail"]


def test_put_no_loguea_valores(base_sqlite, caplog):
    caplog.set_level(logging.DEBUG)
    client.put("/perfil", headers=H, json={**PERFIL, "rfc": "XAXX010101000"})
    assert "XAXX010101000" not in caplog.text


def test_perfil_requiere_autenticacion():
    assert client.get("/perfil").status_code == 401
