"""Discovery partido por modelo para romper el techo de ~2.000 de ML (wdxtkg39v1).

Todo lo que se fija aca salio de medir contra el sitio real el 2026-09-01, no
de suponer:

* ML publica el tope en `search.pagination.results_limit` (2000) y, cuando la
  consulta queda por debajo, ese campo ES el total real.
* `/toyota` alcanza 2.000 de los 6.746 avisos que el propio facet BRAND
  atribuye a Toyota: el 30%.
* El cupo es POR CONSULTA (dos modelos seguidos llegaron ambos al offset 1969),
  que es lo que hace que partir sirva.
* La url del facet (`/{modelo}/{marca}_NoIndex_True`) NO filtra si se la pide
  sin su fragmento `#applied_...`: devuelve query='toyota' y 2000. Por eso el
  slug se usa como TERMINO (`/{slug}`), no como filtro.
"""
import pytest
from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.extraction.mercadolibre import iter_model_facet
from car_tracker_scraper.spiders.mercadolibre_discovery import MercadolibreDiscoverySpider


def _facet(valores):
    """Reproduce la forma real: el facet util cuelga de sidebar.components[].filters[]."""
    return {
        "sidebar": {
            "components": [
                {"filters": [{"id": "MODEL", "name": "Modelo", "values": valores}]}
            ]
        }
    }


def _valor(nombre, slug, conteo):
    return {
        "id": "65895",
        "name": nombre,
        "results": f"({conteo})",
        "url": f"https://autos.mercadolibre.com.ar/{slug}/toyota_NoIndex_True"
               f"#applied_filter_id%3DMODEL%26applied_value_id%3D65895",
    }


def test_el_slug_sale_de_la_url_de_ML_no_de_slugificar_el_nombre():
    """'Hilux Pick-Up' -> 'hilux-pick-up' y 'C-HR' -> 'c-hr' son justo los casos
    donde una slugificacion propia se equivoca. ML ya publica el slug canonico
    en la url del facet."""
    search = _facet([
        _valor("Hilux Pick-Up", "hilux-pick-up", 1318),
        _valor("C-HR", "c-hr", 22),
    ])
    modelos = {m["name"]: m["slug"] for m in iter_model_facet(search)}
    assert modelos == {"Hilux Pick-Up": "hilux-pick-up", "C-HR": "c-hr"}


def test_se_ignora_el_facet_de_melidata_que_no_trae_url_ni_conteo():
    """El facet aparece DOS veces; el de melidata_track es el payload de
    analitica y trae los values como ids pelados. Tomarlo daria modelos sin
    slug ni volumen."""
    search = {
        "melidata_track": {
            "event_data": {
                "displayed_filters": [
                    {"id": "MODEL", "name": "Modelo", "values": ["65895", "9301"]}
                ]
            }
        },
        **_facet([_valor("Corolla", "corolla", 1570)]),
    }
    modelos = list(iter_model_facet(search))
    assert [m["slug"] for m in modelos] == ["corolla"]


def test_conteos_con_separador_de_miles():
    search = _facet([_valor("Corolla", "corolla", "1.570")])
    assert list(iter_model_facet(search))[0]["count"] == 1570


def _spider(**kwargs):
    return MercadolibreDiscoverySpider(marcas="toyota,ford", **kwargs)


def _respuesta_de_marca(spider, search, url="https://autos.mercadolibre.com.ar/toyota"):
    """El parse real necesita el ctx embebido; se arma el minimo que lee."""
    import json

    ctx = {"appProps": {"sharedState": {"search": search}}}
    html = (
        '<script id="__NORDIC_RENDERING_CTX__">_n.ctx.r='
        + json.dumps(ctx)
        + "</script>"
    ).encode()
    request = Request(url, meta={"marca": "toyota", "page_count": 1, "es_pagina_de_marca": True})
    return HtmlResponse(url=url, body=html, encoding="utf-8", request=request)


def _requests_de(spider, search):
    from scrapy import Request as R

    salida = list(spider.parse(_respuesta_de_marca(spider, search)))
    return [x for x in salida if isinstance(x, R)]


def test_abre_una_consulta_por_modelo_con_volumen_propio():
    spider = _spider(modelo_min_volumen="150")
    search = _facet([
        _valor("Corolla", "corolla", 1570),
        _valor("Hilux Pick-Up", "hilux-pick-up", 1318),
        _valor("Prius", "prius", 14),  # bajo el umbral: la marca ya lo cubre
    ])
    urls = [r.url for r in _requests_de(spider, search)]

    assert "https://autos.mercadolibre.com.ar/corolla" in urls
    assert "https://autos.mercadolibre.com.ar/hilux-pick-up" in urls
    assert "https://autos.mercadolibre.com.ar/prius" not in urls


def test_la_marca_que_viaja_es_la_REAL_no_el_termino_de_modelo():
    """El punto delicado de todo el cambio. `_resolve_marca` valida el titulo
    contra self.marcas, que tiene que seguir siendo la lista de marcas reales.
    Si en meta viajara 'corolla' como marca, los items quedarian etiquetados con
    un modelo como si fuera marca - invisibles para due_for_detail, que filtra
    por marca curada. Es la falla silenciosa del incidente 'salto'."""
    spider = _spider()
    search = _facet([_valor("Corolla", "corolla", 1570)])

    por_modelo = [r for r in _requests_de(spider, search) if r.url.endswith("/corolla")]
    assert len(por_modelo) == 1
    assert por_modelo[0].meta["marca"] == "toyota"
    assert por_modelo[0].meta["termino_modelo"] == "corolla"
    # y la lista de marcas conocidas no se contamino
    assert spider.marcas == ["toyota", "ford"]


def test_el_abanico_no_se_redispara_en_las_paginas_siguientes():
    """Sin esto, cada pagina de cada modelo abriria otro abanico: explosion
    combinatoria de requests contra un sitio que ya nos abrio el circuit breaker
    una vez."""
    spider = _spider()
    search = _facet([_valor("Corolla", "corolla", 1570)])
    por_modelo = [r for r in _requests_de(spider, search) if r.url.endswith("/corolla")]
    assert "es_pagina_de_marca" not in por_modelo[0].meta


def test_no_se_repite_la_consulta_de_la_marca_como_si_fuera_modelo():
    spider = _spider()
    search = _facet([_valor("Toyota", "toyota", 6746)])
    assert [r for r in _requests_de(spider, search) if r.url.endswith("/toyota")] == []


def test_tope_de_modelos_por_marca():
    """Cota dura del costo en requests."""
    spider = _spider(max_modelos_por_marca="3")
    search = _facet([_valor(f"M{i}", f"m{i}", 1000 - i) for i in range(10)])
    por_modelo = [r for r in _requests_de(spider, search) if "/m" in r.url]
    assert len(por_modelo) == 3
    # y son los de MAS volumen, no los primeros que aparecen
    assert sorted(r.url.rsplit("/", 1)[1] for r in por_modelo) == ["m0", "m1", "m2"]


def test_volumen_minimo_en_cero_desactiva_el_corte():
    """Para poder comparar contra la linea base sin tocar codigo."""
    spider = _spider(modelo_min_volumen="0")
    search = _facet([_valor("Corolla", "corolla", 1570)])
    assert [r for r in _requests_de(spider, search) if r.url.endswith("/corolla")] == []


def test_sin_facet_el_spider_sigue_funcionando_como_antes():
    """Si ML cambia la forma del sidebar, Discovery tiene que degradar a la
    consulta por marca, no romperse."""
    spider = _spider()
    assert _requests_de(spider, {"results": []}) == []
