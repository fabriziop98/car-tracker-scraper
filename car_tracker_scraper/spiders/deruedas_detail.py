"""Detail Fetch spider para DeRuedas (wdxtkg39pw).

Fase 0 nunca reverse-engineerio esta pagina (a diferencia de ML y Motordil, que
si tienen su seccion "Detail Fetch - RESUELTO" en findings_clickup.md) - el
formato se confirmo contra un fixture real recien el 2026-08-27. Diferencias
con la grilla que hay que tener presentes:

*   El itemtype es `schema.org/Vehicle`, NO `Car`.
*   El año viene en `modelDate`, no `vehicleModelDate`.
*   La marca esta ANIDADA en un itemscope `Brand` (`[itemprop=brand] [itemprop=name]`).
*   Los kilometros vienen como "KMT 109000" (unitCode + valor pegados).
*   NO hay `a.versionLink`: el trim solo aparece dentro de `description`.
*   No hay JSON-LD en toda la pagina.

Uso:
    scrapy crawl deruedas_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

import scrapy
from parsel import Selector

from car_tracker_scraper.extraction.deruedas import (
    detail_published_price,
    detail_version,
    detail_vehicle,
    parse_km,
    parse_year,
    prop,
    seller_id,
)
from car_tracker_scraper.items import ListingDetailItem


class DeruedasDetailSpider(scrapy.Spider):
    name = "deruedas_detail"
    allowed_domains = ["www.deruedas.com.ar", "deruedas.com.ar"]
    sticky_persona = True
    custom_settings = {
        # Mismo respeto al Crawl-delay: 5 del robots.txt que en Discovery.
        "DOWNLOAD_DELAY": 5,
    }

    def __init__(self, urls_file: str | None = None, urls: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._urls: list[str] = []
        if urls_file:
            with open(urls_file, encoding="utf-8") as fh:
                self._urls = [line.strip() for line in fh if line.strip()]
        if urls:
            self._urls += [u.strip() for u in urls.split(",") if u.strip()]
        if not self._urls:
            raise ValueError("Pasar -a urls_file=path/to/urls.txt o -a urls=url1,url2,...")

    async def start(self):
        for url in self._urls:
            yield scrapy.Request(url, callback=self.parse)

    def parse(self, response):
        html = response.text
        vehicle = detail_vehicle(html)
        if vehicle is None:
            # Aviso dado de baja o cambio de formato: no es un crash, no hay
            # item que emitir. Mismo criterio que el Detail de Motordil.
            self.logger.warning("Sin bloque schema.org/Vehicle en %s - aviso caido o cambio de formato", response.url)
            return

        brand = (
            vehicle.css('[itemprop="brand"] [itemprop="name"]::attr(content)').get()
            or vehicle.css('[itemprop="brand"] [itemprop="name"]::text').get()
        )
        brand = brand.strip() if brand else None
        model = prop(vehicle, "model")
        description = prop(vehicle, "description")

        # Precio del HTML plano, NO del microdata: DeRuedas convierte a ARS con
        # su propia cotizacion (17 de 30 avisos del fixture real estan
        # publicados en USD y el microdata los declara ARS igual).
        price_amount, price_currency = detail_published_price(html)

        # "Cordoba, Capital" -> "Cordoba". La provincia alimenta el fingerprint
        # de dedup, asi que se queda con el primer componente, que es el que
        # tiene granularidad de provincia.
        address = prop(vehicle, "address")
        province = address.split(",")[0].strip() if address else None

        # OJO: el link con `codUsr` esta FUERA del itemscope Vehicle (esta en el
        # bloque del vendedor), asi que se busca sobre la pagina entera. Buscarlo
        # dentro del bloque Vehicle devolvia None siempre.
        dealer_id = seller_id(Selector(text=html))

        yield ListingDetailItem(
            source="deruedas",
            source_listing_key=prop(vehicle, "sku"),
            url=response.url,
            brand_raw=brand,
            model_raw=model,
            version_raw=detail_version(description, brand, model),
            year_raw=parse_year(prop(vehicle, "modelDate")),
            km=parse_km(prop(vehicle, "mileageFromOdometer")),
            title_raw=prop(vehicle, "name"),
            color=None,
            fuel_type_raw=None,  # la ficha no lo expone; la grilla si (fuelType)
            number_of_doors=None,
            transmission_raw=None,
            item_condition=prop(vehicle, "availability"),
            price_amount=price_amount,
            price_currency=price_currency,
            price_valid_until=prop(vehicle, "priceValidUntil"),
            breadcrumb_raw=None,
            subtitle_raw=description,
            location_raw=address,
            highlighted_specs_raw=None,
            seller_name=Selector(text=html).css('[itemtype*="schema.org/Person"] [itemprop="name"]::attr(content)').get(),
            # codUsr presente = concesionaria; ausente = particular. Se deja
            # seller_type en None cuando no hay codUsr en vez de asumir
            # 'particular', porque en la ficha el link puede no renderizarse.
            seller_type="car_dealer" if dealer_id else None,
            seller_id=dealer_id,
            province_raw=province,
            item_status=None,
            financing_initial_payment=None,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            s3_key=response.meta.get("s3_key"),
            http_status=response.meta.get("http_status"),
            parser_version=response.meta.get("parser_version"),
        )
