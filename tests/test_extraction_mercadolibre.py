"""Tests de extraccion contra un fixture SINTETICO (construido en base a la
estructura confirmada en findings_clickup.md), no contra HTML real de ML.

Esto prueba que el codigo de parseo (brace-balancing, desanidado de
polycards, JSON-LD) es correcto contra la forma de dato documentada.
No reemplaza probar contra un fixture real: para eso hace falta que alguien
sin el bloqueo de robots.txt (ver car-tracker-scraping-feedback) guarde un
`curl` real de una pagina de listado/detalle y lo pegue en tests/fixtures/.
"""
import json

from car_tracker_scraper.extraction.mercadolibre import (
    extract_json_ld,
    extract_nordic_ctx,
    iter_polycards,
    polycard_components,
)


def _polycard(item_id: str, title: str, price: int, currency: str, is_pad: bool = False) -> dict:
    return {
        "id": "POLYCARD",
        "polycard": {
            "metadata": {
                "id": item_id,
                "url": f"https://auto.mercadolibre.com.ar/{item_id}-some-slug",
                "is_pad": "true" if is_pad else "false",
                "category_id": "MLA1744",
                "domain_id": "MLA-CARS_AND_VANS",
            },
            "components": [
                {"type": "title", "title": {"text": title}},
                {
                    "type": "price",
                    "price": {"current_price": {"value": price, "currency": currency}},
                },
                {
                    "type": "attributes_list",
                    "attributes_list": {"texts": ["2014", "184.000 Km"]},
                },
                {"type": "location", "location": {"text": "Godoy Cruz, Mendoza"}},
            ],
        },
    }


def _build_listing_html() -> str:
    ctx = {
        "appProps": {
            "sharedState": {
                "search": {
                    "results": [
                        _polycard("MLA1111111111", "Fiat Palio 1.4", 5_000_000, "ARS"),
                        {
                            "id": "GROUP_ITEMS_INTERVENTION",
                            "items": [
                                _polycard("MLA2222222222", "Fiat Grand Siena", 7700, "USD"),
                                _polycard("MLA3333333333", "Ad de Fiat 0km", 20_000_000, "ARS", is_pad=True),
                            ],
                        },
                    ],
                    "pagination": {
                        "pagination_nodes_url": [
                            "https://autos.mercadolibre.com.ar/fiat/fiat_Desde_49_ITEM*CONDITION_2230581_NoIndex_True"
                        ]
                    },
                }
            }
        }
    }
    return f'<script id="__NORDIC_RENDERING_CTX__">_n.ctx.r={json.dumps(ctx)}</script>'


def test_extract_nordic_ctx_parses_balanced_json():
    html = _build_listing_html()
    ctx = extract_nordic_ctx(html)
    assert "appProps" in ctx


def test_iter_polycards_unnests_group_items_and_filters_ads():
    html = _build_listing_html()
    ctx = extract_nordic_ctx(html)
    results = ctx["appProps"]["sharedState"]["search"]["results"]

    polycards = list(iter_polycards(results))
    ids = [pc["metadata"]["id"] for pc in polycards]

    # Las 3 tarjetas (incluida la publicidad) deben salir del unnesting;
    # el filtrado de is_pad es responsabilidad del spider, no de esta funcion.
    assert ids == ["MLA1111111111", "MLA2222222222", "MLA3333333333"]


def test_polycard_components_indexes_by_type_and_reads_mixed_currency():
    html = _build_listing_html()
    ctx = extract_nordic_ctx(html)
    results = ctx["appProps"]["sharedState"]["search"]["results"]
    polycards = list(iter_polycards(results))

    usd_card = next(pc for pc in polycards if pc["metadata"]["id"] == "MLA2222222222")
    comp = polycard_components(usd_card)

    # Nunca asumir la moneda por contexto de pagina: siempre leer el campo.
    assert comp["price"]["current_price"]["currency"] == "USD"
    assert comp["price"]["current_price"]["value"] == 7700
    assert comp["attributes_list"]["texts"] == ["2014", "184.000 Km"]


def test_pagination_nodes_url_present():
    html = _build_listing_html()
    ctx = extract_nordic_ctx(html)
    pagination = ctx["appProps"]["sharedState"]["search"]["pagination"]
    assert len(pagination["pagination_nodes_url"]) == 1


def _build_detail_html() -> str:
    vehicle_ld = {
        "@context": "https://schema.org",
        "@type": "Vehicle",
        "brand": "Fiat",
        "sku": "MLA3635810164",
        "color": "Marron",
        "fuelType": "Nafta",
        "numberOfDoors": "5",
        "vehicleTransmission": "Manual",
        "itemCondition": "https://schema.org/UsedCondition",
        "offers": {"price": 13990000, "priceCurrency": "ARS", "priceValidUntil": "2026-08-02"},
    }
    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"position": 1, "name": "Autos y Camionetas"},
            {"position": 2, "name": "Fiat"},
            {"position": 3, "name": "Palio"},
        ],
    }
    scripts = "\n".join(
        f'<script type="application/ld+json">{json.dumps(ld)}</script>' for ld in (vehicle_ld, breadcrumb_ld)
    )
    return f"<html><head>{scripts}</head><body></body></html>"


def test_extract_json_ld_finds_vehicle_and_breadcrumb():
    html = _build_detail_html()

    vehicle = extract_json_ld(html, "Vehicle")
    assert vehicle["sku"] == "MLA3635810164"
    assert vehicle["offers"]["priceCurrency"] == "ARS"

    breadcrumb = extract_json_ld(html, "BreadcrumbList")
    assert [el["name"] for el in breadcrumb["itemListElement"]] == [
        "Autos y Camionetas",
        "Fiat",
        "Palio",
    ]


def test_extract_json_ld_returns_none_when_type_absent():
    html = _build_detail_html()
    assert extract_json_ld(html, "Product") is None
