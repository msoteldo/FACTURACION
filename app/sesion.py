"""Sesión de la webapp: la contraseña es la API_KEY y se responde con una cookie firmada.

El token es "<expiración>.<HMAC-SHA256>" con una clave derivada de la API_KEY: no se
guarda nada en el servidor y cambiar la API_KEY invalida todas las sesiones.
"""
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque

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


def ip_cliente(request):
    """IP para el límite de intentos: la última de X-Forwarded-For, que es la que agrega el
    proxy de Render. Las anteriores las manda el cliente y podría inventarlas para brincarse
    el límite (uvicorn con --forwarded-allow-ips='*' toma la primera)."""
    reenviada = request.headers.get("x-forwarded-for", "")
    ultima = reenviada.split(",")[-1].strip()
    return ultima or (request.client.host if request.client else "?")


class LimiteIntentos:
    """Cuenta logins fallidos por IP y en total, en una ventana deslizante (en memoria: un
    solo worker). El tope global frena a quien rote IPs; con un solo usuario no estorba."""

    def __init__(self, maximo=5, ventana_s=60, maximo_global=20):
        self.maximo = maximo
        self.ventana_s = ventana_s
        self.maximo_global = maximo_global
        self._fallos = {}
        self._global = deque()
        self._lock = threading.Lock()

    def _purgar(self, fallos, ahora):
        while fallos and fallos[0] <= ahora - self.ventana_s:
            fallos.popleft()

    def _limpiar(self, ahora):
        # Se barren todas las IPs: son pocas, porque el tope global corta los fallos en cada
        # ventana, y así no se acumulan en memoria las que ya no intentan.
        self._purgar(self._global, ahora)
        for ip in list(self._fallos):
            self._purgar(self._fallos[ip], ahora)
            if not self._fallos[ip]:
                del self._fallos[ip]

    def bloqueado(self, ip, ahora):
        with self._lock:
            self._limpiar(ahora)
            return (len(self._fallos.get(ip, ())) >= self.maximo
                    or len(self._global) >= self.maximo_global)

    def fallo(self, ip, ahora):
        with self._lock:
            self._limpiar(ahora)
            self._fallos.setdefault(ip, deque()).append(ahora)
            self._global.append(ahora)

    def reiniciar(self):
        with self._lock:
            self._fallos.clear()
            self._global.clear()


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
        ip = ip_cliente(request)
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
