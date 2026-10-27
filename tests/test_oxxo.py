"""Flujo de OXXO contra el portal simulado (tests/mock_portal/oxxo.html)."""
import dataclasses

from app import main
from tests.test_api import H, client, esperar_estado, responder


def nueva_oxxo(folio="1154003", **extra):
    body = {"fecha": "2026-10-09", "folio": folio, "id_venta": "10mon50rcs1", "total": 270,
            "uso_cfdi": "G01", **extra}
    r = client.post("/facturas/oxxo", headers=H, json=body)
    assert r.status_code == 202, r.text
    return r.json()


def test_con_sesion_confirma_antes_de_continuar_y_factura():
    t = nueva_oxxo()
    t = esperar_estado(t["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "esperando_respuesta", t["error"]
    p = t["pregunta"]
    assert p["tipo"] == "confirmar_facturar" and "Continuar" in p["texto"]
    assert t["facturado"] is False
    d = p["datos"]
    assert d["fecha_portal"] == "09/10/2026" and d["id_venta"] == "10MON50RCS1"
    fiscales = d["datos_fiscales_portal"]
    assert fiscales["RFC"] == "CPA010101AB1"                      # elegido de "Selecciona tu RFC"
    assert fiscales["Uso CFDI"] == "Adquisición de mercancias"
    assert fiscales["Régimen fiscal"] == "General de Ley Personas Morales"
    assert any("Sesión iniciada" in e["mensaje"] for e in t["eventos"])
    responder(t, "facturar")
    t = esperar_estado(t["id"], {"completado", "error", "cancelado"})
    assert t["estado"] == "completado", t["error"]
    assert t["facturado"] is True
    assert "Factura generada" in t["resultado"]["texto_final"]


def test_cancelar_no_toca_continuar():
    t = esperar_estado(nueva_oxxo(folio="1154004")["id"], {"esperando_respuesta", "error"})
    responder(t, "cancelar")
    t = esperar_estado(t["id"], {"cancelado", "error", "completado"})
    assert t["estado"] == "cancelado" and t["facturado"] is False


def test_ticket_no_encontrado_y_facturado_en_tienda():
    t = esperar_estado(nueva_oxxo(folio="0001")["id"], {"error", "esperando_respuesta"})
    assert t["estado"] == "error" and "no encontró el ticket" in t["error"]
    t = esperar_estado(nueva_oxxo(folio="9991")["id"], {"error", "esperando_respuesta"})
    assert t["estado"] == "error" and "facturado en tienda" in t["error"]


def test_sin_sesion_llena_datos_y_pasa_verifica(monkeypatch):
    monkeypatch.setattr(main.gestor, "ajustes",
                        dataclasses.replace(main.gestor.ajustes, oxxo_usuario="", oxxo_password=""))
    t = esperar_estado(nueva_oxxo(folio="1154005", uso_cfdi="G03")["id"],
                       {"esperando_respuesta", "error"})
    assert t["pregunta"]["tipo"] == "confirmar_facturar", t["error"]
    responder(t, "facturar")
    t = esperar_estado(t["id"], {"completado", "error", "cancelado"})
    assert t["estado"] == "completado", t["error"]
    assert "06_verifica_tus_datos.png" in t["capturas"]


def test_login_incorrecto(monkeypatch):
    monkeypatch.setattr(main.gestor, "ajustes",
                        dataclasses.replace(main.gestor.ajustes, oxxo_password="mala"))
    t = esperar_estado(nueva_oxxo(folio="1154006")["id"], {"error", "esperando_respuesta"})
    assert t["estado"] == "error" and "iniciar sesión" in t["error"]
    assert "incorrectos" in t["error"]


def test_valida_parametros_oxxo():
    r = client.post("/facturas/oxxo", headers=H, json={
        "fecha": "2026-10-09", "folio": "12a", "id_venta": "X" * 13, "total": 0, "uso_cfdi": "G01"})
    assert r.status_code == 422
