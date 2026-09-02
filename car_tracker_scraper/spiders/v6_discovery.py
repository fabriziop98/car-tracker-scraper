"""Discovery spider para V6 Marketplace (wdxtkg39qx).

Quinta fuente. Un unico GET a `getPublishedCars` trae el catalogo entero (ver
extraction/v6.py para el por que) - no hay paginacion ni loop por marca como
en las otras cuatro fuentes: `marcas` solo se usa para resolver el slug
curado de cada aviso (`resolve_marca_slug`), no para filtrar el pedido en si.

Uso:
    scrapy crawl v6_discovery -O output/discovery_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

import scrapy

from car_tracker_scraper.extraction.common import resolve_marca_slug
from car_tracker_scraper.extraction.v6 import (
    PUBLISHED_CARS_URL,
    detail_path,
    is_zero_km,
    resolve_currency,
    slugify,
    title,
)
from car_tracker_scraper.spiders.base import landing_meta
from car_tracker_scraper.items import ListingSummaryItem

BASE_URL = "https://www.v6.com.ar"


class V6DiscoverySpider(scrapy.Spider):
    name = "v6_discovery"
    allowed_domains = ["autoprecios-api.onrender.com", "www.v6.com.ar"]

    def __init__(self, marcas: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]

    async def start(self):
        # async start() y no start_requests(): en Scrapy 2.17 start_requests()
        # no despacha ningun request - incidente real de produccion
        # 2026-08-18 (wdxtkg34th/wdxtkg35ba), ver motordil_discovery.py.
        yield scrapy.Request(PUBLISHED_CARS_URL, callback=self.parse)

    def parse(self, response):
        cars = response.json()

        emitted = 0
        for car in cars:
            # 0km fuera, mismo criterio e incidente real que Motordil/DeRuedas
            # (ver extraction/v6.py).
            if is_zero_km(car):
                continue

            path = detail_path(car)
            if not path:
                self.logger.warning("Aviso sin uid, no se puede armar URL: %r", car)
                continue

            brand = car.get("brand")
            # 91 de 209 avisos reales (2026-09-02) no tienen `priceHistory`,
            # que es la unica señal de moneda que expone este endpoint (ver
            # resolve_currency en extraction/v6.py) - un precio sin moneda es
            # un bug segun el contrato de este repo (test_source_contract.py,
            # invariante 4), asi que se omiten los dos juntos, igual que
            # Autocity omite el precio entero en Discovery cuando su fuente
            # (el sitemap) no lo expone. Detail SI emite el precio sin moneda
            # en ese caso: ahi es donde el cascade de PriceValidationService
            # del lado Java existe justamente para resolver moneda ambigua.
            currency = resolve_currency(car)
            price = car.get("price") if currency else None

            yield ListingSummaryItem(
                source="v6",
                source_listing_key=car.get("uid"),
                url=f"{BASE_URL}{path}",
                # La marca real del propio aviso normalizada al slug curado.
                # Sin "marca pedida" (no hay loop por marca aca), el fallback
                # conservador es el propio nombre de marca sluggificado.
                marca=resolve_marca_slug(brand, slugify(brand or ""), self.marcas),
                is_ad=False,  # V6 no mezcla publicidad en este endpoint
                category_id=None,
                domain_id=None,
                title_raw=title(car),
                price_amount=price,
                price_currency=currency,
                attributes_raw=[
                    str(car["year"]) if car.get("year") else None,
                    f"{car['kilometers']} km" if car.get("kilometers") is not None else None,
                ],
                location_raw=car.get("city"),
                financing_initial_payment=car.get("anticipo"),
                discovered_at=datetime.now(timezone.utc).isoformat(),
                **landing_meta(response),
            )
            emitted += 1

        self.logger.info("v6_discovery: %d avisos emitidos de %d en el catalogo", emitted, len(cars))
