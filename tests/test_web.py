"""Webapp: estáticos, headers de seguridad y vistas (Playwright contra la app real)."""
import re

from playwright.sync_api import expect

from tests.test_api import client

CSP = "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'"


def test_raiz_sin_auth_con_headers():
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_headers_tambien_en_la_api():
    r = client.get("/salud")
    assert r.headers["content-security-policy"] == CSP and r.headers["x-frame-options"] == "DENY"


def test_api_no_queda_tapada_por_estaticos():
    assert client.get("/salud").json() == {"ok": True}
    assert client.get("/configuracion").status_code == 401
    assert client.get("/docs").status_code == 200


def test_login_e2e(servidor_web, pagina):
    pagina.goto(servidor_web)
    expect(pagina).to_have_url(re.compile("#/login$"))
    pagina.get_by_label("Contraseña").fill("mala")
    pagina.get_by_role("button", name="Entrar").click()
    expect(pagina.get_by_text("Contraseña incorrecta")).to_be_visible()
    pagina.get_by_label("Contraseña").fill("clave-de-prueba")
    pagina.get_by_role("button", name="Entrar").click()
    expect(pagina).to_have_url(re.compile("#/nueva$"))


def test_salir_regresa_al_login(pagina_con_sesion):
    p = pagina_con_sesion
    p.get_by_role("button", name="Salir").click()
    expect(p).to_have_url(re.compile("#/login$"))
    p.goto(p.url.split("#")[0] + "#/perfil")
    expect(p).to_have_url(re.compile("#/login$"))
