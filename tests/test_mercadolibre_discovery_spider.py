import json

from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.spiders.mercadolibre_discovery import (
    MercadolibreDiscoverySpider,
    _is_zero_km,
    _normalize_url,
)


def _polycard(item_id: str, attributes: list[str], is_pad: bool = False, price_complements=None) -> dict:
    price = {"current_price": {"value": 5_000_000, "currency": "ARS"}}
    if price_complements is not None:
        price["price_complements"] = price_complements
    return {
        "id": "POLYCARD",
        "polycard": {
            "metadata": {
                "id": item_id,
                # Sin esquema, tal como lo devuelve ML de verdad (ver _normalize_url)
                "url": f"auto.mercadolibre.com.ar/{item_id}-some-slug",
                "is_pad": "true" if is_pad else "false",
                "category_id": "MLA1744",
                "domain_id": "MLA-CARS_AND_VANS",
            },
            "components": [
                {"type": "title", "title": {"text": "Fiat Palio"}},
                {"type": "price", "price": price},
                {"type": "attributes_list", "attributes_list": {"texts": attributes}},
                {"type": "location", "location": {"text": "Godoy Cruz, Mendoza"}},
            ],
        },
    }


def _build_html(polycards: list[dict]) -> bytes:
    ctx = {
        "appProps": {
            "sharedState": {
                "search": {
                    "results": polycards,
                    "pagination": {"pagination_nodes_url": []},
                }
            }
        }
    }
    html = f'<script id="__NORDIC_RENDERING_CTX__">_n.ctx.r={json.dumps(ctx)}</script>'
    return html.encode("utf-8")


def test_is_zero_km_matches_common_variants():
    assert _is_zero_km(["2027", "0 Km"])
    assert _is_zero_km(["2027", "0Km"])
    assert _is_zero_km(["2027", "0km"])
    assert not _is_zero_km(["2014", "184.000 Km"])
    assert not _is_zero_km(None)


def test_parse_filters_ads_and_zero_km():
    polycards = [
        _polycard("MLA1", ["2014", "184.000 Km"]),  # usado real, debe pasar
        _polycard("MLA2", ["2027", "0 Km"]),  # 0km, debe descartarse
        _polycard("MLA3", ["2018", "50.000 Km"], is_pad=True),  # ad, debe descartarse
    ]
    request = Request(
        url="https://autos.mercadolibre.com.ar/fiat?sb=all_mercadolibre",
        meta={"marca": "fiat", "page_count": 1},
    )
    response = HtmlResponse(
        url=request.url,
        body=_build_html(polycards),
        encoding="utf-8",
        request=request,
    )

    spider = MercadolibreDiscoverySpider(marcas="fiat")
    items = list(spider.parse(response))

    assert len(items) == 1
    assert items[0]["source_listing_key"] == "MLA1"
    assert items[0]["url"] == "https://auto.mercadolibre.com.ar/MLA1-some-slug"


def test_normalize_url_adds_scheme_when_missing():
    # Regresion real (2026-07-31): metadata.url viene sin esquema en
    # produccion. Sin esto, pasarle la URL de un item de Discovery a
    # mercadolibre_detail rompe (Scrapy exige URL absoluta).
    assert _normalize_url("auto.mercadolibre.com.ar/MLA-123-slug") == "https://auto.mercadolibre.com.ar/MLA-123-slug"
    assert _normalize_url("https://auto.mercadolibre.com.ar/MLA-123-slug") == "https://auto.mercadolibre.com.ar/MLA-123-slug"
    assert _normalize_url(None) is None


def test_parse_does_not_crash_when_price_complements_is_a_list():
    # Regresion real (2026-07-31): en produccion, price_complements salio
    # como list en al menos un item, no dict, y crasheaba con
    # AttributeError en price_complements.get(...).
    polycards = [
        _polycard("MLA1", ["2014", "184.000 Km"], price_complements=[{"some": "unexpected shape"}]),
    ]
    request = Request(url="https://autos.mercadolibre.com.ar/fiat", meta={"marca": "fiat", "page_count": 1})
    response = HtmlResponse(url=request.url, body=_build_html(polycards), encoding="utf-8", request=request)

    spider = MercadolibreDiscoverySpider(marcas="fiat")
    items = list(spider.parse(response))

    assert len(items) == 1
    assert items[0]["financing_initial_payment"] is None


def test_start_requests_url_does_not_match_known_robots_disallow_patterns():
    spider = MercadolibreDiscoverySpider(marcas="fiat")
    requests = list(spider.start_requests())
    assert len(requests) == 1
    url = requests[0].url
    assert url == "https://autos.mercadolibre.com.ar/fiat"
    # Reglas confirmadas del robots.txt real de autos.mercadolibre.com.ar
    # (User-agent: *) que ya nos mordieron una vez cada una:
    assert "_NoIndex_True" not in url
    assert "ITEM*CONDITION" not in url
    assert "mercadolibre" not in url.split("mercadolibre.com.ar", 1)[1]
