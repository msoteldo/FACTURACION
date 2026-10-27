"""Login web: cookie firmada derivada de API_KEY, límite de intentos y header anti-CSRF."""
import dataclasses

import pytest
from fastapi.testclient import TestClient

from app import main, sesion
from app.sesion import DURACION_S, crear_token, token_valido
from tests.test_api import H, client

FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture(autouse=True)
def _limite_limpio():
    sesion.limite.reiniciar()
    yield
    sesion.limite.reiniciar()


def entrar(c=None, password="clave-de-prueba"):
    return (c or TestClient(main.app)).post("/sesion", json={"password": password})


def test_login_malo_401_bueno_cookie():
    assert entrar(password="otra").status_code == 401
    r = entrar(password=' "clave-de-prueba" ')
    assert r.status_code == 204
    galleta = r.headers["set-cookie"].lower()
    assert "sesion=" in galleta and "httponly" in galleta and "samesite=strict" in galleta
    assert "secure" not in galleta


def test_secure_en_https():
    c = TestClient(main.app, base_url="https://testserver")
    assert "secure" in entrar(c).headers["set-cookie"].lower()


def test_cookie_abre_get_y_post_exige_header():
    c = TestClient(main.app)
    entrar(c)
    assert c.get("/configuracion").status_code == 200
    assert c.post("/consultas", json={"numero": "123"}).status_code == 403
    r = c.post("/consultas", json={"numero": "123"}, headers=FETCH)
    assert r.status_code == 202
    assert c.post(f"/facturas/{r.json()['id']}/cancelar", headers=FETCH).status_code == 200


def test_salir_borra_la_cookie():
    c = TestClient(main.app)
    entrar(c)
    assert c.delete("/sesion", headers=FETCH).status_code == 204
    assert c.get("/configuracion").status_code == 401


def test_token_alterado_vencido_o_de_otra_clave():
    t = crear_token("k", ahora=1000)
    assert token_valido(t, "k", 1000)
    assert not token_valido(t, "k", 1000 + DURACION_S + 1)
    assert not token_valido(t, "otra", 1000)
    assert not token_valido(t[:-1] + ("0" if t[-1] != "0" else "1"), "k", 1000)
    assert not token_valido("basura", "k", 1000)
    assert not token_valido("", "k", 1000)


def test_sexto_intento_429():
    for _ in range(5):
        assert entrar(password="x").status_code == 401
    r = entrar()
    assert r.status_code == 429 and "Demasiados intentos" in r.json()["detail"]


def test_cookie_sin_api_key_en_servidor_503(monkeypatch):
    c = TestClient(main.app)
    entrar(c)
    monkeypatch.setattr(main, "ajustes", dataclasses.replace(main.ajustes, api_key=""))
    assert c.get("/configuracion").status_code == 503


def test_login_sin_api_key_en_servidor_503(monkeypatch):
    monkeypatch.setattr(main, "ajustes", dataclasses.replace(main.ajustes, api_key=""))
    assert entrar(password="").status_code == 503


def test_cookie_de_otra_api_key_no_sirve(monkeypatch):
    c = TestClient(main.app)
    entrar(c)
    monkeypatch.setattr(main, "ajustes", dataclasses.replace(main.ajustes, api_key="nueva"))
    assert c.get("/configuracion").status_code == 401


def test_header_sigue_funcionando():
    assert client.get("/configuracion", headers=H).status_code == 200
