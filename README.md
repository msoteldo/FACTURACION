# Facturación automática de tickets (receptor CFDI 4.0)

Servicio web (FastAPI) que toma la foto de un ticket de compra, extrae sus datos con
Gemini y automatiza el portal de facturación del comercio con Playwright en modo
headless. Hoy cubre **Sam's Club / Walmart, OXXO y HEB**, con una webapp para usarlo desde la computadora.

Reglas de diseño que el código respeta:

- **El clic final en "Facturar" siempre lo aprueba una persona.** No hay parámetro
  para saltarlo: el trabajo se pausa y espera `respuesta = "facturar"`.
- **Nunca se intenta resolver un captcha.** Si aparece uno visible, el trabajo se pausa
  con una pregunta `captcha` (opciones `reintentar` / `cancelar`) y una captura de pantalla.
- **Las claves solo vienen de variables de entorno** (secretos en Render). Los datos fiscales
  también, salvo que se editen desde la webapp (se guardan en Postgres). Nada va hardcodeado
  ni en el repo.

## Estructura

```
app/
  config.py          variables de entorno (datos fiscales, claves, ajustes)
  extraccion.py      foto -> JSON con Gemini (requests puro, reintentos 429/503, aviso claro en 404)
  portal_walmart.py  flujo del portal, sin input(): /ticket -> /address -> /payment -> /invoiceSelection
  trabajos.py        cada factura corre en un hilo con su Chromium; se pausa cuando necesita a un humano
  main.py            API FastAPI
  db.py, perfil.py   datos fiscales editables en Postgres (Neon); sin base se usan los del entorno
  sesion.py          login de la webapp: cookie firmada con una clave derivada de API_KEY
  static/            landing (index.html) y app (app/, js/, css/): HTML + JS en módulos, sin build
migraciones/         migraciones de Alembic (se aplican al arrancar si hay DATABASE_URL)
tests/               pruebas contra un portal simulado (tests/mock_portal) con Chromium headless
scripts_locales/     scripts originales para uso en local (navegador visible, input() en terminal)
```

## Webapp: TicketIA

- **`/`** es la landing pública: qué hace TicketIA, cómo funciona y el botón "Entrar".
- **`/app/`** es la app. La contraseña es la `API_KEY`. Los enlaces viejos `/#/trabajo/<id>` redirigen solos.

Pantallas de la app:

- **Nueva factura:** arrastra o elige la foto del ticket (también con teclado). Mientras la IA la lee,
  una franja recorre la foto; luego se elige el comercio (Sam's/Walmart, OXXO o HEB) y los campos dudosos
  quedan marcados. Si no se lee, se captura a mano. Abajo está "¿Ya facturaste este ticket?" (Walmart).
- **Trabajo:** línea de pasos, la pregunta del portal con sus capturas y un reloj que usa la hora de
  expiración del servidor. "Facturar" abre una confirmación dentro de la página (razón social y RFC);
  Escape la cancela. Al facturar se "imprime" el comprobante.
- **Datos fiscales:** RFC, razón social y domicilio, con errores junto a cada campo. Requiere
  `DATABASE_URL`; los cambios aplican a las facturas nuevas (y al bot de Telegram).

Diseño: tokens en `app/static/css/tokens.css` (tema claro y oscuro según el sistema, contraste WCAG AA
verificado por `tests/test_contraste.py`), fuentes Instrument Sans e IBM Plex Mono servidas desde
`app/static/fuentes/` (licencia OFL). Las animaciones solo usan `transform` y `opacity` y se apagan
con "reducir movimiento" del sistema. La sesión dura 12 h; cambiar la `API_KEY` cierra todas las sesiones.

## Cómo se usa la API

Todas las rutas, excepto `/salud`, `/` y `POST /sesion`, requieren el header `X-API-Key` o la
cookie de sesión de la webapp. En `/docs` está Swagger UI (botón **Authorize**).

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/salud` | health check de Render |
| GET | `/configuracion` | qué variables faltan (sin mostrar valores) |
| POST | `/tickets/extraer` | multipart `foto` -> TC, TR, total, `forma_pago_sat_sugerida`, `requiere_revision` |
| POST | `/facturas` | inicia un trabajo: `tc`, `tr`, `uso_cfdi` (G01/G03), `forma_pago` (01 efectivo/04/28/05), `metodo_entrega` (email —por defecto— o descarga), `correo_alterno` |
| GET | `/facturas/{id}` | estado, pregunta vigente, eventos, capturas y archivos |
| POST | `/facturas/{id}/respuesta` | `{"pregunta_id": n, "respuesta": "<una de las opciones>"}` |
| POST | `/facturas/{id}/cancelar` | cancela (solo antes del clic en Facturar) |
| GET | `/facturas/{id}/archivos/{nombre}` | descarga XML/PDF o capturas `.png` |
| POST | `/consultas` | `{"numero": "<TC# o folio>"}`: busca un ticket **ya facturado** en "Consulta o reenvía tu factura" y descarga el XML/PDF. No factura ni reenvía nada; el avance se ve con `GET /facturas/{id}` |
| POST / DELETE | `/sesion` | login de la webapp (`{"password": "<API_KEY>"}`) y salir |
| GET / PUT | `/perfil` | datos fiscales vigentes (`origen`: `base` o `entorno`) y editarlos (requiere `DATABASE_URL`) |
| POST | `/diagnostico/portal` | abre el portal en headless y lista campos (equivale a `--inspect`) |

Estados de un trabajo: `en_cola` -> `ejecutando` <-> `esperando_respuesta` -> `completado` | `cancelado` | `error`.

Tipos de pregunta que puede hacer un trabajo:

| tipo | cuándo | opciones |
|---|---|---|
| `modal` | el portal muestra un mensaje **delicado** (refacturar, cancelar, error, "no coincide"...) o ilegible. Las confirmaciones rutinarias se aceptan solas y aparecen en `datos.mensajes_del_portal` de `confirmar_facturar` | `continuar`, `cerrar` |
| `revisar_direccion` | lo que muestra el portal no coincide con los datos configurados (p. ej. razón social como la tiene el SAT) | `continuar`, `cancelar` |
| `captcha` | aparece un captcha visible | `reintentar`, `cancelar` |
| `confirmar_facturar` | todo listo; `datos` trae el resumen (TC, TR, uso CFDI, forma de pago) | `facturar`, `cancelar` |

Si nadie responde en `ANSWER_TIMEOUT_S` segundos (10 min por defecto), el trabajo se
cancela **sin facturar**. El campo `facturado` indica si ya se dio el clic irreversible.

Ejemplo con curl:

```bash
URL=https://tu-servicio.onrender.com; K="X-API-Key: $API_KEY"
curl -H "$K" -F foto=@ticket.jpg $URL/tickets/extraer
curl -H "$K" -H 'Content-Type: application/json' -X POST $URL/facturas \
  -d '{"tc":"99064...","tr":"03388","uso_cfdi":"G01","forma_pago":"28"}'
curl -H "$K" $URL/facturas/<id>          # repetir hasta "esperando_respuesta"
curl -H "$K" -H 'Content-Type: application/json' -X POST $URL/facturas/<id>/respuesta \
  -d '{"pregunta_id":2,"respuesta":"facturar"}'
```

## OXXO

`POST /facturas/oxxo` con `{"fecha": "AAAA-MM-DD", "folio": "<Fol_Vta>", "id_venta": "<ID=>",
"total": 270.00, "uso_cfdi": "G01"}` (o la foto en el bot, que detecta el comercio).

El flujo (`app/portal_oxxo.py`) inicia sesión con `OXXO_USUARIO` / `OXXO_PASSWORD` si están
configurados, llena el ticket, da **Validar Ticket** (no factura), elige tu RFC guardado y se
detiene a confirmar **antes de "Continuar"**: con sesión iniciada, ese clic generó la factura
de inmediato en el portal real. OXXO la envía al correo de la cuenta (hasta 4 horas).
Si OXXO responde "facturado en tienda" o "ticket no encontrado", el trabajo termina con ese error.

## HEB

`POST /facturas/heb` con `{"sucursal": "2950", "ticket": "800544", "fecha": "AAAA-MM-DD",
"total": 345.70, "uso_cfdi": "G01"}` (o la foto en el bot). En el ticket de HEB, `ticket` es el
primer número de la línea de folio (`800544 10-03-26 4:34P 229/09/02950`), `sucursal` es el último
bloque sin ceros (`2950`) y la fecha viene como MES-DÍA-AÑO (`10-03-26` = 3 de octubre de 2026).

El portal (`https://facturacion.heb.com.mx/cli/invoice-create/`, `app/portal_heb.py`) no requiere
cuenta ni tiene captcha: se usan `RFC`, `RAZON_SOCIAL`, `CP`, `REGIMEN_FISCAL_TEXTO` y `EMAIL`.
Pasos: ticket (sucursal, ticket, fecha, venta) > **Agregar ticket** > **Continuar** (solo valida)
> datos fiscales > **Generar factura**. El flujo se detiene a confirmar **antes de "Generar
factura"**, que emite la factura de inmediato; el portal real habilita ese botón aunque falten
campos, así que se validan antes. HEB envía la factura al correo capturado. Solo se factura
hasta el último día del mes de la compra.

## Despliegue en Render (plan gratis)

1. Sube este repo a GitHub (sin `.env`).
2. En Render: **New > Blueprint** y elige el repo; toma `render.yaml` (servicio Docker, plan free).
3. Captura los secretos que pide (`GEMINI_API_KEY`, `RFC`, `RAZON_SOCIAL`, domicilio, `EMAIL`...).
   `API_KEY` la genera Render; cópiala desde Environment para tu frontend.
4. Cuando termine el deploy, abre `https://<servicio>.onrender.com/docs`, autoriza con la API key y:
   - `GET /configuracion` debe devolver `datos_fiscales_faltantes: []`.
   - `POST /diagnostico/portal` y luego `GET /facturas/{id}`: revisa que `elementos`
     tenga `membershipOrRFC`, `postalCode`, `ticketNumber`, `transactionNumber` y que
     `captcha_visible` sea `false`. Así confirmas que el portal responde igual en headless.

### Datos fiscales en Neon (opcional, para editarlos desde la web)

1. Crea un proyecto gratis en [neon.tech](https://neon.tech) y copia la cadena de conexión
   **pooled** (el host lleva `-pooler`; trae `sslmode=require`).
2. Antes del primer deploy, prueba la migración contra una *branch* desechable de Neon:
   `TEST_DATABASE_URL=<cadena de la branch> pytest -n0 tests/test_db.py`.
3. En Render → Environment, pega la cadena en `DATABASE_URL`. Al arrancar, el contenedor
   corre `alembic upgrade head`; si falla, el deploy falla y queda la versión anterior.
4. La primera vez, la web muestra los datos del entorno. Al guardar, pasan a la base.

Si la base está configurada pero no responde, las facturas nuevas se rechazan con 503:
nunca se usan los datos del entorno como respaldo, porque podrían estar desactualizados.
Neon suspende la base tras unos minutos sin uso; la primera consulta tarda ~1 s más.

Límites del plan gratis que el diseño ya considera:

- **512 MB de RAM**: se corre un solo Chromium a la vez (`MAX_BROWSERS=1`); un flujo completo
  midió ~280 MB en el contenedor. Si se queda corto, `BROWSER_CHANNEL=` (vacío) usa
  `chrome-headless-shell`, que consume menos.
- **Se duerme tras 15 min sin tráfico** y el disco es efímero: los trabajos viven en memoria
  y se pierden al reiniciar. Por eso el tiempo máximo de espera de una respuesta es de 10 min
  (el polling del frontend mantiene despierto el servicio). Descarga el XML/PDF en cuanto
  termine, o usa `metodo_entrega: "email"` para que el portal lo mande por correo.
- **Un solo worker de uvicorn** (ya fijado en el Dockerfile): el estado no se comparte entre procesos.

## Bot de Telegram

El bot corre dentro del mismo servicio (webhook en `/telegram/webhook`), así que no
necesita otro servidor: cada mensaje despierta al servicio si estaba dormido.

1. En Telegram, habla con **@BotFather** → `/newbot` → copia el token.
2. En Render → Environment: pon `TELEGRAM_BOT_TOKEN`. `TELEGRAM_WEBHOOK_SECRET` lo genera
   el blueprint (o pon una cadena aleatoria de letras/números). Guarda y espera el deploy:
   el webhook se registra solo al arrancar.
3. Escríbele `/start` a tu bot: como aún no estás autorizado, te responde tu **chat id**.
4. Pon ese id en `TELEGRAM_ALLOWED_CHAT_IDS` (varios, separados por coma) y guarda.
5. Manda la foto de un ticket.

Uso: foto → **🧾 Facturar** o **🔎 Consultar factura** → (para facturar) **G01 / G03** →
(forma de pago si no se leyó) → captura con **✅ Facturar / ✖ Cancelar** → aviso final.
Si la consulta dice que el ticket no está facturado, el aviso trae **🧾 Facturarlo**. Comandos: `/tc`, `/tr` (corregir lo leído),
`/facturar TC TR`, `/consultar TC`, `/estado`, `/cancelar`, `/ayuda`.

Si el bot no responde: `POST /telegram/configurar` (con tu API key) vuelve a registrar el
webhook y muestra el último error que reportó Telegram.

## Desarrollo local

```bash
pip install -r requirements-dev.txt
playwright install chromium
cp .env.example .env   # y llénalo
uvicorn app.main:app --reload --workers 1      # HEADLESS=false en .env para ver el navegador
pytest                                          # no toca el portal real: usa tests/mock_portal
```

`pytest` corre en paralelo (`pytest-xdist`, ver `pytest.ini`). Para depurar una sola prueba:
`pytest -n0 tests/test_api.py::test_salud_sin_auth`. Con `DATABASE_URL=sqlite:///./facturacion.db`
en `.env` se puede probar la edición de datos fiscales sin Postgres (crea la tabla con
`alembic upgrade head`).

## Notas

- El plan gratis de la API de Gemini usa las entradas y salidas para entrenar modelos de Google.
  Antes de mandar tickets reales en producción, conviene pasar a un plan de pago.
- `NOTIFY_WEBHOOK_URL` (opcional) recibe un POST cada vez que un trabajo necesita a un
  humano; sirve para conectar después un bot de Telegram sin hacer polling.
