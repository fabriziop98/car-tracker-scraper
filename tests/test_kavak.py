"""Tests de la fuente Kavak (wdxtkg3hc5).

Contra fixtures HTML REALES capturados del sitio, no sinteticos - mismo
criterio que test_deruedas.py/test_autocosmos.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.extraction.kavak import (
    card_km,
    card_price,
    card_version,
    detail_analytics,
    detail_breadcrumb,
    detail_dynamic,
    detail_price,
    detail_province,
    detail_sucursal,
    iter_cards,
)
from car_tracker_scraper.extraction.motordil import extract_rsc_payload
from car_tracker_scraper.items import ListingDetailItem, ListingSummaryItem
from car_tracker_scraper.spiders.kavak_detail import KavakDetailSpider
from car_tracker_scraper.spiders.kavak_discovery import KavakDiscoverySpider

FIXTURES = Path(__file__).parent / "fixtures"
LISTADO_URL = "https://www.kavak.com/ar/usados?page=1"
DETAIL_URL = "https://www.kavak.com/ar/venta/toyota-yaris-15_s_cvt-hatchback-2019?id=543130"
DEAD_URL = "https://www.kavak.com/ar/venta/fake-model-slug-0000?id=999999999"


def _listado_response(page: int = 1) -> HtmlResponse:
    request = Request(url=LISTADO_URL, meta={"page": page})
    return HtmlResponse(
        url=LISTADO_URL,
        body=(FIXTURES / "kavak_listado.html").read_bytes(),
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


# --- RSC flight stream de la grilla --------------------------------------------


def test_iter_cards_finds_every_card_on_the_page():
    html = (FIXTURES / "kavak_listado.html").read_text(encoding="utf-8")
    payload = extract_rsc_payload(html)
    cards = list(iter_cards(payload))

    # 30 por pagina, confirmado contra el sitio real 2026-09-02.
    assert len(cards) == 30


def test_card_price_is_the_cash_price_not_the_financed_one():
    """Regresion del hallazgo principal de esta fuente (wdxtkg3hc5): la
    primera version de card_price() devolvia `analytics.car_price`, que para
    un aviso CON promocion de financiacion es el precio financiado (mas
    bajo), no el de contado. Verificado contra el Toyota Yaris real: el
    contado (30640000) coincide exactamente con lo que trae su propia ficha
    de detalle (ver test_detail_price_matches_the_grid_card_exactly)."""
    html = (FIXTURES / "kavak_listado.html").read_text(encoding="utf-8")
    payload = extract_rsc_payload(html)
    cards = {c["id"]: c for c in iter_cards(payload)}

    yaris = cards["543130"]
    assert yaris["labelTop"] == "Precio desde"  # tiene promocion de financiacion
    assert card_price(yaris) == ("30640000", "ARS")
    assert yaris["analytics"]["car_price"] == "30312000"  # el financiado - NO es esto lo que devuelve card_price()

    kangoo = cards["543535"]
    assert kangoo["labelTop"] == "Precio de contado"  # sin promocion
    assert card_price(kangoo) == ("23960000", "ARS")


def test_card_fields_come_from_title_and_subtitle():
    html = (FIXTURES / "kavak_listado.html").read_text(encoding="utf-8")
    payload = extract_rsc_payload(html)
    kangoo = next(c for c in iter_cards(payload) if c["id"] == "543535")

    assert kangoo["title"] == "Renault • Kangoo"
    assert card_km(kangoo) == 68500
    assert card_version(kangoo) == "1.6 2A"


# --- Discovery spider -----------------------------------------------------------


def test_discovery_emits_one_item_per_card():
    spider = KavakDiscoverySpider()
    items = [r for r in spider.parse(_listado_response()) if isinstance(r, ListingSummaryItem)]

    assert len(items) == 30
    assert all(i["source"] == "kavak" for i in items)
    assert all(i["url"].startswith("https://www.kavak.com/ar/venta/") for i in items)
    assert all(i["financing_initial_payment"] is None for i in items), "Kavak no tiene anticipos, ver extraction/kavak.py"


def test_discovery_url_always_carries_the_stock_id():
    """Regresion del hallazgo critico de esta fuente: la URL de la tarjeta SIN
    `?id=` no resuelve al auto correcto (verificado en vivo, ver el docstring
    de card_detail_url() en extraction/kavak.py - un Gol Trend PACK I devolvio
    un TRENDLINE distinto). Cada item de Discovery tiene que llevar `?id=` con
    el mismo id que su propio source_listing_key, o Detail va a traer datos de
    otro auto."""
    spider = KavakDiscoverySpider()
    items = [r for r in spider.parse(_listado_response()) if isinstance(r, ListingSummaryItem)]

    for item in items:
        assert f"?id={item['source_listing_key']}" in item["url"], (
            f"{item['source_listing_key']}: URL sin el id correcto ({item['url']})"
        )


def test_discovery_follows_pagination_via_page_param():
    spider = KavakDiscoverySpider()
    requests = [r for r in spider.parse(_listado_response()) if isinstance(r, Request)]

    assert len(requests) == 1
    assert requests[0].url == "https://www.kavak.com/ar/usados?page=2"


def test_discovery_stops_when_a_page_brings_no_new_ids():
    spider = KavakDiscoverySpider()
    response = _listado_response(page=5)
    payload = extract_rsc_payload(response.text)
    seen = {c["id"] for c in iter_cards(payload)}
    response.meta["seen_ids"] = seen

    assert [r for r in spider.parse(response) if isinstance(r, Request)] == []


def test_discovery_start_url_is_page_one():
    spider = KavakDiscoverySpider()
    requests = _drain_async_gen(spider.start())

    assert len(requests) == 1
    assert requests[0].url == "https://www.kavak.com/ar/usados?page=1"


def test_discovery_resolves_curated_brand_slug():
    spider = KavakDiscoverySpider(marcas="toyota,renault")
    items = [r for r in spider.parse(_listado_response()) if isinstance(r, ListingSummaryItem)]

    toyota_items = [i for i in items if i["title_raw"].startswith("Toyota")]
    assert toyota_items and all(i["marca"] == "toyota" for i in toyota_items)


# --- Detail spider ----------------------------------------------------------------


def test_detail_extracts_catalog_fields():
    spider = KavakDetailSpider(urls=DETAIL_URL)
    items = list(spider.parse(_detail_response("kavak_detail.html", DETAIL_URL)))

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ListingDetailItem)
    assert item["source_listing_key"] == "543130"
    assert item["brand_raw"] == "Toyota"
    assert item["model_raw"] == "Yaris"
    assert item["version_raw"] == "1.5 S CVT"
    assert item["year_raw"] == 2019
    assert item["km"] == 46807
    assert item["color"] == "Blanco"
    assert item["fuel_type_raw"] == "Nafta"
    assert item["transmission_raw"] == "Automático"
    assert item["province_raw"] == "Buenos Aires"
    assert item["seller_type"] == "car_dealer"
    assert item["seller_name"] == "Kavak"


def test_detail_price_matches_the_grid_card_exactly():
    """Cruce independiente: el precio de contado que da la ficha (30640000)
    tiene que ser EL MISMO que da su propia tarjeta de grilla (ver
    test_card_price_is_the_cash_price_not_the_financed_one) - asi se detecto
    originalmente que analytics.car_price de la grilla estaba mal."""
    spider = KavakDetailSpider(urls=DETAIL_URL)
    items = list(spider.parse(_detail_response("kavak_detail.html", DETAIL_URL)))

    assert (items[0]["price_amount"], items[0]["price_currency"]) == ("30640000", "ARS")


def test_detail_analytics_ignores_the_financed_price():
    html = (FIXTURES / "kavak_detail.html").read_text(encoding="utf-8")
    payload = extract_rsc_payload(html)
    analytics = detail_analytics(payload)

    assert analytics["car_price_financing_final"] == 30312000  # el financiado, distinto
    assert detail_price(analytics) == ("30640000", "ARS")


def test_detail_dynamic_gives_the_warehouse_province():
    html = (FIXTURES / "kavak_detail.html").read_text(encoding="utf-8")
    payload = extract_rsc_payload(html)
    dynamic = detail_dynamic(payload)

    assert dynamic is not None
    assert detail_province(dynamic) == "Buenos Aires"
    assert detail_sucursal(dynamic) == "Kavak TOM"
    # mismo precio de contado, triple-confirmado entre analytics/dynamic/grilla
    assert dynamic["price"] == 30640000


def test_detail_breadcrumb_reads_the_schema_org_list():
    html = (FIXTURES / "kavak_detail.html").read_text(encoding="utf-8")
    payload = extract_rsc_payload(html)

    assert detail_breadcrumb(payload) == "AUTOS SEMINUEVOS > TOYOTA > YARIS > 2019 > 1.5 S CVT"


# --- el hallazgo del aviso caido con HTTP 200 ----------------------------------


def test_is_dead_detects_a_nonexistent_id_despite_http_200():
    """Regresion del hallazgo central del Detail de esta fuente: Kavak NO usa
    404 para un id inexistente, siempre devuelve 200 con un evento
    `vip_viewed` de 'empty state' (car_id: null). El default de
    BaseDetailSpider (basado en status code) no alcanza - hace falta is_dead()
    propio basado en contenido."""
    spider = KavakDetailSpider(urls=DEAD_URL)
    dead_response = _detail_response("kavak_detail_dead.html", DEAD_URL)
    real_response = _detail_response("kavak_detail.html", DETAIL_URL)

    assert dead_response.status == 200  # la trampa: no es un 404
    assert spider.is_dead(dead_response) is True
    assert spider.is_dead(real_response) is False


def test_dead_listing_yields_nothing():
    spider = KavakDetailSpider(urls=DEAD_URL)
    items = list(spider.parse(_detail_response("kavak_detail_dead.html", DEAD_URL)))

    assert items == []


def test_empty_state_analytics_has_car_id_key_but_null_value():
    """Por que el chequeo de detail_analytics() es `obj.get('car_id')` y no
    `'car_id' in obj` - la clave esta presente incluso en el estado vacio."""
    html = (FIXTURES / "kavak_detail_dead.html").read_text(encoding="utf-8")
    payload = extract_rsc_payload(html)

    assert '"car_id":null' in payload
    assert detail_analytics(payload) is None


# --- filtro de 0km ------------------------------------------------------------


def test_detail_drops_zero_km_units():
    html = (FIXTURES / "kavak_detail.html").read_text(encoding="utf-8")
    spider = KavakDetailSpider(urls=DETAIL_URL)

    assert len(list(spider.parse(_detail_response("kavak_detail.html", DETAIL_URL)))) == 1

    # El HTML crudo trae el JSON con comillas escapadas (esta adentro de un
    # chunk del flight stream, que a su vez es un string JSON-encoded).
    as_zero = html.replace('\\"car_mileage\\":46807', '\\"car_mileage\\":0')
    assert as_zero != html, "no se pudo simular 0km en el fixture"
    response = HtmlResponse(url=DETAIL_URL, body=as_zero.encode(), encoding="utf-8",
                            request=Request(url=DETAIL_URL))
    assert list(spider.parse(response)) == []
