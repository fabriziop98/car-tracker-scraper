"""Tests de la fuente Motordil (wdxtkg30xr).

Contra fixtures HTML REALES capturados del sitio (tests/fixtures/motordil_*.html),
no HTML sintetico - mismo criterio que test_mercadolibre_detail_spider.py, por la
razon que ya documenta el CLAUDE.md: el markup real es lo que rompe en la
practica.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.extraction.common import compact, extract_json_ld
from car_tracker_scraper.extraction.motordil import (
    detail_path,
    extract_rsc_payload,
    iter_objects_with_key,
)
from car_tracker_scraper.items import ListingDetailItem, ListingSummaryItem
from car_tracker_scraper.spiders.motordil_detail import MotordilDetailSpider
from car_tracker_scraper.spiders.motordil_discovery import (
    MotordilDiscoverySpider,
    _resolve_marca_slug,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _response(fixture_name: str, url: str, meta: dict | None = None) -> HtmlResponse:
    request = Request(url=url, meta=meta or {})
    return HtmlResponse(
        url=url,
        body=(FIXTURES / fixture_name).read_bytes(),
        encoding="utf-8",
        request=request,
    )


def _results_response(page: int = 1) -> HtmlResponse:
    return _response(
        "motordil_results.html",
        f"https://www.motordil.com/results?priceCurrency=USD&sellerType=ALL&v=alfa-romeo&page={page}",
        {"marca": "alfa-romeo", "page": page},
    )


def _drain_async_gen(agen):
    async def _collect():
        return [item async for item in agen]

    return asyncio.run(_collect())


# --- extraccion (flight stream de Next.js) ------------------------------------


def test_extract_rsc_payload_reconstructs_the_flight_stream():
    payload = extract_rsc_payload((FIXTURES / "motordil_results.html").read_text(encoding="utf-8"))
    # El stream reconstruido es mucho mas grande que cualquier chunk suelto -
    # si la concatenacion fallara, no aparecerian los objetos completos.
    assert len(payload) > 100_000
    assert '{"listing":' in payload


def test_iter_objects_with_key_finds_every_listing_on_the_page():
    payload = extract_rsc_payload((FIXTURES / "motordil_results.html").read_text(encoding="utf-8"))
    listings = list(iter_objects_with_key(payload, "listing"))

    # 24 por pagina, confirmado tanto en el reverse engineering de Fase 0 como
    # en el fixture real capturado 2026-08-26.
    assert len(listings) == 24
    assert listings[0]["metadata"]["make"]["make"] == "Alfa Romeo"


def test_iter_objects_with_key_uses_a_different_root_key_on_the_detail_page():
    # El gotcha principal de esta fuente: la clave raiz cambia segun la pagina
    # ("listing" en la grilla, "publication" en la ficha).
    payload = extract_rsc_payload((FIXTURES / "motordil_detail.html").read_text(encoding="utf-8"))
    assert list(iter_objects_with_key(payload, "listing")) == []
    assert len(list(iter_objects_with_key(payload, "publication"))) == 1


def test_detail_path_falls_back_to_id_when_slug_is_null():
    assert detail_path({"slug": "2010-alfa-romeo-159-mpdd", "id": "abc"}) == "/auto/2010-alfa-romeo-159-mpdd"
    assert detail_path({"slug": None, "id": "abc"}) == "/auto/abc"
    assert detail_path({}) is None


def test_null_slugs_are_a_real_case_in_the_captured_page():
    # Regresion de expectativa: 3 de 24 avisos reales vienen sin slug. Si esto
    # empieza a dar 0, el fallback por id dejo de estar ejercitado por el
    # fixture y conviene revisar por que.
    payload = extract_rsc_payload((FIXTURES / "motordil_results.html").read_text(encoding="utf-8"))
    listings = list(iter_objects_with_key(payload, "listing"))
    assert sum(1 for listing in listings if not listing.get("slug")) > 0
    assert all(detail_path(listing) for listing in listings)


def test_detail_page_exposes_json_ld_as_car_not_vehicle():
    # El doc de hallazgos de Fase 0 decia "Vehicle" (como ML) y estaba
    # equivocado - confirmado contra el fixture real.
    html = (FIXTURES / "motordil_detail.html").read_text(encoding="utf-8")
    assert extract_json_ld(html, "Vehicle") is None
    assert extract_json_ld(html, "Car")["brand"]["name"] == "Alfa Romeo"


# --- normalizacion de marca ---------------------------------------------------


def test_resolve_marca_slug_maps_the_display_name_to_the_curated_slug():
    # Sin esto, el tracker filtraria Detail por 'alfa-romeo' mientras Discovery
    # guarda 'Alfa Romeo', ningun candidato de esta fuente matchearia nunca y
    # Detail se moriria de hambre en silencio (mismo sintoma que wdxtkg3980).
    known = ["alfa-romeo", "mercedes-benz", "citroen"]
    assert _resolve_marca_slug("Alfa Romeo", "alfa-romeo", known) == "alfa-romeo"
    assert _resolve_marca_slug("Mercedes Benz", "x", known) == "mercedes-benz"
    assert _resolve_marca_slug("Citroën", "x", known) == "citroen"


def test_resolve_marca_slug_keeps_the_requested_one_when_the_brand_is_not_curated():
    known = ["alfa-romeo"]
    assert _resolve_marca_slug("Rolls Royce", "alfa-romeo", known) == "alfa-romeo"
    assert _resolve_marca_slug(None, "alfa-romeo", known) == "alfa-romeo"


def test_compact_ignores_punctuation_and_accents():
    assert compact("Mercedes-Benz") == compact("Mercedes Benz") == "mercedesbenz"
    assert compact("Citroën") == "citroen"


# --- Discovery spider ---------------------------------------------------------


def test_discovery_emits_one_item_per_listing_with_absolute_detail_urls():
    spider = MotordilDiscoverySpider(marcas="alfa-romeo")
    results = list(spider.parse(_results_response()))

    items = [r for r in results if isinstance(r, ListingSummaryItem)]
    assert len(items) == 24
    assert all(item["url"].startswith("https://www.motordil.com/auto/") for item in items)
    assert all(item["source"] == "motordil" for item in items)
    assert all(item["is_ad"] is False for item in items)


def test_discovery_tags_items_with_the_curated_brand_slug_not_the_display_name():
    spider = MotordilDiscoverySpider(marcas="alfa-romeo")
    items = [r for r in spider.parse(_results_response()) if isinstance(r, ListingSummaryItem)]

    assert {item["marca"] for item in items} == {"alfa-romeo"}


def test_discovery_reads_currency_from_the_item_not_from_the_url_param():
    # priceCurrency=USD en la URL es preferencia de visualizacion, no filtro -
    # en Fase 0 aparecieron avisos en ARS con ese mismo parametro.
    spider = MotordilDiscoverySpider(marcas="alfa-romeo")
    items = [r for r in spider.parse(_results_response()) if isinstance(r, ListingSummaryItem)]

    assert all(item["price_currency"] for item in items)


def test_discovery_follows_to_the_next_page_while_the_page_has_listings():
    spider = MotordilDiscoverySpider(marcas="alfa-romeo")
    requests = [r for r in spider.parse(_results_response(page=1)) if isinstance(r, Request)]

    assert len(requests) == 1
    assert "page=2" in requests[0].url
    assert requests[0].meta["page"] == 2


def test_discovery_stops_when_a_page_brings_no_listings():
    # Corte real de la paginacion (no un tope fijo): la ultima pagina de una
    # marca no trae avisos y ahi termina, sin gastar requests de mas.
    empty = HtmlResponse(
        url="https://www.motordil.com/results?v=alfa-romeo&page=99",
        body=b"<html><body>sin resultados</body></html>",
        encoding="utf-8",
        request=Request(url="https://www.motordil.com/results?v=alfa-romeo&page=99",
                        meta={"marca": "alfa-romeo", "page": 99}),
    )
    spider = MotordilDiscoverySpider(marcas="alfa-romeo")
    results = list(spider.parse(empty))

    assert results == []


def test_discovery_respects_the_max_pages_safety_cap():
    spider = MotordilDiscoverySpider(marcas="alfa-romeo", max_pages="1")
    results = list(spider.parse(_results_response(page=1)))

    assert [r for r in results if isinstance(r, ListingSummaryItem)]
    assert [r for r in results if isinstance(r, Request)] == []


def test_discovery_start_urls_have_no_rsc_param():
    # Con `_rsc` la respuesta es solo el shell, sin ningun aviso.
    spider = MotordilDiscoverySpider(marcas="alfa-romeo,ford")
    requests = _drain_async_gen(spider.start())

    assert len(requests) == 2
    assert all("_rsc" not in r.url for r in requests)
    assert all("/results?" in r.url for r in requests)


# --- Detail spider ------------------------------------------------------------


def _detail_item() -> ListingDetailItem:
    spider = MotordilDetailSpider(urls="https://www.motordil.com/auto/2010-alfa-romeo-159-mpdd")
    response = _response("motordil_detail.html", "https://www.motordil.com/auto/2010-alfa-romeo-159-mpdd")
    items = list(spider.parse(response))
    assert len(items) == 1
    return items[0]


def test_detail_extracts_the_structured_catalog_fields():
    # La ventaja real de Motordil sobre ML: marca/modelo/version vienen en
    # campos propios, no embebidos en un titulo libre que haya que parsear.
    item = _detail_item()

    assert item["source"] == "motordil"
    assert item["source_listing_key"] == "6a80ddd74fe156c81a49642d"
    assert item["brand_raw"] == "Alfa Romeo"
    assert item["model_raw"] == "159"
    assert item["version_raw"] == "2.2 JTS"
    assert item["year_raw"] == 2010
    assert item["km"] == 32000


def test_detail_extracts_price_condition_and_seller():
    item = _detail_item()

    assert item["price_amount"] == 17000
    assert item["price_currency"] == "USD"
    assert item["item_condition"] == "USED"
    assert item["seller_type"] == "car_dealer"
    assert item["seller_id"] == "car-hunter"
    assert item["province_raw"] == "Provincia de Buenos Aires"


def test_detail_yields_nothing_when_the_publication_object_is_missing():
    # Una ficha dada de baja puede servir un shell sin `publication`: no es un
    # crash, simplemente no hay item que emitir.
    spider = MotordilDetailSpider(urls="https://www.motordil.com/auto/x")
    response = HtmlResponse(
        url="https://www.motordil.com/auto/x",
        body=b"<html><body>no existe</body></html>",
        encoding="utf-8",
        request=Request(url="https://www.motordil.com/auto/x"),
    )

    assert list(spider.parse(response)) == []
