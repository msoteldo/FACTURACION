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


def test_login_e2e(app_url, pagina):
    pagina.goto(app_url)
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
from datetime import datetime, timedelta, timezone  # noqa: E402

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
    p.get_by_role("button", name="Facturar", exact=True).click(timeout=60000)
    p.get_by_role("button", name="Sí, facturar").click()
    expect(p.locator(".estado")).to_have_attribute("data-estado", "completado", timeout=60000)


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


def test_sesion_vencida_regresa_al_trabajo(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000103")
    try:
        p = pagina_con_sesion
        p.goto(f"{app_url}#/trabajo/{tid}")
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


def test_trabajo_inexistente_no_consulta_mas(app_url, pagina_con_sesion):
    p = pagina_con_sesion
    llamadas = _llamadas_a_facturas(p)
    p.goto(f"{app_url}#/trabajo/noexiste123")
    expect(p.get_by_text("El servicio se reinició")).to_be_visible()
    n = len(llamadas)
    p.wait_for_timeout(3500)
    assert len(llamadas) == n


def test_trabajo_terminado_no_consulta_mas(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000104")
    client.post(f"/facturas/{tid}/cancelar", headers=H)
    p = pagina_con_sesion
    llamadas = _llamadas_a_facturas(p)
    p.goto(f"{app_url}#/trabajo/{tid}")
    expect(p.locator(".estado")).to_have_attribute("data-estado", "cancelado", timeout=60000)
    n = len(llamadas)
    p.wait_for_timeout(3500)
    assert len([u for u in llamadas[n:] if u.endswith(tid)]) == 0


def test_cancelar_desde_la_web(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000105")
    p = pagina_con_sesion
    p.goto(f"{app_url}#/trabajo/{tid}")
    p.get_by_role("button", name="Cancelar trabajo").click()
    expect(p.locator(".estado")).to_have_attribute("data-estado", "cancelado", timeout=60000)
    expect(p.get_by_role("button", name="Cancelar trabajo")).to_be_hidden()


# ------------------------------------------------------------------ datos fiscales

def test_editar_perfil_aplica_a_la_siguiente_factura(app_url, pagina_con_sesion, base_sqlite,
                                                     monkeypatch, png):
    p = pagina_con_sesion
    p.goto(f"{app_url}#/perfil")
    expect(p.get_by_text("Guarda para poder editarlos aquí")).to_be_visible()
    expect(p.get_by_label("RFC")).to_have_value("CPA010101AB1")
    p.get_by_label("CP").fill("1234")
    p.get_by_role("button", name="Guardar").click()
    expect(p.locator("#error-cp")).not_to_be_empty()
    p.get_by_label("CP").fill("01000")
    p.get_by_label("Razón social").fill("CLUB DE PADEL SA DE CV")
    p.get_by_role("button", name="Guardar").click()
    expect(p.get_by_text("Aplica a las facturas nuevas")).to_be_visible()
    expect(p.locator("#error-cp")).to_be_empty()

    tc = "9906400000000000000106"
    leer(monkeypatch, {**TICKET_WALMART, "tc_numero_ticket": tc})
    p.goto(f"{app_url}#/nueva")
    p.get_by_label("Foto del ticket").set_input_files(png)
    expect(p.get_by_label("TC", exact=True)).to_have_value(tc)
    p.get_by_role("button", name="Facturar", exact=True).click()
    expect(p.get_by_role("button", name="Continuar")).to_be_visible(timeout=60000)
    expect(p.locator(".diferencias")).to_contain_text("CLUB DE PADEL SA DE CV")
    p.get_by_role("button", name="Cancelar", exact=True).click()
    expect(p.locator(".estado")).to_have_attribute("data-estado", "cancelado", timeout=60000)


def test_perfil_sin_base_muestra_503(app_url, pagina_con_sesion):
    p = pagina_con_sesion
    p.goto(f"{app_url}#/perfil")
    p.get_by_role("button", name="Guardar").click()
    expect(p.get_by_text("Configura DATABASE_URL")).to_be_visible()


# ------------------------------------------------------------------ correcciones de la revisión final

def test_docs_sigue_funcionando_sin_csp_restrictiva():
    for ruta in ("/docs", "/redoc"):
        r = client.get(ruta)
        assert r.status_code == 200
        assert "default-src 'self'" not in r.headers.get("content-security-policy", "")
        assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/").headers["content-security-policy"] == CSP


def test_barra_oculta_en_login(app_url, pagina):
    pagina.goto(app_url)
    expect(pagina).to_have_url(re.compile("#/login$"))
    expect(pagina.locator("#barra")).to_be_hidden()


def test_forma_de_pago_no_leida_obliga_a_elegir(pagina_con_sesion, monkeypatch, png):
    leer(monkeypatch, {**TICKET_WALMART, "tc_numero_ticket": "9906400000000000000107",
                       "forma_pago_sat_sugerida": None})
    p = pagina_con_sesion
    p.get_by_label("Foto del ticket").set_input_files(png)
    forma = p.get_by_label("Forma de pago")
    expect(forma).to_have_value("")
    expect(forma).to_have_class(re.compile("revisar"))
    p.get_by_role("button", name="Facturar", exact=True).click()
    p.wait_for_timeout(500)
    expect(p).to_have_url(re.compile("#/nueva$"))


# ------------------------------------------------------------------ TicketIA: estructura y sistema de diseño

def test_app_en_app_y_landing_en_raiz():
    assert "TicketIA" in client.get("/").text
    r = client.get("/app/")
    assert r.status_code == 200 and 'src="../js/app.js"' in r.text


def test_fuentes_locales_servidas():
    r = client.get("/fuentes/instrument-sans-latin-wght-normal.woff2")
    assert r.status_code == 200 and r.content[:4] == b"wOF2"


def test_sin_peticiones_externas(app_url, pagina_con_sesion):
    p = pagina_con_sesion
    origen = app_url.split("/app/")[0]
    externas = []
    p.on("request", lambda r: not r.url.startswith(origen) and externas.append(r.url))
    p.goto(app_url + "#/perfil")
    p.wait_for_load_state("networkidle")
    assert externas == []


def test_modo_oscuro(app_url, _navegador):
    ctx = _navegador.new_context(color_scheme="dark")
    p = ctx.new_page()
    p.goto(app_url)
    assert p.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(13, 17, 24)"
    ctx.close()


# ------------------------------------------------------------------ diálogo de confirmación

def test_escape_en_el_dialogo_no_factura(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000202")
    try:
        p = pagina_con_sesion
        p.goto(f"{app_url}#/trabajo/{tid}")
        facturar = p.get_by_role("button", name="Facturar", exact=True)
        facturar.click(timeout=60000)
        expect(p.get_by_role("dialog")).to_be_visible()
        expect(p.get_by_role("button", name="Sí, facturar")).to_be_focused()
        p.keyboard.press("Escape")
        expect(p.get_by_role("dialog")).to_be_hidden()
        expect(facturar).to_be_focused()
        assert main.gestor.obtener(tid).estado == "esperando_respuesta"
    finally:
        client.post(f"/facturas/{tid}/cancelar", headers=H)


def test_doble_clic_en_si_facturar_manda_una_respuesta(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000203")
    p = pagina_con_sesion
    posts = []
    p.on("request", lambda r: r.method == "POST" and r.url.endswith("/respuesta") and posts.append(r.url))
    p.goto(f"{app_url}#/trabajo/{tid}")
    p.get_by_role("button", name="Facturar", exact=True).click(timeout=60000)
    p.get_by_role("button", name="Sí, facturar").dblclick()
    expect(p.locator(".estado")).to_have_attribute("data-estado", "completado", timeout=60000)
    assert len(posts) == 1


# ------------------------------------------------------------------ landing

def test_landing(servidor_web, pagina):
    pagina.goto(servidor_web)
    expect(pagina.get_by_role("heading", level=1)).to_contain_text("convertido en factura")
    assert pagina.get_by_role("link", name="Entrar").first.get_attribute("href") == "/app/#/login"
    expect(pagina.locator("#demo")).to_have_class(re.compile("reproducir"))


def test_enlace_viejo_redirige(servidor_web, pagina_con_sesion):
    # Con sesión, el enlace viejo abre el trabajo en la app (sin sesión, la app pide entrar y luego vuelve).
    pagina_con_sesion.goto(servidor_web + "#/trabajo/abc123")
    expect(pagina_con_sesion).to_have_url(re.compile(r"/app/#/trabajo/abc123$"))


def test_landing_con_movimiento_reducido(servidor_web, _navegador):
    ctx = _navegador.new_context(reduced_motion="reduce")
    p = ctx.new_page()
    p.goto(servidor_web)
    expect(p.get_by_text("Factura enviada a tu correo")).to_be_visible()
    assert p.eval_on_selector(".recibo", "e => getComputedStyle(e).animationName") == "none"
    ctx.close()


# ------------------------------------------------------------------ barra y entrar

def test_barra_muestra_razon_social_una_sola_vez(app_url, pagina_con_sesion):
    p = pagina_con_sesion
    perfiles = []
    p.on("request", lambda r: r.url.endswith("/perfil") and r.method == "GET" and perfiles.append(1))
    p.reload()
    expect(p.locator(".barra")).to_contain_text("CLUB DE PADEL DE PRUEBA")
    p.get_by_role("link", name="Datos fiscales").click()
    expect(p.get_by_label("RFC")).to_have_value("CPA010101AB1")
    p.get_by_role("link", name="Nueva factura").click()
    expect(p.get_by_label("Foto del ticket")).to_be_attached()
    assert len(perfiles) == 2          # uno de la barra al recargar y uno de la vista de datos fiscales


def test_foco_al_titulo_al_cambiar_de_pantalla(pagina_con_sesion):
    p = pagina_con_sesion
    p.get_by_role("link", name="Datos fiscales").click()
    expect(p.get_by_role("heading", level=1)).to_be_focused()


# ------------------------------------------------------------------ pantalla de trabajo

def test_pasos_y_cuenta_regresiva(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000204")
    p = pagina_con_sesion
    try:
        p.goto(f"{app_url}#/trabajo/{tid}")
        expect(p.locator('[data-paso="1"]')).to_have_class(re.compile("actual"), timeout=60000)
        expect(p.locator('[data-paso="0"]')).to_have_class(re.compile("hecho"))
        t = main.gestor.obtener(tid)
        t.pregunta["expira_en"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
        p.reload()
        expect(p.locator(".reloj")).to_contain_text(re.compile(r"\b([0-4]:\d\d|5:00)\b"))
    finally:
        client.post(f"/facturas/{tid}/cancelar", headers=H)
    expect(p.locator('[data-paso="1"]')).to_have_class(re.compile("error"), timeout=60000)


def test_formato_restante_nunca_negativo(app_url, pagina):
    pagina.goto(app_url)
    r = pagina.evaluate("""async () => (await import('/js/vistas/trabajo.js'))
        .formatoRestante(new Date(Date.now() - 5000).toISOString(), Date.now())""")
    assert r == "0:00"


def test_dialogo_se_cierra_si_la_pregunta_cambia(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000205")
    p = pagina_con_sesion
    p.goto(f"{app_url}#/trabajo/{tid}")
    p.get_by_role("button", name="Facturar", exact=True).click(timeout=60000)
    expect(p.get_by_role("dialog")).to_be_visible()
    client.post(f"/facturas/{tid}/cancelar", headers=H)        # otra vía cancela mientras el diálogo está abierto
    expect(p.get_by_role("dialog")).to_be_hidden(timeout=10000)
