"""Detail Fetch spider para Autocosmos (wdxtkg3hc4).

Aviso caido = 404 limpio (confirmado contra el sitio real, mismo
comportamiento que Autocity) - alcanza con el default de BaseDetailSpider, sin
`is_dead()` propio.

Uso:
    scrapy crawl autocosmos_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

from car_tracker_scraper.extraction.autocosmos import (
    card_km,
    card_location,
    card_price,
    card_year,
    detail_breadcrumb,
    detail_brand,
    detail_color,
    detail_model,
    detail_seller_type,
    detail_title,
    detail_vehicle,
    detail_version,
    listing_key_from_url,
)
from car_tracker_scraper.spiders.base import BaseDetailSpider, landing_meta
from car_tracker_scraper.items import ListingDetailItem


class AutocosmosDetailSpider(BaseDetailSpider):
    name = "autocosmos_detail"
    allowed_domains = ["www.autocosmos.com.ar", "autocosmos.com.ar"]
    custom_settings = {
        # Mismo Crawl-delay: 20 que Discovery - ver ese docstring/custom_settings.
        "DOWNLOAD_DELAY": 20,
    }

    def parse(self, response):
        vehicle = detail_vehicle(response.text)
        if vehicle is None:
            self.logger.warning(
                "Sin bloque schema.org/Car en %s - aviso caido o cambio de formato",
                response.url,
            )
            return

        km = card_km(vehicle)
        if km == 0:
            self.logger.debug("0km, no es el segmento usado: %s", response.url)
            return

        city, province = card_location(vehicle)
        # card_price() sirve tal cual sobre el bloque Car de la ficha: mismo
        # itemprop/itemtype que en la grilla (ver detail_vehicle()). Si el
        # aviso esta "financiado en cuotas", price_amount/price_currency
        # quedan en None y financing_initial_payment trae el anticipo -
        # mitigacion del hallazgo de wdxtkg3hc4.
        price_amount, price_currency, financing_initial_payment = card_price(vehicle)

        yield ListingDetailItem(
            source="autocosmos",
            source_listing_key=listing_key_from_url(response.url),
            url=response.url,
            brand_raw=detail_brand(vehicle),
            model_raw=detail_model(vehicle),
            version_raw=detail_version(vehicle),
            year_raw=card_year(vehicle),
            km=km,
            title_raw=detail_title(vehicle),
            color=detail_color(vehicle),
            fuel_type_raw=None,  # la ficha no lo expone
            number_of_doors=None,
            transmission_raw=None,
            item_condition=vehicle.css('[itemprop="itemCondition"]::attr(content)').get(),
            price_amount=price_amount,
            price_currency=price_currency,
            price_valid_until=None,
            breadcrumb_raw=detail_breadcrumb(response.text),
            subtitle_raw=None,
            location_raw=", ".join(p for p in (city, province) if p) or None,
            highlighted_specs_raw=None,
            seller_name=None,  # el sitio no expone nombre de vendedor en la ficha
            seller_type=detail_seller_type(response.text),
            seller_id=None,  # idem: sin identificador de vendedor visible
            province_raw=province,
            item_status=None,
            financing_initial_payment=financing_initial_payment,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            **landing_meta(response),
        )
