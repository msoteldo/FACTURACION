# Extensión de TicketIA para Petro Seven: plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usa superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan casillas (`- [ ]`) para el seguimiento.

**Objetivo:** facturar tickets de Petro Seven llenando el portal en el navegador del propio cliente con una extensión de Chrome que se detiene antes de "Facturar", y quitar el flujo de servidor de Petro Seven, que Radware bloquea.

**Arquitectura:**
- La web de TicketIA lee el ticket con la IA y, con la extensión instalada, le pasa los datos con `window.postMessage`.
- `puente.js` (content script en TicketIA) valida y reenvía a `fondo.js` (service worker). `fondo.js` abre el portal e inyecta el llenador.
- El llenador corre en el mundo aislado y usa un ayudante en el mundo de la página para la fecha de AngularJS y para capturar los `alert`.

**Stack:** Chrome Manifest V3 en JavaScript clásico, sin build; FastAPI; pytest + Playwright (`launch_persistent_context` con `--load-extension`, que **funciona en headless en este entorno**, comprobado); Node 22 para la prueba de `validar.js`.

**Spec:** `docs/superpowers/specs/2026-10-30-extension-petro7-design.md`

## Restricciones globales

- **La extensión nunca pulsa "Facturar"** (`[ng-click^='facturacionCtrl.facturarTicket']`). Solo lo resalta. Cada prueba de llenado verifica `window.__facturado === 0` en el portal simulado.
- Nunca se toca un captcha ni la página de bloqueo de Radware; solo se avisa.
- La extensión no guarda contraseñas ni claves y no hace `fetch` a TicketIA.
- Los content scripts y los archivos inyectados son scripts clásicos (no módulos ES). `validar.js` debe poder cargarse en el navegador y también en Node con `require`.
- `puente.js` solo acepta `event.source === window && event.origin === location.origin && data.tipo === "ticketia:llenar"`.
- Sin código remoto; todo dentro de `extension/`.
- Textos de la barra y de la web en español, tal como los fija la spec.
- `pytest` debe pasar completo al final de cada tarea.

## Revisión: casos que más pueden fallar

1. **Doble clic en "Llenar en Petro Seven"** → se abre una sola pestaña y hay un solo llenado. Prueba en la Tarea 4.
2. **La pestaña del portal se recarga a mitad del llenado** (p. ej. al iniciar sesión) → el llenado sigue y "Agregar Ticket" no se pulsa dos veces. Prueba en la Tarea 5.
3. **Se cierra la pestaña del portal a mitad del llenado** → los datos se borran de `chrome.storage.session` y no queda nada colgado. Prueba en la Tarea 4.
4. **La extensión se recargó con la página de TicketIA abierta** (la marca sigue, pero el puente ya no contesta) → la web muestra "La extensión no respondió; recarga esta página". Prueba en la Tarea 6.
5. **Fecha que la IA no leyó o que es inválida** → la web no manda nada y marca el campo; `puente.js` también rechaza datos inválidos. Pruebas en las Tareas 4 y 6.

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `extension/manifest.json` | MV3, permisos y content scripts |
| `extension/config.js` | `self.TICKETIA_CONFIG = { petro7Url }`; las pruebas lo reescriben |
| `extension/comun/validar.js` | `validarPetro7(datos) -> { ok, errores }` (navegador y Node) |
| `extension/puente.js` | marca la instalación, valida y reenvía el mensaje de la web |
| `extension/fondo.js` | abre la pestaña, guarda los datos por pestaña, inyecta y limpia |
| `extension/comun/aviso.js`, `aviso.css` | la barra de TicketIA sobre el portal |
| `extension/portales/petro7.js` | el llenador |
| `extension/portales/petro7_pagina.js` | ayudante del mundo de la página: fecha y `alert` |
| `tests/test_extension.py` | pruebas de la extensión (validar con Node, puente, fondo, llenado) |
| `tests/conftest.py` | fixtures `extension_dir` y `contexto_extension` |
| `app/extraccion.py`, `app/telegram_bot.py`, `app/static/js/vistas/nueva.js`, `app/static/js/plazo.js` | TicketIA |
| se borran | `app/portal_petro7.py`, `tests/test_petro7.py`, `POST /facturas/petro7`, `GET /diagnostico/petro7`, el log de la huella de `API_KEY`, `PETRO7_*` |

---

### Task 1: Quitar el flujo de servidor de Petro Seven y los diagnósticos

**Archivos:**
- Borrar: `app/portal_petro7.py`, `tests/test_petro7.py`
- Modificar: `app/main.py` (ruta `/facturas/petro7`, `diagnostico_petro7`, log `API_KEY cargada` e `import hashlib`), `app/trabajos.py` (ramas de `SolicitudPetro7` y `FlujoPetro7`, si existen), `app/config.py` (campos `petro7_*`), `render.yaml`, `.env.example`, `README.md` (sección de Petro Seven por API)
- Conservar: `tests/mock_portal/petro7.html` y su ruta `/petro7` en el servidor simulado de `conftest.py`. Quitar solo `PETRO7_URL` del `os.environ.update`.
- Prueba: `tests/test_api.py`

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_petro7_ya_no_se_factura_desde_el_servidor():
    assert client.post("/facturas/petro7", headers=H, json={}).status_code in (404, 405)
    assert client.get("/diagnostico/petro7", headers=H).status_code in (404, 405)
    assert not any(c.startswith("petro7") for c in main.ajustes.__dataclass_fields__)
```

- [ ] **Paso 2:** `python -m pytest -n0 tests/test_api.py -k petro7 -v`. Esperado: FAIL.
- [ ] **Paso 3:** borrar y quitar lo listado. Comprobar con `grep -rn -i "petro7\|PETRO7" app render.yaml .env.example` que solo queda lo que agreguen las tareas siguientes.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `refactor: quitar el flujo de servidor de Petro Seven y los diagnósticos temporales`.

### Task 2: Leer tickets de Petro Seven con la IA

**Archivos:** `app/extraccion.py` · Prueba: `tests/test_extraccion.py`

**Interfaces:**
- Produce, en el JSON de `leer_ticket`:
  - `comercio_clave: "petro7"`;
  - `petro7_estacion: str | None` (solo dígitos), `petro7_folio: str | None`, `petro7_webid: str | None` (sin espacios);
  - `fecha: "AAAA-MM-DD"` (desde "Fecha del recibo", que viene DD/MM/AAAA);
  - `forma_de_pago` (`"credito"` para "Tarjetas Credito", `"debito"` para "Tarjetas Debito", `"efectivo"`);
  - `confianza_webid`;
  - `forma_pago_sat_sugerida` y `requiere_revision`, como en los demás comercios.
- `PROMPT` gana las reglas de Petro Seven: los datos están en la línea "Estacion: … Folio: … WebID: …" y en "Fecha del recibo"; el RFC impreso es de la gasolinera (Petromax) y no se usa.

- [ ] **Paso 1: escribir las pruebas que fallan** (con el doble de `requests.post` que ya usa el archivo)

```python
def test_petro7_normaliza_y_valida(monkeypatch):
    t = {"comercio": "Petro Seven", "comercio_clave": "petro7", "total": 1000.0,
         "petro7_estacion": "62 74", "petro7_folio": " 1234567 ", "petro7_webid": "AB 12",
         "fecha": "2026-07-14", "forma_de_pago": "credito", "confianza_webid": "alta", "advertencias": []}
    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(200, respuesta_gemini(t)))
    r = extraccion.leer_ticket("k", "m", b"img", "image/jpeg")
    assert (r["petro7_estacion"], r["petro7_folio"], r["petro7_webid"]) == ("6274", "1234567", "AB12")
    assert r["forma_pago_sat_sugerida"] == "04" and r["requiere_revision"] is False

def test_petro7_avisa_lo_que_falta(monkeypatch):
    t = {"comercio_clave": "petro7", "total": 1000.0, "petro7_estacion": None, "petro7_folio": "1",
         "petro7_webid": None, "fecha": None, "forma_de_pago": "desconocido", "advertencias": []}
    monkeypatch.setattr(extraccion.requests, "post", lambda *a, **k: Resp(200, respuesta_gemini(t)))
    r = extraccion.leer_ticket("k", "m", b"img", "image/jpeg")
    assert r["requiere_revision"] is True
    assert any("Estación" in a for a in r["advertencias"]) and any("Web ID" in a for a in r["advertencias"])
    assert any("fecha" in a.lower() for a in r["advertencias"])

def test_prompt_menciona_petro7():
    assert '"petro7"' in extraccion.PROMPT and "WebID" in extraccion.PROMPT
```

- [ ] **Paso 2:** `python -m pytest -n0 tests/test_extraccion.py -v`. Esperado: FAIL.
- [ ] **Paso 3:** agregar Petro Seven al `PROMPT` y una rama `clave == "petro7"` en `validar()`. La clave `"petro7"` se acepta en la lista de claves válidas. Avisos:
  - "No se leyó la Estación; captúrala a mano."
  - "No se leyó el Folio; captúralo a mano."
  - "No se leyó el Web ID; captúralo a mano."
  - "No se leyó la fecha del recibo; el portal la exige."
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: leer tickets de Petro Seven con la IA`.

### Task 3: El bot responde a tickets de Petro Seven

**Archivos:** `app/telegram_bot.py` (`_nuevo_pendiente`) · Prueba: `tests/test_telegram.py`

**Interfaces:** si `comercio_clave == "petro7"`, el bot responde con los datos leídos (Estación, Folio, Web ID, Fecha, Total) y el texto fijo **"Petro Seven se factura desde la web en tu computadora, con la extensión de TicketIA."** No crea un pendiente ni un trabajo.

- [ ] **Paso 1: escribir la prueba que falla**

```python
def test_petro7_en_el_bot_solo_informa(monkeypatch):
    monkeypatch.setattr(extraccion, "leer_ticket", lambda *a, **k: {
        "comercio_clave": "petro7", "total": 1000.0, "petro7_estacion": "6274", "petro7_folio": "1234567",
        "petro7_webid": "AB12", "fecha": "2026-07-14", "advertencias": [], "requiere_revision": False})
    bot, api = nuevo_bot()
    try:
        antes = set(main.gestor.trabajos)
        bot.procesar(foto())
        msj = api.buscar(lambda m, p: "con la extensión de TicketIA" in texto(p))
        assert "6274" in texto(msj) and "AB12" in texto(msj) and not botones(msj)
        assert CHAT not in bot.pendientes and set(main.gestor.trabajos) == antes
    finally:
        main.gestor.oyentes.remove(bot)
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: el bot indica que Petro Seven se factura con la extensión`.

### Task 4: Base de la extensión (manifest, validar, puente, fondo, barra)

**Archivos:**
- Crear: `extension/manifest.json`, `extension/config.js`, `extension/comun/validar.js`, `extension/puente.js`, `extension/fondo.js`, `extension/comun/aviso.js`, `extension/comun/aviso.css`, `extension/portales/petro7.js` (en esta tarea solo muestra "Llenando…" en la barra)
- Modificar: `tests/conftest.py`
- Prueba: `tests/test_extension.py`

**Interfaces:**
- `manifest.json`:
  - `"manifest_version": 3`, `"name": "TicketIA"`, `"version": "0.1.0"`;
  - `"permissions": ["scripting", "storage", "tabs"]`;
  - `"host_permissions": ["https://facturacion-tickets.onrender.com/*", "https://tarjetapetro-7.com.mx/*"]`;
  - `"background": {"service_worker": "fondo.js"}`;
  - `"content_scripts": [{ "matches": ["https://facturacion-tickets.onrender.com/*", "http://localhost/*", "http://127.0.0.1/*"], "js": ["comun/validar.js", "puente.js"] }]`.
- `config.js`: `self.TICKETIA_CONFIG = { petro7Url: "https://tarjetapetro-7.com.mx/KPortalExterno/" }`. `fondo.js` lo carga con `importScripts("config.js")`.
- `validar.js`: `validarPetro7(datos) -> { ok: boolean, errores: string[] }`, con las reglas de la tabla de datos de la spec. Expone `self.TicketIA.validarPetro7` y `module.exports.validarPetro7` cuando existe `module`.
- `puente.js`:
  - pone `document.documentElement.dataset.ticketiaExtension = chrome.runtime.getManifest().version`;
  - con un mensaje válido llama `chrome.runtime.sendMessage({ tipo: "llenar", comercio, datos })`;
  - contesta con `window.postMessage({ tipo: "ticketia:estado", estado: "abierto" | "error", mensaje }, location.origin)`.
- `fondo.js`:
  - recibe `llenar`; si ya hay una pestaña en curso con los mismos datos (comparando `JSON.stringify`), la enfoca y no abre otra;
  - si no, `chrome.tabs.create({ url: petro7Url })` y guarda `chrome.storage.session` → `{ ["llenar:" + tabId]: datos }`;
  - en `tabs.onUpdated` con `status === "complete"` y datos guardados, inyecta `aviso.css`, `comun/aviso.js` y `portales/petro7.js`, y luego `tabs.sendMessage(tabId, { tipo: "llenar", datos })`;
  - en `tabs.onRemoved` borra la entrada;
  - con `{ tipo: "terminado", tabId }` del llenador también la borra.
- `aviso.js`: `self.TicketIA.aviso = { mostrar(texto, tipo = "info" | "ok" | "error"), boton(texto, alClic) }`. Es una barra fija arriba (`#ticketia-aviso`) con `aria-live="polite"`.
- Fixtures en `conftest.py`:
  - `extension_dir(tmp_path_factory)`: copia `extension/` y reescribe `config.js` con `petro7Url = f"http://127.0.0.1:{puerto_mock}/petro7/KPortalExterno/"`; agrega `"http://127.0.0.1/*"` a `host_permissions`;
  - `contexto_extension(extension_dir)`: `launch_persistent_context("", executable_path=…, headless=True, args=[--disable-extensions-except, --load-extension])`.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_validar_petro7_con_node():
    r = subprocess.run(["node", "-e", """
      const { validarPetro7: v } = require('./extension/comun/validar.js');
      const ok = { estacion: '6274', folio: 'A12', web_id: 'B3', fecha: '2026-07-14', forma_pago: '04', uso_cfdi: 'G01' };
      const casos = [[ok, true], [{ ...ok, estacion: '62a' }, false], [{ ...ok, fecha: '2026-13-40' }, false],
                     [{ ...ok, forma_pago: '99' }, false], [{ ...ok, uso_cfdi: 'G02' }, false], [{ ...ok, web_id: '' }, false]];
      for (const [d, esperado] of casos) if (v(d).ok !== esperado) { console.error(JSON.stringify(d)); process.exit(1); }
    """], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

def test_puente_marca_y_abre_el_portal(contexto_extension, app_url, pagina_con_sesion_ext):
    p = pagina_con_sesion_ext
    assert p.evaluate("document.documentElement.dataset.ticketiaExtension") == "0.1.0"
    with contexto_extension.expect_page() as nueva:
        p.evaluate("window.postMessage({tipo:'ticketia:llenar', comercio:'petro7', datos:%s}, location.origin)" % DATOS_JSON)
    portal = nueva.value
    portal.wait_for_load_state()
    expect(portal.locator("#ticketia-aviso")).to_be_visible()

def test_doble_mensaje_abre_una_sola_pestana(contexto_extension, pagina_con_sesion_ext): ...
    # dos postMessage seguidos con los mismos datos → len(contexto_extension.pages) aumenta en 1

def test_mensaje_de_otro_origen_se_ignora(contexto_extension, pagina_con_sesion_ext): ...
    # postMessage con targetOrigin '*' desde un iframe de otro origen → no se abre pestaña en 2 s

def test_datos_invalidos_no_abren_nada(contexto_extension, pagina_con_sesion_ext): ...
    # fecha 'ayer' → respuesta ticketia:estado con estado 'error' y sin pestaña nueva

def test_cerrar_la_pestana_limpia_los_datos(contexto_extension, pagina_con_sesion_ext): ...
    # abrir, cerrar la pestaña del portal; el service worker reporta chrome.storage.session vacío
```

`pagina_con_sesion_ext` es `pagina_con_sesion` pero en `contexto_extension`. `DATOS_JSON` es un diccionario válido. Las pruebas marcadas con `...` se escriben con las aserciones del comentario.

- [ ] **Paso 2:** `python -m pytest -n0 tests/test_extension.py -v`. Esperado: FAIL.
- [ ] **Paso 3:** implementar los archivos.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: base de la extensión de TicketIA (puente, fondo y barra)`.

### Task 5: El llenador de Petro Seven

**Archivos:** `extension/portales/petro7.js`, `extension/portales/petro7_pagina.js`, `extension/fondo.js` (inyecta el ayudante con `world: "MAIN"`), `tests/mock_portal/petro7.html` (solo si falta algo) · Prueba: `tests/test_extension.py`

**Interfaces:**
- `petro7.js`: `async llenar(datos)`, con los pasos 1–10 de la spec, los textos de la barra de la spec, y esperas de 15 s por control y 5 min para la sesión.
  - **Estado por pestaña:** antes de "Agregar Ticket" escribe `sessionStorage["ticketia:agregado"] = "1"`. Si la pestaña se recarga, no vuelve a agregar si ya hay una fila con el mismo folio.
  - Al terminar o fallar manda `{ tipo: "terminado" }` a `fondo.js`.
- `petro7_pagina.js`:
  - escucha `document` `ticketia:fecha` (`detail: "AAAA-MM-DD"`): si existe `window.angular`, fija el modelo y hace `$apply()`; si no, escribe DD/MM/AAAA en `md-datepicker input` y dispara `input`/`change`;
  - mientras haya un llenado, reemplaza `window.alert` por una función que dispara `ticketia:alerta` con el texto y no muestra el diálogo.
- Forma de pago: en `#formaPago` se elige la opción que contiene "crédito" para `"04"` o "débito" para `"28"`, sin distinguir acentos ni mayúsculas. Con `#formaPagoAux` no se toca.
- Uso CFDI: en `#usoCfdi` se elige la opción cuyo `value` o texto empieza con la clave.

- [ ] **Paso 1: escribir las pruebas que fallan** (contra el portal simulado; la prueba hace de persona: inicia sesión con `club@example.com` / `secreto` cuando la barra lo pide)

```python
def test_llena_y_se_detiene_antes_de_facturar(abrir_portal):
    portal = abrir_portal(DATOS)                      # helper: manda el mensaje y devuelve la pestaña
    expect(portal.locator("#ticketia-aviso")).to_contain_text("Inicia sesión en Petro Seven")
    iniciar_sesion_simulada(portal)
    expect(portal.locator("#ticketia-aviso")).to_contain_text("revisa los datos y da Facturar", timeout=20000)
    assert portal.locator("#tickets tr").count() == 1
    assert portal.eval_on_selector("#formaPago", "e => e.options[e.selectedIndex].text") == "Tarjeta de crédito"
    assert portal.eval_on_selector("#usoCfdi", "e => e.value").startswith("G01")
    assert portal.evaluate("window.__facturado") == 0

def test_efectivo_no_toca_forma_de_pago(abrir_portal): ...      # folio '55…' → #formaPagoAux intacto, aviso "Listo"
def test_ticket_rechazado_muestra_el_alert(abrir_portal): ...   # folio '000…' → barra contiene "ya se encuentra facturado", sin diálogo nativo
def test_bloqueo_pide_verificacion(abrir_portal): ...           # petro7Url con ?bloqueo=1 → barra "pidió una verificación" + botón Reintentar
def test_rfc_distinto_avisa(abrir_portal): ...                  # ?rfc=XAXX010101000 y rfc_esperado CPA010101AB1 → barra muestra ambos
def test_recarga_no_agrega_dos_veces(abrir_portal): ...         # tras la fila agregada, portal.reload() → sigue 1 fila
```

`abrir_portal` acepta la query string opcional del portal simulado (`?bloqueo=1`, `?rfc=…`). La reescribe en `config.js` del `extension_dir` de esa prueba, o usa un fixture parametrizado.

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar. El llenador nunca hace `querySelector` del botón de Facturar más que para resaltarlo con la clase `ticketia-resaltado`. Pasa la skill `animate` a la barra: una entrada de 200 ms con `transform` y `opacity`.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: la extensión llena el portal de Petro Seven y se detiene antes de Facturar`.

### Task 6: Petro Seven en la web de TicketIA

**Archivos:** `app/static/js/vistas/nueva.js`, `app/static/js/plazo.js` (nuevo), `app/static/css/vistas.css` · Prueba: `tests/test_web.py`

**Interfaces:**
- `plazo.js`: `estadoPlazo(fechaISO: string, hoy: Date) -> "vigente" | "por_vencer" | "vencido"`. El límite es el fin del día 5 del mes siguiente al de la fecha; `"por_vencer"` si faltan 2 días o menos.
- `nueva.js`:
  - chip `petro7` "Petro Seven"; campos `estacion` "Estación", `folio` "Folio", `web_id` "Web ID", `fecha` "Fecha", `forma_pago` "Forma de pago" (04, 28, 01; sin valor por defecto si la IA no la leyó), `uso_cfdi` "Uso CFDI";
  - se llena desde `petro7_estacion`, `petro7_folio`, `petro7_webid`, `fecha` y `forma_pago_sat_sugerida`;
  - con `dataset.ticketiaExtension`, el botón es **"Llenar en Petro Seven"**: valida, pide `GET /perfil` para `rfc_esperado`, manda `postMessage` y espera `ticketia:estado` hasta 3 s;
  - `"abierto"` → "Abrimos Petro Seven en otra pestaña; revisa y da Facturar ahí.";
  - sin respuesta → "La extensión no respondió; recarga esta página.";
  - sin la marca: tarjeta "Instala la extensión de TicketIA" con los pasos de la spec, y sin botón de llenar;
  - aviso de plazo debajo de la fecha: "Por vencer: factúralo antes del <día>." o "Fuera de plazo según el ticket.".

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_estado_plazo(app_url, pagina):
    pagina.goto(app_url)
    r = pagina.evaluate("""async () => { const { estadoPlazo } = await import('/js/plazo.js');
        return [estadoPlazo('2026-07-14', new Date('2026-07-20')), estadoPlazo('2026-07-14', new Date('2026-08-04')),
                estadoPlazo('2026-07-14', new Date('2026-08-06'))]; }""")
    assert r == ["vigente", "por_vencer", "vencido"]

def test_sin_extension_muestra_como_instalarla(pagina_con_sesion, monkeypatch, png): ...
    # ticket petro7 leído → tarjeta "Instala la extensión de TicketIA" y sin botón "Llenar en Petro Seven"

def test_con_extension_llena_de_punta_a_punta(contexto_extension, pagina_con_sesion_ext, monkeypatch, png): ...
    # foto → chip Petro Seven activo, campos llenos → "Llenar en Petro Seven" → texto "Abrimos Petro Seven"
    # y en la pestaña nueva el portal simulado termina en "revisa los datos y da Facturar" con __facturado == 0

def test_extension_que_no_contesta(pagina_con_sesion): ...
    # poner a mano data-ticketia-extension="0.1.0" sin extensión real → clic → "La extensión no respondió"

def test_fecha_invalida_no_se_manda(contexto_extension, pagina_con_sesion_ext, monkeypatch, png): ...
    # IA sin fecha → clic en Llenar → error en el campo Fecha y ninguna pestaña nueva
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar.
- [ ] **Paso 4:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `feat: facturar Petro Seven desde la web con la extensión`.

### Task 7: Documentación e instalación

**Archivos:** `README.md` (sección "Extensión de navegador": qué es, por qué existe, instalación en modo desarrollador, que nunca da clic en Facturar, y cómo correr sus pruebas), `extension/LEEME.md` (los mismos pasos de instalación, cortos)

- [ ] **Paso 1:** escribir la documentación.
- [ ] **Paso 2:** `python -m pytest`. Esperado: todo PASS.
- [ ] **Paso 3:** commit `docs: instalar y usar la extensión de TicketIA`.
