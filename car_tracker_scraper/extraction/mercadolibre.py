"""Extraccion de datos embebidos en las paginas de MercadoLibre.

Ambas paginas (listado y detalle) sirven el mismo mecanismo de estado inicial
via <script id="__NORDIC_RENDERING_CTX__">_n.ctx.r={...}</script> - no hay XHR
que llamar. Ver findings_clickup.md (Fase 0) para el detalle completo de como
se confirmo esto con datos reales.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

_NORDIC_CTX_RE = re.compile(
    r'<script id="__NORDIC_RENDERING_CTX__"[^>]*>(.*?)</script>', re.S
)
_JSON_LD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)


def _extract_balanced_json(raw: str) -> Any:
    """Parsea el primer objeto JSON balanceado al inicio de `raw`.

    Regex simple no alcanza: el blob tiene objetos anidados. Se cuenta
    profundidad de llaves caracter a caracter hasta volver a 0.
    """
    depth = 0
    for i, ch in enumerate(raw):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[: i + 1])
    raise ValueError("no se encontro un objeto JSON balanceado")


def extract_nordic_ctx(html: str) -> dict:
    """Extrae el blob __NORDIC_RENDERING_CTX__ de una pagina de ML (listado o detalle)."""
    match = _NORDIC_CTX_RE.search(html)
    if match is None:
        raise ValueError("__NORDIC_RENDERING_CTX__ no encontrado en el HTML")
    raw = match.group(1).split("_n.ctx.r=", 1)[1]
    return _extract_balanced_json(raw)


def iter_polycards(results: list[dict]) -> Iterator[dict]:
    """Desanida los polycards de search.results, incluyendo los agrupados
    dentro de GROUP_ITEMS_INTERVENTION."""
    for r in results:
        if r.get("id") == "POLYCARD":
            yield r["polycard"]
        elif r.get("id") == "GROUP_ITEMS_INTERVENTION":
            for it in r.get("items", []):
                if it.get("id") == "POLYCARD":
                    yield it["polycard"]


def polycard_components(polycard: dict) -> dict[str, Any]:
    """Convierte la lista components[] de un polycard en un dict indexado por tipo."""
    return {c["type"]: c.get(c["type"]) for c in polycard.get("components", [])}


def extract_json_ld(html: str, type_name: str) -> dict | None:
    """Busca entre los bloques <script type="application/ld+json"> el que
    tenga @type == type_name (ej. "Vehicle", "BreadcrumbList")."""
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
