import json

import pytest

from app import extraccion


class Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text or json.dumps(data)

    def json(self):
        return self._data


def respuesta_gemini(ticket):
    return {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(ticket) + "\n```"}]}}]}


TICKET = {
    "comercio": "Sam's Club", "tc_numero_ticket": "9906 4123 4567 8901 2345 67",
    "tr_numero_transaccion": "03388", "total": 1234.5, "forma_de_pago": "debito",
    "confianza_tc": "alta", "confianza_tr": "alta", "advertencias": [],
}


def test_parsea_y_valida(monkeypatch):
    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(200, respuesta_gemini(TICKET)))
    r = extraccion.leer_ticket("k", "m", b"img", "image/jpeg")
    assert r["tc_numero_ticket"] == "9906412345678901234567"
    assert r["forma_pago_sat_sugerida"] == "28"
    assert r["requiere_revision"] is False


def test_baja_confianza_requiere_revision(monkeypatch):
    t = dict(TICKET, confianza_tr="media", tr_numero_transaccion="O3388")
    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(200, respuesta_gemini(t)))
    r = extraccion.leer_ticket("k", "m", b"img", "image/jpeg")
    assert r["requiere_revision"] is True
    assert any("TR#" in a for a in r["advertencias"])


def test_reintenta_503(monkeypatch):
    llamadas = []

    def post(*a, **k):
        llamadas.append(1)
        return Resp(503, text="overloaded") if len(llamadas) < 3 else Resp(200, respuesta_gemini(TICKET))

    monkeypatch.setattr(extraccion.requests, "post", post)
    r = extraccion.leer_ticket("k", "m", b"img", "image/jpeg", espera_base=0)
    assert len(llamadas) == 3 and r["total"] == 1234.5


def test_404_modelo_mensaje_claro(monkeypatch):
    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(404, text="model no longer available"))
    with pytest.raises(extraccion.ExtraccionError) as e:
        extraccion.leer_ticket("k", "gemini-viejo", b"img", "image/jpeg", espera_base=0)
    assert "GEMINI_MODEL" in e.value.mensaje


def test_endpoint_extraer(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(200, respuesta_gemini(TICKET)))
    c = TestClient(main.app)
    H = {"X-API-Key": "clave-de-prueba"}
    r = c.post("/tickets/extraer", headers=H, files={"foto": ("t.jpg", b"img", "image/jpeg")})
    assert r.status_code == 200 and r.json()["tr_numero_transaccion"] == "03388"
    r = c.post("/tickets/extraer", headers=H, files={"foto": ("t.txt", b"x", "text/plain")})
    assert r.status_code == 415


TICKET_OXXO = {
    "comercio": "OXXO", "comercio_clave": "oxxo", "fecha": "2026-10-09",
    "oxxo_folio_venta": "1154003", "oxxo_id_venta": "10mon50rcs1", "total": 270.0,
    "forma_de_pago": "debito", "confianza_id_venta": "alta", "advertencias": [],
}


def test_oxxo_valida_folio_e_id(monkeypatch):
    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(200, respuesta_gemini(TICKET_OXXO)))
    r = extraccion.leer_ticket("k", "m", b"img", "image/jpeg")
    assert r["comercio_clave"] == "oxxo"
    assert r["oxxo_id_venta"] == "10MON50RCS1"  # en mayúsculas, como lo pide el portal
    assert r["forma_pago_sat_sugerida"] == "28"
    assert r["requiere_revision"] is False
    assert not any("TC#" in a for a in r["advertencias"])  # no se exigen campos de Walmart


def test_oxxo_id_dudoso_requiere_revision(monkeypatch):
    t = dict(TICKET_OXXO, confianza_id_venta="media", oxxo_folio_venta="11540O3", fecha=None)
    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(200, respuesta_gemini(t)))
    r = extraccion.leer_ticket("k", "m", b"img", "image/jpeg")
    assert r["requiere_revision"] is True
    assert any("folio" in a for a in r["advertencias"]) and any("fecha" in a for a in r["advertencias"])


def test_sin_comercio_clave_se_deduce(monkeypatch):
    t = dict(TICKET)
    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(200, respuesta_gemini(t)))
    assert extraccion.leer_ticket("k", "m", b"img", "image/jpeg")["comercio_clave"] == "walmart"
