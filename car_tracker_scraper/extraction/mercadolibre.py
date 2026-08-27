"""Extraccion de datos embebidos en las paginas de MercadoLibre.

Ambas paginas (listado y detalle) sirven el mismo mecanismo de estado inicial
via <script id="__NORDIC_RENDERING_CTX__">_n.ctx.r={...}</script> - no hay XHR
que llamar. Ver findings_clickup.md (Fase 0) para el detalle completo de como
se confirmo esto con datos reales.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

# wdxtkg30xr: el scanner de llaves balanceadas y el lector de JSON-LD se
# movieron a extraction/common.py (no tienen nada de especifico de ML, los
# necesita cualquier fuente que embeba JSON en HTML). Se re-exportan aca para
# no romper los imports existentes de este modulo.
from car_tracker_scraper.extraction.common import extract_balanced_json, extract_json_ld

__all__ = [
    "extract_balanced_json",
    "extract_json_ld",
    "extract_nordic_ctx",
    "iter_polycards",
    "polycard_components",
]

_NORDIC_CTX_RE = re.compile(
    r'<script id="__NORDIC_RENDERING_CTX__"[^>]*>(.*?)</script>', re.S
)


def extract_nordic_ctx(html: str) -> dict:
    """Extrae el blob __NORDIC_RENDERING_CTX__ de una pagina de ML (listado o detalle)."""
    match = _NORDIC_CTX_RE.search(html)
    if match is None:
        raise ValueError("__NORDIC_RENDERING_CTX__ no encontrado en el HTML")
    raw = match.group(1).split("_n.ctx.r=", 1)[1]
    return extract_balanced_json(raw)


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
