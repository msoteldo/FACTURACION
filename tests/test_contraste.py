"""Contraste WCAG 2.1 AA de los tokens de color (tema claro y oscuro) de css/tokens.css."""
import re
from pathlib import Path

import pytest

TOKENS = (Path(__file__).parent.parent / "app" / "static" / "css" / "tokens.css").read_text()


def _bloque(css):
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", css))


CLARO = _bloque(TOKENS.split("@media (prefers-color-scheme: dark)")[0])
OSCURO = {**CLARO, **_bloque(TOKENS.split("@media (prefers-color-scheme: dark)")[1])}


def _lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contraste(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# (texto o borde, fondo, mínimo): 4.5 para texto (1.4.3), 3 para bordes de campos e íconos (1.4.11).
PARES = [
    ("ink", "bg", 4.5), ("soft", "bg", 4.5), ("soft", "surface", 4.5), ("faint", "surface", 4.5),
    ("faint", "raised", 4.5), ("accent", "surface", 4.5), ("accent-ink", "accent", 4.5),
    ("accent-soft-ink", "accent-soft", 4.5), ("ok", "ok-soft", 4.5), ("ok", "surface", 4.5),
    ("danger", "surface", 4.5), ("danger", "danger-soft", 4.5), ("ink", "mark", 4.5),
    ("line-campo", "surface", 3), ("surface", "ok", 3), ("surface", "danger", 3),
]


@pytest.mark.parametrize("tema", ["claro", "oscuro"])
@pytest.mark.parametrize("frente,fondo,minimo", PARES)
def test_contraste_aa(tema, frente, fondo, minimo):
    t = CLARO if tema == "claro" else OSCURO
    assert contraste(t[frente], t[fondo]) >= minimo, f"{frente} sobre {fondo} ({tema})"
