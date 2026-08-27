"""Test contra un fixture REAL (a diferencia de test_extraction_mercadolibre.py,
que usa datos sinteticos). El fixture es un HTML de detalle real de ML
guardado por Fabrizio via curl (ver README, seccion de pendientes)."""
from pathlib import Path

import pytest
from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.items import DeadListingItem
from car_tracker_scraper.spiders.mercadolibre_detail import MercadolibreDetailSpider

FIXTURE = Path(__file__).parent / "fixtures" / "ml_detail_sample.html"
FIXTURE_PARTICULAR = Path(__file__).parent / "fixtures" / "ml_detail_sample2.html"
FIXTURE_DEAD = Path(__file__).parent / "fixtures" / "ml_detail_dead_sample.html"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture real no disponible en este checkout")
def test_parse_real_detail_fixture():
    spider = MercadolibreDetailSpider(urls="https://auto.mercadolibre.com.ar/fake-url-for-test")
    url = "https://auto.mercadolibre.com.ar/MLA-3635810164-fiat-palio-weekend-adventure-16-locker-xtreme-2014-_JM"
    request = Request(url, meta={"s3_key": "mercadolibre/2026-07-31/test.html.gz", "http_status": 200, "parser_version": "test"})
    response = HtmlResponse(url=url, body=FIXTURE.read_bytes(), encoding="utf-8", request=request)

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


@pytest.mark.skipif(not FIXTURE_PARTICULAR.exists(), reason="fixture real no disponible en este checkout")
def test_parse_real_detail_fixture_particular_seller():
    # Regresion real (2026-07-31): un vendedor particular no tiene
    # seller_card_motors (ese componente solo aparece para concesionarias) -
    # antes de este fix, seller_name/seller_type/seller_id/province_raw/
    # item_status salian todos None para este caso.
    spider = MercadolibreDetailSpider(urls="https://auto.mercadolibre.com.ar/fake-url-for-test")
    url = "https://auto.mercadolibre.com.ar/MLA-3664412834-fiat-toro-20-volcano-4x4-at-_JM"
    request = Request(url, meta={"s3_key": "mercadolibre/2026-07-31/test.html.gz", "http_status": 200, "parser_version": "test"})
    response = HtmlResponse(url=url, body=FIXTURE_PARTICULAR.read_bytes(), encoding="utf-8", request=request)

    items = list(spider.parse(response))
    assert len(items) == 1
    item = items[0]

    assert item["source_listing_key"] == "MLA3664412834"
    assert item["seller_name"] == "Rodrigo Atilio"
    assert item["seller_type"] == "particular"
    assert item["seller_id"] == 67156401
    assert item["province_raw"] == "Mendoza"
    assert item["item_status"] == "active"


@pytest.mark.skipif(not FIXTURE_DEAD.exists(), reason="fixture real no disponible en este checkout")
def test_parse_real_dead_listing_fixture_yields_a_dead_signal_not_a_listing():
    # wdxtkg39vm: fixture real capturado por la landing zone (MinIO,
    # mercadolibre/2026-08-27/025146_a934231b.html.gz) de una URL de detalle
    # que ML responde con HTTP 200 pero sirviendo el buscador de la marca
    # ("Baic") en su lugar - ni Vehicle JSON-LD ni initialState.components.
    # Confirmado contra 400 HTMLs reales que este es el 100% de los casos
    # "sin item" de ML (ver comentario en mercadolibre_detail.py), no una
    # pagina de "publicacion finalizada" separada.
    spider = MercadolibreDetailSpider(urls="https://auto.mercadolibre.com.ar/fake-url-for-test")
    url = "https://auto.mercadolibre.com.ar/MLA-1713238925-baic-u5-15-plus-_JM"
    request = Request(url, meta={"s3_key": "mercadolibre/2026-08-27/test.html.gz", "http_status": 200, "parser_version": "test"})
    response = HtmlResponse(url=url, body=FIXTURE_DEAD.read_bytes(), encoding="utf-8", request=request)

    items = list(spider.parse(response))

    assert len(items) == 1
    assert isinstance(items[0], DeadListingItem)
    assert items[0]["source"] == "mercadolibre"
    assert items[0]["url"] == url
    assert items[0]["item_type"] == "dead_listing"
