import http.server
import os
import shutil
import threading
from pathlib import Path

import pytest

MOCK = Path(__file__).parent / "mock_portal" / "index.html"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # la SPA simulada responde el mismo HTML en cualquier ruta
        cuerpo = MOCK.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *args):
        pass


_servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
threading.Thread(target=_servidor.serve_forever, daemon=True).start()

# Configuración de prueba: debe existir ANTES de importar app.main. Datos ficticios.
os.environ.update({
    "API_KEY": "clave-de-prueba",
    "GEMINI_API_KEY": "gemini-de-prueba",
    "WALMART_URL": f"http://127.0.0.1:{_servidor.server_port}/ticket",
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
    "JOBS_DIR": "/tmp/facturacion_jobs_tests",
})
# En entornos donde el Chromium de Playwright está preinstalado en otra ruta.
if "CHROMIUM_EXECUTABLE" not in os.environ and Path("/opt/pw-browsers/chromium").exists():
    os.environ["CHROMIUM_EXECUTABLE"] = "/opt/pw-browsers/chromium"


@pytest.fixture(scope="session", autouse=True)
def _limpiar_directorio():
    yield
    shutil.rmtree("/tmp/facturacion_jobs_tests", ignore_errors=True)
