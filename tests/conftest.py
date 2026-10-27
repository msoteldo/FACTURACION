import http.server
import os
import shutil
import threading
from pathlib import Path

import pytest

MOCK = Path(__file__).parent / "mock_portal" / "index.html"
MOCK_OXXO = Path(__file__).parent / "mock_portal" / "oxxo.html"
MOCK_HEB = Path(__file__).parent / "mock_portal" / "heb.html"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # la SPA simulada responde el mismo HTML en cualquier ruta
        mock = MOCK_OXXO if self.path.startswith("/oxxo") else MOCK_HEB if self.path.startswith("/heb") else MOCK
        cuerpo = mock.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *args):
        pass


_servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
threading.Thread(target=_servidor.serve_forever, daemon=True).start()

# Con pytest-xdist cada proceso usa su propia carpeta de trabajos: si la compartieran,
# el primero en terminar borraría los archivos de los demás.
JOBS_DIR = f"/tmp/facturacion_jobs_tests_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"

# Configuración de prueba: debe existir ANTES de importar app.main. Datos ficticios.
os.environ.update({
    "API_KEY": "clave-de-prueba",
    "GEMINI_API_KEY": "gemini-de-prueba",
    "WALMART_URL": f"http://127.0.0.1:{_servidor.server_port}/ticket",
    "OXXO_URL": f"http://127.0.0.1:{_servidor.server_port}/oxxo/inicio.do",
    "HEB_URL": f"http://127.0.0.1:{_servidor.server_port}/heb/cli/invoice-create/",
    "OXXO_USUARIO": "club@example.com",
    "OXXO_PASSWORD": "secreto",
    "RFC": "CPA010101AB1",
    "CP": "01000",
    "RAZON_SOCIAL": "CLUB DE PADEL DE PRUEBA",
    "CALLE": "Calle Falsa",
    "NUM_EXT": "123",
    "ESTADO": "Ciudad de México",
    "MUNICIPIO": "Benito Juárez",
    "COLONIA": "Centro",
    "EMAIL": "facturas@example.com",
    "ANSWER_TIMEOUT_S": "30",
    "JOBS_DIR": JOBS_DIR,
})
# En entornos donde el Chromium de Playwright está preinstalado en otra ruta.
if "CHROMIUM_EXECUTABLE" not in os.environ and Path("/opt/pw-browsers/chromium").exists():
    os.environ["CHROMIUM_EXECUTABLE"] = "/opt/pw-browsers/chromium"


@pytest.fixture(scope="session", autouse=True)
def _limpiar_directorio():
    yield
    shutil.rmtree(JOBS_DIR, ignore_errors=True)


@pytest.fixture
def base_sqlite(tmp_path):
    """Base SQLite temporal con las tablas creadas; al terminar se vuelve a 'sin base'."""
    from app import db
    from app.perfil import Base
    db.iniciar(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(db.motor())
    yield
    db.cerrar()
