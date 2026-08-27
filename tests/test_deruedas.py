"""Tests de la fuente DeRuedas (wdxtkg39pw).

Contra fixtures HTML REALES capturados del sitio, no sinteticos - mismo criterio
que test_motordil.py y test_mercadolibre_detail_spider.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from parsel import Selector
from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.extraction.deruedas import (
    card_id,
    detail_published_price,
    detail_version,
    detail_vehicle,
    iter_cards,
    nested_prop,
    parse_km,
    parse_year,
    prop,
    published_price,
    seller_id,
    version_text,
)
from car_tracker_scraper.items import ListingDetailItem, ListingSummaryItem
from car_tracker_scraper.spiders.deruedas_detail import DeruedasDetailSpider
from car_tracker_scraper.spiders.deruedas_discovery import (
    DeruedasDiscoverySpider,
    marca_param,
)

FIXTURES = Path(__file__).parent / "fixtures"
RESULTS_URL = "https://www.deruedas.com.ar/busCraw.asp?segmento=0&marca=Audi&weNeed=divAll&pag=1"
DETAIL_URL = "https://www.deruedas.com.ar/vendo/Audi/Q5/Usado/Cordoba?cod=802906"


def _results_response(page: int = 1) -> HtmlResponse:
    request = Request(url=RESULTS_URL, meta={"marca": "audi", "page": page})
    return HtmlResponse(
        url=RESULTS_URL,
        body=(FIXTURES / "deruedas_results.html").read_bytes(),
        encoding="utf-8",
        request=request,
    )


def _detail_response() -> HtmlResponse:
    return HtmlResponse(
        url=DETAIL_URL,
        body=(FIXTURES / "deruedas_detail.html").read_bytes(),
        encoding="utf-8",
        request=Request(url=DETAIL_URL),
    )


def _drain_async_gen(agen):
    async def _collect():
        return [item async for item in agen]

    return asyncio.run(_collect())


# --- microdata de la grilla ---------------------------------------------------


def test_iter_cards_finds_every_card_on_the_page():
    html = (FIXTURES / "deruedas_results.html").read_text(encoding="utf-8")
    cards = list(iter_cards(html))

    # 30 por pagina, confirmado en Fase 0 y en el fixture real de 2026-08-27.
    assert len(cards) == 30
    assert card_id(cards[0]) == "802906"


def test_card_fields_come_from_microdata_itemprops():
    html = (FIXTURES / "deruedas_results.html").read_text(encoding="utf-8")
    card = next(iter_cards(html))

    assert prop(card, "brand") == "Audi"
    assert prop(card, "model") == "Q5"
    assert parse_year(prop(card, "vehicleModelDate")) == 2014
    assert parse_km(prop(card, "mileageFromOdometer")) == 109000
    assert prop(card, "url").endswith("?cod=802906")


def test_seller_id_is_none_for_private_sellers():
    """`?codUsr=` presente = concesionaria, ausente = particular. None a
    proposito y no un placeholder: el seller_id alimenta el fingerprint de
    dedup y un valor compartido agruparia a todos los particulares como si
    fueran el mismo vendedor."""
    html = (FIXTURES / "deruedas_results.html").read_text(encoding="utf-8")
    sellers = [seller_id(c) for c in iter_cards(html)]

    assert any(s is not None for s in sellers), "ninguna concesionaria en el fixture"
    assert any(s is None for s in sellers), "ningun particular en el fixture"


# --- el hallazgo del precio convertido ----------------------------------------


def test_microdata_price_is_converted_and_must_not_be_trusted():
    """Regresion del hallazgo mas importante de esta fuente (2026-08-27).

    DeRuedas declara `priceCurrency = ARS` en TODAS las tarjetas, pero mas de
    la mitad estan publicadas en USD y el microdata trae el monto ya convertido
    a pesos con la cotizacion interna del sitio. Tomar ese valor meteria un
    precio fabricado en la serie historica, plausible e indetectable.
    """
    html = (FIXTURES / "deruedas_results.html").read_text(encoding="utf-8")
    cards = list(iter_cards(html))

    # El microdata miente: dice ARS para todas.
    assert {nested_prop(c, "offers", "priceCurrency") for c in cards} == {"ARS"}

    # El texto publicado no: hay avisos en USD de verdad.
    published = [published_price(c) for c in cards]
    currencies = {cur for _, cur in published}
    assert currencies == {"ARS", "USD"}
    assert sum(1 for _, cur in published if cur == "USD") > 0

    # Y para un aviso en USD, el monto publicado NO es el del microdata.
    usd_card = next(c for c in cards if published_price(c)[1] == "USD")
    amount, _ = published_price(usd_card)
    assert amount != nested_prop(usd_card, "offers", "price")


def test_published_price_parses_both_currency_formats():
    html = (FIXTURES / "deruedas_results.html").read_text(encoding="utf-8")
    published = [published_price(c) for c in iter_cards(html)]

    assert all(amount and amount.isdigit() for amount, _ in published)
    assert all(cur in ("ARS", "USD") for _, cur in published)


def test_every_card_exposes_its_version():
    html = (FIXTURES / "deruedas_results.html").read_text(encoding="utf-8")
    versions = [version_text(c) for c in iter_cards(html)]

    assert all(versions), "alguna tarjeta sin a.versionLink"
    assert "S-tronic" in versions[0]


# --- Discovery spider ---------------------------------------------------------


def test_marca_param_capitalizes_each_word():
    assert marca_param("audi") == "Audi"
    assert marca_param("mercedes-benz") == "Mercedes-Benz"


def test_discovery_emits_one_item_per_card_with_published_price():
    spider = DeruedasDiscoverySpider(marcas="audi")
    items = [r for r in spider.parse(_results_response()) if isinstance(r, ListingSummaryItem)]

    assert len(items) == 30
    assert all(i["source"] == "deruedas" for i in items)
    assert all(i["url"].startswith("https://www.deruedas.com.ar/vendo/") for i in items)
    assert {i["price_currency"] for i in items} == {"ARS", "USD"}


def test_discovery_tags_items_with_the_curated_brand_slug():
    spider = DeruedasDiscoverySpider(marcas="audi")
    items = [r for r in spider.parse(_results_response()) if isinstance(r, ListingSummaryItem)]

    assert {i["marca"] for i in items} == {"audi"}


def test_discovery_sends_the_xhr_headers_when_following_pages():
    spider = DeruedasDiscoverySpider(marcas="audi")
    requests = [r for r in spider.parse(_results_response()) if isinstance(r, Request)]

    assert len(requests) == 1
    assert "pag=2" in requests[0].url
    assert requests[0].headers.get("X-Requested-With") == b"XMLHttpRequest"


def test_discovery_stops_when_a_page_brings_no_new_ids():
    """El corte mira ids NUEVOS, no solo 'vino vacia': quedo sin confirmar si
    el sitio repite la ultima pagina cuando `pag` se pasa del final, asi que el
    spider se banca las dos formas."""
    spider = DeruedasDiscoverySpider(marcas="audi")
    response = _results_response(page=2)
    seen = {card_id(c) for c in iter_cards(response.text)}
    response.meta["seen_ids"] = seen

    assert [r for r in spider.parse(response) if isinstance(r, Request)] == []


def test_discovery_start_urls_carry_the_xhr_headers():
    spider = DeruedasDiscoverySpider(marcas="audi,ford")
    requests = _drain_async_gen(spider.start())

    assert len(requests) == 2
    assert all("busCraw.asp" in r.url for r in requests)
    assert all(r.headers.get("X-Requested-With") == b"XMLHttpRequest" for r in requests)


# --- Detail spider ------------------------------------------------------------


def test_detail_page_uses_vehicle_itemtype_not_car():
    """La ficha usa schema.org/Vehicle y la grilla schema.org/Car - si se
    parsea la ficha con el selector de la grilla no encuentra nada."""
    html = (FIXTURES / "deruedas_detail.html").read_text(encoding="utf-8")

    assert list(iter_cards(html)) == []
    assert detail_vehicle(html) is not None


def test_detail_extracts_catalog_fields_and_published_price():
    spider = DeruedasDetailSpider(urls=DETAIL_URL)
    items = list(spider.parse(_detail_response()))

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ListingDetailItem)
    assert item["source_listing_key"] == "802906"
    assert item["brand_raw"] == "Audi"
    assert item["model_raw"] == "Q5"
    assert item["version_raw"] == "2.0T S-tronic Quattro 225cv"
    assert item["year_raw"] == 2014
    assert item["km"] == 109000
    # publicado en USD, no los 37.852.500 ARS que declara su microdata
    assert (item["price_amount"], item["price_currency"]) == ("24500", "USD")
    assert item["province_raw"] == "Córdoba"
    assert item["seller_id"] == "294391"
    assert item["seller_type"] == "car_dealer"


def test_detail_version_is_recovered_from_the_description_text():
    assert detail_version("Encontrá tu Audi Q5 2.0T S-tronic en deRuedas.", "Audi", "Q5") == "2.0T S-tronic"
    # sin la forma esperada NO se inventa un trim
    assert detail_version("texto raro cualquiera", "Audi", "Q5") is None
    assert detail_version(None, "Audi", "Q5") is None


def test_detail_published_price_reads_the_plain_html_label():
    html = (FIXTURES / "deruedas_detail.html").read_text(encoding="utf-8")

    assert detail_published_price(html) == ("24500", "USD")
    assert detail_published_price("<p>sin precio</p>") == (None, None)


def test_detail_yields_nothing_when_the_vehicle_block_is_missing():
    spider = DeruedasDetailSpider(urls=DETAIL_URL)
    response = HtmlResponse(
        url=DETAIL_URL, body=b"<html><body>aviso caido</body></html>",
        encoding="utf-8", request=Request(url=DETAIL_URL),
    )

    assert list(spider.parse(response)) == []
