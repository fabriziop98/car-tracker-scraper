"""Discovery partido por año, un nivel mas abajo del modelo (wdxtkg3j4o).

Un modelo individual puede por si solo superar el mismo tope de ~2.000 por
consulta que wdxtkg39v1 ya resolvio a nivel marca -> modelo. Medido contra el
sitio real (2026-09-06): Ford Ranger tiene 3.160 avisos reales (facet MODEL de
`/ford`) y `/ranger` sola solo alcanza los 2.000 de siempre - el 63%.
Confirmado ademas cruzando avisos reales de "Ford Ranger 2019 Limited": de 14
que ML muestra hoy, 9 nunca habian sido vistos por Discovery.

Mismo mecanismo, un nivel mas abajo: el facet VEHICLE_YEAR de la pagina de un
modelo tiene el mismo shape que el facet MODEL de la pagina de una marca.
"""
from scrapy.http import HtmlResponse, Request
from scrapy import Request as R

from car_tracker_scraper.extraction.mercadolibre import iter_year_facet
from car_tracker_scraper.spiders.mercadolibre_discovery import MercadolibreDiscoverySpider


def _facet_modelo(valores):
    return {
        "sidebar": {
            "components": [
                {"filters": [{"id": "MODEL", "name": "Modelo", "values": valores}]}
            ]
        }
    }


def _facet_anio(valores):
    return {
        "sidebar": {
            "components": [
                {"filters": [{"id": "VEHICLE_YEAR", "name": "Año", "values": valores}]}
            ]
        }
    }


def _valor_anio(anio, conteo):
    return {
        "id": f"[{anio}-{anio}]",
        "name": str(anio),
        "results": f"({conteo})",
        "url": f"https://autos.mercadolibre.com.ar/{anio}/ranger_NoIndex_True"
               f"#applied_filter_id%3DVEHICLE_YEAR%26applied_value_id%3D%5B{anio}-{anio}%5D",
    }


def test_el_slug_del_anio_sale_de_la_url_no_del_name():
    search = _facet_anio([_valor_anio(2019, 69), _valor_anio(2024, 207)])
    anios = {a["name"]: a["slug"] for a in iter_year_facet(search)}
    assert anios == {"2019": "2019", "2024": "2024"}


def test_conteos_con_parentesis_y_separador_de_miles():
    # A diferencia del facet MODEL (conteo como "1.570" pelado), VEHICLE_YEAR
    # publica el conteo entre parentesis ("(2.068)") - _iter_facet_values ya
    # limpia cualquier caracter no numerico, pero se confirma explicitamente
    # para este facet porque la forma es distinta.
    search = _facet_anio([_valor_anio(2024, "2.068")])
    assert list(iter_year_facet(search))[0]["count"] == 2068


def _spider(**kwargs):
    return MercadolibreDiscoverySpider(marcas="ford", **kwargs)


def _respuesta_de_modelo(search, modelo_count, url="https://autos.mercadolibre.com.ar/ranger"):
    import json

    ctx = {"appProps": {"sharedState": {"search": search}}}
    html = (
        '<script id="__NORDIC_RENDERING_CTX__">_n.ctx.r='
        + json.dumps(ctx)
        + "</script>"
    ).encode()
    request = Request(
        url,
        meta={
            "marca": "ford",
            "page_count": 1,
            "termino_modelo": "ranger",
            "es_pagina_de_modelo": True,
            "modelo_count": modelo_count,
        },
    )
    return HtmlResponse(url=url, body=html, encoding="utf-8", request=request)


def _requests_de(spider, search, modelo_count=3160):
    salida = list(spider.parse(_respuesta_de_modelo(search, modelo_count)))
    return [x for x in salida if isinstance(x, R)]


def test_abre_una_consulta_por_anio_con_volumen_propio_cuando_el_modelo_supera_el_gate():
    spider = _spider(anio_min_volumen="100", anio_fanout_min_volumen="1500")
    search = _facet_anio([
        _valor_anio(2024, 207),
        _valor_anio(2019, 69),  # bajo el umbral: la consulta del modelo ya lo cubre
    ])
    urls = [r.url for r in _requests_de(spider, search, modelo_count=3160)]

    assert "https://autos.mercadolibre.com.ar/2024/ranger" in urls
    assert "https://autos.mercadolibre.com.ar/2019/ranger" not in urls


def test_no_abre_por_anio_cuando_el_modelo_no_llega_al_gate():
    """El punto central del fix: no todo modelo con volumen paga el costo
    extra, solo los que ya rozan o superan el tope de ~2.000 de ML."""
    spider = _spider(anio_fanout_min_volumen="1500")
    search = _facet_anio([_valor_anio(2024, 900)])

    assert _requests_de(spider, search, modelo_count=1200) == []


def test_la_marca_que_viaja_es_la_real_y_el_termino_modelo_se_conserva():
    spider = _spider()
    search = _facet_anio([_valor_anio(2024, 207)])

    por_anio = [r for r in _requests_de(spider, search) if r.url.endswith("/2024/ranger")]
    assert len(por_anio) == 1
    assert por_anio[0].meta["marca"] == "ford"
    assert por_anio[0].meta["termino_modelo"] == "ranger"
    assert por_anio[0].meta["termino_anio"] == "2024"


def test_el_abanico_no_se_redispara_en_las_paginas_siguientes():
    spider = _spider()
    search = _facet_anio([_valor_anio(2024, 207)])
    por_anio = [r for r in _requests_de(spider, search) if r.url.endswith("/2024/ranger")]
    assert "es_pagina_de_modelo" not in por_anio[0].meta


def test_tope_de_anios_por_modelo():
    spider = _spider(anio_min_volumen="1", max_anios_por_modelo="2")
    search = _facet_anio([_valor_anio(2020 + i, 1000 - i) for i in range(5)])
    por_anio = [r for r in _requests_de(spider, search) if "/ranger" in r.url]
    assert len(por_anio) == 2
    # y son los de MAS volumen, no los primeros que aparecen
    assert sorted(r.url.split("/")[3] for r in por_anio) == ["2020", "2021"]


def test_volumen_minimo_en_cero_desactiva_el_corte():
    spider = _spider(anio_min_volumen="0")
    search = _facet_anio([_valor_anio(2024, 207)])
    assert [r for r in _requests_de(spider, search) if "/2024/" in r.url] == []


def test_sin_facet_de_anio_el_spider_sigue_funcionando_como_antes():
    spider = _spider()
    assert _requests_de(spider, {"results": []}) == []


def test_abanico_por_modelo_marca_es_pagina_de_modelo_y_propaga_el_conteo():
    """El otro extremo del cableado: `_abanico_por_modelo` (wdxtkg39v1) tiene
    que dejar todo listo para que `_abanico_por_anio` pueda decidir sin
    volver a pedir nada - el gate usa el conteo que el facet MODEL ya trajo."""
    spider = MercadolibreDiscoverySpider(marcas="ford", modelo_min_volumen="150")
    search_de_marca = _facet_modelo([
        {
            "id": "65895",
            "name": "Ranger",
            "results": "(3.160)",
            "url": "https://autos.mercadolibre.com.ar/ranger/ford_NoIndex_True"
                   "#applied_filter_id%3DMODEL%26applied_value_id%3D65895",
        },
    ])
    import json

    ctx = {"appProps": {"sharedState": {"search": search_de_marca}}}
    html = (
        '<script id="__NORDIC_RENDERING_CTX__">_n.ctx.r=' + json.dumps(ctx) + "</script>"
    ).encode()
    request = Request(
        "https://autos.mercadolibre.com.ar/ford",
        meta={"marca": "ford", "page_count": 1, "es_pagina_de_marca": True},
    )
    response = HtmlResponse(url=request.url, body=html, encoding="utf-8", request=request)

    resultados = [r for r in spider.parse(response) if isinstance(r, R)]
    por_modelo = [r for r in resultados if r.url.endswith("/ranger")]
    assert len(por_modelo) == 1
    assert por_modelo[0].meta["es_pagina_de_modelo"] is True
    assert por_modelo[0].meta["modelo_count"] == 3160
