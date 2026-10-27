"""Sesión de la webapp: la contraseña es la API_KEY y se responde con una cookie firmada.

El token es "<expiración>.<HMAC-SHA256>" con una clave derivada de la API_KEY: no se
guarda nada en el servidor y cambiar la API_KEY invalida todas las sesiones.
"""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

COOKIE = "sesion"
DURACION_S = 12 * 3600


def clave_firma(api_key):
    return hmac.new(api_key.encode(), b"sesion-web", hashlib.sha256).digest()


def _firma(api_key, exp):
    return hmac.new(clave_firma(api_key), str(exp).encode(), hashlib.sha256).hexdigest()


def crear_token(api_key, ahora):
    exp = int(ahora) + DURACION_S
    return f"{exp}.{_firma(api_key, exp)}"


def token_valido(token, api_key, ahora):
    exp, _, firma = (token or "").partition(".")
    if not exp.isdigit() or not firma or not api_key:
        return False
    if not secrets.compare_digest(firma.encode(), _firma(api_key, int(exp)).encode()):
        return False
    return int(exp) >= ahora


def limpiar_clave(valor):
    """Tolera espacios o comillas que se cuelan al copiar y pegar la clave."""
    return (valor or "").strip().strip("\"'")


class LimiteIntentos:
    """Cuenta logins fallidos por IP en una ventana deslizante (en memoria: un solo worker)."""

    def __init__(self, maximo=5, ventana_s=60):
        self.maximo = maximo
        self.ventana_s = ventana_s
        self._fallos = defaultdict(deque)
        self._lock = threading.Lock()

    def _vigentes(self, ip, ahora):
        fallos = self._fallos[ip]
        while fallos and fallos[0] <= ahora - self.ventana_s:
            fallos.popleft()
        return fallos

    def bloqueado(self, ip, ahora):
        with self._lock:
            return len(self._vigentes(ip, ahora)) >= self.maximo

    def fallo(self, ip, ahora):
        with self._lock:
            self._vigentes(ip, ahora).append(ahora)

    def reiniciar(self):
        with self._lock:
            self._fallos.clear()


limite = LimiteIntentos()


class Entrada(BaseModel):
    password: str


def crear_router(obtener_api_key):
    """obtener_api_key se llama en cada petición, así un cambio de ajustes aplica de inmediato."""
    router = APIRouter(tags=["sesion"])

    @router.post("/sesion", status_code=204)
    def entrar(body: Entrada, request: Request, response: Response):
        api_key = obtener_api_key()
        if not api_key:
            raise HTTPException(503, "El servidor no tiene API_KEY configurada; se rechazan todas las solicitudes.")
        ip = request.client.host if request.client else "?"
        ahora = time.time()
        if limite.bloqueado(ip, ahora):
            raise HTTPException(429, "Demasiados intentos; espera un minuto.")
        if not secrets.compare_digest(limpiar_clave(body.password).encode(), api_key.encode()):
            limite.fallo(ip, ahora)
            raise HTTPException(401, "Contraseña incorrecta.")
        response.set_cookie(COOKIE, crear_token(api_key, ahora), max_age=DURACION_S, path="/",
                            httponly=True, samesite="strict",
                            secure=request.url.scheme == "https")

    @router.delete("/sesion", status_code=204)
    def salir(response: Response):
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")

    return router
