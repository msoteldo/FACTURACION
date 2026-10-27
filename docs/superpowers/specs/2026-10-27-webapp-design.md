# Webapp de escritorio (un usuario): diseño

**Fecha:** 2026-10-27
**Estado:** aprobado en conversación; pendiente de revisión escrita.

## Propósito

El dueño de la empresa quiere facturar tickets desde la computadora sin usar Swagger ni curl. En el celular sigue usando el bot de Telegram. Esta webapp es la primera pieza visible del futuro SaaS, pero en esta versión **la usa una sola persona**.

**Criterio de éxito:** facturar un ticket de Sam's/Walmart, OXXO o HEB de punta a punta desde el navegador (foto, revisión, aprobación, resultado) y poder editar los datos fiscales de la empresa desde la misma web.

## Alcance

**Incluye:**
- login de un solo usuario;
- subir la foto del ticket, revisar y corregir lo que leyó Gemini, facturar y aprobar las preguntas del trabajo;
- consultar una factura ya emitida (Walmart);
- ver y editar los datos fiscales, guardados en Postgres (Neon) para que sobrevivan los reinicios de Render.

**No incluye:**
- historial de facturas;
- varios usuarios;
- varios perfiles fiscales;
- uso desde el celular (la página funciona en móvil, pero no se optimiza para él);
- notificaciones push.

## Arquitectura

Todo corre en el mismo servicio de Render y en el mismo proceso de FastAPI. No hay un segundo despliegue ni CORS.

### Componentes nuevos

| Archivo | Responsabilidad |
|---|---|
| `app/static/index.html`, `app/static/app.js`, `app/static/estilos.css` | SPA en JS puro, sin paso de build. Se sirve en `/`. |
| `app/sesion.py` | login, cookie de sesión firmada, límite de intentos y dependencia de autenticación que acepta header o cookie |
| `app/db.py` | motor de SQLAlchemy, sesiones y normalización de `DATABASE_URL` |
| `app/perfil.py` | modelo `PerfilFiscal` (una sola fila), función `obtener_datos()` y rutas `GET`/`PUT /perfil` |
| `alembic.ini`, `migraciones/` | migración `0001_perfil_fiscal` |

### Cambios a componentes existentes

- **`app/trabajos.py`:** `GestorTrabajos` recibe una función `obtener_datos: Callable[[], DatosFiscales]` en lugar de un `DatosFiscales` fijo. La llama al crear cada trabajo y guarda el resultado en el trabajo. Un trabajo iniciado conserva sus datos aunque luego se edite el perfil.
- **Error de trabajo duplicado:** pasa a ser `TrabajoDuplicado(ValueError)` con el atributo `trabajo_id`. El texto del mensaje no cambia.
- **`app/main.py`:**
  - monta los estáticos y agrega los headers de seguridad;
  - las rutas protegidas usan la nueva dependencia de autenticación;
  - el 409 por duplicado responde `{"error": <texto>, "trabajo_id": <id>}`;
  - crear una factura con la base caída responde 503.
- **`app/telegram_bot.py`:** usa `obtener_datos()` en lugar de `gestor.datos`. Sus mensajes no cambian.

### Autenticación

- **Login:** `POST /sesion` con `{"password": ...}`. La contraseña es la `API_KEY` y se compara con `secrets.compare_digest`. Si es correcta, el servidor pone la cookie `sesion`.
- **Cookie:** `HttpOnly`, `SameSite=Strict`, con `Max-Age` de 12 horas. `Secure` se pone cuando el esquema de la petición es `https`.
- **Token:** `"<expiración_unix>.<hex de HMAC-SHA256(clave, expiración)>"`, donde `clave = HMAC-SHA256(API_KEY, "sesion-web")`. Al cambiar la `API_KEY`, todas las sesiones dejan de servir. No se agregan dependencias.
- **Logout:** `DELETE /sesion` borra la cookie. No hay revocación del lado del servidor; para un solo usuario es aceptable.
- **Límite de intentos:** 5 fallos por IP en 60 s hacen que `POST /sesion` responda 429. El contador vive en memoria.
- **Rutas protegidas:** aceptan el header `X-API-Key` (curl, pruebas actuales) **o** la cookie. Con cookie, los métodos que cambian algo (`POST`, `PUT`, `DELETE`) exigen el header `X-Requested-With: fetch`; si falta, responden 403.
- **Sin base de datos:** verificar la cookie no consulta la base.

### Datos fiscales

- **Tabla `perfil_fiscal`:**
  - `id` (entero, PK, con `CHECK (id = 1)`);
  - las 12 columnas de `DatosFiscales` con los mismos nombres;
  - `actualizado` (timestamp con zona horaria).
- **`obtener_datos() -> DatosFiscales`:**
  - sin `DATABASE_URL`: `DatosFiscales.desde_env()`;
  - con `DATABASE_URL` y la tabla vacía: `DatosFiscales.desde_env()`;
  - con la fila 1: los datos de esa fila;
  - si la base falla: lanza `PerfilNoDisponible`. **Nunca usa el entorno como respaldo**, porque podría facturarse con un RFC viejo.
- **`GET /perfil`:** devuelve `{"origen": "base" | "entorno", "datos": {...}, "faltantes": [...]}`.
- **`PUT /perfil`:** hace upsert de la fila 1.
  - Validación: `rfc` de 12 o 13 caracteres alfanuméricos (se guarda en mayúsculas); `cp` de 5 dígitos; `email` válido; obligatorios los mismos de `DatosFiscales.OBLIGATORIOS`.
  - Sin `DATABASE_URL`: 503 "Configura DATABASE_URL para editar los datos desde la web."
  - No escribe los valores en el log.
- **Motor:** `pool_pre_ping=True` y `pool_recycle=300`. `postgres://` y `postgresql://` se normalizan a `postgresql+psycopg://`. Se usa la cadena *pooled* de Neon.
- **Dependencias nuevas:** `sqlalchemy`, `alembic` y `psycopg[binary]`, con versión fija.

### Interfaz (una página, 4 vistas, rutas por hash)

1. **`#/login`:** campo de contraseña.
2. **`#/nueva`:**
   - Arrastrar o elegir la foto. El navegador rechaza archivos de más de 10 MB o que no sean imagen.
   - La app llama `POST /tickets/extraer` y llena el formulario según `comercio_clave`:
     - `walmart`: TC, TR y forma de pago (de `forma_pago_sat_sugerida`);
     - `oxxo`: fecha, folio, ID de venta y total;
     - `heb`: sucursal, ticket, fecha y total;
     - en todos: uso CFDI (G01/G03) y método de entrega (Walmart; por defecto `email`).
   - Los campos con confianza `media` o `baja` se marcan; las `advertencias` aparecen arriba.
   - Si la extracción falla o el comercio es `otro`, el formulario queda vacío y se elige el comercio a mano.
   - También tiene el campo "Consultar factura ya emitida".
3. **`#/trabajo/<id>`:**
   - Consulta `GET /facturas/{id}` cada 1.5 s solo mientras el estado sea activo.
   - Muestra eventos y capturas.
   - Si hay pregunta: muestra la captura, el texto, un botón por opción y una cuenta regresiva con `expira_en_s`. El título de la pestaña cambia a "⚠ Pregunta pendiente" y suena un aviso.
   - "Facturar" pide confirmación con el resumen de `datos` y el RFC.
   - Los botones se desactivan al enviar.
   - "Cancelar" se ve solo mientras `facturado` sea falso.
   - Al terminar: con `completado`, los enlaces a los archivos o "llegará a tu correo"; con `cancelado` o `error`, el motivo y la última captura.
4. **`#/perfil`:**
   - Formulario de datos fiscales con errores por campo (422).
   - Si los datos vienen del entorno: "Guarda para poder editarlos aquí".
   - Al guardar: "Aplica a las facturas nuevas".

**Regla del JS:** todo texto que viene del servidor se pinta con `textContent`. No se usa `innerHTML` con datos.

### Manejo de errores en la web

| Caso | Comportamiento |
|---|---|
| Petición tarda más de 3 s | "Despertando el servidor (~1 min)…" |
| 401 | Va a `#/login` y después regresa a la ruta anterior. |
| 409 por duplicado | Botón "Ver trabajo existente" hacia `#/trabajo/<trabajo_id>`. |
| 409 al responder | Vuelve a consultar el estado. |
| 404 de un trabajo | "El servicio se reinició y el trabajo se perdió. Si ya habías confirmado Facturar, revisa tu correo o usa Consultar." |
| 503 de perfil al crear una factura | Muestra el mensaje; no se crea ningún trabajo. |
| 429 en el login | "Demasiados intentos; espera un minuto." |

### Headers de seguridad (en todas las respuestas)

- `Content-Security-Policy: default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`

## Despliegue

- **Neon:** crear un proyecto gratis y copiar la cadena de conexión *pooled* a `DATABASE_URL` en Render (`sync: false` en `render.yaml`).
- **Dockerfile:**
  - copia `alembic.ini` y `migraciones/`;
  - el CMD corre `alembic upgrade head` solo si `DATABASE_URL` no está vacía, y luego `uvicorn` como hoy.
  - Si la migración falla, el deploy falla y Render mantiene la versión anterior.
- **Sin `DATABASE_URL`:** todo funciona como antes y solo la edición de datos fiscales queda desactivada.
- **Prueba previa:** antes del primer deploy, correr la prueba de migración contra una *branch* de Neon.
- **README:** sección "Webapp" y pasos de Neon.

## Pruebas

**Servidor (pytest, SQLite):**
- **Sesión:** contraseña incorrecta da 401; contraseña correcta da cookie con `HttpOnly` y `SameSite=Strict`; la cookie abre las rutas; una cookie alterada o vencida da 401; el header `X-API-Key` sigue funcionando; con otra `API_KEY`, la cookie vieja da 401; el sexto intento fallido da 429; un POST con cookie sin `X-Requested-With` da 403.
- **Perfil:** `origen` entorno y luego base; validación 422; sin `DATABASE_URL`, `PUT` da 503; con la base caída (URL inalcanzable), crear factura da 503 y no hay ningún trabajo nuevo; un trabajo iniciado conserva sus datos; el bot usa los datos editados.
- **Duplicado:** el 409 trae `trabajo_id`; el texto del bot no cambia.
- **Estáticos y headers:** `/` responde 200 sin autenticación, con CSP, `X-Frame-Options` y `nosniff`.
- **Migración:** `alembic upgrade head` + `alembic check` sobre SQLite. Contra `TEST_DATABASE_URL` si existe; si no, se omite.

**E2E (Playwright, uvicorn en un hilo, portal simulado, `extraccion.leer_ticket` sustituido por un doble):**
1. Login, foto, formulario prellenado, Facturar, confirmar y "completado".
2. Editar el perfil y que la siguiente factura llene el portal simulado con el RFC nuevo.
3. Extracción fallida y captura manual.
4. Sesión vencida a mitad de un trabajo: login y regreso al mismo trabajo.
5. `advertencias` con `<img src=x onerror=alert(1)>` aparece como texto y no se dispara ningún diálogo.

## Relación con el plan multiusuario

`app/db.py`, Alembic y la tabla `perfil_fiscal` adelantan parte de las Tareas 1–2 de `docs/superpowers/plans/2026-10-27-multiusuario.md`. Ese plan se ajusta al terminar la webapp: una migración agrega `usuario_id`, `alias` y `predeterminado`, quita el `CHECK (id = 1)` y asigna la fila existente al admin. El login por cookie se reemplaza por cuentas reales en el paso 3.
