"""Base compartida de los spiders, para que sumar un proveedor N+1 no implique
recopiar boilerplate (wdxtkg39pw).

Lo que vive aca es SOLO lo que era byte-identico entre los tres proveedores.
Deliberadamente NO hay una abstraccion sobre el parseo: los formatos de origen
son genuinamente distintos (blob JSON de ML, flight stream de Next.js en
Motordil, microdata schema.org en DeRuedas) y forzarlos bajo una interfaz comun
seria peor que la duplicacion que elimina. Cada proveedor sigue teniendo su
`extraction/{proveedor}.py` y su propio `parse()`.
"""
from __future__ import annotations

import scrapy


def landing_meta(response) -> dict:
    """Los tres campos que LandingZoneMiddleware deja en `response.meta`.

    Se repetian identicos en los 6 spiders. Son el puente a `raw_payload` del
    lado Java (wdxtkg30nm): sin ellos no se puede reprocesar desde el HTML
    crudo cuando se encuentra un bug de parseo.

    Uso: `**landing_meta(response)` al final de la construccion del item.
    """
    return {
        "s3_key": response.meta.get("s3_key"),
        "http_status": response.meta.get("http_status"),
        "parser_version": response.meta.get("parser_version"),
    }


class BaseDetailSpider(scrapy.Spider):
    """Recibe una lista de URLs ya descubiertas y pide la ficha de cada una.

    Las subclases solo implementan `parse()`. Todo lo de aca era identico en
    mercadolibre_detail, motordil_detail y deruedas_detail.
    """

    # wdxtkg30nk ("sticky session en Detail"): AntiBlockingMiddleware mantiene
    # la misma persona (UA/headers) durante toda la corrida en vez de rotar por
    # request - un mismo "usuario" mirando varias fichas seguidas es el patron
    # esperado, y rotar lo haria mas sospechoso, no menos.
    sticky_persona = True

    def __init__(self, urls_file: str | None = None, urls: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._urls: list[str] = []
        if urls_file:
            with open(urls_file, encoding="utf-8") as fh:
                self._urls = [line.strip() for line in fh if line.strip()]
        if urls:
            self._urls += [u.strip() for u in urls.split(",") if u.strip()]
        if not self._urls:
            raise ValueError("Pasar -a urls_file=path/to/urls.txt o -a urls=url1,url2,...")

    async def start(self):
        # async start() y no start_requests(): en Scrapy 2.17 start_requests()
        # no despacha NINGUN request (el spider abre y cierra en ~12ms, 0 items,
        # sin error visible) - incidente real de produccion 2026-08-18
        # (wdxtkg34th/wdxtkg35ba). Al estar en la base, un proveedor nuevo no
        # puede volver a caer en eso por copiar un ejemplo viejo.
        for url in self._urls:
            yield scrapy.Request(url, callback=self.parse)
