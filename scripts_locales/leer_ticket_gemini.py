"""Prueba de extracción de datos de un ticket de Sam's/Walmart con la API de Google Gemini
(nivel gratis, sin tarjeta de crédito). Usa requests puro por HTTP, sin el SDK de Google,
para evitar problemas de compilación en Windows ARM64.

Instalación (una vez):
    pip install requests python-dotenv

Configuración: crea un archivo .env junto a este script (NUNCA lo compartas ni lo subas
a ningún lado; contiene tu clave secreta):
    GEMINI_API_KEY=tu_api_key

Si tu clave ya se compartió por accidente en algún chat o mensaje, revócala en
aistudio.google.com y genera una nueva antes de usarla aquí.

Uso:
    python leer_ticket_gemini.py ruta/a/la/foto.jpg
    python leer_ticket_gemini.py ruta/a/la/foto1.jpg ruta/a/la/foto2.jpg

Imprime el JSON extraído de cada ticket y lo guarda en salida_lecturas/<nombre>.json
"""
import base64
import json
import mimetypes
import os
import sys
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OUT = Path("salida_lecturas")
OUT.mkdir(exist_ok=True)

MODELO = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent"

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


def leer_ticket(api_key, ruta):
    ruta = Path(ruta)
    media_type = mimetypes.guess_type(ruta)[0] or "image/jpeg"
    data_b64 = base64.standard_b64encode(ruta.read_bytes()).decode("utf-8")

    body = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {"inline_data": {"mime_type": media_type, "data": data_b64}},
                ]
            }
        ]
    }

    resp = requests.post(
        URL,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )

    if resp.status_code != 200:
        return {"_error": f"HTTP {resp.status_code}", "_detalle": resp.text[:2000]}

    data = resp.json()
    try:
        texto = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        return {"_error": "respuesta inesperada de Gemini", "_detalle": data}

    texto = texto.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return {"_error": "la respuesta no fue JSON válido", "_texto_crudo": texto}


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: python leer_ticket_gemini.py foto1.jpg [foto2.jpg ...]")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("Falta GEMINI_API_KEY en tu archivo .env")

    for ruta in sys.argv[1:]:
        print(f"\n=== {ruta} ===")
        resultado = leer_ticket(api_key, ruta)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))

        destino = OUT / (Path(ruta).stem + ".json")
        destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[guardado en {destino}]")


if __name__ == "__main__":
    main()