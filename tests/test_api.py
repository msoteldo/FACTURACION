"""Pruebas de punta a punta contra el portal simulado (tests/mock_portal), con Chromium headless."""
import dataclasses
import time

import pytest
from fastapi.testclient import TestClient

from app import main

H = {"X-API-Key": "clave-de-prueba"}
TC = "9906412345678901234567"

client = TestClient(main.app)


def esperar_estado(tid, estados, timeout=60):
    fin = time.time() + timeout
    while time.time() < fin:
        t = client.get(f"/facturas/{tid}", headers=H).json()
        if t["estado"] in estados:
            return t
        time.sleep(0.3)
    raise AssertionError(f"el trabajo no llegó a {estados}; último estado: {t['estado']} {t.get('error')}")


def responder(t, respuesta):
    r = client.post(f"/facturas/{t['id']}/respuesta", headers=H,
                    json={"pregunta_id": t["pregunta"]["id"], "respuesta": respuesta})
    assert r.status_code == 200, r.text


def nueva(tc=TC, **extra):
    body = {"tc": tc, "tr": "03388", "uso_cfdi": "G01", "forma_pago": "28",
            "metodo_entrega": "descarga", **extra}
    r = client.post("/facturas", headers=H, json=body)
    assert r.status_code == 202, r.text
    return r.json()


def test_salud_sin_auth():
    assert client.get("/salud").json() == {"ok": True}


def test_requiere_api_key():
    assert client.get("/configuracion").status_code == 401
    assert client.get("/configuracion", headers={"X-API-Key": "otra"}).status_code == 401
    conf = client.get("/configuracion", headers=H).json()
    assert conf["datos_fiscales_faltantes"] == []


def test_valida_parametros():
    r = client.post("/facturas", headers=H,
                    json={"tc": "12ab", "tr": "1", "uso_cfdi": "G02", "forma_pago": "01"})
    assert r.status_code == 422


def test_flujo_completo_con_confirmacion_humana():
    t = nueva()
    # Primera pausa: modal de confirmación del portal tras /address.
    t = esperar_estado(t["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "esperando_respuesta", t["error"]
    assert t["pregunta"]["tipo"] == "modal"
    assert "datos fiscales" in t["pregunta"]["texto"]
    responder(t, "continuar")

    # Segunda pausa: el clic irreversible en "Facturar".
    t = esperar_estado(t["id"], {"esperando_respuesta", "error"})
    assert t["pregunta"]["tipo"] == "confirmar_facturar"
    assert t["facturado"] is False
    assert t["pregunta"]["datos"]["forma_pago"] == "Tarjeta de débito"
    assert "Adquisición" in t["pregunta"]["datos"]["uso_cfdi_portal"]

    # Respuesta a una pregunta vieja: se rechaza.
    r = client.post(f"/facturas/{t['id']}/respuesta", headers=H,
                    json={"pregunta_id": t["pregunta"]["id"] - 1, "respuesta": "facturar"})
    assert r.status_code == 409
    responder(t, "facturar")

    t = esperar_estado(t["id"], {"completado", "error", "cancelado"})
    assert t["estado"] == "completado", t["error"]
    assert t["facturado"] is True
    assert "factura_prueba.xml" in t["archivos"]
    assert "Factura generada" in t["resultado"]["texto_final"]
    xml = client.get(f"/facturas/{t['id']}/archivos/factura_prueba.xml", headers=H)
    assert xml.status_code == 200 and b"cfdi:Comprobante" in xml.content
    assert client.get(f"/facturas/{t['id']}/archivos/..%2Fetc", headers=H).status_code == 404


def test_cancelar_antes_de_facturar_no_factura():
    t = nueva(tc="9906400000000000000001")
    t = esperar_estado(t["id"], {"esperando_respuesta"})
    responder(t, "continuar")
    t = esperar_estado(t["id"], {"esperando_respuesta"})
    assert t["pregunta"]["tipo"] == "confirmar_facturar"
    responder(t, "cancelar")
    t = esperar_estado(t["id"], {"cancelado", "completado", "error"})
    assert t["estado"] == "cancelado"
    assert t["facturado"] is False
    assert t["archivos"] == []


def test_captcha_pausa_y_no_se_resuelve():
    t = nueva(tc="9990000000000000000001")
    t = esperar_estado(t["id"], {"esperando_respuesta", "error"})
    assert t["pregunta"]["tipo"] == "captcha"
    assert t["pregunta"]["captura"] in t["capturas"]
    responder(t, "cancelar")
    t = esperar_estado(t["id"], {"cancelado", "error"})
    assert t["estado"] == "cancelado"


def test_error_del_portal_incluye_texto_visible():
    t = nueva(tc="0000000000000000000001")
    t = esperar_estado(t["id"], {"error", "esperando_respuesta"})
    assert t["estado"] == "error"
    assert "Ticket ya facturado" in t["error"]


def test_tc_duplicado_activo_se_rechaza():
    t = nueva(tc="9906411111111111111111")
    r = client.post("/facturas", headers=H, json={
        "tc": "9906411111111111111111", "tr": "1", "uso_cfdi": "G03", "forma_pago": "04"})
    assert r.status_code == 409
    client.post(f"/facturas/{t['id']}/cancelar", headers=H)
    esperar_estado(t["id"], {"cancelado", "error", "completado"})


def test_diferencias_en_datos_fiscales_se_preguntan(monkeypatch):
    datos = dataclasses.replace(main.gestor.datos, razon_social="CLUB DE PADEL SA DE CV")
    monkeypatch.setattr(main.gestor, "datos", datos)
    t = nueva(tc="9906422222222222222222")
    t = esperar_estado(t["id"], {"esperando_respuesta", "error"})
    assert t["pregunta"]["tipo"] == "revisar_direccion"
    assert t["pregunta"]["datos"]["RAZON_SOCIAL"]["en_portal"] == "CLUB DE PADEL S.A. DE C.V."
    responder(t, "cancelar")
    assert esperar_estado(t["id"], {"cancelado", "error"})["estado"] == "cancelado"


def test_diagnostico_portal():
    r = client.post("/diagnostico/portal", headers=H)
    t = esperar_estado(r.json()["id"], {"completado", "error"})
    assert t["estado"] == "completado", t["error"]
    names = {e["name"] for e in t["resultado"]["elementos"]}
    assert {"membershipOrRFC", "postalCode", "ticketNumber", "transactionNumber"} <= names
    assert t["resultado"]["captcha_visible"] is False


def test_consulta_descarga_y_no_reenvia():
    r = client.post("/consultas", headers=H, json={"numero": "9906433333333333333333"})
    assert r.status_code == 202, r.text
    t = esperar_estado(r.json()["id"], {"completado", "error", "esperando_respuesta"})
    assert t["estado"] == "completado", t["error"]
    assert sorted(t["archivos"]) == ["consulta.pdf", "consulta.xml"]
    assert not any("Reenviar" in d for d in t["resultado"]["descargas_intentadas"])
    assert "folio ABC123" in t["resultado"]["texto_visible"]
    assert t["facturado"] is False
    pdf = client.get(f"/facturas/{t['id']}/archivos/consulta.pdf", headers=H)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_consulta_valida_numero():
    assert client.post("/consultas", headers=H, json={"numero": "abc 123;"}).status_code == 422
