"""Extraccion de datos embebidos en las paginas de MercadoLibre.

Ambas paginas (listado y detalle) sirven el mismo mecanismo de estado inicial
via <script id="__NORDIC_RENDERING_CTX__">_n.ctx.r={...}</script> - no hay XHR
que llamar. Ver findings_clickup.md (Fase 0) para el detalle completo de como
se confirmo esto con datos reales.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse
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


def _iter_facet_values(search: dict, filtro_id: str) -> Iterator[dict[str, Any]]:
    """Valores de un facet de `sidebar.components[].filters[]`, con su slug
    canonico (primer segmento de la URL real que publica ML) y el conteo real
    de avisos. Comun a `iter_model_facet` (wdxtkg39v1) e `iter_year_facet`
    (wdxtkg3j4o) - mismo shape de respuesta para los dos facets.

    Dos detalles medidos contra el sitio real (2026-09-01, MODEL; confirmado
    de nuevo 2026-09-06 para VEHICLE_YEAR - mismo shape), no asumidos:

    * El facet aparece DOS veces en la respuesta. El de
      `melidata_track.event_data.displayed_filters` es el payload de analitica y
      trae los values como strings pelados (ids), sin url ni conteo: inservible.
      El util esta en `sidebar.components[].filters[]`. Por eso se busca por
      `id == filtro_id` con values de dicts, en vez de por una ruta fija.
    * El slug se toma del PRIMER segmento de la url del facet
      (`/{valor}/{termino}_NoIndex_True`), que es el slug canonico de ML, en vez
      de slugificar `name` nosotros - "Hilux Pick-Up" -> "hilux-pick-up" y
      "C-HR" -> "c-hr" son justo los casos donde una slugificacion propia se
      equivoca.

    Ojo con lo que ML hace con esa url: pedida sin el fragmento `#applied_...`
    NO aplica el filtro (devuelve el termino sin filtrar). El slug sirve como
    TERMINO de busqueda (`/{slug}`), no como filtro estructurado.
    """
    for nodo in _iter_filtros(search, filtro_id):
        for valor in nodo.get("values") or []:
            if not isinstance(valor, dict):
                continue  # el facet de melidata: ids pelados, sin url ni conteo
            url = str(valor.get("url") or "").split("#")[0]
            slug = urlparse(url).path.strip("/").split("/")[0] if url else ""
            if not slug:
                continue
            digitos = "".join(c for c in str(valor.get("results") or "") if c.isdigit())
            yield {
                "name": valor.get("name"),
                "slug": slug,
                "count": int(digitos) if digitos else 0,
            }


def iter_model_facet(search: dict) -> Iterator[dict[str, Any]]:
    """Modelos que ML ofrece como filtro en una pagina de listado, con su slug
    canonico y el conteo real de avisos (wdxtkg39v1).

    Es la fuente de la lista de modelos a scrapear: sale del propio sitio, con
    los slugs que ML usa y conteos que se actualizan solos cuando aparece un
    modelo nuevo - a diferencia de derivarla de nuestro catalogo curado, cuyos
    slugs no tienen por que coincidir con los de ML y que quedaria viejo sin
    que nadie se entere. Ver `_iter_facet_values` para el detalle de como se
    extrae cada valor.
    """
    yield from _iter_facet_values(search, "MODEL")


def iter_year_facet(search: dict) -> Iterator[dict[str, Any]]:
    """Años que ML ofrece como filtro en la pagina de listado de UN modelo, con
    su slug (el año como string, ej. "2019") y el conteo real de avisos de ese
    modelo en ese año (wdxtkg3j4o).

    Es el segundo nivel del mismo problema que resolvio wdxtkg39v1: un modelo
    individual puede por si solo superar el tope de ~2.000 resultados por
    consulta de ML (medido 2026-09-06: Ford Ranger tiene 3.160 avisos reales,
    `/ranger` sola solo alcanza 2.000). Ver `_abanico_por_anio` en
    `mercadolibre_discovery.py` para como se usa. Mismo shape y mismas
    salvedades que `iter_model_facet` - ver `_iter_facet_values`.
    """
    yield from _iter_facet_values(search, "VEHICLE_YEAR")


def _iter_filtros(nodo: Any, filtro_id: str, prof: int = 0) -> Iterator[dict]:
    if prof > 6:
        return
    if isinstance(nodo, dict):
        if nodo.get("id") == filtro_id and isinstance(nodo.get("values"), list):
            yield nodo
        for v in nodo.values():
            yield from _iter_filtros(v, filtro_id, prof + 1)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from _iter_filtros(v, filtro_id, prof + 1)
