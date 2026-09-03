"""Tests de la fuente Autocosmos (wdxtkg3hc4).

Contra fixtures HTML REALES capturados del sitio, no sinteticos - mismo
criterio que test_deruedas.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.extraction.autocosmos import (
    card_brand,
    card_km,
    card_location,
    card_model,
    card_price,
    card_url,
    card_version,
    card_year,
    detail_brand,
    detail_breadcrumb,
    detail_color,
    detail_model,
    detail_seller_type,
    detail_title,
    detail_vehicle,
    detail_version,
    is_financed_price,
    iter_cards,
    listing_key_from_url,
)
from car_tracker_scraper.items import ListingDetailItem, ListingSummaryItem
from car_tracker_scraper.spiders.autocosmos_detail import AutocosmosDetailSpider
from car_tracker_scraper.spiders.autocosmos_discovery import AutocosmosDiscoverySpider

FIXTURES = Path(__file__).parent / "fixtures"
LISTADO_URL = "https://www.autocosmos.com.ar/auto/usado"
DETAIL_URL_CONTADO = "https://www.autocosmos.com.ar/auto/usado/nissan/march/active/5098b1aef5f44aaea6567c16d79da7f2"
DETAIL_URL_CUOTAS = "https://www.autocosmos.com.ar/auto/usado/toyota/corolla-cross-hybrid/hibrida-18-seg-ecvt/526c497655b24adca245e8c41e27ab93"


def _listado_response(page: int = 1) -> HtmlResponse:
    request = Request(url=LISTADO_URL, meta={"page": page})
    return HtmlResponse(
        url=LISTADO_URL,
        body=(FIXTURES / "autocosmos_listado.html").read_bytes(),
        encoding="utf-8",
        request=request,
    )


def _detail_response(fixture: str, url: str) -> HtmlResponse:
    return HtmlResponse(
        url=url,
        body=(FIXTURES / fixture).read_bytes(),
        encoding="utf-8",
        request=Request(url=url),
    )


def _drain_async_gen(agen):
    async def _collect():
        return [item async for item in agen]

    return asyncio.run(_collect())


# --- microdata de la grilla ---------------------------------------------------


def test_iter_cards_finds_every_card_on_the_page():
    html = (FIXTURES / "autocosmos_listado.html").read_text(encoding="utf-8")
    cards = list(iter_cards(html))

    # 48 por pagina, confirmado contra el sitio real 2026-09-02.
    assert len(cards) == 48


def test_card_fields_come_from_microdata_and_css_classes():
    html = (FIXTURES / "autocosmos_listado.html").read_text(encoding="utf-8")
    card = next(iter_cards(html))

    assert card_brand(card) == "KIA"
    assert card_model(card) == "Carnival"
    assert card_version(card) == "EX 2.2 CRDi Premium Aut"
    assert card_year(card) == 2019
    assert card_km(card) == 128000
    assert card_location(card) == ("1a. Seccion", "Mendoza")
    assert listing_key_from_url(card_url(card)) == "348e7ab411e74c0db7d1e07cc80fc5bd"


# --- el hallazgo del anticipo --------------------------------------------------


def test_grid_distinguishes_financed_cards_from_real_price_cards():
    """Regresion del hallazgo principal de esta fuente (wdxtkg3hc4): una parte
    de los avisos "financiados en cuotas" muestran el ANTICIPO como si fuera
    el precio del auto. El fixture real mezcla los dos casos (48 tarjetas,
    30 financiadas medido 2026-09-02) - exactamente por eso se eligio como
    fixture de grilla, en vez de una pagina ya filtrada por seccion."""
    html = (FIXTURES / "autocosmos_listado.html").read_text(encoding="utf-8")
    cards = list(iter_cards(html))

    financed = [c for c in cards if is_financed_price(c)]
    normal = [c for c in cards if not is_financed_price(c)]
    assert financed, "el fixture ya no tiene tarjetas financiadas"
    assert normal, "el fixture ya no tiene tarjetas de precio real"

    for card in financed:
        amount, currency, anticipo = card_price(card)
        assert (amount, currency) == (None, None), "un anticipo no debe pisar price_amount"
        assert anticipo is not None

    for card in normal:
        amount, currency, anticipo = card_price(card)
        assert amount is not None and currency is not None
        assert anticipo is None


def test_financed_card_matches_the_real_example():
    """Chevrolet Tracker 2025, anticipo $4.000.000 - el ejemplo real que
    origino el hallazgo (ver docstring de extraction/autocosmos.py)."""
    html = (FIXTURES / "autocosmos_listado.html").read_text(encoding="utf-8")
    card = next(c for c in iter_cards(html) if is_financed_price(c))

    assert card_brand(card) == "Chevrolet"
    assert card_price(card) == (None, None, "4000000")


def test_detail_page_price_specification_wraps_both_cases():
    """A diferencia de la grilla, en la FICHA de detalle el envoltorio
    itemtype PriceSpecification aparece en los dos casos (normal y
    financiado) - por eso is_financed_price() se ancla en la clase CSS
    `m-anticipo` y no en el itemtype. Este test es la regresion de ese
    hallazgo: si algun dia se cambia el detector para volver a itemtype, el
    caso "contado" de aca abajo se rompe."""
    contado = detail_vehicle((FIXTURES / "autocosmos_detail_contado.html").read_text(encoding="utf-8"))
    cuotas = detail_vehicle((FIXTURES / "autocosmos_detail_cuotas.html").read_text(encoding="utf-8"))

    assert contado.css('[itemtype*="PriceSpecification"]'), "el fixture ya no envuelve el precio normal"
    assert not is_financed_price(contado)
    assert is_financed_price(cuotas)


# --- Discovery spider -----------------------------------------------------------


def test_discovery_emits_one_item_per_card():
    spider = AutocosmosDiscoverySpider()
    items = [r for r in spider.parse(_listado_response()) if isinstance(r, ListingSummaryItem)]

    assert len(items) == 48
    assert all(i["source"] == "autocosmos" for i in items)
    assert all(i["url"].startswith("https://www.autocosmos.com.ar/auto/usado/") for i in items)


def test_discovery_keeps_financed_cards_with_price_amount_none():
    """No se filtran por seccion=precio-final (ver wdxtkg3hc4): un aviso
    financiado se emite igual, sin price_amount, en vez de perderse."""
    spider = AutocosmosDiscoverySpider()
    items = [r for r in spider.parse(_listado_response()) if isinstance(r, ListingSummaryItem)]

    financed = [i for i in items if i["financing_initial_payment"]]
    assert len(financed) == 30
    assert all(i["price_amount"] is None for i in financed)
    assert all(i["price_currency"] is None for i in financed)


def test_discovery_follows_pagination_via_pidx():
    spider = AutocosmosDiscoverySpider()
    requests = [r for r in spider.parse(_listado_response()) if isinstance(r, Request)]

    assert len(requests) == 1
    assert requests[0].url == f"{LISTADO_URL}?pidx=2"


def test_discovery_stops_when_a_page_brings_no_new_ids():
    spider = AutocosmosDiscoverySpider()
    response = _listado_response(page=7)
    seen = {listing_key_from_url(card_url(c)) for c in iter_cards(response.text)}
    response.meta["seen_ids"] = seen

    assert [r for r in spider.parse(response) if isinstance(r, Request)] == []


def test_discovery_start_url_is_the_unfiltered_listing():
    spider = AutocosmosDiscoverySpider()
    requests = _drain_async_gen(spider.start())

    assert len(requests) == 1
    assert requests[0].url == LISTADO_URL


def test_discovery_resolves_curated_brand_slug():
    spider = AutocosmosDiscoverySpider(marcas="kia,chevrolet")
    items = [r for r in spider.parse(_listado_response()) if isinstance(r, ListingSummaryItem)]

    kia_items = [i for i in items if i["title_raw"].startswith("KIA")]
    assert kia_items and all(i["marca"] == "kia" for i in kia_items)


# --- Detail spider ----------------------------------------------------------------


def test_detail_extracts_catalog_fields_for_a_real_price_listing():
    spider = AutocosmosDetailSpider(urls=DETAIL_URL_CONTADO)
    items = list(spider.parse(_detail_response("autocosmos_detail_contado.html", DETAIL_URL_CONTADO)))

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ListingDetailItem)
    assert item["source_listing_key"] == "5098b1aef5f44aaea6567c16d79da7f2"
    assert item["brand_raw"] == "Nissan"
    assert item["model_raw"] == "March"
    assert item["version_raw"] == "Active"
    assert item["year_raw"] == 2016
    assert item["km"] == 110000
    assert (item["price_amount"], item["price_currency"]) == ("13900000", "ARS")
    assert item["financing_initial_payment"] is None
    assert item["province_raw"] == "Ciudad Autónoma Buenos Aires"
    assert item["seller_type"] == "particular"
    assert item["color"] == "Gris"


def test_detail_extracts_the_anticipo_instead_of_a_fake_price():
    spider = AutocosmosDetailSpider(urls=DETAIL_URL_CUOTAS)
    items = list(spider.parse(_detail_response("autocosmos_detail_cuotas.html", DETAIL_URL_CUOTAS)))

    assert len(items) == 1
    item = items[0]
    assert item["source_listing_key"] == "526c497655b24adca245e8c41e27ab93"
    assert item["brand_raw"] == "Toyota"
    assert item["model_raw"] == "Corolla Cross Hybrid"
    assert item["version_raw"] == "Híbrida 1.8 SEG eCVT"
    # el hallazgo central: NO hay price_amount fabricado a partir del anticipo
    assert (item["price_amount"], item["price_currency"]) == (None, None)
    assert item["financing_initial_payment"] == "21000000"
    assert item["seller_type"] == "car_dealer"
    assert item["province_raw"] == "Córdoba"


def test_detail_yields_nothing_when_the_vehicle_block_is_missing():
    spider = AutocosmosDetailSpider(urls=DETAIL_URL_CONTADO)
    response = HtmlResponse(
        url=DETAIL_URL_CONTADO, body=b"<html><body>aviso caido</body></html>",
        encoding="utf-8", request=Request(url=DETAIL_URL_CONTADO),
    )

    assert list(spider.parse(response)) == []


def test_detail_seller_type_reads_the_ad_targeting_meta():
    contado_html = (FIXTURES / "autocosmos_detail_contado.html").read_text(encoding="utf-8")
    cuotas_html = (FIXTURES / "autocosmos_detail_cuotas.html").read_text(encoding="utf-8")

    assert detail_seller_type(contado_html) == "particular"
    assert detail_seller_type(cuotas_html) == "car_dealer"
    assert detail_seller_type("<html></html>") is None


def test_detail_breadcrumb_is_read_from_outside_the_vehicle_block():
    """El nav de breadcrumb vive fuera del itemscope Car - si detail_breadcrumb
    escopeara al Selector que devuelve detail_vehicle() en vez del HTML
    entero, esto devolveria None siempre."""
    html = (FIXTURES / "autocosmos_detail_contado.html").read_text(encoding="utf-8")

    assert detail_breadcrumb(html) == "Usados > Autos en venta > Usados > Nissan > March"


# --- filtro de 0km ------------------------------------------------------------


def test_detail_drops_zero_km_units():
    html = (FIXTURES / "autocosmos_detail_contado.html").read_text(encoding="utf-8")
    spider = AutocosmosDetailSpider(urls=DETAIL_URL_CONTADO)

    assert len(list(spider.parse(_detail_response("autocosmos_detail_contado.html", DETAIL_URL_CONTADO)))) == 1

    as_zero = html.replace('content="KMT 110000"', 'content="KMT 0"')
    assert as_zero != html, "no se pudo simular 0km en el fixture"
    response = HtmlResponse(url=DETAIL_URL_CONTADO, body=as_zero.encode(), encoding="utf-8",
                            request=Request(url=DETAIL_URL_CONTADO))
    assert list(spider.parse(response)) == []
