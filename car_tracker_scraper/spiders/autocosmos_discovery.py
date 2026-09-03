"""Discovery spider para Autocosmos (wdxtkg3hc4).

**No filtra por `seccion` ni por marca.** La investigacion inicial penso en
restringir a `?seccion=precio-final` para esquivar los avisos "financiados en
cuotas" (donde el numero grande es un ANTICIPO, no el precio del auto - ver
extraction/autocosmos.py), pero el HTML real confirmo una señal mas fina: cada
tarjeta se puede clasificar sola (`is_financed_price()`, por itemtype
`PriceSpecification`), asi que el filtro de URL sobra - recorrer el catalogo
completo (`/auto/usado`, paginado por `pidx`) cubre los 5.690 usados reales en
vez de solo los 5.330 de "Precio final".

Tampoco hace falta paginar por marca como en MercadoLibre: el catalogo entero
son ~119 paginas de 48 tarjetas (medido 2026-09-02), muy por debajo del techo
de paginacion que si le hace falta a ML.

Uso:
    scrapy crawl autocosmos_discovery -O output/discovery_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

import scrapy

from car_tracker_scraper.extraction.autocosmos import (
    BASE_URL,
    card_brand,
    card_km,
    card_location,
    card_model,
    card_price,
    card_url,
    card_version,
    card_year,
    iter_cards,
    listing_key_from_url,
)
from car_tracker_scraper.extraction.common import resolve_marca_slug
from car_tracker_scraper.spiders.base import landing_meta
from car_tracker_scraper.items import ListingSummaryItem

LISTADO_URL = f"{BASE_URL}/auto/usado"


class AutocosmosDiscoverySpider(scrapy.Spider):
    name = "autocosmos_discovery"
    allowed_domains = ["www.autocosmos.com.ar", "autocosmos.com.ar"]
    custom_settings = {
        # robots.txt de Autocosmos pide Crawl-delay: 20 bajo User-agent: *
        # (confirmado 2026-09-02). El token bucket global (~1 req/s) es mas
        # rapido que eso - sin este DOWNLOAD_DELAY propio, corrida real
        # verificada: 5 requests de Detail en 2.6s en vez de ~100s. Mismo
        # patron que DeRuedas (DOWNLOAD_DELAY=5).
        "DOWNLOAD_DELAY": 20,
    }

    def __init__(self, marcas: str = "", max_pages: str = "150", *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `marcas` no filtra la request (ver docstring del modulo): solo se
        # usa para normalizar la marca cruda de cada tarjeta al slug curado,
        # mismo patron que autocity_discovery.
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]
        # Tope de seguridad: ~119 paginas cubren el catalogo entero medido
        # 2026-09-02 (5.690 usados / 48 por pagina). 150 deja margen sin
        # arriesgar una corrida infinita si el sitio cambia el tamano de pagina.
        self.max_pages = int(max_pages)

    def _page_url(self, page: int) -> str:
        return LISTADO_URL if page == 1 else f"{LISTADO_URL}?pidx={page}"

    async def start(self):
        # async start() y no start_requests(): ver el comentario en
        # spiders/base.py (start_requests() no despacha nada en Scrapy 2.17).
        yield scrapy.Request(self._page_url(1), callback=self.parse, meta={"page": 1})

    def parse(self, response):
        page = response.meta["page"]
        seen_ids = response.meta.get("seen_ids", set())

        page_ids = set()
        for card in iter_cards(response.text):
            url = card_url(card)
            listing_id = listing_key_from_url(url)
            if not listing_id or not url:
                self.logger.warning("Tarjeta sin id o sin URL, la salteo: %r", url)
                continue
            page_ids.add(listing_id)

            km = card_km(card)
            if km == 0:
                continue  # 0km fuera, proyecto = mercado de usados

            brand = card_brand(card)
            model = card_model(card)
            version = card_version(card)
            year = card_year(card)
            city, province = card_location(card)
            price_amount, price_currency, financing_initial_payment = card_price(card)

            yield ListingSummaryItem(
                source="autocosmos",
                source_listing_key=listing_id,
                url=url,
                marca=resolve_marca_slug(brand, brand or "", self.marcas),
                is_ad=False,
                category_id=None,
                domain_id=None,
                title_raw=" ".join(p for p in (brand, model, version) if p) or None,
                price_amount=price_amount,
                price_currency=price_currency,
                attributes_raw=[
                    str(year) if year else None,
                    f"{km} km" if km is not None else None,
                ],
                location_raw=", ".join(p for p in (city, province) if p) or None,
                financing_initial_payment=financing_initial_payment,
                discovered_at=datetime.now(timezone.utc).isoformat(),
                **landing_meta(response),
            )

        # Corte real: una pagina sin ids nuevos agoto el inventario. Mismo
        # criterio que DeRuedas (no confiar en "vino vacia" a secas).
        if not (page_ids - seen_ids):
            return
        if page >= self.max_pages:
            self.logger.warning(
                "autocosmos: llego al tope de seguridad max_pages=%d sin agotar "
                "el inventario (ver wdxtkg3hc4).", self.max_pages,
            )
            return

        yield response.follow(
            self._page_url(page + 1),
            callback=self.parse,
            meta={"page": page + 1, "seen_ids": seen_ids | page_ids},
        )
