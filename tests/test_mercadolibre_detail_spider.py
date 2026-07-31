"""Test contra un fixture REAL (a diferencia de test_extraction_mercadolibre.py,
que usa datos sinteticos). El fixture es un HTML de detalle real de ML
guardado por Fabrizio via curl (ver README, seccion de pendientes)."""
from pathlib import Path

import pytest
from scrapy.http import HtmlResponse

from car_tracker_scraper.spiders.mercadolibre_detail import MercadolibreDetailSpider

FIXTURE = Path(__file__).parent / "fixtures" / "ml_detail_sample.html"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture real no disponible en este checkout")
def test_parse_real_detail_fixture():
    spider = MercadolibreDetailSpider(urls="https://auto.mercadolibre.com.ar/fake-url-for-test")
    response = HtmlResponse(
        url="https://auto.mercadolibre.com.ar/MLA-3635810164-fiat-palio-weekend-adventure-16-locker-xtreme-2014-_JM",
        body=FIXTURE.read_bytes(),
        encoding="utf-8",
    )

    items = list(spider.parse(response))
    assert len(items) == 1
    item = items[0]

    assert item["source_listing_key"] == "MLA3635810164"
    assert item["brand_raw"] == "Fiat"
    assert item["color"] == "Marrón"
    assert item["fuel_type_raw"] == "Nafta"
    assert item["transmission_raw"] == "Manual"
    assert item["price_amount"] == 13990000
    assert item["price_currency"] == "ARS"

    assert item["subtitle_raw"] == "2014 | 110.000 km · Publicado hace 1 año"
    assert item["location_raw"] == "Godoy Cruz, Mendoza"

    assert item["seller_name"] == "Colonautomotores"
    assert item["seller_type"] == "car_dealer"
    assert item["seller_id"] == 232888281
    assert item["province_raw"] == "Mendoza"
    assert item["item_status"] == "active"

    assert item["financing_initial_payment"] == "Anticipo de $\xa07.000.000"  # \xa0 = non-breaking space, tal como lo formatea ML
    assert isinstance(item["highlighted_specs_raw"], list)
    assert len(item["highlighted_specs_raw"]) > 0
