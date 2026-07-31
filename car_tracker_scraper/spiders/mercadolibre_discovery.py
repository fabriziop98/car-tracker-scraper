"""Discovery spider para MercadoLibre.

Recorre los listados paginados de una o mas marcas y descubre avisos nuevos
(usados, sin publicidad). Barato, pensado para correr cada 4-6h (seccion 3.4
del doc de arquitectura). NO trae la ficha completa - eso es el trabajo
separado de mercadolibre_detail.

Filtrado de 0km: el filtro de condicion de ML (`ITEM*CONDITION_2230581`) va
pegado a un segmento `_NoIndex_True` en la URL, y el robots.txt real de
autos.mercadolibre.com.ar bloquea justo ese patron
(`Disallow: /*_NoIndex_True` bajo `User-agent: *`) - confirmado 2026-07-31.
En vez de usar ese filtro via URL, este spider pide la pagina de marca SIN
filtrar (URL que no choca con ninguna regla del robots.txt) y descarta los
0km el mismo, en base al mismo dato que revelo el bug original en Fase 0:
el texto "0 Km" en attributes_list. Es una heuristica de texto, no un campo
de condicion explicito - monitorear si empieza a fallar (ver
`_is_zero_km`).

Uso:
    scrapy crawl mercadolibre_discovery -a marcas=fiat,ford -a max_pages=3 \
        -O output/discovery_%(time)s.jsonl
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import scrapy

from car_tracker_scraper.extraction.mercadolibre import (
    extract_nordic_ctx,
    iter_polycards,
    polycard_components,
)
from car_tracker_scraper.items import ListingSummaryItem

_ZERO_KM_RE = re.compile(r"^0[.,]?0*\s*km$", re.IGNORECASE)


def _is_zero_km(attributes_raw: list[str] | None) -> bool:
    return any(_ZERO_KM_RE.match(attr.strip()) for attr in (attributes_raw or []))


def _normalize_url(url: str | None) -> str | None:
    """metadata.url viene sin esquema en produccion (ej. "auto.mercadolibre.com.ar/MLA-...",
    no "https://auto..."). Confirmado corriendo el spider real 2026-07-31 - sin esto,
    pasarle este valor tal cual a mercadolibre_detail rompe (Scrapy exige URL absoluta)."""
    if url and not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


class MercadolibreDiscoverySpider(scrapy.Spider):
    name = "mercadolibre_discovery"
    allowed_domains = ["autos.mercadolibre.com.ar"]

    def __init__(self, marcas: str = "fiat", max_pages: str = "3", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]
        self.max_pages = int(max_pages)

    def start_requests(self):
        # Sin query string: "?sb=all_mercadolibre" (el sort-order que traia el
        # comando curl original de findings_clickup.md) contiene el substring
        # "mercadolibre", que matchea "Disallow: /*mercadolibre" bajo
        # "User-agent: *" en el robots.txt real de autos.mercadolibre.com.ar
        # (confirmado corriendo el spider real 2026-07-31: bloqueado en
        # silencio via RobotsTxtMiddleware). La URL base sin query, tal como
        # esta documentada en findings_clickup.md ("URL de listado:
        # https://autos.mercadolibre.com.ar/{marca}"), no choca con ninguna
        # regla.
        for marca in self.marcas:
            url = f"https://autos.mercadolibre.com.ar/{marca}"
            yield scrapy.Request(url, callback=self.parse, meta={"marca": marca, "page_count": 1})

    def parse(self, response):
        marca = response.meta["marca"]
        page_count = response.meta["page_count"]

        ctx = extract_nordic_ctx(response.text)
        search = ctx["appProps"]["sharedState"]["search"]

        for polycard in iter_polycards(search.get("results", [])):
            metadata = polycard.get("metadata", {})
            if str(metadata.get("is_pad")).lower() == "true":
                continue  # publicidad, no contaminar agregados

            comp = polycard_components(polycard)
            attributes_raw = (comp.get("attributes_list") or {}).get("texts")
            if _is_zero_km(attributes_raw):
                continue  # 0km, no es el segmento usado que nos interesa

            price = (comp.get("price") or {}).get("current_price") or {}
            # price_complements no tiene forma confirmada todavia (findings_clickup.md
            # solo lo menciona en prosa): en produccion salio como list en vez de
            # dict en al menos un item real - no crashear, solo no sacar el dato.
            price_complements = (comp.get("price") or {}).get("price_complements")

            yield ListingSummaryItem(
                source="mercadolibre",
                source_listing_key=metadata.get("id"),
                url=_normalize_url(metadata.get("url")),
                is_ad=False,
                category_id=metadata.get("category_id"),
                domain_id=metadata.get("domain_id"),
                title_raw=(comp.get("title") or {}).get("text"),
                price_amount=price.get("value"),
                price_currency=price.get("currency"),
                attributes_raw=attributes_raw,
                location_raw=(comp.get("location") or {}).get("text"),
                financing_initial_payment=(
                    price_complements.get("initial_payment_amount")
                    if isinstance(price_complements, dict)
                    else None
                ),
                discovered_at=datetime.now(timezone.utc).isoformat(),
            )

        if page_count >= self.max_pages:
            return

        pagination = search.get("pagination", {})
        for next_url in pagination.get("pagination_nodes_url", []):
            yield response.follow(
                next_url,
                callback=self.parse,
                meta={"marca": marca, "page_count": page_count + 1},
            )
