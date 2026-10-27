"""Flujo de HEB contra el portal simulado (tests/mock_portal/heb.html)."""
from datetime import date

from tests.test_api import H, client, esperar_estado, responder


def _mes_anterior(hoy):
    return date(hoy.year - (hoy.month == 1), 12 if hoy.month == 1 else hoy.month - 1, 1)


def nueva_heb(ticket="800544", fecha=None, **extra):
    fecha = fecha or date.today().replace(day=1)
    body = {"sucursal": "2950", "ticket": ticket, "fecha": fecha.isoformat(), "total": 345.7,
            "uso_cfdi": "G01", **extra}
    r = client.post("/facturas/heb", headers=H, json=body)
    assert r.status_code == 202, r.text
    return r.json()


def test_confirma_antes_de_generar_y_factura():
    t = esperar_estado(nueva_heb()["id"], {"esperando_respuesta", "error"})
    assert t["estado"] == "esperando_respuesta", t["error"]
    p = t["pregunta"]
    assert p["tipo"] == "confirmar_facturar" and "Generar factura" in p["texto"]
    assert t["facturado"] is False
    d = p["datos"]
    assert d["comercio"] == "HEB" and d["ticket"] == "800544" and d["sucursal"] == "2950"
    fiscales = d["datos_fiscales_portal"]
    assert fiscales["RFC"] == "CPA010101AB1" and fiscales["Razón social"] == "CLUB DE PADEL DE PRUEBA"
    assert fiscales["Código postal"] == "01000" and fiscales["Correo"] == "facturas@example.com"
    assert fiscales["Régimen fiscal"] == "601 - General de Ley Personas Morales"
    assert fiscales["Uso CFDI"] == "G01 - Adquisición de mercancías."
    assert any("Enterado" not in m and "último día del mes" in m for m in d["mensajes_del_portal"])
    responder(t, "facturar")
    t = esperar_estado(t["id"], {"completado", "error", "cancelado"})
    assert t["estado"] == "completado", t["error"]
    assert t["facturado"] is True
    assert t["resultado"]["serie_folio"] == "JA1933771" and t["resultado"]["estado"] == "VIGENTE"
    assert t["resultado"]["correo"] == "facturas@example.com"
    assert "06_resultado_final.png" in t["capturas"]


def test_cancelar_no_genera_la_factura():
    t = esperar_estado(nueva_heb(ticket="800545")["id"], {"esperando_respuesta", "error"})
    responder(t, "cancelar")
    t = esperar_estado(t["id"], {"cancelado", "error", "completado"})
    assert t["estado"] == "cancelado" and t["facturado"] is False


def test_fecha_de_mes_anterior_navega_el_calendario():
    t = esperar_estado(nueva_heb(ticket="800546", fecha=_mes_anterior(date.today()))["id"],
                       {"esperando_respuesta", "error"})
    assert t["estado"] == "esperando_respuesta", t["error"]
    mes = _mes_anterior(date.today())
    assert t["pregunta"]["datos"]["fecha_portal"] == f"1/{mes.month}/{mes.year}"
    responder(t, "cancelar")
    esperar_estado(t["id"], {"cancelado", "error", "completado"})


def test_ticket_no_aceptado_por_heb():
    t = esperar_estado(nueva_heb(ticket="0001")["id"], {"error", "esperando_respuesta"})
    assert t["estado"] == "error" and "HEB no aceptó el ticket" in t["error"]
    assert "Ticket no encontrado" in t["error"] and t["facturado"] is False


def test_sucursal_inexistente():
    t = esperar_estado(nueva_heb(sucursal="9999")["id"], {"error", "esperando_respuesta"})
    assert t["estado"] == "error" and "no mostró opciones" in t["error"]


def test_valida_parametros_heb():
    r = client.post("/facturas/heb", headers=H, json={
        "sucursal": "29x", "ticket": "12a", "fecha": "2026-10-03", "total": 0, "uso_cfdi": "G02"})
    assert r.status_code == 422
