"""Detail Fetch spider para MercadoLibre.

Trae la ficha completa de avisos ya conocidos (via source_listing_key/url que
salieron de mercadolibre_discovery). Caro, priorizado por tier A/B/C (seccion
3.4 del doc de arquitectura) - por eso este spider recibe una lista de URLs
en vez de descubrirlas el mismo.

Uso:
    scrapy crawl mercadolibre_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

import scrapy

from car_tracker_scraper.extraction.mercadolibre import extract_json_ld, extract_nordic_ctx
from car_tracker_scraper.items import ListingDetailItem


class MercadolibreDetailSpider(scrapy.Spider):
    name = "mercadolibre_detail"
    allowed_domains = ["auto.mercadolibre.com.ar"]

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

    def start_requests(self):
        for url in self._urls:
            yield scrapy.Request(url, callback=self.parse)

    def parse(self, response):
        html = response.text
        vehicle = extract_json_ld(html, "Vehicle") or {}
        offers = vehicle.get("offers") or {}

        # initialState.components confirmado contra un fixture real
        # (tests/fixtures/ml_detail_sample.html, un Fiat Palio real de ML) -
        # ya no es best-effort. Ojo: cuelga de appProps.pageProps, no de
        # appProps directamente como en el listado (paginas distintas, mismo
        # mecanismo __NORDIC_RENDERING_CTX__).
        ctx = extract_nordic_ctx(html)
        components = ctx.get("appProps", {}).get("pageProps", {}).get("initialState", {}).get(
            "components", {}
        )

        seller_card = components.get("seller_card_motors") or {}
        event_data = (
            ((seller_card.get("phone_link") or {}).get("track") or {}).get("melidata_event") or {}
        ).get("event_data") or {}

        item_proximity_rows = (components.get("item_proximity") or {}).get("content_rows") or []
        location_text = item_proximity_rows[0]["label"]["text"] if item_proximity_rows else None

        financing = components.get("initial_payment_amount") or {}

        yield ListingDetailItem(
            source="mercadolibre",
            source_listing_key=vehicle.get("sku"),
            url=response.url,
            brand_raw=vehicle.get("brand"),
            color=vehicle.get("color"),
            fuel_type_raw=vehicle.get("fuelType"),
            number_of_doors=vehicle.get("numberOfDoors"),
            transmission_raw=vehicle.get("vehicleTransmission"),
            item_condition=vehicle.get("itemCondition"),
            price_amount=offers.get("price"),
            price_currency=offers.get("priceCurrency"),
            price_valid_until=offers.get("priceValidUntil"),
            breadcrumb_raw=_breadcrumb_text(extract_json_ld(html, "BreadcrumbList")),
            subtitle_raw=(components.get("header") or {}).get("subtitle"),
            location_raw=location_text,
            highlighted_specs_raw=(components.get("highlighted_specs_attrs") or {}).get("components"),
            seller_name=(seller_card.get("seller_name") or {}).get("title", {}).get("text"),
            seller_type=event_data.get("item_seller_type"),
            seller_id=event_data.get("seller_id"),
            province_raw=event_data.get("state"),
            item_status=event_data.get("item_status"),
            financing_initial_payment=financing.get("title", {}).get("text"),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )


def _breadcrumb_text(breadcrumb: dict | None) -> str | None:
    if not breadcrumb:
        return None
    items = sorted(breadcrumb.get("itemListElement", []), key=lambda el: el.get("position", 0))
    names = [el.get("name") or (el.get("item") or {}).get("name") for el in items]
    names = [n for n in names if n]
    return " > ".join(names) if names else None
