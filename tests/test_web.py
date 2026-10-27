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


# ------------------------------------------------------------------ nueva factura y trabajo

import base64  # noqa: E402

import pytest  # noqa: E402

from app import extraccion, main  # noqa: E402
from tests.test_api import H  # noqa: E402

TC_WEB = "9906400000000000000101"
TICKET_WALMART = {
    "comercio": "Sam's Club", "comercio_clave": "walmart", "total": 1417.86,
    "tc_numero_ticket": TC_WEB, "tr_numero_transaccion": "03388", "forma_pago_sat_sugerida": "28",
    "confianza_tc": "alta", "confianza_tr": "alta", "advertencias": [], "requiere_revision": False,
}
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def png(tmp_path):
    ruta = tmp_path / "ticket.png"
    ruta.write_bytes(PNG_1X1)
    return str(ruta)


def leer(monkeypatch, ticket):
    monkeypatch.setattr(extraccion, "leer_ticket", lambda *a, **k: ticket)


def test_factura_walmart_de_punta_a_punta(pagina_con_sesion, monkeypatch, png):
    leer(monkeypatch, {**TICKET_WALMART, "confianza_tr": "baja"})
    p = pagina_con_sesion
    p.get_by_label("Foto del ticket").set_input_files(png)
    expect(p.get_by_label("TC", exact=True)).to_have_value(TC_WEB)
    expect(p.get_by_label("TR", exact=True)).to_have_class(re.compile("revisar"))
    expect(p.get_by_label("Forma de pago")).to_have_value("28")
    p.get_by_label("Entrega").select_option("descarga")
    p.get_by_role("button", name="Facturar", exact=True).click()
    expect(p).to_have_url(re.compile(r"#/trabajo/\w+$"))
    p.aceptar_confirm = True
    p.get_by_role("button", name="Facturar", exact=True).click(timeout=60000)
    expect(p.locator(".estado")).to_have_text("completado", timeout=60000)


def test_extraccion_fallida_permite_captura_manual(pagina_con_sesion, monkeypatch, png):
    def falla(*a, **k):
        raise extraccion.ExtraccionError("Gemini no respondió", 502)
    monkeypatch.setattr(extraccion, "leer_ticket", falla)
    p = pagina_con_sesion
    p.get_by_label("Foto del ticket").set_input_files(png)
    expect(p.get_by_text("Gemini no respondió")).to_be_visible()
    expect(p.get_by_label("Comercio")).to_have_value("")
    p.get_by_label("Comercio").select_option("heb")
    expect(p.get_by_label("Sucursal")).to_be_editable()
    expect(p.get_by_label("Ticket", exact=True)).to_be_editable()


def test_archivo_que_no_es_imagen_se_rechaza(pagina_con_sesion, tmp_path):
    ruta = tmp_path / "notas.txt"
    ruta.write_text("hola")
    p = pagina_con_sesion
    p.get_by_label("Foto del ticket").set_input_files(str(ruta))
    expect(p.get_by_text("Sube una imagen de hasta 10 MB.")).to_be_visible()


def test_advertencia_maliciosa_se_ve_como_texto(pagina_con_sesion, monkeypatch, png):
    carga = "<img src=x onerror=alert(1)>"
    leer(monkeypatch, {**TICKET_WALMART, "advertencias": [carga]})
    p = pagina_con_sesion
    p.get_by_label("Foto del ticket").set_input_files(png)
    expect(p.get_by_text(carga)).to_be_visible()
    p.wait_for_timeout(500)          # el fixture falla si se disparó algún diálogo


def test_duplicado_ofrece_ver_trabajo_existente(pagina_con_sesion, monkeypatch, png):
    tc = "9906400000000000000102"
    previo = main.gestor.crear("factura", main.SolicitudFactura(
        tc=tc, tr="1", uso_cfdi="G01", forma_pago="28", metodo_entrega="email", correo_alterno=None))
    try:
        leer(monkeypatch, {**TICKET_WALMART, "tc_numero_ticket": tc})
        p = pagina_con_sesion
        p.get_by_label("Foto del ticket").set_input_files(png)
        expect(p.get_by_label("TC", exact=True)).to_have_value(tc)
        p.get_by_role("button", name="Facturar", exact=True).click()
        p.get_by_role("button", name="Ver trabajo existente").click()
        expect(p).to_have_url(re.compile(f"#/trabajo/{previo.id}$"))
    finally:
        main.gestor.cancelar(previo.id)


def _trabajo_por_api(tc):
    r = client.post("/facturas", headers=H, json={"tc": tc, "tr": "03388", "uso_cfdi": "G01",
                                                   "forma_pago": "28", "metodo_entrega": "descarga"})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_sesion_vencida_regresa_al_trabajo(servidor_web, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000103")
    try:
        p = pagina_con_sesion
        p.goto(f"{servidor_web}#/trabajo/{tid}")
        expect(p.get_by_role("button", name="Facturar", exact=True)).to_be_visible(timeout=60000)
        p.context.clear_cookies()
        p.reload()
        expect(p).to_have_url(re.compile("#/login$"))
        p.get_by_label("Contraseña").fill("clave-de-prueba")
        p.get_by_role("button", name="Entrar").click()
        expect(p).to_have_url(re.compile(f"#/trabajo/{tid}$"))
        expect(p.get_by_role("button", name="Facturar", exact=True)).to_be_visible()
        expect(p).to_have_title("⚠ Pregunta pendiente")
    finally:
        client.post(f"/facturas/{tid}/cancelar", headers=H)


def _llamadas_a_facturas(p):
    llamadas = []
    p.on("request", lambda r: "/facturas/" in r.url and llamadas.append(r.url))
    return llamadas


def test_trabajo_inexistente_no_consulta_mas(servidor_web, pagina_con_sesion):
    p = pagina_con_sesion
    llamadas = _llamadas_a_facturas(p)
    p.goto(f"{servidor_web}#/trabajo/noexiste123")
    expect(p.get_by_text("El servicio se reinició")).to_be_visible()
    n = len(llamadas)
    p.wait_for_timeout(3500)
    assert len(llamadas) == n


def test_trabajo_terminado_no_consulta_mas(servidor_web, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000104")
    client.post(f"/facturas/{tid}/cancelar", headers=H)
    p = pagina_con_sesion
    llamadas = _llamadas_a_facturas(p)
    p.goto(f"{servidor_web}#/trabajo/{tid}")
    expect(p.locator(".estado")).to_have_text("cancelado", timeout=60000)
    n = len(llamadas)
    p.wait_for_timeout(3500)
    assert len([u for u in llamadas[n:] if u.endswith(tid)]) == 0


def test_cancelar_desde_la_web(servidor_web, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000105")
    p = pagina_con_sesion
    p.goto(f"{servidor_web}#/trabajo/{tid}")
    p.get_by_role("button", name="Cancelar trabajo").click()
    expect(p.locator(".estado")).to_have_text("cancelado", timeout=60000)
    expect(p.get_by_role("button", name="Cancelar trabajo")).to_be_hidden()
