"""Señales de aviso caido por proveedor (wdxtkg3auw).

Cada sitio da de baja un aviso de una forma distinta, y confundir "fallo de
parseo" con "aviso muerto" tira candidatos VIVOS en silencio. Por eso cada
señal se fija con un test contra la respuesta real que emite ese sitio, medida
en produccion:

  - Autocity  -> 404 limpio (123 de 150 URLs en la corrida del 31/8 10:00)
  - ML        -> 302 a autos.mercadolibre.com.ar (con "s") + #redirectedFromVip
                 (37 de 400 en la corrida del 31/8 20:15)
  - ML        -> ademas, 302 dentro del MISMO host = cambio de slug canonico,
                 el aviso sigue vivo y hay que seguirlo (1 de esos 38)
"""
from scrapy.http import HtmlResponse, Request, Response

from car_tracker_scraper.items import DeadListingItem, ListingDetailItem
from car_tracker_scraper.spiders.autocity_detail import AutocityDetailSpider
from car_tracker_scraper.spiders.deruedas_detail import DeruedasDetailSpider
from car_tracker_scraper.spiders.mercadolibre_detail import MercadolibreDetailSpider
from car_tracker_scraper.spiders.motordil_detail import MotordilDetailSpider

ML_VIP = "https://auto.mercadolibre.com.ar/MLA-1972166055-fiat-strada-adventure-_JM"


def _redirect(url, location, status=302):
    return Response(
        url=url,
        status=status,
        headers={"Location": location},
        request=Request(url),
    )


def _all_spiders():
    return [
        MercadolibreDetailSpider(urls=ML_VIP),
        MotordilDetailSpider(urls="https://www.motordil.com/auto/x"),
        DeruedasDetailSpider(urls="https://www.deruedas.com.ar/vendo/x"),
        AutocityDetailSpider(urls="https://autocity.com.ar/catalogo/usados/m-x/m-y/z/"),
    ]


def test_404_es_señal_de_baja_en_todos_los_proveedores():
    """404/410 valen para cualquier sitio - va en la base, no por spider."""
    for spider in _all_spiders():
        url = spider._urls[0]
        for status in (404, 410):
            response = Response(url=url, status=status, request=Request(url))
            assert spider.is_dead(response), f"{spider.name} deberia dar {status} por muerto"


def test_el_404_llega_a_parse_en_vez_de_ser_descartado():
    """Sin handle_httpstatus_list, HttpErrorMiddleware se come el 404 antes de
    parse() y el aviso caido se pierde sin dejar rastro - que era el bug."""
    for spider in _all_spiders():
        assert 404 in spider.handle_httpstatus_list, spider.name


def test_404_produce_DeadListingItem_y_ningun_ListingDetailItem():
    spider = AutocityDetailSpider(urls="https://autocity.com.ar/catalogo/usados/m-chery/m-tiggo/4-2-0/")
    url = spider._urls[0]
    response = Response(url=url, status=404, request=Request(url))

    items = list(spider._parse_or_dead(response))

    assert len(items) == 1
    assert isinstance(items[0], DeadListingItem)
    assert items[0]["source"] == "autocity"
    assert items[0]["url"] == url
    assert items[0]["item_type"] == "dead_listing"
    # Critico: DeadListingItem NO es un ListingDetailItem, para que
    # RabbitMQPublishPipeline (que filtra por isinstance) nunca lo publique.
    assert not isinstance(items[0], ListingDetailItem)


def test_ml_302_al_subdominio_de_busqueda_es_baja():
    spider = MercadolibreDetailSpider(urls=ML_VIP)
    location = (
        "https://autos.mercadolibre.com.ar/fiat/strada-adventure/2015/"
        "#redirectedFromVip=https%3A%2F%2Fauto.mercadolibre.com.ar%2FMLA-1972166055"
    )
    response = _redirect(ML_VIP, location)

    assert spider.is_dead(response)
    items = list(spider._parse_or_dead(response))
    assert len(items) == 1 and isinstance(items[0], DeadListingItem)
    assert items[0]["source"] == "mercadolibre"


def test_ml_302_dentro_del_mismo_host_NO_es_baja_y_se_sigue():
    """El aviso sigue vivo, ML solo reescribio el slug del titulo. Marcarlo
    muerto seria tirar un candidato vivo - el riesgo que este modulo evita."""
    spider = MercadolibreDetailSpider(urls=ML_VIP)
    location = "https://auto.mercadolibre.com.ar/MLA-1972486055-honda-fit-15-ex-5-ptas-_JM"
    response = _redirect(ML_VIP, location)

    assert not spider.is_dead(response)
    assert spider.redirect_to_follow(response) == location

    out = list(spider._parse_or_dead(response))
    assert len(out) == 1
    assert isinstance(out[0], Request), "deberia re-pedir la URL nueva, no emitir item"
    assert out[0].url == location


def test_ml_302_se_recibe_en_crudo_en_vez_de_seguirlo_el_middleware():
    spider = MercadolibreDetailSpider(urls=ML_VIP)
    assert 302 in spider.handle_httpstatus_list
    # y sin pisar los de la base
    assert 404 in spider.handle_httpstatus_list


def test_200_normal_sigue_yendo_al_parse_del_proveedor():
    """La red de seguridad no puede alterar el camino feliz."""
    spider = AutocityDetailSpider(urls="https://autocity.com.ar/catalogo/usados/m-x/m-y/z/")
    url = spider._urls[0]
    response = HtmlResponse(url=url, body=b"<html></html>", encoding="utf-8", request=Request(url))

    assert not spider.is_dead(response)
    assert spider.redirect_to_follow(response) is None


def test_source_slug_sale_del_nombre_del_spider():
    """Misma convencion que LandingZoneMiddleware para el prefijo S3."""
    esperado = {
        "mercadolibre_detail": "mercadolibre",
        "motordil_detail": "motordil",
        "deruedas_detail": "deruedas",
        "autocity_detail": "autocity",
    }
    for spider in _all_spiders():
        assert spider.source_slug == esperado[spider.name]
