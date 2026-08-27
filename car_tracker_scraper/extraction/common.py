"""Helpers de extraccion compartidos entre fuentes.

wdxtkg30xr: estos dos vivian en extraction/mercadolibre.py pero no tienen nada
de especifico de ML - el scanner de llaves balanceadas lo necesita cualquier
fuente que embeba JSON dentro de HTML (Motordil lo hace con el flight stream
de Next.js), y schema.org JSON-LD lo exponen las tres fuentes reverse-
engineered en Fase 0 (ML, DeRuedas y Motordil). mercadolibre.py los re-exporta
para no romper sus imports/tests existentes.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

_JSON_LD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def compact(text: str) -> str:
    """Minusculas, sin acentos y sin caracteres no alfanumericos.

    Sirve para comparar el mismo nombre de marca escrito distinto segun la
    fuente: 'Mercedes-Benz' (titulo de ML) vs 'mercedes-benz' (slug de la API
    de marcas) vs 'Mercedes Benz' (Motordil). Mismo criterio que _compact() en
    dnrpa_lookup.py del lado car-tracker (ver su CLAUDE.md, caso real
    'C-HR' vs 'CHR').
    """
    ascii_only = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM_RE.sub("", ascii_only.lower())


def extract_balanced_json(text: str, start: int = 0) -> Any:
    """Parsea el objeto JSON balanceado que empieza en `start`.

    Regex simple no alcanza: los blobs tienen objetos anidados. Se cuenta
    profundidad de llaves caracter a caracter hasta volver a 0.

    `start` existe por Motordil: ML siempre parsea desde el inicio del blob,
    pero en el flight stream de Next.js los objetos que interesan aparecen en
    cualquier posicion del stream reconstruido (ver extraction/motordil.py).
    """
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("no se encontro un objeto JSON balanceado")


def extract_json_ld(html: str, type_name: str) -> dict | None:
    """Busca entre los bloques <script type="application/ld+json"> el que
    tenga @type == type_name (ej. "Vehicle", "BreadcrumbList", "Car").

    Ojo con el @type exacto por fuente: ML usa "Vehicle", Motordil usa "Car"
    (confirmado contra fixtures reales de ambas, 2026-08-26 - el doc de
    hallazgos de Fase 0 decia "Vehicle" para Motordil y estaba equivocado).
    """
    for match in _JSON_LD_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if candidate.get("@type") == type_name:
                return candidate
    return None
