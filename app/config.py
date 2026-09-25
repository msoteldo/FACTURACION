"""Configuración del servicio. Todo sale de variables de entorno (secretos en Render);
nada de datos fiscales ni claves va hardcodeado en el código."""
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(nombre, default=""):
    return os.environ.get(nombre, default).strip()


def _env_int(nombre, default):
    try:
        return int(_env(nombre, str(default)))
    except ValueError:
        return default


def _env_bool(nombre, default):
    valor = _env(nombre, "")
    if not valor:
        return default
    return valor.lower() in ("1", "true", "si", "sí", "yes")


@dataclass(frozen=True)
class DatosFiscales:
    """Datos del receptor (el club) que se capturan en el portal del comercio."""
    rfc: str
    cp: str
    razon_social: str
    calle: str
    num_ext: str
    num_int: str
    referencia: str
    estado: str
    municipio: str
    colonia: str
    email: str
    regimen_texto: str

    OBLIGATORIOS = ("rfc", "cp", "razon_social", "calle", "num_ext",
                    "estado", "municipio", "colonia", "email")

    @classmethod
    def desde_env(cls):
        return cls(
            rfc=_env("RFC").upper(),
            cp=_env("CP"),
            razon_social=_env("RAZON_SOCIAL"),
            calle=_env("CALLE"),
            num_ext=_env("NUM_EXT"),
            num_int=_env("NUM_INT"),
            referencia=_env("REFERENCIA"),
            estado=_env("ESTADO"),
            municipio=_env("MUNICIPIO"),
            colonia=_env("COLONIA"),
            email=_env("EMAIL"),
            # Texto (o fragmento) de la opción de Régimen Fiscal en el portal.
            regimen_texto=_env("REGIMEN_FISCAL_TEXTO", "General de Ley Personas Morales"),
        )

    def faltantes(self):
        return [c.upper() for c in self.OBLIGATORIOS if not getattr(self, c)]


@dataclass(frozen=True)
class Ajustes:
    api_key: str
    gemini_api_key: str
    gemini_modelo: str
    walmart_url: str
    headless: bool
    # "chromium" = headless "nuevo" (el Chrome real sin ventana, más parecido al local).
    # "" = chrome-headless-shell (usa menos RAM, pero es más fácil de distinguir).
    navegador_canal: str
    navegador_ejecutable: str
    dir_trabajos: Path
    max_navegadores: int
    timeout_respuesta_s: int
    ttl_trabajos_s: int
    max_bytes_imagen: int
    webhook_url: str
    telegram_token: str
    telegram_secreto: str
    telegram_chats: frozenset
    url_publica: str

    @classmethod
    def desde_env(cls):
        return cls(
            api_key=_env("API_KEY"),
            gemini_api_key=_env("GEMINI_API_KEY"),
            gemini_modelo=_env("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            walmart_url=_env("WALMART_URL", "https://facturacion-clientes.walmart.com/ticket"),
            headless=_env_bool("HEADLESS", True),
            navegador_canal=_env("BROWSER_CHANNEL", "chromium"),
            navegador_ejecutable=_env("CHROMIUM_EXECUTABLE"),
            dir_trabajos=Path(_env("JOBS_DIR", "/tmp/facturacion_jobs")),
            # En el plan gratis de Render (512 MB) solo cabe un Chromium a la vez.
            max_navegadores=_env_int("MAX_BROWSERS", 1),
            # Cuánto espera un trabajo pausado a que un humano responda antes de cancelarse.
            # Mantenerlo < 15 min: Render duerme el servicio tras 15 min sin tráfico.
            timeout_respuesta_s=_env_int("ANSWER_TIMEOUT_S", 600),
            ttl_trabajos_s=_env_int("JOBS_TTL_S", 3600),
            max_bytes_imagen=_env_int("MAX_IMAGE_BYTES", 10 * 1024 * 1024),
            webhook_url=_env("NOTIFY_WEBHOOK_URL"),
            telegram_token=_env("TELEGRAM_BOT_TOKEN"),
            # Telegram lo manda en cada webhook; así nadie más puede inyectar mensajes.
            telegram_secreto=_env("TELEGRAM_WEBHOOK_SECRET"),
            # Chats autorizados (ids separados por coma). Vacío = nadie (salvo /start).
            telegram_chats=frozenset(
                int(x) for x in _env("TELEGRAM_ALLOWED_CHAT_IDS").replace(" ", "").split(",")
                if x.lstrip("-").isdigit()),
            # Render define RENDER_EXTERNAL_URL solo; PUBLIC_URL permite sobrescribirla.
            url_publica=(_env("PUBLIC_URL") or _env("RENDER_EXTERNAL_URL")).rstrip("/"),
        )
