"""Extracción de datos de un ticket (foto) con la API REST de Google Gemini.

Usa `requests` directo (sin el SDK google-genai) para seguir funcionando igual en
Windows ARM64 y en el servidor.
"""
import base64
import json
import re
import time

import requests

URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

PROMPT = """Eres un extractor de datos de tickets de compra de Sam's Club / Walmart México.
Analiza la imagen del ticket y devuelve SOLO un JSON (sin texto antes ni después,
sin ``` de markdown) con esta forma exacta:

{
  "comercio": string,
  "tienda_numero": string o null,
  "fecha": "YYYY-MM-DD" o null,
  "hora": "HH:MM" o null,
  "tc_numero_ticket": string o null,
  "tr_numero_transaccion": string o null,
  "subtotal": number o null,
  "descuento": number o null,
  "iva_monto": number o null,
  "total": number,
  "forma_de_pago": "credito" | "debito" | "monedero" | "efectivo" | "mixto" | "desconocido",
  "confianza_tc": "alta" | "media" | "baja",
  "confianza_tr": "alta" | "media" | "baja",
  "advertencias": [string]
}

Reglas importantes:
- El TC (número de ticket) y TR (número de transacción) suelen estar juntos, cerca de
  "TDA#", "OP#", "TE#" y "TR#". Léelos con mucho cuidado, dígito por dígito; son la parte
  más importante y más propensa a errores. Si algún dígito no es 100% legible, indícalo
  en "advertencias" y baja la "confianza" correspondiente a "media" o "baja".
- No inventes datos: si algo no se puede leer, usa null y explica por qué en "advertencias".
- "total" es el único campo obligatorio; si no lo puedes leer con seguridad, ponlo en 0
  y explica en "advertencias".
- Verifica que subtotal - descuento + iva_monto sea aproximadamente igual a total; si no
  cuadra, dilo en "advertencias" en vez de forzar los números.
"""

# Forma de pago del ticket -> clave SAT que espera el portal de Walmart.
FORMA_PAGO_SAT = {"credito": "04", "debito": "28", "monedero": "05"}

REINTENTABLES = {429, 500, 503}


class ExtraccionError(Exception):
    def __init__(self, mensaje, status_http=502, detalle=None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status_http = status_http
        self.detalle = detalle


def _llamar_gemini(api_key, modelo, body, reintentos, espera_base):
    url = URL_BASE.format(modelo=modelo)
    for intento in range(reintentos + 1):
        try:
            resp = requests.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=60,
            )
        except requests.RequestException as e:
            if intento < reintentos:
                time.sleep(espera_base * 2 ** intento)
                continue
            raise ExtraccionError(f"No se pudo contactar a Gemini: {type(e).__name__}") from e

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            raise ExtraccionError(
                f"El modelo '{modelo}' ya no está disponible en Gemini (404). "
                "Cambia la variable de entorno GEMINI_MODEL por un modelo vigente.",
                status_http=502, detalle=resp.text[:500],
            )
        if resp.status_code in (401, 403):
            raise ExtraccionError("Gemini rechazó la API key (revisa GEMINI_API_KEY).",
                                  status_http=502, detalle=resp.text[:500])
        if resp.status_code in REINTENTABLES and intento < reintentos:
            time.sleep(espera_base * 2 ** intento)
            continue
        if resp.status_code in REINTENTABLES:
            raise ExtraccionError(
                f"Gemini está saturado (HTTP {resp.status_code}); intenta de nuevo en un momento.",
                status_http=503, detalle=resp.text[:500],
            )
        raise ExtraccionError(f"Gemini respondió HTTP {resp.status_code}",
                              detalle=resp.text[:500])


def _parsear(data):
    try:
        texto = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise ExtraccionError("Respuesta inesperada de Gemini (¿imagen bloqueada o vacía?)",
                              detalle=str(data)[:500])
    texto = texto.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        raise ExtraccionError("La respuesta de Gemini no fue JSON válido", detalle=texto[:500])


def validar(ticket):
    """Revisiones baratas sobre lo extraído; agrega advertencias en vez de corregir."""
    advertencias = list(ticket.get("advertencias") or [])
    for campo, nombre in (("tc_numero_ticket", "TC#"), ("tr_numero_transaccion", "TR#")):
        valor = ticket.get(campo)
        if valor is None:
            advertencias.append(f"No se leyó el {nombre}; captúralo a mano.")
            continue
        limpio = re.sub(r"\s", "", str(valor))
        ticket[campo] = limpio
        if not limpio.isdigit():
            advertencias.append(f"El {nombre} '{limpio}' contiene caracteres que no son dígitos.")

    ticket["advertencias"] = advertencias
    ticket["forma_pago_sat_sugerida"] = FORMA_PAGO_SAT.get(ticket.get("forma_de_pago"))
    ticket["requiere_revision"] = bool(
        advertencias
        or ticket.get("confianza_tc") != "alta"
        or ticket.get("confianza_tr") != "alta"
    )
    return ticket


def leer_ticket(api_key, modelo, imagen, mime_type, reintentos=3, espera_base=2.0):
    if not api_key:
        raise ExtraccionError("Falta configurar GEMINI_API_KEY en el servidor.", status_http=503)
    body = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"inline_data": {"mime_type": mime_type,
                                 "data": base64.standard_b64encode(imagen).decode("ascii")}},
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    data = _llamar_gemini(api_key, modelo, body, reintentos, espera_base)
    return validar(_parsear(data))
