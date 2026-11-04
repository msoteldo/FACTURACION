"""Diagnóstico con IA: solo sugiere, nunca rompe el error original."""
from app import diagnostico, extraccion


def _gemini(texto):
    return {"candidates": [{"content": {"parts": [{"text": texto}]}}]}


def test_devuelve_la_sugerencia_en_una_linea(monkeypatch):
    monkeypatch.setenv("DIAGNOSTICO_IA", "1")
    visto = {}

    def falso(api_key, modelo, body, reintentos, espera_base, timeout):
        visto["partes"] = body["contents"][0]["parts"]
        return _gemini("La fila sí está.\n  Revisar el selector.")
    monkeypatch.setattr(extraccion, "_llamar_gemini", falso)
    r = diagnostico.diagnosticar("Heb", "no agregó", "texto del portal", b"png")
    assert r == "La fila sí está. Revisar el selector."
    assert "texto del portal" in visto["partes"][0]["text"] and len(visto["partes"]) == 2


def test_si_gemini_falla_no_levanta(monkeypatch):
    monkeypatch.setenv("DIAGNOSTICO_IA", "1")

    def roto(*a, **k):
        raise extraccion.ExtraccionError("saturado")
    monkeypatch.setattr(extraccion, "_llamar_gemini", roto)
    assert diagnostico.diagnosticar("Heb", "x", "t", None) is None


def test_desactivado_no_llama(monkeypatch):
    monkeypatch.setenv("DIAGNOSTICO_IA", "0")
    monkeypatch.setattr(extraccion, "_llamar_gemini", lambda *a, **k: 1 / 0)
    assert diagnostico.diagnosticar("Heb", "x", "t", None) is None
