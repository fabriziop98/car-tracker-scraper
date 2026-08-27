"""Tests de la fuente Autocity (wdxtkg39qx).

Contra fixtures reales capturados del sitio. El de la ficha esta RECORTADO (se
le sacaron los <script>): la pagina real pesa 2.7 MB de los cuales el 95% son
bundles de JS inline que este parser no lee - todo el dato del vehiculo esta en
los data-* del <main class="ficha-producto-page"> y en el <title>.
"""
from __future__ import annotations

from pathlib import Path

from scrapy.http import HtmlResponse, Request, TextResponse

from car_tracker_scraper.extraction.autocity import (
    SITEMAP_URL,
    brand_from_url,
    ficha,
    iter_used_urls,
    listing_key_from_url,
    parse_km,
    parse_price,
    parse_year,
    version_from_title,
)
from car_tracker_scraper.items import ListingDetailItem, ListingSummaryItem
from car_tracker_scraper.spiders.autocity_detail import AutocityDetailSpider
from car_tracker_scraper.spiders.autocity_discovery import AutocityDiscoverySpider

FIXTURES = Path(__file__).parent / "fixtures"
DETAIL_URL = "https://autocity.com.ar/catalogo/usados/m-citroen/m-c5-aircross/1-6-thp-feel-pack-at6-l20/"


def _sitemap_response() -> TextResponse:
    return TextResponse(
        url=SITEMAP_URL,
        body=(FIXTURES / "autocity_sitemap.xml").read_bytes(),
        encoding="utf-8",
        request=Request(url=SITEMAP_URL),
    )


def _detail_response() -> HtmlResponse:
    return HtmlResponse(
        url=DETAIL_URL,
        body=(FIXTURES / "autocity_detail.html").read_bytes(),
        encoding="utf-8",
        request=Request(url=DETAIL_URL),
    )


# --- sitemap: el 0km se filtra por el path ------------------------------------


def test_sitemap_yields_only_used_car_urls():
    """La ventaja principal de esta fuente: el 0km se descarta por la URL
    (/catalogo/usados/ vs /catalogo/0km/), sin heuristica. Las otras tres
    fuentes necesitaron inferirlo del odometro o del texto."""
    xml = (FIXTURES / "autocity_sitemap.xml").read_text(encoding="utf-8")
    urls = list(iter_used_urls(xml))

    assert len(urls) == 282
    assert all("/catalogo/usados/" in u for u in urls)
    assert not any("/catalogo/0km/" in u for u in urls)


def test_sitemap_skips_the_wordpress_feed_url():
    # /catalogo/usados/feed/ matchea el prefijo pero es el RSS, no un auto.
    xml = (FIXTURES / "autocity_sitemap.xml").read_text(encoding="utf-8")
    assert not any(u.rstrip("/").endswith("/feed") for u in iter_used_urls(xml))


def test_brand_and_key_come_from_the_url_path():
    url = "https://autocity.com.ar/catalogo/usados/m-mercedes-benz/m-clase-a/200-l17/"
    # el segmento ya tiene forma de slug: NO convertir guiones a espacios
    assert brand_from_url(url) == "mercedes-benz"
    assert listing_key_from_url(url) == "m-mercedes-benz/m-clase-a/200-l17"
    assert brand_from_url("https://autocity.com.ar/catalogo/0km/m-peugeot/m-208/x/") is None


# --- parseo de la ficha -------------------------------------------------------


def test_price_keeps_the_published_currency():
    """No se asume ARS por contexto de pagina: si aparece un aviso en dolares,
    se respeta. Asumir la moneda fue el error que DeRuedas casi mete en la
    serie historica."""
    assert parse_price("$ 33.200.000") == ("33200000", "ARS")
    assert parse_price("U$S 24.500") == ("24500", "USD")
    assert parse_price("") == (None, None)
    assert parse_price("a convenir") == (None, None)


def test_km_and_year_parse_from_the_data_attributes():
    assert parse_km("70.700 km ") == 70700
    assert parse_km(None) is None
    assert parse_year("2021") == 2021
    assert parse_year(None) is None


def test_version_comes_from_the_title_and_is_validated_against_the_model():
    title = "1.6 thp feel pack at6 l20 - C5 Aircross - Citroen - Catálogo de Autos Usados | Autocity"
    assert version_from_title(title, "C5 Aircross") == "1.6 thp feel pack at6 l20"
    # si el <title> cambia de formato se devuelve None en vez de inventar trim
    assert version_from_title(title, "Otro Modelo") is None
    assert version_from_title("titulo raro", "C5 Aircross") is None
    assert version_from_title(None, "C5 Aircross") is None


def test_ficha_block_is_found_in_the_real_page():
    html = (FIXTURES / "autocity_detail.html").read_text(encoding="utf-8")
    block = ficha(html)

    assert block is not None
    assert block.attrib.get("data-brand") == "Citroen"
    assert block.attrib.get("data-estado") == "Usado"


# --- Discovery spider ---------------------------------------------------------


def test_discovery_emits_one_item_per_used_listing():
    spider = AutocityDiscoverySpider(marcas="citroen,ford,peugeot")
    items = [r for r in spider.parse(_sitemap_response()) if isinstance(r, ListingSummaryItem)]

    assert len(items) == 282
    assert all(i["source"] == "autocity" for i in items)
    assert all(i["url"].startswith("https://autocity.com.ar/catalogo/usados/") for i in items)


def test_discovery_does_not_paginate():
    """Unico Discovery del proyecto sin paginacion: el sitemap trae todo en una
    request, asi que parse() no debe encargar ninguna pagina mas."""
    spider = AutocityDiscoverySpider()
    assert [r for r in spider.parse(_sitemap_response()) if isinstance(r, Request)] == []


def test_discovery_emits_no_price_because_the_sitemap_has_none():
    # No es una perdida: los ListingSummaryItem nunca se publican a RabbitMQ,
    # solo alimentan el tracker (url + marca). El precio llega en Detail.
    spider = AutocityDiscoverySpider()
    items = [r for r in spider.parse(_sitemap_response()) if isinstance(r, ListingSummaryItem)]

    assert all(i["price_amount"] is None and i["price_currency"] is None for i in items)


# --- Detail spider ------------------------------------------------------------


def test_detail_extracts_everything_from_the_data_attributes():
    spider = AutocityDetailSpider(urls=DETAIL_URL)
    items = list(spider.parse(_detail_response()))

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ListingDetailItem)
    assert item["source"] == "autocity"
    assert item["brand_raw"] == "Citroen"
    assert item["model_raw"] == "C5 Aircross"
    assert item["version_raw"] == "1.6 thp feel pack at6 l20"
    assert item["year_raw"] == 2021
    assert item["km"] == 70700
    assert (item["price_amount"], item["price_currency"]) == ("33200000", "ARS")
    assert item["seller_type"] == "car_dealer"


def test_detail_drops_units_that_are_not_used():
    """Segunda puerta del filtro de 0km, con el campo autoritativo de la ficha
    (data-estado). Ataja un aviso que haya cambiado de estado despues de que
    Discovery lo descubriera por el path de la URL."""
    html = (FIXTURES / "autocity_detail.html").read_text(encoding="utf-8")
    as_new = html.replace('data-estado="Usado"', 'data-estado="0km"')
    assert as_new != html, "no se pudo simular data-estado 0km en el fixture"

    spider = AutocityDetailSpider(urls=DETAIL_URL)
    response = HtmlResponse(url=DETAIL_URL, body=as_new.encode(), encoding="utf-8",
                            request=Request(url=DETAIL_URL))

    assert list(spider.parse(response)) == []


def test_detail_yields_nothing_when_the_ficha_block_is_missing():
    spider = AutocityDetailSpider(urls=DETAIL_URL)
    response = HtmlResponse(url=DETAIL_URL, body=b"<html><body>aviso caido</body></html>",
                            encoding="utf-8", request=Request(url=DETAIL_URL))

    assert list(spider.parse(response)) == []
