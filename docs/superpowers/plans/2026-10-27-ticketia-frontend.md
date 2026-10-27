# TicketIA: landing y rediseño de la app (plan de implementación)

> **Para agentes:** SUB-SKILL REQUERIDA: usa superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan casillas (`- [ ]`) para el seguimiento.

**Objetivo:** dar a la webapp la identidad TicketIA, con una landing pública en `/`, la app rediseñada en `/app/`, un sistema de diseño en CSS y animaciones que sólo usan `transform` y `opacity`.

**Arquitectura:**
- Sigue siendo JS puro en módulos ES, sin paso de build. FastAPI sigue sirviendo `app/static`.
- La app se mueve a `app/static/app/`; la landing ocupa `app/static/index.html`.
- Tokens, base y componentes van en `css/`, compartidos por la landing y la app.
- El movimiento sale de una sola fuente (`tokens.css` + `js/movimiento.js`).
- El backend sólo agrega `pregunta.expira_en`.

**Stack:** HTML, CSS y JS (ES modules); fuentes woff2 de Fontsource; FastAPI; pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-10-27-ticketia-frontend-design.md`. Los mockups que enlaza son la referencia visual. Copia sus valores (colores, tamaños, textos), pero **no** sus `style=` en línea ni sus fuentes de Google.

## Restricciones globales

- La CSP sigue igual: `default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'`. Por eso no puede haber `<script>`, `<style>` ni atributos `style=` u `on*=` en el HTML; todo va a archivos `.css` y `.js`. Los atributos de presentación SVG (`fill`, `stroke`) sí están permitidos.
- Todo texto que venga del servidor se pinta con `textContent`. Los íconos se crean con `icono(id)` (Tarea 2), nunca con `innerHTML`.
- Sólo se animan `transform` y `opacity`. Los `:hover` con movimiento van dentro de `@media (hover: hover) and (pointer: fine)`. Con `prefers-reduced-motion: reduce` no hay desplazamientos.
- Curvas: `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)` y `--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1)`. Las duraciones son las de la tabla "Movimiento" de la spec.
- Fuentes, con versión fija, descargadas de `https://cdn.jsdelivr.net/npm/`:
  - `@fontsource-variable/instrument-sans@5.1.0`: `files/instrument-sans-latin-wght-normal.woff2` y `files/instrument-sans-latin-ext-wght-normal.woff2`.
  - `@fontsource/ibm-plex-mono@5.1.0`: `files/ibm-plex-mono-{latin,latin-ext}-{400,500,600}-normal.woff2`.
  - El `LICENSE` de cada paquete se copia a `fuentes/OFL-InstrumentSans.txt` y `fuentes/OFL-IBMPlexMono.txt`.
- Los nombres de clases, ids y textos van en español, como el resto del repo.
- Cada pantalla de la app pasa el foco a su `h1` (`tabindex="-1"`) al montarse.
- `pytest` debe pasar completo al final de cada tarea.

## Revisión: casos que más pueden fallar

1. **Un enlace viejo `/#/trabajo/<id>`** (de Telegram o de favoritos) → debe abrir `/app/#/trabajo/<id>` con el mismo id. Prueba en la Tarea 4.
2. **La pregunta deja de estar vigente con el diálogo abierto** (alguien respondió desde Telegram o se agotó el tiempo) → el diálogo se cierra solo y no se manda ninguna respuesta. Prueba en la Tarea 6.
3. **Doble clic en "Sí, facturar"** → sale una sola petición `POST /respuesta`. Prueba en la Tarea 3.
4. **`expira_en` ya pasó** → la cuenta muestra "0:00", nunca un valor negativo. Prueba en la Tarea 6.
5. **Modo oscuro del sistema** → la app y la landing usan el fondo oscuro (`#0d1118`). Prueba en la Tarea 2.

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `app/trabajos.py` | agrega `expira_en` a la pregunta |
| `app/static/css/tokens.css` | tokens de color (claro y oscuro), tipografía, espacios, radios, sombras, curvas y duraciones, más `@font-face` |
| `app/static/css/base.css` | reset, `[hidden]`, foco visible, escala tipográfica, `body` |
| `app/static/css/componentes.css` | `.btn`, `.input`, `.chip`, `.pill`, `.tarjeta`, `.ticket-termico`, `.dialogo`, `.toast`, `.pasos`, `.rejilla` |
| `app/static/fuentes/*` | woff2 y licencias |
| `app/static/img/logo.svg`, `favicon.svg` | marca |
| `app/static/index.html`, `landing.css`, `landing.js` | landing, demo y redirección de hash |
| `app/static/app/index.html` | contenedor de la app, con el sprite de íconos en `<svg hidden><symbol …>` |
| `app/static/js/dom.js`, `api.js` | se mueven desde la raíz de `static`; `dom.js` gana `icono()` |
| `app/static/js/movimiento.js` | `reducido()`, `reproducir()` |
| `app/static/js/dialogo.js`, `toast.js` | confirmación y avisos |
| `app/static/js/app.js` | enrutador, barra superior, foco al cambiar de pantalla |
| `app/static/js/vistas/{login,nueva,trabajo,perfil}.js` | las 4 pantallas |
| `tests/test_web.py`, `tests/conftest.py` | pruebas de punta a punta |

Se borran `app/static/{app.js,api.js,dom.js,login.js,nueva.js,trabajo.js,perfil.js,estilos.css}` de la raíz cuando su reemplazo exista (Tarea 2).

---

### Task 1: `expira_en` en la pregunta

**Archivos:**
- Modificar: `app/trabajos.py:120-135` (`InteraccionTrabajo.preguntar`)
- Prueba: `tests/test_api.py`

**Interfaces:**
- Produce: `pregunta["expira_en"]: str`, en ISO 8601 UTC con segundos (`datetime.now(timezone.utc) + timedelta(seconds=timeout_respuesta_s)`, `isoformat(timespec="seconds")`). `expira_en_s` se queda.

- [ ] **Paso 1: escribir la prueba que falla**

```python
def test_pregunta_trae_expira_en():
    t = nueva(tc="9906400000000000000201")
    t = esperar_estado(t["id"], {"esperando_respuesta", "error"})
    p = t["pregunta"]
    restante = (datetime.fromisoformat(p["expira_en"]) - datetime.now(timezone.utc)).total_seconds()
    assert 0 < restante <= p["expira_en_s"]
    client.post(f"/facturas/{t['id']}/cancelar", headers=H)
```

- [ ] **Paso 2:** `python -m pytest -n0 tests/test_api.py::test_pregunta_trae_expira_en -v`. Esperado: FAIL con `KeyError: 'expira_en'`.
- [ ] **Paso 3:** agregar `"expira_en"` en `preguntar()`.
- [ ] **Paso 4:** misma prueba, PASS. Luego `python -m pytest`: todo PASS.
- [ ] **Paso 5:** commit `feat: la pregunta trae la hora de expiración`.

### Task 2: Mover la app a `/app/` y crear el sistema de diseño

**Archivos:**
- Crear: `css/tokens.css`, `css/base.css`, `css/componentes.css`, `fuentes/*`, `img/logo.svg`, `img/favicon.svg`, `app/index.html`, `js/dom.js`, `js/api.js`, `js/app.js`, `js/vistas/*.js` (movidos)
- Crear: `index.html` provisional de la landing: barra con logo y un enlace "Entrar" a `/app/#/login`. La landing completa llega en la Tarea 4.
- Borrar: los archivos viejos de la raíz de `app/static`
- Modificar: `tests/conftest.py`, `tests/test_web.py`

**Interfaces:**
- Produce:
  - `conftest.py`: fixture `app_url` (scope de sesión) = `servidor_web + "app/"`. `pagina_con_sesion` entra por `app_url`. Las pruebas de `test_web.py` cambian `servidor_web + "#/…"` por `app_url + "#/…"`.
  - `dom.js`: se conservan `el()` y `vaciar()` como hoy. Nueva `icono(id: string) -> SVGSVGElement`: crea `<svg class="icono" aria-hidden="true"><use href="#i-<id>"></use></svg>` con `createElementNS`. Los símbolos viven en `app/index.html`: `check`, `subir`, `alerta`, `info`, `reloj`, `punto`, `cerrar`.
  - `api.js`: se conserva `api()` como hoy.
  - `tokens.css`:
    - los nombres y valores de la tabla de paleta de la spec, en `:root` y redefinidos en `@media (prefers-color-scheme: dark)`;
    - `--f-sans: "Instrument Sans", system-ui, -apple-system, "Segoe UI", sans-serif` y `--f-mono: "IBM Plex Mono", ui-monospace, Menlo, monospace`;
    - `--ease-out`, `--ease-in-out`, `--dur-press: 160ms`, `--dur-vista: 150ms`;
    - los `@font-face` con `font-display: swap` y `unicode-range` de Fontsource.
  - `componentes.css`: toma los estilos del mockup de la app para `.btn` (`.sec`, `.peligro`), `.input` (`.mono`, `.revisar`), `.chip[aria-pressed]`, `.pill` (`.ok`, `.alerta`, `.error`), `.tarjeta`, `.rejilla`, `.ayuda` (`.error`), `.aviso`, `.advertencia` y `.ticket-termico` (la máscara perforada).
  - Las vistas movidas siguen funcionando con sus clases actuales mientras las Tareas 5–8 las rehacen. En `componentes.css`, `.secundario` es alias de `.sec`.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_app_en_app_y_landing_en_raiz():
    assert "TicketIA" in client.get("/").text
    r = client.get("/app/")
    assert r.status_code == 200 and 'src="../js/app.js"' in r.text

def test_fuentes_locales_servidas():
    r = client.get("/fuentes/instrument-sans-latin-wght-normal.woff2")
    assert r.status_code == 200 and r.content[:4] == b"wOF2"

def test_sin_peticiones_externas(app_url, pagina_con_sesion):
    p = pagina_con_sesion
    externas = []
    p.on("request", lambda r: not r.url.startswith(app_url.split("/app/")[0]) and externas.append(r.url))
    p.goto(app_url + "#/perfil"); p.wait_for_load_state("networkidle")
    assert externas == []

def test_modo_oscuro(app_url, _navegador):
    ctx = _navegador.new_context(color_scheme="dark"); p = ctx.new_page()
    p.goto(app_url)
    assert p.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(13, 17, 24)"
    ctx.close()
```

- [ ] **Paso 2:** `python -m pytest -n0 tests/test_web.py -k "raiz or fuentes or externas or oscuro" -v`. Esperado: FAIL.
- [ ] **Paso 3:** descargar las fuentes con `curl` desde las URLs de las Restricciones globales; crear los CSS y el contenedor; mover los JS; ajustar los imports relativos (`../js/…` desde `app/index.html`); actualizar las URLs de las pruebas existentes.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS, incluidas las pruebas web existentes ya en `/app/`.
- [ ] **Paso 5:** commit `feat: sistema de diseño TicketIA y app en /app/`.

### Task 3: Movimiento, diálogo de confirmación y toasts

**Archivos:**
- Crear: `js/movimiento.js`, `js/dialogo.js`, `js/toast.js`; estilos `.capa`, `.dialogo`, `.toasts`, `.toast` en `componentes.css`
- Modificar: `js/vistas/trabajo.js` (`facturar` usa `confirmar()` en lugar de `window.confirm`), `tests/test_web.py`, `tests/conftest.py` (se quita `aceptar_confirm`; cualquier `dialog` del navegador hace fallar la prueba)

**Interfaces:**
- Produce:
  - `movimiento.js`:
    - `reducido() -> boolean`;
    - `reproducir(el: Element, clase: string) -> void`: quita la clase, fuerza un reflow y la vuelve a poner.
  - `dialogo.js`: `confirmar({titulo, texto, aceptar, cancelar}) -> Promise<boolean>`.
    - Inserta `.capa > .dialogo[role=dialog][aria-modal=true]` en el `body`.
    - El foco va al botón de aceptar y queda atrapado con Tab y Shift+Tab.
    - Escape o el botón de cancelar resuelven `false`; aceptar resuelve `true`.
    - Al cerrar, el foco vuelve a `document.activeElement` previo.
    - Un segundo clic en aceptar no hace nada.
    - `cerrarDialogo() -> void` cierra el diálogo abierto con `false`.
    - La entrada usa `@starting-style`: capa con opacidad, diálogo con `scale(.96)`, 200 ms con `--ease-out`.
  - `toast.js`: `avisar(texto: string, tipo: "ok" | "error" = "ok") -> void`.
    - Contenedor `.toasts[aria-live=polite]` abajo a la derecha (respetando `env(safe-area-inset-*)`).
    - Entra con `@starting-style` (`translateY(100%)`, 260 ms) y sale con la clase `.saliendo` (180 ms) a los 3200 ms.
    - El temporizador se pausa con `document.hidden`.

- [ ] **Paso 1: escribir las pruebas que fallan** (en las pruebas existentes, `p.aceptar_confirm = True` + clic en "Facturar" pasa a ser clic en "Facturar" + clic en "Sí, facturar")

```python
def test_escape_en_el_dialogo_no_factura(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000202")
    try:
        p = pagina_con_sesion; p.goto(f"{app_url}#/trabajo/{tid}")
        p.get_by_role("button", name="Facturar", exact=True).click(timeout=60000)
        expect(p.get_by_role("dialog")).to_be_visible()
        p.keyboard.press("Escape")
        expect(p.get_by_role("dialog")).to_be_hidden()
        expect(p.get_by_role("button", name="Facturar", exact=True)).to_be_focused()
        assert main.gestor.obtener(tid).estado == "esperando_respuesta"
    finally:
        client.post(f"/facturas/{tid}/cancelar", headers=H)

def test_doble_clic_en_si_facturar_manda_una_respuesta(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000203")
    p = pagina_con_sesion; posts = []
    p.on("request", lambda r: r.method == "POST" and r.url.endswith("/respuesta") and posts.append(r.url))
    p.goto(f"{app_url}#/trabajo/{tid}")
    p.get_by_role("button", name="Facturar", exact=True).click(timeout=60000)
    p.get_by_role("button", name="Sí, facturar").dblclick()
    expect(p.locator(".estado, [data-estado]").first).to_contain_text(re.compile("completado|ejecutando"), timeout=60000)
    assert len(posts) == 1
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL (hoy sale el `confirm` del navegador y la prueba lo trata como diálogo inesperado).
- [ ] **Paso 3:** implementar los tres módulos y conectar `confirmar()` en `trabajo.js`. El texto sale de la spec: título "¿Facturar $<total>?" o "¿Facturar este ticket?", el cuerpo con razón social y RFC de `datos_fiscales_portal` si vienen, y "Este paso no se puede deshacer."
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: confirmación dentro de la página y avisos`.

### Task 4: Landing

**Archivos:**
- Modificar: `app/static/index.html`
- Crear: `landing.css`, `landing.js`
- Prueba: `tests/test_web.py`

**Interfaces:**
- Consume: `css/tokens.css`, `css/base.css`, `css/componentes.css` (Tarea 2).
- Produce: copia la estructura, los textos y la demo del mockup de la landing (spec, sección "Landing"), con estos cambios:
  - la demo es la versión 2 del mockup: una línea de tiempo CSS con la clase `.reproducir` en `#demo`, las variables `--t` por campo calculadas una sola vez, y arranque tras `document.fonts.ready`;
  - el botón "Repetir";
  - en reposo se ve el estado final;
  - los `href` de "Entrar" son `/app/#/login`;
  - `landing.js` empieza con `if (location.hash.startsWith("#/")) location.replace("/app/" + location.hash)`;
  - `<link rel="icon" href="img/favicon.svg">`.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_landing(servidor_web, pagina):
    pagina.goto(servidor_web)
    expect(pagina.get_by_role("heading", level=1)).to_contain_text("convertido en factura")
    assert pagina.get_by_role("link", name="Entrar").first.get_attribute("href") == "/app/#/login"
    expect(pagina.locator("#demo")).to_have_class(re.compile("reproducir"))

def test_enlace_viejo_redirige(servidor_web, pagina):
    pagina.goto(servidor_web + "#/trabajo/abc123")
    expect(pagina).to_have_url(re.compile(r"/app/#/trabajo/abc123$"))

def test_landing_con_movimiento_reducido(servidor_web, _navegador):
    ctx = _navegador.new_context(reduced_motion="reduce"); p = ctx.new_page()
    p.goto(servidor_web)
    expect(p.get_by_text("Factura enviada a tu correo")).to_be_visible()
    assert p.eval_on_selector(".recibo", "e => getComputedStyle(e).animationName") == "none"
    ctx.close()
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar. Usa la skill `animate` para revisar la demo antes de cerrar: sólo `transform` y `opacity`, sin `backdrop-filter`, y nada de `setTimeout` para animar.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS. El fixture `pagina` ya falla si hay errores de CSP.
- [ ] **Paso 5:** commit `feat: landing pública de TicketIA`.

### Task 5: Barra de la app y pantalla de entrar

**Archivos:**
- Modificar: `app/index.html`, `js/app.js`, `js/vistas/login.js`, `componentes.css` (`.barra`, `.login`, `.ranura`, `.boleto`)

**Interfaces:**
- Consume: `reproducir()`, `reducido()` (Tarea 3).
- Produce:
  - Barra:
    - logo, pestañas "Nueva factura" y "Datos fiscales" (`aria-current="page"` en la activa), la razón social con el avatar de iniciales (2 letras), y "Salir";
    - la razón social se pide una vez por carga de página con `GET /perfil` y se guarda en una variable del módulo; si falla, no se muestra;
    - la barra no aparece en `#/login`.
  - Al montar una vista, el foco va a su `h1` (`tabindex="-1"`).
  - El cambio de vista es un fundido de 150 ms (`--dur-vista`, sólo opacidad).
  - Login: el ticket de la spec. Sale de la ranura con la animación `salir-ranura` (620 ms) cada vez que se monta. Con 401 o 429, el temblor de 260 ms se aplica a `.boleto-clip`. El enlace "Conoce TicketIA" apunta a `/`.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_barra_muestra_razon_social_una_sola_vez(app_url, pagina_con_sesion):
    p = pagina_con_sesion; perfiles = []
    p.on("request", lambda r: r.url.endswith("/perfil") and r.method == "GET" and perfiles.append(1))
    p.reload()
    expect(p.locator(".barra")).to_contain_text("CLUB DE PADEL DE PRUEBA")
    p.get_by_role("link", name="Datos fiscales").click(); p.get_by_role("link", name="Nueva factura").click()
    assert len(perfiles) == 2          # uno de la barra al recargar y uno de la vista de datos fiscales

def test_foco_al_titulo_al_cambiar_de_pantalla(pagina_con_sesion):
    p = pagina_con_sesion
    p.get_by_role("link", name="Datos fiscales").click()
    expect(p.get_by_role("heading", level=1)).to_be_focused()
```

Las pruebas `test_login_e2e`, `test_salir_regresa_al_login` y `test_barra_oculta_en_login` deben seguir pasando sin cambios.
- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: barra de la app y pantalla de entrar`.

### Task 6: Pantalla de trabajo

**Archivos:**
- Modificar: `js/vistas/trabajo.js`, `componentes.css` (`.pasos`, `.pregunta`, `.reloj`, `.capturas`, `.resultado`, `.actividad`)

**Interfaces:**
- Consume: `confirmar()` y `cerrarDialogo()` (Tarea 3); `expira_en` (Tarea 1).
- Produce:
  - `pasoActual(t) -> {indice: 0..3, estado: "actual" | "hecho" | "error"}`: función pura exportada, con las reglas de la spec, sección "Trabajo". Cada `<li>` lleva `data-paso="0..3"` y la clase `hecho`, `actual` o `error`.
  - `formatoRestante(expiraEn: string, ahora: number) -> string`: devuelve `"m:ss"`, con mínimo `"0:00"`. El anillo usa `stroke-dashoffset` con una transición lineal de 1 s.
  - La pregunta, las capturas (cada una es `<a href=… target=_blank>` con `<img>`), el resultado con el comprobante impreso y la palomita, la actividad y la nota de la spec.
  - Si en una consulta la pregunta vigente cambia de id o desaparece mientras el diálogo está abierto, se llama `cerrarDialogo()`.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_pasos_y_cuenta_regresiva(app_url, pagina_con_sesion):
    tid = _trabajo_por_api("9906400000000000000204")
    try:
        p = pagina_con_sesion; p.goto(f"{app_url}#/trabajo/{tid}")
        expect(p.locator('[data-paso="1"]')).to_have_class(re.compile("actual"), timeout=60000)
        expect(p.locator('[data-paso="0"]')).to_have_class(re.compile("hecho"))
        t = main.gestor.obtener(tid)
        t.pregunta["expira_en"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
        p.reload()
        expect(p.locator(".reloj")).to_contain_text(re.compile(r"\b[0-4]:\d\d|5:00"))
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
    p = pagina_con_sesion; p.goto(f"{app_url}#/trabajo/{tid}")
    p.get_by_role("button", name="Facturar", exact=True).click(timeout=60000)
    expect(p.get_by_role("dialog")).to_be_visible()
    client.post(f"/facturas/{tid}/cancelar", headers=H)        # otra vía responde/cancela
    expect(p.get_by_role("dialog")).to_be_hidden(timeout=10000)
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar. Revisa con la skill `animate`: la pregunta entra con `@starting-style` (240 ms), el comprobante se imprime con `transform`, y la palomita usa `stroke-dashoffset` sobre el SVG. `stroke-dashoffset` es una propiedad de pintura, pero se acepta porque es un elemento de 44 px que se ve rara vez; anótalo como decisión.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS (las pruebas de trabajo de las Tareas 3 y 5 también).
- [ ] **Paso 5:** commit `feat: pantalla de trabajo con pasos, cuenta real y comprobante`.

### Task 7: Pantalla de nueva factura

**Archivos:**
- Modificar: `js/vistas/nueva.js`, `componentes.css` (`.nueva`, `.zona`, `.foto`, `.lector`, `.estado-foto`, `.consulta`), `tests/test_web.py`

**Interfaces:**
- Produce:
  - El comercio son chips `button.chip[aria-pressed]` dentro de `role="group"` con `aria-label="Comercio"` (sustituyen al `select`). `comercioActual()` lee el chip activo.
  - Zona de la foto:
    - `role="button"`, `tabindex="0"`, `aria-label="Subir foto del ticket"`;
    - abre el `input[type=file]` oculto con clic, Enter o Espacio;
    - acepta arrastrar y soltar (`dragenter`/`dragover`/`dragleave`/`drop`, con la clase `.sobre`);
    - el `<label for="foto">` "Foto del ticket" se conserva (visualmente oculto) para las pruebas.
  - Mientras dura `POST /tickets/extraer`, la zona lleva la clase `.leyendo`: la franja en bucle (1300 ms, lineal) y el texto "Leyendo con IA…". Al terminar, "Ticket leído" y el botón "Cambiar foto".
  - Los campos entran con la cascada `llenar` (40 ms entre campos, 260 ms cada uno) mediante `reproducir(campos, "llenar")`.
  - El resto del comportamiento (validaciones, 409 "Ver trabajo existente", consulta) no cambia.

- [ ] **Paso 1: adaptar y escribir las pruebas**

En `test_extraccion_fallida_permite_captura_manual`, cambiar `select_option("heb")` por `p.get_by_role("group", name="Comercio").get_by_role("button", name="HEB").click()`, y la aserción de valor vacío por `expect(p.get_by_role("button", name="HEB")).to_have_attribute("aria-pressed", "false")`.

```python
def test_zona_de_foto_con_teclado(pagina_con_sesion):
    p = pagina_con_sesion
    with p.expect_file_chooser() as fc:
        p.get_by_role("button", name="Subir foto del ticket").focus(); p.keyboard.press("Enter")
    assert fc.value is not None

def test_chip_del_comercio_leido(pagina_con_sesion, monkeypatch, png):
    leer(monkeypatch, {**TICKET_WALMART, "tc_numero_ticket": "9906400000000000000206"})
    p = pagina_con_sesion
    p.get_by_label("Foto del ticket").set_input_files(png)
    expect(p.get_by_role("button", name="Sam's Club / Walmart")).to_have_attribute("aria-pressed", "true")
    expect(p.get_by_text("Ticket leído")).to_be_visible()
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: nueva factura con zona de foto y lectura con IA`.

### Task 8: Pantalla de datos fiscales

**Archivos:**
- Modificar: `js/vistas/perfil.js`, `componentes.css` (`fieldset`, `legend`, `.guardar`), `tests/test_web.py`

**Interfaces:**
- Consume: `avisar()` (Tarea 3).
- Produce:
  - Grupos "Contribuyente" y "Domicilio fiscal" con los campos y textos de ayuda de la spec. Las etiquetas existentes se conservan (`RFC`, `CP`, `Razón social`…).
  - Al guardar: el botón cambia a "Guardado ✓" con un desenfoque de 2 px (sólo `opacity` y `filter` en el texto del botón) y vuelve a "Guardar" a los 2400 ms.
  - Además aparece `avisar("Datos guardados. Aplican a las facturas nuevas.")`.
  - Los 422 van junto a cada campo y un 503 sale como `avisar(detalle, "error")`.

- [ ] **Paso 1: adaptar las pruebas**

En `test_editar_perfil_aplica_a_la_siguiente_factura`, la aserción pasa a `expect(p.get_by_text("Datos guardados. Aplican a las facturas nuevas.")).to_be_visible()`. En `test_perfil_sin_base_muestra_503`, el texto se busca dentro de `.toast`.

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: datos fiscales agrupados con aviso al guardar`.

### Task 9: Movimiento reducido, revisión y documentación

**Archivos:**
- Modificar: `tests/test_web.py`, `README.md` (sección "Webapp": la landing en `/`, la app en `/app/`, el nombre TicketIA)

- [ ] **Paso 1: escribir la prueba**

```python
def test_flujo_completo_con_movimiento_reducido(app_url, _navegador, monkeypatch, png):
    leer(monkeypatch, {**TICKET_WALMART, "tc_numero_ticket": "9906400000000000000207"})
    ctx = _navegador.new_context(reduced_motion="reduce"); p = ctx.new_page()
    # login → foto → Facturar → Sí, facturar → "Factura emitida", igual que test_factura_walmart_de_punta_a_punta
    ctx.close()
```

- [ ] **Paso 2:** ejecutar. Debe pasar sin cambios de código; si falla, el defecto está en la tarea que dueña de esa vista. Se corrige allí, con una prueba que falle primero.
- [ ] **Paso 3:** pasar las skills `review-animations`, `accessibility-review` (WCAG 2.1 AA, en los dos temas) y `design-critique` sobre `app/static`. Cada hallazgo Crítico o Importante se corrige con su prueba (RED→GREEN); los menores se anotan.
- [ ] **Paso 4:** actualizar el README y correr `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `docs: TicketIA en el README y revisión de movimiento y accesibilidad`.
