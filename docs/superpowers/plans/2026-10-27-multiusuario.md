# Multiusuario (paso 1 hacia SaaS): plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usa superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan casillas (`- [ ]`) para el seguimiento.

**Objetivo:** que un mismo despliegue atienda a varios usuarios, cada uno con sus datos fiscales, sus credenciales de portal y sus trabajos. Ningún usuario debe poder ver nada de otro.

**Arquitectura:** se agrega una base de datos (SQLAlchemy 2 + Alembic; Postgres en producción y SQLite en pruebas y en local) con usuarios, claves de API, perfiles fiscales y credenciales cifradas. Los flujos de portal (`portal_*.py`) **no cambian**: ya reciben un `DatosFiscales` por parámetro. Lo que cambia es de dónde sale ese objeto: antes salía de variables de entorno y ahora sale del perfil del usuario autenticado. Los trabajos siguen en memoria (persistirlos es el paso 2), pero cada uno guarda su `usuario_id` y todo acceso se filtra por él.

**Stack:** FastAPI, SQLAlchemy 2.x, Alembic, psycopg 3 (Postgres), `cryptography` (Fernet), pytest.

**Spec:** no hay documento aparte. Las decisiones están en la sección "Decisiones" de este plan; quien implemente las toma como especificación.

## Decisiones

1. **Autenticación por clave de API por usuario**, con el mismo header `X-API-Key`. Solo se guarda el SHA-256 de la clave; la clave en claro se muestra una sola vez, al crearla. El login web (sesiones, contraseñas, OAuth) llega con el frontend (paso 3).
2. **Los usuarios los crea un admin** (`POST /admin/usuarios`). El registro abierto queda para el paso 3, junto con el cobro.
3. **Un usuario puede tener varios perfiles fiscales** (persona física y empresa, varios clubes). Uno es el predeterminado. Las rutas de factura aceptan un `perfil_id` opcional.
4. **Credenciales de portal** (hoy solo OXXO) cifradas con Fernet usando `ENCRYPTION_KEY`. La API nunca devuelve la contraseña.
5. **La clave de Gemini sigue siendo global**: la plataforma paga la extracción.
6. **Compatibilidad con el despliegue actual:** `python -m app.semilla` crea, si la base está vacía, un usuario admin con el `API_KEY`, los datos fiscales, las credenciales OXXO y los chats de Telegram que hoy están en el entorno. La URL de Render y la clave actual siguen funcionando sin tocar nada.
7. **Acceso cruzado responde 404**, nunca 403. Así no se revela si el id de otro usuario existe.

## Restricciones globales

- Fijar versiones exactas en `requirements.txt` (estilo actual del repo): `sqlalchemy` ≥ 2.0, `alembic` ≥ 1.13, `psycopg[binary]` ≥ 3.2, `cryptography` ≥ 43. Usar la última estable al implementar.
- Nombres de código, mensajes y comentarios en español, como el resto del repo.
- Ningún secreto en el repo. Nuevas variables: `DATABASE_URL`, `ENCRYPTION_KEY`, `MAX_TRABAJOS_POR_USUARIO`.
- `pytest` debe pasar completo al final de cada tarea; las pruebas existentes se adaptan, no se borran.
- Se mantiene `--workers 1`: los trabajos siguen en memoria hasta el paso 2.

## Revisión: casos que más pueden fallar

1. **Usuario B pide `GET /facturas/{id}` (o `/respuesta`, `/cancelar`, `/archivos/...`) de un trabajo de A** → 404 idéntico al de un id inexistente. Prueba en la Tarea 7.
2. **Usuario B manda `perfil_id` de un perfil de A** → 404 "Perfil no encontrado"; el trabajo no se crea. Prueba en la Tarea 7.
3. **Clave revocada o usuario desactivado** → 401, aunque el hash exista. Prueba en la Tarea 4.
4. **Render entrega `DATABASE_URL` como `postgres://...`** → se normaliza a `postgresql+psycopg://...`. Prueba en la Tarea 1.
5. **El candado de "ya hay un trabajo activo para ese TC" es por usuario**: si A tiene uno activo, B puede crear el suyo, y el mensaje de error nunca muestra ids de trabajos de otro usuario. Prueba en la Tarea 6.

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `app/db.py` (nuevo) | motor, sesiones, `Base`, normalización de la URL |
| `app/modelos.py` (nuevo) | tablas ORM |
| `migraciones/` + `alembic.ini` (nuevos) | migraciones de Alembic |
| `app/cifrado.py` (nuevo) | cifrar y descifrar con Fernet |
| `app/cuentas.py` (nuevo) | lógica de usuarios, claves, perfiles y credenciales (sin FastAPI) |
| `app/auth.py` (nuevo) | dependencias de FastAPI `usuario_actual` y `solo_admin` |
| `app/rutas_cuentas.py` (nuevo) | `APIRouter` de `/perfiles`, `/credenciales`, `/admin` |
| `app/semilla.py` (nuevo) | migrar el despliegue de un solo usuario a la base |
| `app/config.py` | nuevos campos en `Ajustes` |
| `app/trabajos.py` | `usuario_id`, `datos` y credenciales por trabajo; filtrado por dueño |
| `app/main.py` | rutas de facturas con usuario y perfil |
| `app/telegram_bot.py` | chat → usuario desde la base |
| `tests/conftest.py` | base SQLite de prueba + semilla |
| `Dockerfile`, `render.yaml`, `.env.example`, `README.md` | despliegue |

---

### Tarea 1: Base de datos

**Archivos:**
- Crear: `app/db.py`
- Modificar: `app/config.py` (campos nuevos en `Ajustes`), `requirements.txt`
- Prueba: `tests/test_db.py`

**Interfaces:**
- Produce:
  - `Ajustes.database_url: str` (env `DATABASE_URL`, por defecto `sqlite:///./facturacion.db`), `Ajustes.clave_cifrado: str` (env `ENCRYPTION_KEY`, sin strip), `Ajustes.max_trabajos_por_usuario: int` (env `MAX_TRABAJOS_POR_USUARIO`, por defecto 3).
  - `normalizar_url(url: str) -> str`
  - `class Base(DeclarativeBase)`
  - `iniciar(url: str) -> None`: crea el motor global y `SesionLocal`. En SQLite usa `connect_args={"check_same_thread": False}` porque los trabajos corren en hilos.
  - `sesion() -> Iterator[Session]`: dependencia de FastAPI; hace commit al salir sin error y rollback si hay excepción.
  - `nueva_sesion() -> Session`: para código fuera de FastAPI (bot, semilla).

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
from app.db import normalizar_url

def test_normaliza_postgres_de_render():
    assert normalizar_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalizar_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"

def test_respeta_sqlite_y_driver_explicito():
    assert normalizar_url("sqlite:///./x.db") == "sqlite:///./x.db"
    assert normalizar_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"

def test_sesion_ejecuta_consulta(tmp_path):
    from app import db
    db.iniciar(f"sqlite:///{tmp_path}/t.db")
    with db.nueva_sesion() as s:
        assert s.execute(sqlalchemy.text("select 1")).scalar() == 1
```

- [ ] **Paso 2:** `pytest tests/test_db.py -v`. Esperado: FAIL (`ModuleNotFoundError: app.db`).
- [ ] **Paso 3:** agregar las dependencias, los campos de `Ajustes` e implementar `app/db.py` con las firmas de arriba.
- [ ] **Paso 4:** `pytest tests/test_db.py -v`. Esperado: PASS. Luego `pytest`: todo en verde.
- [ ] **Paso 5:** commit `feat: base de datos con SQLAlchemy`.

### Tarea 2: Modelos y migración inicial

**Archivos:**
- Crear: `app/modelos.py`, `alembic.ini`, `migraciones/env.py`, `migraciones/script.py.mako`, `migraciones/versions/0001_multiusuario.py`
- Prueba: `tests/test_modelos.py`

**Interfaces:**
- Consume: `Base`, `normalizar_url` (Tarea 1).
- Produce (todas las tablas con `id: int` como PK y `creado: datetime` en UTC):
  - `Usuario`: `nombre: str`, `email: str` (único, en minúsculas), `es_admin: bool = False`, `activo: bool = True`. Relaciones `claves`, `perfiles`, `credenciales`, `chats`.
  - `ClaveApi`: `usuario_id` (FK), `prefijo: str` (los primeros 8 caracteres, para identificarla en listados), `hash_sha256: str` (único, indexado), `revocada: datetime | None`.
  - `PerfilFiscal`: `usuario_id` (FK, indexado), `alias: str`, `predeterminado: bool`, y los 12 campos de `DatosFiscales` con los mismos nombres (`rfc`, `cp`, `razon_social`, `calle`, `num_ext`, `num_int`, `referencia`, `estado`, `municipio`, `colonia`, `email`, `regimen_texto`). Método `a_datos_fiscales() -> DatosFiscales`.
  - `CredencialPortal`: `usuario_id` (FK), `portal: str` (por ahora solo `"oxxo"`), `usuario: str`, `password_cifrado: str`, `actualizado: datetime`. Único (`usuario_id`, `portal`).
  - `ChatTelegram`: `chat_id: BigInteger` (PK, sin `id`), `usuario_id` (FK).
- `migraciones/env.py` toma la URL de `normalizar_url(os.environ["DATABASE_URL"])` y usa `Base.metadata` de `app.modelos`.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_migracion_coincide_con_modelos(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/m.db")
    cfg = alembic.config.Config("alembic.ini")
    alembic.command.upgrade(cfg, "head")
    alembic.command.check(cfg)   # lanza excepción si los modelos y la migración difieren

def test_perfil_a_datos_fiscales():
    p = PerfilFiscal(alias="Club", predeterminado=True, rfc="CPA010101AB1", cp="01000",
                     razon_social="CLUB", calle="C", num_ext="1", num_int="", referencia="",
                     estado="CDMX", municipio="BJ", colonia="Centro", email="a@b.mx",
                     regimen_texto="General de Ley Personas Morales")
    d = p.a_datos_fiscales()
    assert isinstance(d, DatosFiscales) and d.rfc == "CPA010101AB1" and d.faltantes() == []
```

- [ ] **Paso 2:** `pytest tests/test_modelos.py -v`. Esperado: FAIL.
- [ ] **Paso 3:** escribir los modelos y luego generar la migración con `DATABASE_URL=sqlite:///./tmp.db alembic revision --autogenerate -m multiusuario`. Renombrar el archivo a `0001_multiusuario.py` y revisarlo a mano: la FK de `CredencialPortal` y la unicidad deben estar ahí. Borrar `tmp.db`.
- [ ] **Paso 4:** `pytest tests/test_modelos.py -v`. Esperado: PASS.
- [ ] **Paso 5:** commit `feat: modelos multiusuario y migración inicial`.

### Tarea 3: Cifrado de credenciales

**Archivos:**
- Crear: `app/cifrado.py`
- Prueba: `tests/test_cifrado.py`

**Interfaces:**
- Produce: `class CifradoNoConfigurado(Exception)`, `cifrar(texto: str, clave: str) -> str`, `descifrar(token: str, clave: str) -> str`. Si `clave` está vacía o no es una clave Fernet válida → `CifradoNoConfigurado`.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
CLAVE = Fernet.generate_key().decode()

def test_ida_y_vuelta_conserva_espacios():
    token = cifrar(" secreto con espacios ", CLAVE)
    assert "secreto" not in token
    assert descifrar(token, CLAVE) == " secreto con espacios "

@pytest.mark.parametrize("clave", ["", "no-es-fernet"])
def test_clave_invalida(clave):
    with pytest.raises(CifradoNoConfigurado):
        cifrar("x", clave)

def test_otra_clave_no_descifra():
    with pytest.raises(CifradoNoConfigurado):
        descifrar(cifrar("x", CLAVE), Fernet.generate_key().decode())
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar con `cryptography.fernet.Fernet`; convertir `ValueError` e `InvalidToken` en `CifradoNoConfigurado`.
- [ ] **Paso 4:** ejecutar. Esperado: PASS.
- [ ] **Paso 5:** commit `feat: cifrado de credenciales de portal`.

### Tarea 4: Cuentas y autenticación

**Archivos:**
- Crear: `app/cuentas.py`, `app/auth.py`
- Prueba: `tests/test_cuentas.py`

**Interfaces:**
- Consume: modelos (Tarea 2), `cifrar`/`descifrar` (Tarea 3), `db.sesion` (Tarea 1).
- Produce en `app/cuentas.py` (todas reciben una `Session` `s` como primer argumento; ninguna hace commit):
  - `class NoEncontrado(Exception)`, `class SinPerfil(Exception)`
  - `hash_clave(clave: str) -> str`: SHA-256 en hex.
  - `nueva_clave(s, usuario_id: int, clave: str | None = None) -> str`: genera `"fk_" + secrets.token_urlsafe(32)` si `clave` es None, guarda solo el hash y devuelve la clave en claro.
  - `crear_usuario(s, nombre: str, email: str, es_admin: bool = False) -> tuple[Usuario, str]`
  - `usuario_por_clave(s, clave: str) -> Usuario | None`: solo si la clave no está revocada y el usuario está activo.
  - `usuario_por_chat(s, chat_id: int) -> Usuario | None`
  - `guardar_perfil(s, usuario_id: int, campos: dict, perfil_id: int | None = None) -> PerfilFiscal`: crea o actualiza. El primer perfil de un usuario queda como predeterminado. Si `campos["predeterminado"]` es verdadero, los demás perfiles del usuario quedan en falso. Un `perfil_id` de otro usuario lanza `NoEncontrado`.
  - `perfil_para(s, usuario_id: int, perfil_id: int | None) -> PerfilFiscal`: con `None` devuelve el predeterminado y, si no hay ninguno, lanza `SinPerfil`. Un id ajeno o inexistente lanza `NoEncontrado`.
  - `guardar_credencial(s, usuario_id: int, portal: str, usuario: str, password: str, clave_cifrado: str) -> None`
  - `credencial(s, usuario_id: int, portal: str, clave_cifrado: str) -> tuple[str, str]`: devuelve `("", "")` si no hay.
- Produce en `app/auth.py`:
  - `usuario_actual(clave = Security(APIKeyHeader("X-API-Key")), s = Depends(db.sesion)) -> Usuario`: limpia espacios y comillas igual que el `requiere_api_key` actual y responde 401 si la clave no es válida.
  - `solo_admin(u = Depends(usuario_actual)) -> Usuario`: responde 403 si `not u.es_admin`. Aquí 403 sí está bien: el usuario está autenticado y la ruta no esconde recursos ajenos.

- [ ] **Paso 1: escribir las pruebas que fallan** (fixture `s` con SQLite en `tmp_path` y `Base.metadata.create_all`)

```python
def test_clave_solo_se_guarda_hasheada(s):
    u, clave = crear_usuario(s, "Ana", "Ana@Ej.mx")
    assert clave.startswith("fk_") and u.email == "ana@ej.mx"
    assert all(c.hash_sha256 != clave for c in u.claves)
    assert usuario_por_clave(s, clave).id == u.id

def test_clave_revocada_o_usuario_inactivo_no_entra(s):
    u, clave = crear_usuario(s, "Ana", "a@ej.mx")
    u.claves[0].revocada = datetime.now(timezone.utc)
    assert usuario_por_clave(s, clave) is None
    u2, clave2 = crear_usuario(s, "Beto", "b@ej.mx")
    u2.activo = False
    assert usuario_por_clave(s, clave2) is None

def test_un_solo_predeterminado(s):
    u, _ = crear_usuario(s, "Ana", "a@ej.mx")
    p1 = guardar_perfil(s, u.id, CAMPOS)
    p2 = guardar_perfil(s, u.id, {**CAMPOS, "predeterminado": True})
    assert p1.predeterminado is False and p2.predeterminado is True
    assert perfil_para(s, u.id, None).id == p2.id

def test_perfil_ajeno_es_no_encontrado(s):
    a, _ = crear_usuario(s, "Ana", "a@ej.mx")
    b, _ = crear_usuario(s, "Beto", "b@ej.mx")
    p = guardar_perfil(s, a.id, CAMPOS)
    with pytest.raises(NoEncontrado):
        perfil_para(s, b.id, p.id)
    with pytest.raises(SinPerfil):
        perfil_para(s, b.id, None)

def test_credencial_cifrada(s):
    u, _ = crear_usuario(s, "Ana", "a@ej.mx")
    guardar_credencial(s, u.id, "oxxo", "club@ej.mx", "secreto", CLAVE)
    assert "secreto" not in u.credenciales[0].password_cifrado
    assert credencial(s, u.id, "oxxo", CLAVE) == ("club@ej.mx", "secreto")
    assert credencial(s, u.id, "otro", CLAVE) == ("", "")
```

Prueba de `auth`: una app FastAPI mínima con una ruta `GET /yo` protegida con `usuario_actual` devuelve 401 sin clave, 401 con una clave revocada y 200 con `" 'fk_...' "` (con espacios y comillas).

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar `app/cuentas.py` y `app/auth.py` con las firmas de arriba.
- [ ] **Paso 4:** ejecutar. Esperado: PASS.
- [ ] **Paso 5:** commit `feat: usuarios, claves de API y perfiles fiscales`.

### Tarea 5: Semilla desde el entorno y pruebas con base de datos

**Archivos:**
- Crear: `app/semilla.py`
- Modificar: `tests/conftest.py`
- Prueba: `tests/test_semilla.py`

**Interfaces:**
- Consume: `cuentas.*` (Tarea 4), `DatosFiscales.desde_env`, `Ajustes.desde_env`.
- Produce:
  - `sembrar_desde_env(s, ajustes: Ajustes, datos: DatosFiscales) -> Usuario | None`: si ya existe algún usuario, no hace nada y devuelve `None`. Si no, crea un admin (`nombre="Admin"`, email `datos.email` o `"admin@localhost"`) y le registra:
    - una `ClaveApi` con `ajustes.api_key` (si no viene vacía);
    - un perfil predeterminado con `datos` (si `datos.faltantes()` está vacío);
    - la credencial OXXO (si hay usuario y `clave_cifrado`);
    - un `ChatTelegram` por cada chat de `ajustes.telegram_chats`.
  - `main() -> None`: `python -m app.semilla` llama `db.iniciar` y `sembrar_desde_env`, hace commit e imprime qué creó, sin mostrar valores secretos.
- `tests/conftest.py`: agregar `DATABASE_URL=sqlite:////tmp/facturacion_tests.db` y una `ENCRYPTION_KEY` Fernet fija de prueba al `os.environ.update`. Después, borrar el archivo si existe, correr `db.iniciar`, `Base.metadata.create_all` y `sembrar_desde_env`, todo antes de que algún test importe `app.main`. Agregar dos fixtures que devuelven `{"X-API-Key": clave}`: `otro_usuario` (un segundo usuario con perfil completo y un `ChatTelegram` cuyo id expone el fixture `chat_de_otro_usuario`) y `usuario_sin_perfil` (un usuario sin perfiles).

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_semilla_crea_admin_compatible(s, ajustes_env, datos_env):
    u = sembrar_desde_env(s, ajustes_env, datos_env)
    assert u.es_admin
    assert usuario_por_clave(s, ajustes_env.api_key).id == u.id
    assert perfil_para(s, u.id, None).rfc == datos_env.rfc
    assert credencial(s, u.id, "oxxo", ajustes_env.clave_cifrado) == (
        ajustes_env.oxxo_usuario, ajustes_env.oxxo_password)

def test_semilla_es_idempotente(s, ajustes_env, datos_env):
    sembrar_desde_env(s, ajustes_env, datos_env)
    assert sembrar_desde_env(s, ajustes_env, datos_env) is None
    assert s.scalar(select(func.count()).select_from(Usuario)) == 1
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar `app/semilla.py` y ajustar `conftest.py`.
- [ ] **Paso 4:** `pytest`. Esperado: todo PASS. Las pruebas viejas siguen pasando porque `main` todavía no usa la base.
- [ ] **Paso 5:** commit `feat: semilla para migrar el despliegue de un solo usuario`.

### Tarea 6: Trabajos por usuario

**Archivos:**
- Modificar: `app/trabajos.py`
- Prueba: `tests/test_trabajos.py` (nuevo; usa un `GestorTrabajos` con `_correr` sustituido por un doble que deja el trabajo en `esperando_respuesta`, sin Chromium)

**Interfaces:**
- Produce:
  - `Trabajo(tipo, solicitud, directorio, usuario_id: int, datos: DatosFiscales | None, oxxo: tuple[str, str] = ("", ""))`. `a_dict()` **no** incluye `datos` ni `oxxo`, pero sí `usuario_id`.
  - `GestorTrabajos(ajustes)`: deja de recibir `datos_fiscales`.
  - `crear(tipo, solicitud=None, *, usuario_id: int, datos: DatosFiscales | None = None, oxxo=("", ""), origen=None) -> Trabajo`. El candado de TC duplicado solo compara trabajos del mismo `usuario_id`. Si el usuario ya tiene `ajustes.max_trabajos_por_usuario` trabajos activos → `ValueError("Tienes N trabajos activos; espera a que terminen o cancela alguno.")`.
  - `obtener(trabajo_id: str, usuario_id: int) -> Trabajo | None`: devuelve `None` si el trabajo no existe o es de otro usuario.
  - `responder(trabajo_id, usuario_id, pregunta_id, respuesta)` y `cancelar(trabajo_id, usuario_id)`: lanzan `KeyError` si el trabajo es ajeno.
  - `_correr` usa `t.datos` y `t.oxxo` en lugar de `self.datos` y `self.ajustes.oxxo_*`.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_otro_usuario_no_ve_ni_toca(gestor):
    t = gestor.crear("factura", SOL, usuario_id=1, datos=DATOS)
    assert gestor.obtener(t.id, 2) is None
    with pytest.raises(KeyError):
        gestor.cancelar(t.id, 2)
    with pytest.raises(KeyError):
        gestor.responder(t.id, 2, 1, "facturar")

def test_candado_de_tc_es_por_usuario(gestor):
    t = gestor.crear("factura", SOL, usuario_id=1, datos=DATOS)
    otro = gestor.crear("factura", SOL, usuario_id=2, datos=DATOS)  # no lanza
    with pytest.raises(ValueError) as e:
        gestor.crear("factura", SOL, usuario_id=2, datos=DATOS)
    assert t.id not in str(e.value) and otro.id in str(e.value)

def test_limite_por_usuario(gestor):   # max_trabajos_por_usuario=2 en el fixture
    for tc in ("1111111111", "2222222222"):
        gestor.crear("factura", sol(tc), usuario_id=1, datos=DATOS)
    with pytest.raises(ValueError, match="trabajos activos"):
        gestor.crear("factura", sol("3333333333"), usuario_id=1, datos=DATOS)
    gestor.crear("factura", sol("3333333333"), usuario_id=2, datos=DATOS)

def test_a_dict_no_expone_secretos(gestor):
    t = gestor.crear("factura", SOL, usuario_id=1, datos=DATOS, oxxo=("u", "secreto"))
    assert "secreto" not in json.dumps(t.a_dict())
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar los cambios en `app/trabajos.py`.
- [ ] **Paso 4:** `pytest tests/test_trabajos.py -v`. Esperado: PASS. (`test_api` y `test_telegram` fallan hasta las Tareas 7 y 9; es esperado.)
- [ ] **Paso 5:** commit `feat: trabajos aislados por usuario`.

### Tarea 7: Rutas de facturas con usuario y perfil

**Archivos:**
- Modificar: `app/main.py`, `tests/test_api.py`, `tests/test_oxxo.py`, `tests/test_heb.py`

**Interfaces:**
- Consume: `usuario_actual` (Tarea 4), `perfil_para` y `credencial` (Tarea 4), `GestorTrabajos` (Tarea 6).
- Produce:
  - `main.py` llama `db.iniciar(ajustes.database_url)` al importar. Desaparecen `datos_fiscales = DatosFiscales.desde_env()` y `requiere_api_key`.
  - `NuevaFactura`, `NuevaFacturaOxxo` y `NuevaFacturaHeb` ganan `perfil_id: Optional[int] = None`.
  - Helper `_datos_para(s, usuario, perfil_id) -> DatosFiscales`: `NoEncontrado` → 404 "Perfil no encontrado."; `SinPerfil` → 409 "Primero registra un perfil fiscal en POST /perfiles."; perfil incompleto → 409 con la lista de campos faltantes.
  - `/facturas/oxxo` pasa `oxxo=credencial(s, u.id, "oxxo", ajustes.clave_cifrado)`. Si `CifradoNoConfigurado` → 503 "El servidor no tiene ENCRYPTION_KEY válida.".
  - `GET /configuracion` devuelve los datos del usuario: `perfiles: int`, `perfil_predeterminado_faltantes: list[str]`, `oxxo_configurado: bool`, más los campos globales actuales (`gemini_*`, `headless`, `navegador_canal`).
  - `/diagnostico/portal` y `/telegram/configurar` pasan a ser solo para admin.
  - `_trabajo(trabajo_id, usuario)` usa `gestor.obtener(trabajo_id, usuario.id)` y mantiene el mismo 404.

- [ ] **Paso 1: escribir las pruebas que fallan** (en `tests/test_api.py`, con el fixture `otro_usuario` de la Tarea 5)

```python
def test_otro_usuario_no_ve_trabajo_ajeno(otro_usuario):
    t = nueva(tc="9906400000000000000001")
    for metodo, ruta, kw in [
        ("get", f"/facturas/{t['id']}", {}),
        ("post", f"/facturas/{t['id']}/cancelar", {}),
        ("post", f"/facturas/{t['id']}/respuesta", {"json": {"pregunta_id": 1, "respuesta": "facturar"}}),
        ("get", f"/facturas/{t['id']}/archivos/estado.json", {}),
    ]:
        r = getattr(client, metodo)(ruta, headers=otro_usuario, **kw)
        assert r.status_code == 404, ruta
    client.post(f"/facturas/{t['id']}/cancelar", headers=H)

def test_perfil_ajeno_es_404(otro_usuario):
    perfil_admin = client.get("/perfiles", headers=H).json()[0]["id"]
    r = client.post("/facturas", headers=otro_usuario, json={
        "tc": "9906400000000000000002", "tr": "1", "uso_cfdi": "G01",
        "forma_pago": "28", "perfil_id": perfil_admin})
    assert r.status_code == 404

def test_usuario_sin_perfil_recibe_409(usuario_sin_perfil):
    r = client.post("/facturas", headers=usuario_sin_perfil, json={
        "tc": "9906400000000000000003", "tr": "1", "uso_cfdi": "G01", "forma_pago": "28"})
    assert r.status_code == 409 and "POST /perfiles" in r.text

def test_diagnostico_solo_admin(otro_usuario):
    assert client.post("/diagnostico/portal", headers=otro_usuario).status_code == 403
```

(`test_perfil_ajeno_es_404` usa `GET /perfiles`, que llega en la Tarea 8. Mientras tanto, obtener el id con `cuentas.perfil_para` sobre `db.nueva_sesion()`; en la Tarea 8 se cambia por la ruta.) Ajustar `test_requiere_api_key` para la nueva forma de `/configuracion`.

- [ ] **Paso 2:** ejecutar `pytest tests/test_api.py -v`. Esperado: FAIL en las pruebas nuevas.
- [ ] **Paso 3:** implementar los cambios en `app/main.py`.
- [ ] **Paso 4:** `pytest tests/test_api.py tests/test_oxxo.py tests/test_heb.py -v`. Esperado: PASS.
- [ ] **Paso 5:** commit `feat: rutas de facturas por usuario y perfil fiscal`.

### Tarea 8: Rutas de perfiles, credenciales y administración

**Archivos:**
- Crear: `app/rutas_cuentas.py` (un `APIRouter` que se incluye en `main.app`)
- Prueba: `tests/test_rutas_cuentas.py`

**Interfaces:**
- Consume: `cuentas.*` y `auth.*` (Tarea 4).
- Produce:

| Método | Ruta | Quién | Cuerpo → respuesta |
|---|---|---|---|
| GET | `/perfiles` | usuario | → lista de perfiles propios |
| POST | `/perfiles` | usuario | `PerfilEntrada` → 201 perfil |
| PUT | `/perfiles/{id}` | usuario | `PerfilEntrada` → perfil; 404 si es ajeno |
| DELETE | `/perfiles/{id}` | usuario | → 204; 404 si es ajeno; 409 si es el predeterminado y quedan otros |
| PUT | `/credenciales/oxxo` | usuario | `{usuario, password}` → `{"portal": "oxxo", "usuario": ..., "configurada": true}` |
| DELETE | `/credenciales/oxxo` | usuario | → 204 |
| POST | `/admin/usuarios` | admin | `{nombre, email, es_admin?}` → 201 `{id, nombre, email, clave}` |
| PATCH | `/admin/usuarios/{id}` | admin | `{activo?, chats_telegram?: list[int]}` → usuario |
| POST | `/admin/usuarios/{id}/claves` | admin | `{revocar_anteriores: bool = true}` → 201 `{clave}` |

  - `PerfilEntrada`: los campos de `DatosFiscales` más `alias` y `predeterminado: bool = False`. Valida `rfc` (12 o 13 caracteres alfanuméricos, se guarda en mayúsculas), `cp` (5 dígitos) y `email` (`EmailStr`).
  - Ninguna respuesta incluye `password`, `password_cifrado` ni `hash_sha256`. La `clave` en claro solo aparece en las respuestas 201 de creación.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_crud_de_perfil_propio(otro_usuario):
    r = client.post("/perfiles", headers=otro_usuario, json=PERFIL)
    assert r.status_code == 201 and r.json()["rfc"] == PERFIL["rfc"].upper()
    assert [p["id"] for p in client.get("/perfiles", headers=otro_usuario).json()].count(r.json()["id"]) == 1

def test_no_toca_perfil_ajeno(otro_usuario):
    pid = client.get("/perfiles", headers=H).json()[0]["id"]
    assert client.put(f"/perfiles/{pid}", headers=otro_usuario, json=PERFIL).status_code == 404
    assert client.delete(f"/perfiles/{pid}", headers=otro_usuario).status_code == 404

def test_credencial_nunca_regresa_password(otro_usuario):
    r = client.put("/credenciales/oxxo", headers=otro_usuario,
                   json={"usuario": "x@ej.mx", "password": "secreto"})
    assert r.status_code == 200 and "secreto" not in r.text

def test_admin_crea_usuario_y_rota_clave():
    r = client.post("/admin/usuarios", headers=H, json={"nombre": "Caro", "email": "c@ej.mx"})
    uid, clave = r.json()["id"], r.json()["clave"]
    assert client.get("/configuracion", headers={"X-API-Key": clave}).status_code == 200
    nueva = client.post(f"/admin/usuarios/{uid}/claves", headers=H, json={}).json()["clave"]
    assert client.get("/configuracion", headers={"X-API-Key": clave}).status_code == 401
    assert client.get("/configuracion", headers={"X-API-Key": nueva}).status_code == 200

def test_no_admin_no_administra(otro_usuario):
    assert client.post("/admin/usuarios", headers=otro_usuario,
                       json={"nombre": "X", "email": "x@ej.mx"}).status_code == 403
```

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar el router, incluirlo en `main.py` y cambiar en `test_perfil_ajeno_es_404` (Tarea 7) la consulta directa por `GET /perfiles`.
- [ ] **Paso 4:** `pytest`. Esperado: todo PASS salvo `test_telegram`.
- [ ] **Paso 5:** commit `feat: API de perfiles, credenciales y administración de usuarios`.

### Tarea 9: Bot de Telegram multiusuario

**Archivos:**
- Modificar: `app/telegram_bot.py`, `app/main.py` (cómo se construye el bot), `tests/test_telegram.py`

**Interfaces:**
- Consume: `db.nueva_sesion`, `usuario_por_chat`, `perfil_para`, `credencial` (Tareas 1 y 4), y el nuevo `GestorTrabajos` (Tarea 6).
- Produce:
  - `BotFacturacion.autorizado(chat) -> bool`: se resuelve con `usuario_por_chat`. `ajustes.telegram_chats` ya solo lo usa la semilla.
  - `BotFacturacion._contexto(chat) -> tuple[int, DatosFiscales, tuple[str, str]] | str`: devuelve el usuario, los datos del perfil predeterminado y las credenciales OXXO. Si falta algo, devuelve el mensaje para el usuario ("Registra tu perfil fiscal en la web/API antes de facturar.").
  - Los 4 sitios que hoy llaman `self.gestor.datos.faltantes()` y `self.gestor.crear(...)` (líneas ~377–424 y ~509) usan `_contexto` y pasan `usuario_id`, `datos` y `oxxo`.
  - `gestor.obtener`, `responder` y `cancelar` reciben el `usuario_id` del chat. Un botón de un trabajo ajeno responde "Trabajo no encontrado".
  - El respaldo de `/start` para chats no autorizados (mostrar el chat id) se queda igual: el admin lo vincula con `PATCH /admin/usuarios/{id}`.

- [ ] **Paso 1: escribir las pruebas que fallan** (en `tests/test_telegram.py`)

```python
def test_chat_no_vinculado_no_factura(bot_y_api):
    bot, api = bot_y_api
    bot.procesar(update_texto(chat=999, texto="/facturar 9906412345678901234567 03388"))
    assert "privado" in api.ultimo_texto(999)

def test_chat_de_otro_usuario_no_responde_trabajo_ajeno(bot_y_api, chat_de_otro_usuario):
    bot, api = bot_y_api
    t = nueva(tc="9906400000000000000009")            # trabajo del admin vía API
    bot.procesar(update_boton(chat=chat_de_otro_usuario, data=f"r:{t['id']}:1:facturar"))
    assert "no encontrado" in api.ultimo_texto(chat_de_otro_usuario).lower()
```

(Usar el formato de `callback_data` que ya tiene el bot; el de arriba es ilustrativo.)

- [ ] **Paso 2:** ejecutar. Esperado: FAIL.
- [ ] **Paso 3:** implementar los cambios en el bot.
- [ ] **Paso 4:** `pytest`. Esperado: **todo** PASS.
- [ ] **Paso 5:** commit `feat: bot de Telegram por usuario`.

### Tarea 10: Despliegue y documentación

**Archivos:**
- Modificar: `Dockerfile`, `render.yaml`, `.env.example`, `README.md`, `.dockerignore` (copiar `alembic.ini` y `migraciones/`)

**Interfaces:**
- `Dockerfile`: `COPY alembic.ini ./` y `COPY migraciones ./migraciones`. El CMD queda `alembic upgrade head && python -m app.semilla && uvicorn ...` (igual que el actual en lo demás).
- `render.yaml`:
  - bloque `databases:` con `name: facturacion-db`, `plan: free`;
  - `DATABASE_URL` con `fromDatabase: {name: facturacion-db, property: connectionString}`;
  - `ENCRYPTION_KEY` con `sync: false`; el README explica cómo generarla con `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- `.env.example`: `DATABASE_URL=sqlite:///./facturacion.db` y `ENCRYPTION_KEY=`. Explicar que los datos fiscales del entorno ya solo los usa la semilla la primera vez.
- `README.md`:
  - sección "Usuarios y perfiles" con las rutas de la Tarea 8 y un ejemplo con curl;
  - en "Despliegue": la base Postgres gratis de Render **caduca a los 30 días**; para clientes reales, usar un plan pagado o una base externa (Neon, Supabase);
  - **perder `ENCRYPTION_KEY` deja ilegibles las credenciales guardadas** (hay que capturarlas de nuevo);
  - en la tabla de rutas, `/diagnostico/portal` pasa a ser solo para admin.

- [ ] **Paso 1:** `docker build -t facturacion .`. Esperado: termina sin error.
- [ ] **Paso 2:** probar el contenedor con SQLite:

```bash
docker run --rm -p 10000:10000 --env-file .env -e DATABASE_URL=sqlite:////tmp/f.db facturacion
curl -H "X-API-Key: $API_KEY" localhost:10000/configuracion
```

Esperado: la migración corre, la semilla imprime "Admin creado" y `/configuracion` responde `perfiles: 1` con faltantes `[]`.
- [ ] **Paso 3:** reiniciar el contenedor con el mismo volumen. Esperado: la semilla imprime que no hizo nada (idempotente).
- [ ] **Paso 4:** `pytest`. Esperado: todo PASS.
- [ ] **Paso 5:** commit `docs: despliegue multiusuario con Postgres y semilla`.

---

## Fuera de alcance (pasos siguientes)

- Persistir los trabajos y moverlos a una cola con workers separados (paso 2).
- Registro abierto, login web y frontend (paso 3).
- Archivos en S3/R2, cobro y notificaciones por usuario (paso 4).
- Auditoría por usuario (quién facturó qué y cuándo): conviene agregarla junto con la persistencia de trabajos del paso 2.
