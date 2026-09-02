"""Tests de la fuente V6 Marketplace (wdxtkg39qx).

Contra un fixture JSON REAL (tests/fixtures/v6_published_cars_sample.json,
4 registros tal cual los devolvio `getPublishedCars` el 2026-09-02), no datos
sinteticos - mismo criterio que test_motordil.py, por la razon que ya
documenta el CLAUDE.md del repo: el dato real es lo que rompe en la practica
(ver el caso de `agencyUrl` o de los campos de specs en "").
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scrapy.http import Request, TextResponse

from car_tracker_scraper.extraction.v6 import (
    agency_seller,
    detail_path,
    flatten_specs,
    is_zero_km,
    resolve_currency,
    slugify,
)
from car_tracker_scraper.items import DeadListingItem, ListingDetailItem, ListingSummaryItem
from car_tracker_scraper.spiders.v6_detail import V6DetailSpider, uid_from_url
from car_tracker_scraper.spiders.v6_discovery import V6DiscoverySpider

FIXTURES = Path(__file__).parent / "fixtures"
CARS = json.loads((FIXTURES / "v6_published_cars_sample.json").read_text(encoding="utf-8"))

# uids de conveniencia para no repetir los strings en cada test
HIACE_0KM = "a6LTIVsfBb"  # Toyota Hiace, 0km, sin priceHistory, fromAgency=True
AUDI_A3 = "FBEo5y5iZe"  # sin priceHistory
COROLLA_CROSS = "4eXEkE9eUP"  # priceHistory en ARS
PEUGEOT_208 = "llsL6hZgfu"  # priceHistory en USD, specs con campos en ""


def _published_cars_response(cars=None) -> TextResponse:
    body = json.dumps(cars if cars is not None else CARS).encode("utf-8")
    request = Request(url="https://autoprecios-api.onrender.com/api/db/getPublishedCars")
    return TextResponse(url=request.url, body=body, encoding="utf-8", request=request)


def _drain_async_gen(agen):
    async def _collect():
        return [item async for item in agen]

    return asyncio.run(_collect())


def _car(uid: str) -> dict:
    return next(c for c in CARS if c["uid"] == uid)


# --- extraccion ----------------------------------------------------------------


def test_slugify_matches_real_site_urls():
    # Los tres casos vienen de URLs de ficha REALES capturadas navegando el
    # sitio en vivo (2026-09-02) - si el algoritmo cambia, dejan de matchear.
    assert slugify("Ford Fiesta Kinetic Design SE 1.6 MT 5P 2018") == "ford-fiesta-kinetic-design-se-1-6-mt-5p-2018"
    assert slugify("Honda City EXL 1.5 MT 4P 2014") == "honda-city-exl-1-5-mt-4p-2014"
    assert (
        slugify("Audi A5 Sportback Sportback 2.0 TFSI S-tronic 5P 2020")
        == "audi-a5-sportback-sportback-2-0-tfsi-s-tronic-5p-2020"
    )


def test_detail_path_builds_the_same_url_the_real_site_uses():
    assert detail_path(_car(PEUGEOT_208)) == "/auto/peugeot-208-gt-1-6-thp-mt-5p-2020-llsL6hZgfu"


def test_detail_path_is_none_without_a_uid():
    assert detail_path({"brand": "Ford"}) is None


def test_is_zero_km_flags_only_the_hiace():
    assert is_zero_km(_car(HIACE_0KM)) is True
    assert is_zero_km(_car(PEUGEOT_208)) is False


def test_resolve_currency_reads_the_newest_price_history_entry_not_the_oldest():
    # El Corolla Cross real tiene 2 entradas en ARS con el precio BAJANDO
    # (40.500.000 -> 39.000.000): si se leyera con [-1] en vez de [0] el
    # resultado seria el mismo string "ARS" en este caso puntual, pero el
    # criterio de "mas reciente" es lo que hay que blindar, no la moneda.
    assert resolve_currency(_car(COROLLA_CROSS)) == "ARS"
    assert resolve_currency(_car(PEUGEOT_208)) == "USD"


def test_resolve_currency_is_none_without_price_history():
    # 91 de 209 avisos reales (2026-09-02) no tienen priceHistory - dejarlo
    # en None a proposito, ver el docstring de resolve_currency.
    assert resolve_currency(_car(AUDI_A3)) is None
    assert resolve_currency(_car(HIACE_0KM)) is None


def test_flatten_specs_drops_empty_values():
    # El Peugeot 208 real trae varios campos de carroceria en "" (el
    # concesionario no los cargo) - no deberian aparecer como "clave: ".
    flat = flatten_specs(_car(PEUGEOT_208)["specs"])
    assert "cantidad_puertas: " not in flat
    assert any(item.startswith("caja_tipo:") for item in flat)
    assert not any(item.endswith(": ") for item in flat)


def test_agency_seller_requires_the_from_agency_flag():
    # Toyota Hiace real: fromAgency=True + agencyUrl -> se puede derivar.
    assert agency_seller(_car(HIACE_0KM)) == ("Car Cash Argentina", "car_dealer", "Car-Cash-Argentina")
    # El resto del fixture no tiene fromAgency=True -> sin señal, todo None.
    assert agency_seller(_car(PEUGEOT_208)) == (None, None, None)


def test_uid_from_url_reads_the_last_dash_separated_segment():
    assert uid_from_url("https://www.v6.com.ar/auto/peugeot-208-gt-1-6-thp-mt-5p-2020-llsL6hZgfu") == "llsL6hZgfu"
    assert uid_from_url("https://www.v6.com.ar/auto/onlyuid") == "onlyuid"


# --- Discovery spider ------------------------------------------------------------


def test_discovery_hits_the_published_cars_endpoint_once_with_no_pagination():
    # A diferencia de las otras cuatro fuentes, no hay loop por marca ni por
    # pagina - un unico GET trae el catalogo entero (ver extraction/v6.py).
    spider = V6DiscoverySpider()
    requests = _drain_async_gen(spider.start())

    assert len(requests) == 1
    assert requests[0].url == "https://autoprecios-api.onrender.com/api/db/getPublishedCars"


def test_discovery_emits_one_item_per_listing_and_drops_zero_km():
    spider = V6DiscoverySpider()
    items = list(spider.parse(_published_cars_response()))

    # 4 avisos en el fixture, 1 de ellos (Hiace) en 0km -> 3 items.
    assert len(items) == 3
    assert all(isinstance(i, ListingSummaryItem) for i in items)
    assert HIACE_0KM not in {i["source_listing_key"] for i in items}
    assert all(i["url"].startswith("https://www.v6.com.ar/auto/") for i in items)
    assert all(i["source"] == "v6" for i in items)
    assert all(i["is_ad"] is False for i in items)


def test_discovery_tags_the_curated_brand_slug_when_it_matches():
    spider = V6DiscoverySpider(marcas="peugeot,toyota,audi")
    items = list(spider.parse(_published_cars_response()))

    by_key = {i["source_listing_key"]: i for i in items}
    assert by_key[PEUGEOT_208]["marca"] == "peugeot"
    assert by_key[AUDI_A3]["marca"] == "audi"


def test_discovery_falls_back_to_a_slugified_brand_when_not_curated():
    spider = V6DiscoverySpider(marcas="peugeot")  # sin toyota curada
    items = list(spider.parse(_published_cars_response()))

    by_key = {i["source_listing_key"]: i for i in items}
    assert by_key[COROLLA_CROSS]["marca"] == "toyota"  # slugify("Toyota"), no el nombre de pantalla


# --- Detail spider ---------------------------------------------------------------


def _detail_items(uid: str, cars=None) -> list:
    url = f"https://www.v6.com.ar/auto/x-{uid}"
    spider = V6DetailSpider(urls=url)
    return list(spider.parse(_published_cars_response(cars)))


def test_detail_extracts_structured_fields_and_specs():
    items = _detail_items(PEUGEOT_208)

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ListingDetailItem)
    assert item["source"] == "v6"
    assert item["brand_raw"] == "Peugeot"
    assert item["model_raw"] == "208"
    assert item["version_raw"] == "GT 1.6 THP MT 5P"
    assert item["year_raw"] == "2020"
    assert item["km"] == "63000"
    assert item["price_currency"] == "USD"
    assert item["transmission_raw"] == "MT"
    assert item["province_raw"] == "Santa Fe"


def test_detail_marks_a_uid_missing_from_the_catalog_as_dead():
    spider = V6DetailSpider(urls="https://www.v6.com.ar/auto/x-doesnotexist")
    items = list(spider.parse(_published_cars_response()))

    assert len(items) == 1
    assert isinstance(items[0], DeadListingItem)
    assert items[0]["item_type"] == "dead_listing"


def test_detail_skips_zero_km_without_marking_it_dead():
    # 0km fuera, pero NO es un aviso caido (mismo patron que
    # motordil_detail.py con vehiculos NEW) - no debe emitir nada, ni item ni
    # DeadListingItem.
    assert _detail_items(HIACE_0KM) == []


def test_detail_only_sets_seller_fields_for_agency_listings():
    hiace_items = _detail_items(HIACE_0KM, cars=[c for c in CARS if c["uid"] != HIACE_0KM] + [{**_car(HIACE_0KM), "kilometers": "5000"}])
    peugeot_items = _detail_items(PEUGEOT_208)

    assert hiace_items[0]["seller_type"] == "car_dealer"
    assert hiace_items[0]["seller_id"] == "Car-Cash-Argentina"
    assert peugeot_items[0]["seller_type"] is None
