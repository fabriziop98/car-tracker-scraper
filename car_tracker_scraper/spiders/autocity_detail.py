"""Detail Fetch spider para Autocity (wdxtkg39qx).

Todo el dato del vehiculo sale de los atributos `data-*` del
`<main class="ficha-producto-page">` (data-brand/data-model/data-ano/data-kms/
data-price/data-estado), que es tan estructurado como un JSON. La version/trim
no esta ahi pero si en el <title>. Ver extraction/autocity.py.

Ojo con el peso: la ficha real pesa ~2.7 MB, de los cuales el 95% son bundles
de JS inline que este parser no lee. El fixture de tests esta recortado por eso
(los <script> se sacaron), y por eso mismo Detail de esta fuente descarga
bastante mas por aviso que las otras tres aunque parsee menos.

Uso:
    scrapy crawl autocity_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

from parsel import Selector

from car_tracker_scraper.extraction.autocity import (
    ficha,
    parse_km,
    parse_price,
    parse_year,
    version_from_title,
)
from car_tracker_scraper.spiders.base import BaseDetailSpider, landing_meta
from car_tracker_scraper.items import ListingDetailItem


class AutocityDetailSpider(BaseDetailSpider):
    name = "autocity_detail"
    allowed_domains = ["autocity.com.ar", "www.autocity.com.ar"]

    def parse(self, response):
        html = response.text
        block = ficha(html)
        if block is None:
            # Aviso dado de baja o cambio de formato: no es un crash, no hay
            # item que emitir. Mismo criterio que las otras fuentes.
            self.logger.warning(
                "Sin <main class=ficha-producto-page> en %s - aviso caido o cambio de formato",
                response.url,
            )
            return

        estado = (block.attrib.get("data-estado") or "").strip().lower()
        # 0km fuera: el proyecto trackea USADOS. Discovery ya filtra por el path
        # de la URL (/catalogo/usados/ vs /catalogo/0km/); esto es la segunda
        # puerta, con el campo autoritativo de la propia ficha, para atajar un
        # aviso que haya cambiado de estado despues de descubrirse.
        if estado and estado != "usado":
            self.logger.debug("estado=%r, no es el segmento usado: %s", estado, response.url)
            return

        brand = (block.attrib.get("data-brand") or "").strip() or None
        model = (block.attrib.get("data-model") or "").strip() or None
        title = Selector(text=html).css("title::text").get()
        price_amount, price_currency = parse_price(block.attrib.get("data-price"))

        yield ListingDetailItem(
            source="autocity",
            source_listing_key=response.url.split("/catalogo/usados/", 1)[-1].strip("/") or None,
            url=response.url,
            brand_raw=brand,
            model_raw=model,
            version_raw=version_from_title(title, model),
            year_raw=parse_year(block.attrib.get("data-ano")),
            km=parse_km(block.attrib.get("data-kms")),
            title_raw=title,
            color=None,
            fuel_type_raw=None,
            number_of_doors=None,
            transmission_raw=None,
            item_condition=block.attrib.get("data-estado"),
            price_amount=price_amount,
            price_currency=price_currency,
            price_valid_until=None,
            breadcrumb_raw=None,
            subtitle_raw=None,
            location_raw=None,
            highlighted_specs_raw=None,
            # Autocity es una concesionaria oficial, no un marketplace de
            # terceros: todos los avisos son de la misma empresa
            # (data-compania="AUTOCITY"), asi que el vendedor es constante y no
            # aporta nada al fingerprint de dedup.
            seller_name=block.attrib.get("data-compania"),
            seller_type="car_dealer",
            seller_id=None,
            province_raw=None,
            item_status=None,
            financing_initial_payment=None,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            **landing_meta(response),
        )
