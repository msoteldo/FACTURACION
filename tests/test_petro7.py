"""Flujo de Petro Seven contra el portal simulado (tests/mock_portal/petro7.html)."""
import dataclasses

from app import main
from tests.test_api import H, client, esperar_estado, responder


def nueva_p7(folio="1234567", forma_pago="28", **extra):
    body = {"estacion": "10452", "folio": folio, "web_id": "a1b2c3", "fecha": "2026-10-20",
            "uso_cfdi": "G01", "forma_pago": forma_pago, **extra}
    r = client.post("/facturas/petro7", headers=H, json=body)
    assert r.status_code == 202, r.text
    return r.json()


def con_ajustes(monkeypatch, **cambios):
    monkeypatch.setattr(main.gestor, "ajustes", dataclasses.replace(main.gestor.ajustes, **cambios))


def test_inicia_sesion_valida_y_confirma_antes_de_facturar():
    t = esperar_estado(nueva_p7()["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "esperando_respuesta", t["error"]
    p = t["pregunta"]
    assert p["tipo"] == "confirmar_facturar" and "Facturar" in p["texto"]
    assert t["facturado"] is False
    d = p["datos"]
    assert d["estacion"] == "10452" and d["folio"] == "1234567" and d["web_id"] == "A1B2C3"
    assert d["forma_pago"] == "Tarjeta de débito"
    assert d["datos_fiscales_portal"]["RFC"] == "CPA010101AB1"
    assert "G01" in d["uso_cfdi_portal"]
    assert any("Sesión iniciada" in e["mensaje"] for e in t["eventos"])
    responder(t, "facturar")
    t = esperar_estado(t["id"], {"completado", "error", "cancelado"})
    assert t["estado"] == "completado", t["error"]
    assert t["facturado"] is True
    assert "factura ha sido generada" in t["resultado"]["texto_final"]


def test_cancelar_antes_de_facturar_no_factura():
    t = esperar_estado(nueva_p7(folio="1234568")["id"], {"esperando_respuesta", "error"})
    responder(t, "cancelar")
    t = esperar_estado(t["id"], {"cancelado", "error", "completado"})
    assert t["estado"] == "cancelado" and t["facturado"] is False


def test_ticket_rechazado_por_el_portal():
    t = esperar_estado(nueva_p7(folio="0001234")["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "error"
    assert "no aceptó el ticket" in t["error"] and "ya se encuentra facturado" in t["error"]


def test_efectivo_sin_forma_de_pago():
    t = esperar_estado(nueva_p7(folio="5501234", forma_pago="")["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "esperando_respuesta", t["error"]
    assert t["pregunta"]["datos"]["forma_pago"] == "Efectivo"
    responder(t, "cancelar")


def test_pide_forma_de_pago_y_no_se_indico():
    t = esperar_estado(nueva_p7(folio="1234569", forma_pago="")["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "error" and "pide forma de pago" in t["error"]


def test_rfc_de_la_cuenta_distinto_se_pregunta(monkeypatch):
    con_ajustes(monkeypatch, petro7_url=main.gestor.ajustes.petro7_url + "?rfc=XAXX010101000")
    t = esperar_estado(nueva_p7(folio="1234570")["id"], {"esperando_respuesta", "error"})
    assert t["pregunta"]["tipo"] == "revisar_direccion", t["error"]
    assert t["pregunta"]["datos"]["RFC"]["en_portal"] == "XAXX010101000"
    responder(t, "cancelar")
    assert esperar_estado(t["id"], {"cancelado", "error"})["estado"] == "cancelado"


def test_login_incorrecto(monkeypatch):
    con_ajustes(monkeypatch, petro7_password="mala")
    t = esperar_estado(nueva_p7(folio="1234571")["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "error" and "No se pudo iniciar sesión" in t["error"]
    assert "incorrectos" in t["error"]


def test_sin_credenciales(monkeypatch):
    con_ajustes(monkeypatch, petro7_usuario="", petro7_password="")
    t = esperar_estado(nueva_p7(folio="1234572")["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "error" and "PETRO7_USUARIO" in t["error"]


def test_bloqueo_del_detector_de_bots_termina_con_error(monkeypatch):
    # Nunca se intenta pasar el bloqueo: el trabajo termina y se factura a mano.
    con_ajustes(monkeypatch, petro7_url=main.gestor.ajustes.petro7_url + "?bloqueo=1")
    t = esperar_estado(nueva_p7(folio="1234573")["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "error" and "bloqueó el acceso automático" in t["error"]
    assert t["facturado"] is False


def test_valida_parametros():
    r = client.post("/facturas/petro7", headers=H, json={
        "estacion": "abc", "folio": "1", "web_id": "x", "fecha": "2026-10-20", "uso_cfdi": "G01"})
    assert r.status_code == 422
