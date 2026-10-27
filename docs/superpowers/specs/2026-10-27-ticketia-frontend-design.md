# TicketIA: identidad, landing y rediseño de la app

**Fecha:** 2026-10-27
**Estado:** aprobado en conversación; pendiente de revisión escrita.
**Mockups de referencia (privados del dueño):**
- identidades: https://claude.ai/artifact/8KcWUWo6aiboNfuMvnrRte
- landing: https://claude.ai/artifact/B9pZRa5i18JUvxoVHQTCYX
- app: https://claude.ai/artifact/DEdApfh6eTCQ8Lgq17agbc

## Propósito

El software va a salir al público. Necesita una marca, una landing para visitantes y una app que se vea y se sienta como producto terminado, con animaciones que expliquen lo que pasa sin estorbar.

**Criterio de éxito:**
- un visitante entiende qué hace TicketIA en la landing y llega al login;
- facturar un ticket desde la app se siente rápido y claro;
- las animaciones son fluidas (solo `transform` y `opacity`) y desaparecen con movimiento reducido;
- no hay peticiones a otros sitios;
- todas las pruebas pasan.

## Alcance

**Incluye:**
- la marca TicketIA;
- la landing pública en `/`;
- el rediseño de las 4 pantallas de la app en `/app/`;
- un sistema de diseño en CSS;
- el campo `pregunta.expira_en` en el backend.

**No incluye:**
- registro de usuarios ni multiusuario (es el plan SaaS);
- un botón para cambiar el tema;
- testimonios;
- logos de los comercios;
- cambios en el bot o en la API, salvo `expira_en`.

## Identidad

- **Nombre:** TicketIA, escrito "Ticket" en tinta e "IA" en el color de acento. Antes de lanzar hay que verificar la marca en el IMPI y el dominio; eso queda fuera de este trabajo.
- **Logo:** un recibo con la esquina doblada en cobalto y tres renglones. Se usa como SVG en línea y como `favicon.svg`.
- **Lema:** "Tu ticket de compra, convertido en factura."
- **Paleta (tokens):**

| Token | Claro | Oscuro |
|---|---|---|
| `--bg` | `#f6f6f3` | `#0d1118` |
| `--surface` | `#ffffff` | `#151a24` |
| `--raised` | `#fbfbfa` | `#19202c` |
| `--ink` | `#141a26` | `#e8ebf2` |
| `--soft` | `#586072` | `#98a1b3` |
| `--faint` | `#6b7280` | `#8a93a5` |
| `--line` | `#e3e5ea` | `#252c3a` |
| `--line-campo` (borde de campos) | `#858c9b` | `#737b8d` |
| `--accent` | `#2b55d6` | `#6f8fff` |
| `--accent-ink` | `#ffffff` | `#0d1118` |
| `--accent-soft` | `#e8eeff` | `#1b2440` |
| `--accent-soft-ink` | `#1d3fa8` | `#b9c8ff` |
| `--mark` (campo leído o por revisar) | `#fff0bf` | `#4a3d10` |
| `--mark-line` | `#e6c75a` | `#8a6f1d` |
| `--ok` / `--ok-soft` | `#177a4a` / `#e3f5ec` | `#4cc28a` / `#12301f` |
| `--danger` / `--danger-soft` | `#b8392a` / `#fdecea` | `#ff7a66` / `#3a1914` |

  Valores ajustados en la revisión de accesibilidad para cumplir WCAG AA (texto 4.5:1, bordes de campos 3:1); `tests/test_contraste.py` lo verifica. La landing usa `--bg: #fafaf8` en modo claro. El tema sigue a `prefers-color-scheme`.
- **Tipografía:**
  - Instrument Sans (400–700) para títulos, textos y formularios;
  - IBM Plex Mono (400–600) para TC, TR, RFC, montos, folios, horas y etiquetas tipo ticket.
  - Las dos van como woff2 en `app/static/fuentes/`, con su licencia OFL.
- **Motivo visual:** el ticket térmico, con borde inferior perforado (una máscara radial), renglones en monoespaciada y líneas de corte punteadas.

## Movimiento

Curvas y duraciones únicas, definidas en `tokens.css` y usadas por `js/movimiento.js`:
- `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)`
- `--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1)`

Reglas:
- solo se animan `transform` y `opacity`;
- las entradas usan `@starting-style` o transiciones, y las secuencias largas usan animaciones CSS;
- `:hover` con movimiento solo bajo `(hover: hover) and (pointer: fine)`;
- con `prefers-reduced-motion: reduce` no hay desplazamientos; quedan solo fundidos cortos.

| Momento | Frecuencia | Animación |
|---|---|---|
| Cambio de pantalla | muchas veces al día | fundido de 150 ms, sin movimiento |
| Presionar un botón | constante | `scale(.97)` en 160 ms |
| Demo de la landing | rara | una sola línea de tiempo CSS: el ticket se imprime (1100 ms), la franja lo escanea (1500 ms), los campos se resaltan con opacidad, entra la tarjeta (320 ms) y aparece la confirmación. Arranca después de `document.fonts.ready`; en reposo se ve el estado final. |
| Login | al abrir | el ticket sale de la ranura (620 ms, ease-in-out); con contraseña incorrecta, un temblor de 260 ms |
| Leyendo con IA | por factura | una franja recorre la foto en bucle (1300 ms, lineal) mientras dura la petición |
| Campos llenados | por factura | cascada de 40 ms entre campos, 260 ms cada uno |
| Pregunta nueva | por pregunta | opacidad y 8 px, 240 ms |
| Diálogo de confirmación | por factura | capa con opacidad y diálogo con escala .96→1, 200 ms, centrado |
| Factura emitida | rara | el comprobante se imprime (900 ms) y la palomita se dibuja |
| Toast | ocasional | entra y sale por abajo, 260 ms de entrada y 180 ms de salida, con transiciones interrumpibles |
| Guardar perfil | ocasional | el texto del botón cambia con un desenfoque de 2 px |

## Rutas

- `/` sirve la landing (`app/static/index.html`).
- `/app/` sirve la app (`app/static/app/index.html`), con las mismas rutas por hash que hoy: `#/login`, `#/nueva`, `#/trabajo/<id>`, `#/perfil`.
- Si la landing se abre con un hash que empieza con `#/`, `landing.js` redirige a `/app/` con el mismo hash. Así siguen funcionando los enlaces viejos.
- Los botones "Entrar" de la landing apuntan a `/app/#/login`. El enlace "Conoce TicketIA" del login apunta a `/`.
- La API, `/docs`, `/redoc` y el webhook del bot no cambian. La CSP tampoco cambia.

## Pantallas

**Landing**
- barra superior fija con fondo sólido (sin `backdrop-filter`);
- bloque principal con el kicker "CFDI 4.0 · Sam's Club · Walmart · OXXO · HEB", el título, el texto, los botones "Entrar" y "Ver cómo funciona", los nombres de los comercios como texto, y la demo animada;
- "Cómo funciona": 3 pasos numerados "1 / 3", "2 / 3", "3 / 3";
- "Seguridad": tres puntos, todos ciertos hoy: "Tú das el último clic", "Portales oficiales", "Sin trucos con captchas";
- llamada final y pie de página con la nota de marcas de terceros.

**Barra de la app**
- logo, pestañas "Nueva factura" y "Datos fiscales", la razón social con un avatar de iniciales, y "Salir";
- la razón social se lee con `GET /perfil` una vez al abrir la app, no en cada cambio de pantalla;
- en el login la barra no aparece.

**Entrar**
- un ticket térmico que sale de una ranura;
- arriba, el logo y "ACCESO · FACTURACIÓN CFDI 4.0";
- el título "Entra a tu cuenta", la contraseña y "Entrar";
- al pie, como renglones, "SESIÓN 12 H" y "CONEXIÓN CIFRADA";
- debajo del ticket, "¿Primera vez aquí? Conoce TicketIA";
- los errores 401 y 429 se muestran bajo el campo con el temblor.

**Nueva factura**
- **Columna izquierda:** zona para arrastrar o elegir la foto ("Arrastra la foto aquí"), que rechaza en el navegador lo que no sea imagen o pese más de 10 MB.
  - Con foto: miniatura, franja "Leyendo con IA…" y barra de estado con "Cambiar foto".
- **Columna derecha:** tarjeta con el comercio como chips (Sam's Club / Walmart, OXXO, HEB), avisos y campos por comercio, igual que hoy.
  - Los campos dudosos se marcan con `--mark`.
  - La forma de pago no leída obliga a elegir.
  - El botón "Facturar" está desactivado hasta tener comercio y foto, o comercio capturado a mano, junto con el texto "Te pediremos confirmación antes del clic final".
- **Abajo:** tarjeta "¿Ya facturaste este ticket? Consulta con el TC o el folio".

**Trabajo**
- Encabezado: "Factura de <comercio>", el id en monoespaciada y un pill de estado.
- Columna principal: línea de pasos calculada en el frontend:
  1. **Llenando el portal:** activo mientras el estado sea `en_cola` o `ejecutando` y aún no haya pregunta.
  2. **Esperando tu respuesta:** activo con `esperando_respuesta`; el texto cambia según `pregunta.tipo`.
  3. **Emitiendo la factura:** activo cuando `facturado` es verdadero y el estado sigue activo.
  4. **Factura emitida:** con `completado`.

  Con `cancelado` o `error`, el paso activo se marca en rojo y se muestra el motivo.
- Tarjeta de pregunta:
  - título según el tipo;
  - un anillo de cuenta regresiva calculado con `expira_en`;
  - el resumen (RFC, razón social, TC o folio, uso CFDI, forma de pago, lo que traiga `datos`);
  - las diferencias de `revisar_direccion`;
  - la cuadrícula de capturas (`t.capturas`, cada una abre la imagen);
  - los botones por opción.
- "Facturar" abre el diálogo de confirmación.
- Al completar, el comprobante se imprime con la palomita, aparecen los archivos o el aviso de correo, y el botón "Facturar otro ticket".
- Columna lateral: "Actividad", con los eventos y su hora en monoespaciada, y una nota: "Si cierras esta pestaña, el trabajo sigue en el servidor. Vuelve con este enlace para ver el resultado."

**Diálogo de confirmación** (sustituye a `window.confirm`)
- título "¿Facturar $<total>?" si hay total; si no, "¿Facturar este ticket?";
- texto con la razón social y el RFC, y "Este paso no se puede deshacer";
- botones "Revisar de nuevo" y "Sí, facturar";
- al abrir, el foco va a "Sí, facturar" y queda atrapado en el diálogo;
- Escape o "Revisar de nuevo" cierran y devuelven el foco al botón que lo abrió.

**Datos fiscales**
- grupos "Contribuyente" (RFC, régimen, razón social, correo) y "Domicilio fiscal" (CP, colonia, calle, número exterior e interior, referencia, municipio, estado);
- texto de ayuda en cada campo y errores 422 junto al campo;
- aviso cuando los datos vienen del entorno;
- "Guardar" cambia a "Guardado ✓" y aparece un toast "Datos guardados. Aplican a las facturas nuevas.";
- un 503 se muestra como toast de error.

## Estructura de archivos (`app/static/`)

```
index.html  landing.css  landing.js
app/index.html
css/tokens.css  css/base.css  css/componentes.css
fuentes/  (InstrumentSans-*.woff2, IBMPlexMono-*.woff2, OFL.txt)
img/logo.svg  img/favicon.svg
js/dom.js  js/api.js  js/movimiento.js  js/dialogo.js  js/toast.js  js/app.js
js/vistas/login.js  js/vistas/nueva.js  js/vistas/trabajo.js  js/vistas/perfil.js
```

Se mantienen las reglas de la spec anterior:
- todo texto del servidor se pinta con `textContent`;
- nada de `<script>` ni `<style>` en línea, ni atributos `style=` u `on*=`;
- `[hidden] { display: none !important; }` en `base.css`.

## Backend

- En `app/trabajos.py`, al crear la pregunta se agrega `"expira_en"`: la hora de expiración en ISO 8601 UTC, igual a ahora más `timeout_respuesta_s`. `expira_en_s` se queda para compatibilidad.
- No hay más cambios.

## Accesibilidad

- contraste WCAG AA en los dos temas;
- `<label>` en cada control;
- al cambiar de pantalla, el foco va al `h1`;
- estados en `aria-live="polite"`;
- foco visible en todo control;
- el diálogo usa `role="dialog"` y `aria-modal`, con el foco atrapado;
- la zona de la foto se activa con teclado (Enter y Espacio).

## Pruebas

- Se adaptan las pruebas de punta a punta existentes: URL `/app/` y confirmación con el botón "Sí, facturar" del diálogo, en lugar del `confirm` del navegador.
- Pruebas nuevas:
  - `/` sirve la landing y "Entrar" lleva a `/app/#/login`;
  - `/#/trabajo/x` redirige a `/app/#/trabajo/x`;
  - Escape en el diálogo no responde la pregunta;
  - la app no hace ninguna petición a otro origen (fuentes e imágenes locales);
  - con `reduced_motion="reduce"`, el flujo completo funciona;
  - la cuenta regresiva usa `expira_en`: si al recargar quedan 5 minutos, muestra menos de 5:01;
  - `expira_en` existe en la pregunta;
  - la línea de pasos marca el paso correcto en cada estado.
- Al terminar: skills `review-animations`, `accessibility-review` y `design-critique`, y un revisor independiente sobre toda la rama.

## Rama

Se trabaja en `claude/youthful-cerf-lf132c`, así que el PR msoteldo/FACTURACION#3 crece con este rediseño.
