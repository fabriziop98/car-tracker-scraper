import asyncio
import json

from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.items import ListingSummaryItem
from car_tracker_scraper.spiders.mercadolibre_discovery import (
    MercadolibreDiscoverySpider,
    _is_zero_km,
    _normalize_url,
    _resolve_marca,
)


def _drain_async_gen(agen):
    async def _collect():
        return [item async for item in agen]

    return asyncio.run(_collect())


def _polycard(
    item_id: str,
    attributes: list[str],
    is_pad: bool = False,
    price_complements=None,
    title: str = "Fiat Palio",
) -> dict:
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
                {"type": "title", "title": {"text": title}},
                {"type": "price", "price": price},
                {"type": "attributes_list", "attributes_list": {"texts": attributes}},
                {"type": "location", "location": {"text": "Godoy Cruz, Mendoza"}},
            ],
        },
    }


def _build_html(polycards: list[dict], pagination_nodes_url=None) -> bytes:
    ctx = {
        "appProps": {
            "sharedState": {
                "search": {
                    "results": polycards,
                    "pagination": {"pagination_nodes_url": pagination_nodes_url or []},
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


def test_parse_follows_pagination_dicts_and_skips_the_current_page():
    # Forma real confirmada 2026-07-31 (diagnose_pagination.py contra el
    # sitio real): pagination_nodes_url es una lista de DICTS
    # ({"value": "2", "url": "...", "is_actual_page": False}), no de strings
    # planos. El primer fix solo evitaba el crash pero de hecho nunca
    # seguia ninguna pagina (todo se salteaba por no ser str).
    polycards = [_polycard("MLA1", ["2014", "184.000 Km"])]
    request = Request(
        url="https://autos.mercadolibre.com.ar/fiat",
        meta={"marca": "fiat", "page_count": 1},
    )
    pagination_nodes = [
        {"value": "1", "url": "https://autos.mercadolibre.com.ar/fiat", "is_actual_page": True},
        {"value": "2", "url": "https://autos.mercadolibre.com.ar/fiat_Desde_49_NoIndex_True", "is_actual_page": False},
        {"garbage": "shape"},  # defensivo: no deberia crashear ante una forma inesperada
    ]
    response = HtmlResponse(
        url=request.url,
        body=_build_html(polycards, pagination_nodes_url=pagination_nodes),
        encoding="utf-8",
        request=request,
    )

    spider = MercadolibreDiscoverySpider(marcas="fiat", max_pages="2")
    results = list(spider.parse(response))

    items = [r for r in results if isinstance(r, ListingSummaryItem)]
    requests = [r for r in results if isinstance(r, Request)]
    assert len(items) == 1
    assert len(requests) == 1
    assert requests[0].url == "https://autos.mercadolibre.com.ar/fiat_Desde_49_NoIndex_True"


def test_parse_stops_when_no_node_advances_past_current_page():
    # Confirmado 2026-08-26 (diagnose_pagination.py contra Toyota real,
    # wdxtkg398b): en la ultima pagina real (value=42, offset 1969) los
    # resultados NO vienen vacios, pero ningun nodo de la ventana supera el
    # value de la pagina actual (is_actual_page). Esa es la señal real de
    # "se acabo el inventario", no una pagina vacia - sin ella, el spider
    # antes solo cortaba por un max_pages fijo, arbitrario e independiente
    # del inventario real de cada marca.
    polycards = [_polycard("MLA1", ["2014", "184.000 Km"])]
    pagination_nodes = [
        {"value": "32", "url": "https://autos.mercadolibre.com.ar/toyota_Desde_1489_NoIndex_True", "is_actual_page": False},
        {"value": "41", "url": "https://autos.mercadolibre.com.ar/toyota_Desde_1921_NoIndex_True", "is_actual_page": False},
        {"value": "42", "url": "https://autos.mercadolibre.com.ar/toyota_Desde_1969_NoIndex_True", "is_actual_page": True},
    ]
    request = Request(
        url="https://autos.mercadolibre.com.ar/toyota_Desde_1969_NoIndex_True",
        meta={"marca": "toyota", "page_count": 3},
    )
    response = HtmlResponse(
        url=request.url,
        body=_build_html(polycards, pagination_nodes_url=pagination_nodes),
        encoding="utf-8",
        request=request,
    )

    spider = MercadolibreDiscoverySpider(marcas="toyota")
    results = list(spider.parse(response))

    items = [r for r in results if isinstance(r, ListingSummaryItem)]
    requests = [r for r in results if isinstance(r, Request)]
    assert len(items) == 1
    assert requests == []


def test_parse_skips_nodes_at_or_before_current_page():
    # Los nodos con value <= la pagina actual son paginas ya recorridas
    # (hacia atras) - seguirlos no suma cobertura nueva, solo trafico extra
    # (aunque el dupefilter de Scrapy los frenaria igual en la practica).
    polycards = [_polycard("MLA1", ["2014", "184.000 Km"])]
    pagination_nodes = [
        {"value": "5", "url": "https://autos.mercadolibre.com.ar/toyota_Desde_193_NoIndex_True", "is_actual_page": False},
        {"value": "10", "url": "https://autos.mercadolibre.com.ar/toyota_Desde_433_NoIndex_True", "is_actual_page": True},
        {"value": "11", "url": "https://autos.mercadolibre.com.ar/toyota_Desde_481_NoIndex_True", "is_actual_page": False},
    ]
    request = Request(
        url="https://autos.mercadolibre.com.ar/toyota_Desde_433_NoIndex_True",
        meta={"marca": "toyota", "page_count": 2},
    )
    response = HtmlResponse(
        url=request.url,
        body=_build_html(polycards, pagination_nodes_url=pagination_nodes),
        encoding="utf-8",
        request=request,
    )

    spider = MercadolibreDiscoverySpider(marcas="toyota")
    results = list(spider.parse(response))

    requests = [r for r in results if isinstance(r, Request)]
    assert len(requests) == 1
    assert requests[0].url == "https://autos.mercadolibre.com.ar/toyota_Desde_481_NoIndex_True"


def test_resolve_marca_recovers_real_brand_from_fallback_page():
    # Caso real de produccion (2026-08-26): pedir /salto (marca DNRPA sin path
    # de filtro real en ML) no da 0 resultados - ML cae a un listado generico
    # sin filtrar. Sin el fix, este item quedaba taggeado marca=salto para
    # siempre y era invisible para due_for_detail (marca curada).
    known = ["salto", "toyota", "fiat", "nissan"]
    assert _resolve_marca("Nissan Kicks 2022 1.6 Advance", "salto", known) == "nissan"
    assert _resolve_marca("Toyota Corolla 2021 2.0 Gr Sport Cvt", "salto", known) == "toyota"


def test_resolve_marca_keeps_requested_marca_when_filter_is_real():
    # No debe romper el caso donde la marca pedida SI es la real (la inmensa
    # mayoria de las paginas) - el titulo ya arranca con esa misma marca.
    known = ["toyota", "fiat", "nissan"]
    assert _resolve_marca("Toyota Hilux 2020 2.8 4x4", "toyota", known) == "toyota"


def test_resolve_marca_falls_back_when_title_matches_nothing_known():
    # Texto libre / marca no reconocida: se mantiene el comportamiento previo
    # al fix (requested_marca), sin arriesgar un falso positivo.
    known = ["toyota", "fiat", "nissan"]
    assert _resolve_marca("Utilitario Sin Marca Especificada", "salto", known) == "salto"
    assert _resolve_marca(None, "salto", known) == "salto"


def test_resolve_marca_matches_multiword_slugs_and_accents():
    # 'Mercedes-Benz' (titulo) vs 'mercedes-benz' (slug), y acentos ('Citroen'
    # vs 'Citroën') no deben impedir el match - mismo criterio _compact() que
    # dnrpa_lookup.py del lado car-tracker.
    known = ["mercedes-benz", "land-rover", "citroen"]
    assert _resolve_marca("Mercedes-Benz Sprinter 2011 2.1", "salto", known) == "mercedes-benz"
    assert _resolve_marca("Land Rover Discovery 2019", "salto", known) == "land-rover"
    assert _resolve_marca("Citroën Berlingo Furgon 2018", "salto", known) == "citroen"


def test_parse_uses_resolved_marca_per_item():
    polycards = [
        _polycard("MLA1", ["2014", "184.000 Km"], title="Nissan Kicks 2022 1.6 Advance"),
        _polycard("MLA2", ["2018", "50.000 Km"], title="Toyota Corolla 2021 2.0"),
    ]
    request = Request(
        url="https://autos.mercadolibre.com.ar/salto",
        meta={"marca": "salto", "page_count": 1},
    )
    response = HtmlResponse(url=request.url, body=_build_html(polycards), encoding="utf-8", request=request)

    spider = MercadolibreDiscoverySpider(marcas="salto,nissan,toyota")
    items = list(spider.parse(response))

    assert [item["marca"] for item in items] == ["nissan", "toyota"]


def test_start_requests_url_does_not_match_known_robots_disallow_patterns():
    spider = MercadolibreDiscoverySpider(marcas="fiat")
    requests = _drain_async_gen(spider.start())
    assert len(requests) == 1
    url = requests[0].url
    assert url == "https://autos.mercadolibre.com.ar/fiat"
    # Reglas confirmadas del robots.txt real de autos.mercadolibre.com.ar
    # (User-agent: *) que ya nos mordieron una vez cada una:
    assert "_NoIndex_True" not in url
    assert "ITEM*CONDITION" not in url
    assert "mercadolibre" not in url.split("mercadolibre.com.ar", 1)[1]
