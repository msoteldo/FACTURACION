# Facturación automática de tickets (receptor CFDI 4.0)

Servicio web (FastAPI) que toma la foto de un ticket de compra, extrae sus datos con
Gemini y automatiza el portal de facturación del comercio con Playwright en modo
headless. Hoy cubre **Sam's Club / Walmart México**.

Reglas de diseño que el código respeta:

- **El clic final en "Facturar" siempre lo aprueba una persona.** No hay parámetro
  para saltarlo: el trabajo se pausa y espera `respuesta = "facturar"`.
- **Nunca se intenta resolver un captcha.** Si aparece uno visible, el trabajo se pausa
  con una pregunta `captcha` (opciones `reintentar` / `cancelar`) y una captura de pantalla.
- **Los datos fiscales y las claves solo vienen de variables de entorno** (secretos en
  Render). Nada va hardcodeado ni en el repo.

## Estructura

```
app/
  config.py          variables de entorno (datos fiscales, claves, ajustes)
  extraccion.py      foto -> JSON con Gemini (requests puro, reintentos 429/503, aviso claro en 404)
  portal_walmart.py  flujo del portal, sin input(): /ticket -> /address -> /payment -> /invoiceSelection
  trabajos.py        cada factura corre en un hilo con su Chromium; se pausa cuando necesita a un humano
  main.py            API FastAPI
tests/               pruebas contra un portal simulado (tests/mock_portal) con Chromium headless
scripts_locales/     scripts originales para uso en local (navegador visible, input() en terminal)
```

## Cómo se usa la API

Todas las rutas, excepto `/salud`, requieren el header `X-API-Key`. En `/docs` está
Swagger UI (botón **Authorize**), que sirve como frontend provisional.

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/salud` | health check de Render |
| GET | `/configuracion` | qué variables faltan (sin mostrar valores) |
| POST | `/tickets/extraer` | multipart `foto` -> TC, TR, total, `forma_pago_sat_sugerida`, `requiere_revision` |
| POST | `/facturas` | inicia un trabajo: `tc`, `tr`, `uso_cfdi` (G01/G03), `forma_pago` (04/28/05), `metodo_entrega` (email/descarga), `correo_alterno` |
| GET | `/facturas/{id}` | estado, pregunta vigente, eventos, capturas y archivos |
| POST | `/facturas/{id}/respuesta` | `{"pregunta_id": n, "respuesta": "<una de las opciones>"}` |
| POST | `/facturas/{id}/cancelar` | cancela (solo antes del clic en Facturar) |
| GET | `/facturas/{id}/archivos/{nombre}` | descarga XML/PDF o capturas `.png` |
| POST | `/consultas` | `{"numero": "<TC# o folio>"}`: busca un ticket **ya facturado** en "Consulta o reenvía tu factura" y descarga el XML/PDF. No factura ni reenvía nada; el avance se ve con `GET /facturas/{id}` |
| POST | `/diagnostico/portal` | abre el portal en headless y lista campos (equivale a `--inspect`) |

Estados de un trabajo: `en_cola` -> `ejecutando` <-> `esperando_respuesta` -> `completado` | `cancelado` | `error`.

Tipos de pregunta que puede hacer un trabajo:

| tipo | cuándo | opciones |
|---|---|---|
| `modal` | el portal muestra un mensaje de confirmación | `continuar`, `cerrar` |
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
  -d '{"tc":"99064...","tr":"03388","uso_cfdi":"G01","forma_pago":"28","metodo_entrega":"descarga"}'
curl -H "$K" $URL/facturas/<id>          # repetir hasta "esperando_respuesta"
curl -H "$K" -H 'Content-Type: application/json' -X POST $URL/facturas/<id>/respuesta \
  -d '{"pregunta_id":2,"respuesta":"facturar"}'
```

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

Límites del plan gratis que el diseño ya considera:

- **512 MB de RAM**: se corre un solo Chromium a la vez (`MAX_BROWSERS=1`); un flujo completo
  midió ~280 MB en el contenedor. Si se queda corto, `BROWSER_CHANNEL=` (vacío) usa
  `chrome-headless-shell`, que consume menos.
- **Se duerme tras 15 min sin tráfico** y el disco es efímero: los trabajos viven en memoria
  y se pierden al reiniciar. Por eso el tiempo máximo de espera de una respuesta es de 10 min
  (el polling del frontend mantiene despierto el servicio). Descarga el XML/PDF en cuanto
  termine, o usa `metodo_entrega: "email"` para que el portal lo mande por correo.
- **Un solo worker de uvicorn** (ya fijado en el Dockerfile): el estado no se comparte entre procesos.

## Desarrollo local

```bash
pip install -r requirements-dev.txt
playwright install chromium
cp .env.example .env   # y llénalo
uvicorn app.main:app --reload --workers 1      # HEADLESS=false en .env para ver el navegador
pytest                                          # no toca el portal real: usa tests/mock_portal
```

## Notas

- El plan gratis de la API de Gemini usa las entradas y salidas para entrenar modelos de Google.
  Antes de mandar tickets reales en producción, conviene pasar a un plan de pago.
- `NOTIFY_WEBHOOK_URL` (opcional) recibe un POST cada vez que un trabajo necesita a un
  humano; sirve para conectar después un bot de Telegram sin hacer polling.
