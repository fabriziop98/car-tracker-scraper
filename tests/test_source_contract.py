"""Contrato transversal que TODO proveedor de Discovery debe cumplir.

Por que existe: los dos bugs que mas caro salieron en este proyecto se
repitieron al sumar una fuente nueva, y ninguno de los dos daba error - los dos
producian datos que se veian perfectamente validos:

1. **Marca sin normalizar al slug curado.** `due_for_detail` filtra por marca
   curada (un slug: 'alfa-romeo'). Una fuente que emite el nombre display
   ('Alfa Romeo') deja a Detail sin candidatos EN SILENCIO. Paso de verdad en
   MercadoLibre (wdxtkg3980) y estuvo a punto de repetirse en Motordil y en
   DeRuedas.
2. **0km sin filtrar.** El proyecto trackea USADOS. MercadoLibre filtra 0km
   desde Fase 1, pero Motordil y DeRuedas se sumaron sin el equivalente y
   entraron 115 autos nuevos a la serie de precios (wdxtkg39pw).

En ambos casos la causa raiz no fue copy-paste: fue que cada proveedor nuevo
re-implementa una checklist de memoria y puede omitir un item sin que nada se
queje. Estos tests convierten esa checklist en algo que falla en CI.

**Un proveedor N+1 tiene que agregarse a PROVIDER_CASES o el primer test de
este archivo falla.** Eso es a proposito: es el mecanismo que fuerza a que la
arquitectura se mantenga igual a medida que crecen las fuentes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from scrapy.http import HtmlResponse, Request, TextResponse

import run_batch
from car_tracker_scraper.extraction.autocity import SITEMAP_URL
from car_tracker_scraper.items import ListingSummaryItem
from car_tracker_scraper.spiders.autocity_discovery import AutocityDiscoverySpider
from car_tracker_scraper.spiders.deruedas_discovery import DeruedasDiscoverySpider
from car_tracker_scraper.spiders.mercadolibre_discovery import MercadolibreDiscoverySpider
from car_tracker_scraper.spiders.motordil_discovery import MotordilDiscoverySpider
from car_tracker_scraper.extraction.v6 import PUBLISHED_CARS_URL
from car_tracker_scraper.spiders.v6_discovery import V6DiscoverySpider
from car_tracker_scraper.spiders.autocosmos_discovery import AutocosmosDiscoverySpider

FIXTURES = Path(__file__).parent / "fixtures"


def _response(url, body: bytes, meta: dict) -> HtmlResponse:
    request = Request(url=url, meta=meta)
    return HtmlResponse(url=url, body=body, encoding="utf-8", request=request)


def _mercadolibre_case():
    """ML no tiene fixture HTML de Discovery (sus tests arman el JSON inline),
    asi que se construye uno equivalente incluyendo un 0km y una publicidad."""
    def polycard(item_id, attributes, title, is_pad=False):
        return {
            "id": "POLYCARD",
            "polycard": {
                "metadata": {
                    "id": item_id,
                    "url": f"auto.mercadolibre.com.ar/{item_id}-slug",
                    "is_pad": "true" if is_pad else "false",
                },
                "components": [
                    {"type": "title", "title": {"text": title}},
                    {"type": "price", "price": {"current_price": {"value": 5_000_000, "currency": "ARS"}}},
                    {"type": "attributes_list", "attributes_list": {"texts": attributes}},
                    {"type": "location", "location": {"text": "Godoy Cruz, Mendoza"}},
                ],
            },
        }

    ctx = {"appProps": {"sharedState": {"search": {
        "results": [
            polycard("MLA1", ["2014", "184.000 Km"], "Fiat Palio"),
            polycard("MLA2", ["2027", "0 Km"], "Fiat Cronos"),          # 0km -> fuera
            polycard("MLA3", ["2018", "50.000 Km"], "Fiat Argo", True),  # ad -> fuera
        ],
        "pagination": {"pagination_nodes_url": []},
    }}}}
    body = f'<script id="__NORDIC_RENDERING_CTX__">_n.ctx.r={json.dumps(ctx)}</script>'.encode()
    return (
        MercadolibreDiscoverySpider(marcas="fiat"),
        _response("https://autos.mercadolibre.com.ar/fiat", body, {"marca": "fiat", "page_count": 1}),
        ["fiat"],
    )


def _motordil_case():
    return (
        MotordilDiscoverySpider(marcas="alfa-romeo"),
        _response(
            "https://www.motordil.com/results?v=alfa-romeo&page=1",
            (FIXTURES / "motordil_results.html").read_bytes(),
            {"marca": "alfa-romeo", "page": 1},
        ),
        ["alfa-romeo"],
    )


def _deruedas_case():
    return (
        DeruedasDiscoverySpider(marcas="audi"),
        _response(
            "https://www.deruedas.com.ar/busCraw.asp?marca=Audi&pag=1",
            (FIXTURES / "deruedas_results.html").read_bytes(),
            {"marca": "audi", "page": 1},
        ),
        ["audi"],
    )


def _autocity_case():
    """Unico Discovery que no pagina: descubre por sitemap, asi que sus items
    salen SIN precio (el sitemap solo tiene URLs). El precio llega en Detail,
    que es lo unico que se persiste."""
    return (
        AutocityDiscoverySpider(marcas="citroen,ford,fiat,peugeot,renault"),
        TextResponse(
            url=SITEMAP_URL,
            body=(FIXTURES / "autocity_sitemap.xml").read_bytes(),
            encoding="utf-8",
            request=Request(url=SITEMAP_URL),
        ),
        None,  # no filtra por marcas: el sitemap trae el inventario entero
    )


def _v6_case():
    """Unico Discovery que no pagina NI filtra por marca en el request: un
    solo GET trae el catalogo entero (ver extraction/v6.py), asi que las
    'marcas pedidas' no son un filtro real - se pasan solo para validar que
    la normalizacion a slug curado siga funcionando."""
    return (
        V6DiscoverySpider(marcas="toyota,audi,peugeot"),
        TextResponse(
            url=PUBLISHED_CARS_URL,
            body=(FIXTURES / "v6_published_cars_sample.json").read_bytes(),
            encoding="utf-8",
            request=Request(url=PUBLISHED_CARS_URL),
        ),
        None,  # no filtra por marcas: el catalogo entero llega en un unico GET
    )


def _autocosmos_case():
    """wdxtkg3hc4: como autocity/v6, no filtra por marca en el request (pagina
    el catalogo entero via `pidx`) - `marcas` solo normaliza al slug curado.
    El fixture mezcla avisos "financiados en cuotas" (price_amount=None,
    financing_initial_payment seteado) con avisos de precio real, a proposito:
    es la regresion del hallazgo central de esta fuente."""
    return (
        AutocosmosDiscoverySpider(marcas="chery,chevrolet,fiat,ford,kia,peugeot,renault,toyota,volkswagen"),
        _response(
            "https://www.autocosmos.com.ar/auto/usado",
            (FIXTURES / "autocosmos_listado.html").read_bytes(),
            {"page": 1},
        ),
        ["chery", "chevrolet", "fiat", "ford", "kia", "peugeot", "renault", "toyota", "volkswagen"],
    )


PROVIDER_CASES = {
    "mercadolibre": _mercadolibre_case,
    "autocity": _autocity_case,
    "motordil": _motordil_case,
    "deruedas": _deruedas_case,
    "v6": _v6_case,
    "autocosmos": _autocosmos_case,
}


def _items(case):
    spider, response, marcas = case()
    items = [r for r in spider.parse(response) if isinstance(r, ListingSummaryItem)]
    return items, marcas


# --- el test que fuerza la cobertura -----------------------------------------


def test_every_registered_source_has_a_contract_case():
    """Si se suma una fuente a run_batch.SOURCES sin sumarla aca, esto falla.

    Es el unico punto del repo que obliga a que un proveedor nuevo pase por los
    invariantes de abajo en vez de confiar en que quien lo escriba se acuerde.
    """
    registradas = {s.slug for s in run_batch.SOURCES}
    cubiertas = set(PROVIDER_CASES)

    assert registradas == cubiertas, (
        f"fuentes sin contrato: {registradas - cubiertas} / "
        f"contratos sin fuente: {cubiertas - registradas}"
    )


# --- invariantes, uno por proveedor ------------------------------------------


@pytest.mark.parametrize("slug", sorted(PROVIDER_CASES))
def test_discovery_emits_items(slug):
    items, _ = _items(PROVIDER_CASES[slug])
    assert items, f"{slug}: Discovery no emitio ningun item"


@pytest.mark.parametrize("slug", sorted(PROVIDER_CASES))
def test_marca_is_a_curated_slug(slug):
    """Invariante 1: la marca emitida tiene que ser uno de los slugs curados
    que se le pidieron al spider - nunca el nombre display de la fuente."""
    items, marcas = _items(PROVIDER_CASES[slug])

    emitidas = {i["marca"] for i in items}
    if marcas is not None:
        assert emitidas <= set(marcas), (
            f"{slug}: emitio marcas que no son slugs curados: {emitidas - set(marcas)}. "
            "Usar resolve_marca_slug() de extraction/common.py."
        )
    for marca in emitidas:
        assert marca == marca.lower() and " " not in marca, f"{slug}: '{marca}' no tiene forma de slug"


@pytest.mark.parametrize("slug", sorted(PROVIDER_CASES))
def test_zero_km_units_are_filtered_out(slug):
    """Invariante 2: el proyecto trackea usados. Ningun 0km puede salir de
    Discovery - ni por odometro en 0 ni por el texto '0 Km'."""
    items, _ = _items(PROVIDER_CASES[slug])

    for item in items:
        for attr in item.get("attributes_raw") or []:
            if attr and attr.strip().lower().replace(".", "").startswith("0 km"):
                pytest.fail(f"{slug}: se colo un 0km ({item['source_listing_key']})")
            if attr and attr.strip() == "0 km":
                pytest.fail(f"{slug}: se colo un 0km ({item['source_listing_key']})")


@pytest.mark.parametrize("slug", sorted(PROVIDER_CASES))
def test_item_carries_the_fields_the_pipeline_needs(slug):
    """Invariante 3: los campos sin los cuales el resto del pipeline no
    funciona - el source (dispatch del lado Java), la clave de idempotencia, y
    una URL absoluta (Scrapy la exige en el Detail spider)."""
    items, _ = _items(PROVIDER_CASES[slug])

    for item in items:
        assert item["source"] == slug, f"{slug}: source mal seteado ({item['source']})"
        assert item["source_listing_key"], f"{slug}: item sin source_listing_key"
        assert str(item["url"]).startswith("https://"), f"{slug}: URL no absoluta ({item['url']})"
        assert item["discovered_at"], f"{slug}: item sin discovered_at"
        assert item["is_ad"] is False, f"{slug}: se colo una publicidad"


@pytest.mark.parametrize("slug", sorted(PROVIDER_CASES))
def test_currency_is_read_per_item_never_assumed(slug):
    """Invariante 4: precio y moneda viajan JUNTOS, y la moneda se lee de cada
    aviso.

    Argentina publica en ARS y en USD mezclado en el mismo listado, asi que
    asumir la moneda por el contexto de la pagina es el error que DeRuedas casi
    mete en la serie (declara ARS para todo y convierte los USD con su
    cotizacion). Lo que NO se exige es que Discovery traiga precio: Autocity
    descubre por sitemap, que solo tiene URLs, y su precio llega en Detail - que
    es lo unico que se persiste. Un precio SIN moneda si es siempre un bug.
    """
    items, _ = _items(PROVIDER_CASES[slug])

    for item in items:
        if item.get("price_amount") is None:
            assert item.get("price_currency") is None, (
                f"{slug}: moneda sin precio en {item['source_listing_key']}"
            )
            continue
        assert item["price_currency"] in ("ARS", "USD"), (
            f"{slug}: moneda invalida {item['price_currency']!r} en {item['source_listing_key']}"
        )


@pytest.mark.parametrize("slug", sorted(PROVIDER_CASES))
def test_landing_zone_meta_is_propagated(slug):
    """Invariante 5: el puente a `raw_payload` del lado Java (wdxtkg30nm). Sin
    estos campos no se puede reprocesar desde el HTML crudo cuando aparece un
    bug de parseo. Se verifica que las CLAVES existan (los valores son None
    fuera de una corrida real, que es cuando el middleware las completa)."""
    items, _ = _items(PROVIDER_CASES[slug])

    for item in items:
        for field in ("s3_key", "http_status", "parser_version"):
            assert field in item, f"{slug}: falta {field} - usar **landing_meta(response)"
