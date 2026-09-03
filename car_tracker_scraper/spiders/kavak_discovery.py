"""Discovery spider para Kavak (wdxtkg3hc5).

No filtra por marca en el request (mismo criterio que autocity/v6/autocosmos):
pagina el catalogo entero via `?page=N` y usa `marcas` solo para normalizar al
slug curado. Inventario chico (1.112 usados medido 2026-09-02, 30 por pagina)
-> ~38 paginas, no hace falta segmentar por marca como en MercadoLibre.

Uso:
    scrapy crawl kavak_discovery -O output/discovery_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

import scrapy

from car_tracker_scraper.extraction.common import resolve_marca_slug
from car_tracker_scraper.extraction.kavak import BASE_URL, card_detail_url, card_km, card_price, card_version, iter_cards
from car_tracker_scraper.extraction.motordil import extract_rsc_payload
from car_tracker_scraper.spiders.base import landing_meta
from car_tracker_scraper.items import ListingSummaryItem

LISTADO_URL = f"{BASE_URL}/ar/usados"


class KavakDiscoverySpider(scrapy.Spider):
    name = "kavak_discovery"
    allowed_domains = ["www.kavak.com", "kavak.com"]
    custom_settings = {
        # robots.txt de Kavak pide Crawl-delay: 20 bajo User-agent: *
        # (confirmado 2026-09-02, mismo orden que Autocosmos). El token bucket
        # global (~1 req/s) es mas rapido - sin este DOWNLOAD_DELAY propio no
        # se respeta.
        "DOWNLOAD_DELAY": 20,
    }

    def __init__(self, marcas: str = "", max_pages: str = "60", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]
        # ~38 paginas cubren el catalogo entero medido 2026-09-02 (1.112
        # usados / 30 por pagina). 60 deja margen sin arriesgar una corrida
        # infinita si el sitio cambia el tamano de pagina.
        self.max_pages = int(max_pages)

    def _page_url(self, page: int) -> str:
        return f"{LISTADO_URL}?page={page}"

    async def start(self):
        # async start() y no start_requests(): ver el comentario en
        # spiders/base.py (start_requests() no despacha nada en Scrapy 2.17).
        yield scrapy.Request(self._page_url(1), callback=self.parse, meta={"page": 1})

    def parse(self, response):
        page = response.meta["page"]
        seen_ids = response.meta.get("seen_ids", set())

        payload = extract_rsc_payload(response.text)
        page_ids = set()
        for card in iter_cards(payload):
            listing_id = card.get("id")
            url = card.get("url")
            if not listing_id or not url:
                self.logger.warning("Tarjeta sin id o sin URL, la salteo: %r", listing_id)
                continue
            page_ids.add(listing_id)

            km = card_km(card)
            if km == 0:
                continue  # 0km fuera, proyecto = mercado de usados

            title = card.get("title") or ""
            brand, _, model = title.partition("•")
            brand, model = brand.strip(), model.strip()
            version = card_version(card)
            price_amount, price_currency = card_price(card)

            yield ListingSummaryItem(
                source="kavak",
                source_listing_key=listing_id,
                url=card_detail_url(card),
                marca=resolve_marca_slug(brand, brand or "", self.marcas),
                is_ad=False,
                category_id=None,
                domain_id=None,
                title_raw=" ".join(p for p in (brand, model, version) if p) or None,
                price_amount=price_amount,
                price_currency=price_currency,
                attributes_raw=[
                    str(card.get("analytics", {}).get("car_year") or "") or None,
                    f"{km} km" if km is not None else None,
                ],
                location_raw=card.get("footerInfo") or None,
                financing_initial_payment=None,  # no aplica: ver docstring de extraction/kavak.py
                discovered_at=datetime.now(timezone.utc).isoformat(),
                **landing_meta(response),
            )

        # Corte real: una pagina sin ids nuevos agoto el inventario. Mismo
        # criterio que DeRuedas/Autocosmos.
        if not (page_ids - seen_ids):
            return
        if page >= self.max_pages:
            self.logger.warning(
                "kavak: llego al tope de seguridad max_pages=%d sin agotar "
                "el inventario (ver wdxtkg3hc5).", self.max_pages,
            )
            return

        yield response.follow(
            self._page_url(page + 1),
            callback=self.parse,
            meta={"page": page + 1, "seen_ids": seen_ids | page_ids},
        )
