# Extensión de navegador de TicketIA (Petro Seven): diseño

**Fecha:** 2026-10-30
**Estado:** aprobado en conversación; pendiente de revisión escrita.

## Propósito

El portal de Petro Seven (`tarjetapetro-7.com.mx/KPortalExterno/`) usa el detector de bots de Radware y bloquea las IPs de datacenter. Lo comprobamos desde el entorno de desarrollo y desde Render (`GET /diagnostico/petro7` → "Radware Captcha Page"). Por eso no se puede automatizar desde el servidor, y un SaaS no puede depender de la computadora del dueño.

La solución es una **extensión de navegador**: el portal se llena **en el navegador del propio cliente**, con su IP, su sesión y sus clics. Para Radware es una persona; no hay nada que burlar. La extensión se detiene antes de "Facturar" y el clic lo da la persona.

**Criterio de éxito:** desde la web de TicketIA, con la extensión instalada, se sube la foto de un ticket de Petro Seven, se da "Llenar en Petro Seven", y el portal queda llenado y validado ("Agregar Ticket"), con la forma de pago y el uso CFDI, listo para que la persona dé "Facturar". La extensión nunca da ese clic.

## Alcance

**Incluye:**
- la extensión de Chrome (Manifest V3; también funciona en Edge y Brave) con el llenador de Petro Seven;
- el comercio Petro Seven en la web (Nueva factura), en la lectura con IA y en el bot de Telegram;
- quitar el flujo de servidor de Petro Seven y los diagnósticos temporales.

**No incluye:**
- publicar en Chrome Web Store (la primera versión se instala en modo desarrollador);
- otros portales en la extensión (la estructura los permite, pero solo se hace Petro Seven);
- varios tickets en una factura;
- que la extensión avise a TicketIA del resultado (no hay historial);
- celular (la extensión es de escritorio).

## Reglas que no cambian

- **El clic en "Facturar" siempre lo da una persona.** La extensión nunca busca ni pulsa ese botón.
- **Nunca se resuelve un captcha.** Si aparece la verificación de Radware, la resuelve la persona en su navegador.
- La extensión **no guarda contraseñas ni claves** y no hace peticiones a TicketIA.

## Arquitectura

### Comunicación web ↔ extensión (enfoque A)

1. La extensión inyecta `puente.js` solo en las páginas de TicketIA: `https://facturacion-tickets.onrender.com/*`, y `http://localhost/*` y `http://127.0.0.1/*` para desarrollo y pruebas.
2. `puente.js` marca la página con `document.documentElement.dataset.ticketiaExtension = "<versión>"`. Así la web sabe que está instalada.
3. La web manda los datos con `window.postMessage({ tipo: "ticketia:llenar", comercio: "petro7", datos }, location.origin)`.
4. `puente.js` solo acepta mensajes con `event.source === window`, `event.origin === location.origin` y `tipo === "ticketia:llenar"`. Valida `datos` y los reenvía a `fondo.js` con `chrome.runtime.sendMessage`.
5. `puente.js` contesta a la página con `{ tipo: "ticketia:estado", estado: "abierto" | "error", mensaje }`.

### Archivos (`extension/`)

| Archivo | Responsabilidad |
|---|---|
| `manifest.json` | MV3. `host_permissions`: TicketIA (dominios de arriba) y `https://tarjetapetro-7.com.mx/*`. Permisos: `scripting`, `storage`, `tabs`. `content_scripts`: `puente.js` en TicketIA. `background.service_worker`: `fondo.js`. Sin código remoto. |
| `puente.js` | Puente de la página de TicketIA: marca la instalación, valida y reenvía. |
| `fondo.js` | Abre una pestaña nueva con el portal, guarda los datos en `chrome.storage.session` por `tabId` e inyecta el llenador cuando la pestaña termina de cargar. |
| `comun/validar.js` | `validarPetro7(datos) -> {ok, errores}`. Función pura, compartida y probada. |
| `comun/aviso.js` + `comun/aviso.css` | La barra de TicketIA sobre el portal: estado, mensajes y botón **Reintentar**. |
| `portales/petro7.js` | El llenador (pasos de abajo). Interfaz común para futuros portales: `async llenar(datos, aviso)`. |
| `portales/petro7_pagina.js` | Ayudante que corre en el mundo de la página (`world: "MAIN"`): fija la fecha del `md-datepicker` vía `angular.element(el).scope().$apply()` y captura los `window.alert` del portal mientras la extensión trabaja. |

### Datos que pasa la web (`datos`)

| Campo | Regla |
|---|---|
| `estacion` | dígitos, 1–8 |
| `folio` | dígitos o letras, 1–20 |
| `web_id` | dígitos o letras, 1–20 |
| `fecha` | `AAAA-MM-DD` válida |
| `forma_pago` | `"04"` crédito, `"28"` débito o `"01"` efectivo |
| `uso_cfdi` | `"G01"` o `"G03"` |
| `rfc_esperado` | RFC de los datos fiscales de TicketIA (opcional) |

## Llenado del portal (`portales/petro7.js`)

Los selectores salen del código público del portal: `kportalexterno.js` y `body.html`.

- Login: `[ng-model='email']`, `[ng-model='password']`, `[ng-click='bannerCtrl.loginUser()']`
- Ticket: `[ng-model='selectedEmployee.noEstacion']`, `[ng-model='selectedEmployee.noTicket']`, `[ng-model='selectedEmployee.wid']`, `[ng-model='selectedEmployee.fechaTicket']`
- `[ng-click='addRow()']` (Agregar Ticket)
- `#formaPago` (select), `#formaPagoAux` (texto), `#usoCfdi`, `#rfcCliente`
- Facturar: `[ng-click^='facturacionCtrl.facturarTicket']`. **Solo se resalta, nunca se pulsa.**

Pasos:

1. **Bloqueo de Radware** (título "Radware Captcha Page" o el texto "you are a bot"): la barra dice "Petro Seven pidió una verificación: resuélvela tú y da Reintentar".
2. **Sesión:** si el formulario de login es visible, la barra dice "Inicia sesión en Petro Seven; en cuanto entres, sigo yo". La extensión espera hasta 5 min a que desaparezca el login. Nunca escribe la contraseña.
3. **Abrir "Facturar":** se pulsa la opción del menú cuyo texto es "Facturar" y que no es el botón `facturarTicket`.
4. **Llenar** estación, folio y Web ID con eventos `input` y `change`, para que AngularJS los tome. La fecha se fija con el ayudante del mundo de la página.
5. **Agregar Ticket.** Si el portal responde con un `alert` (ya facturado, no encontrado, otro mes), su texto sale en la barra y se detiene. Éxito: aparece la fila del ticket.
6. **Forma de pago:** si `#formaPago` es visible, se elige la opción que corresponde a `forma_pago` (crédito o débito). Si se muestra `#formaPagoAux`, se deja como la pone el portal.
7. **Uso CFDI:** se elige `#usoCfdi` por la clave (`G01`, `G03`).
8. **RFC:** si `rfc_esperado` viene y es distinto de `#rfcCliente`, la barra muestra los dos y pide revisar.
9. **Alto:** la barra dice "Listo: revisa los datos y da Facturar", resalta el botón y deja de trabajar.
10. Si después aparece la pantalla de resultado, la barra dice "Factura emitida; llega a tu correo".

**Esperas:** cada control se espera hasta 15 s. Si no aparece: "No encontré <campo>; el portal pudo cambiar. Factúralo a mano esta vez."

## TicketIA

- **Web (`nueva.js`):**
  - chip **Petro Seven** con los campos Estación, Folio, Web ID, Fecha, Forma de pago y Uso CFDI;
  - con la extensión, el botón **"Llenar en Petro Seven"** manda los datos (con el RFC de `GET /perfil`) y muestra "Abrimos Petro Seven en otra pestaña; revisa y da Facturar ahí";
  - sin la extensión, una tarjeta "Instala la extensión de TicketIA" con los pasos;
  - aviso de plazo: válido en el mes de la fecha y los primeros 5 días del siguiente; "por vencer" si faltan 2 días o menos, y "fuera de plazo" si ya pasó.
- **IA (`extraccion.py`):**
  - `comercio_clave: "petro7"`, con los campos `petro7_estacion`, `petro7_folio`, `petro7_webid`, `fecha` (desde "Fecha del recibo", DD/MM/AAAA), `total` y `forma_de_pago` ("Tarjetas Credito" = crédito, "Tarjetas Debito" = débito, efectivo);
  - se le aclara que el RFC impreso es de la gasolinera;
  - `validar()` agrega avisos si falta algún campo.
- **Bot de Telegram:** con una foto de Petro Seven, muestra los datos leídos y "Petro Seven se factura desde la web en tu computadora, con la extensión de TicketIA". No crea trabajos.

## Lo que se quita

- `app/portal_petro7.py`, `POST /facturas/petro7`, `tests/test_petro7.py`.
- `PETRO7_URL`, `PETRO7_USUARIO` y `PETRO7_PASSWORD` en `config.py`, `render.yaml`, `.env.example` y README.
- Diagnósticos temporales: `GET /diagnostico/petro7` y el log de la huella de `API_KEY`.
- `tests/mock_portal/petro7.html` **se conserva**, porque es el portal simulado de las pruebas de la extensión. Se le agregan la página de bloqueo y el `alert` de ticket rechazado si faltan.

## Instalación (privada)

En `chrome://extensions` → activar **Modo desarrollador** → **Cargar descomprimida** → elegir la carpeta `extension/` del repo. El README y la tarjeta de la web traen los pasos.

## Pruebas

- **`comun/validar.js`:** casos válidos e inválidos (fecha, dígitos, claves), con Node.
- **Extensión de punta a punta:** Playwright con `launch_persistent_context` y `--load-extension`, contra un TicketIA de prueba (página local que manda el mensaje) y el portal simulado `petro7.html` servido localmente. Cubren:
  - llena los cuatro campos, da "Agregar" y **el contador de clics de "Facturar" queda en 0**;
  - elige crédito o débito solo si el portal lo pide;
  - elige el uso CFDI;
  - muestra en la barra el `alert` de ticket rechazado;
  - espera mientras no hay sesión y sigue al entrar;
  - avisa ante la página de bloqueo;
  - avisa si el RFC es distinto;
  - ignora mensajes de otro origen.

  Para las pruebas, el portal se toma de una variable de la extensión que apunta a `http://127.0.0.1:<puerto>/petro7/` en lugar del real.
- **Web:** el chip, el botón solo con `data-ticketia-extension`, la tarjeta sin ella, el aviso de plazo, y el mensaje que se manda (forma y origen).
- **IA y bot:** lectura y validación de un ticket de Petro Seven, y la respuesta del bot.
- **Riesgo:** si el Chromium de este entorno no carga extensiones en modo headless, esas pruebas corren con `headless=False` bajo `xvfb-run`, o se prueban por separado `validar.js` y el llenador inyectado en la página simulada.

## Riesgos

- Radware podría marcar como sospechoso el llenado automático aunque sea el navegador del cliente. Es poco probable (mira red y huella, no el formulario) y se sabrá en el primer intento real.
- Los selectores salen del código público del portal, no de una sesión real; el primer intento real puede requerir ajustes.
