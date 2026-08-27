"""Discovery spider para Autocity (wdxtkg39qx).

**Es el unico Discovery del proyecto que no pagina.** Autocity publica su
sitemap de productos (`wp-sitemap-posts-product-1.xml`) con las 405 fichas, asi
que UNA request trae el inventario completo. El catalogo paginado tambien
existe (/catalogo/usados/page/N/) pero da ~13 autos por pagina y con
solapamiento entre paginas: serian ~22 requests y deduplicacion para llegar al
mismo resultado.

Por eso este spider no emite precio: el sitemap solo tiene URLs. No es una
perdida - los ListingSummaryItem NUNCA se publican a RabbitMQ (eso lo hace solo
el pipeline con los ListingDetailItem, ver queue_publish/pipeline.py), su unico
consumidor es DiscoveryCandidateTracker, que necesita url + marca. El precio
viaja en Detail, que es lo que realmente se persiste.

0km: se descartan por el path de la URL (`/catalogo/usados/` vs
`/catalogo/0km/`), antes de gastar un solo fetch de Detail. Es el filtro mas
confiable de las cuatro fuentes - no depende de heuristica alguna.

Uso:
    scrapy crawl autocity_discovery -O output/discovery_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

import scrapy

from car_tracker_scraper.extraction.autocity import (
    SITEMAP_URL,
    brand_from_url,
    iter_used_urls,
    listing_key_from_url,
)
from car_tracker_scraper.extraction.common import resolve_marca_slug
from car_tracker_scraper.spiders.base import landing_meta
from car_tracker_scraper.items import ListingSummaryItem


class AutocityDiscoverySpider(scrapy.Spider):
    name = "autocity_discovery"
    allowed_domains = ["autocity.com.ar", "www.autocity.com.ar"]

    def __init__(self, marcas: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `marcas` se recibe por consistencia con las otras fuentes (run_batch
        # se lo pasa a todas por igual) pero aca NO filtra: el sitemap trae el
        # inventario entero y cada aviso se taggea con su marca real. Se usa
        # solo para normalizar esa marca al slug curado.
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]

    async def start(self):
        # async start() y no start_requests(): ver el comentario en
        # spiders/base.py (start_requests() no despacha nada en Scrapy 2.17).
        yield scrapy.Request(SITEMAP_URL, callback=self.parse)

    def parse(self, response):
        for url in iter_used_urls(response.text):
            marca_cruda = brand_from_url(url)
            yield ListingSummaryItem(
                source="autocity",
                source_listing_key=listing_key_from_url(url),
                url=url,
                marca=resolve_marca_slug(marca_cruda, marca_cruda or "", self.marcas),
                is_ad=False,
                category_id=None,
                domain_id=None,
                title_raw=None,  # el sitemap no lo trae; sale de la ficha
                price_amount=None,  # idem - ver el docstring del modulo
                price_currency=None,
                attributes_raw=None,
                location_raw=None,
                financing_initial_payment=None,
                discovered_at=datetime.now(timezone.utc).isoformat(),
                **landing_meta(response),
            )
