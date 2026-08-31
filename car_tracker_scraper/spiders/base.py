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

from urllib.parse import urlparse

import scrapy

from car_tracker_scraper.items import DeadListingItem


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

    # wdxtkg3auw: sin esto, HttpErrorMiddleware descarta los 404 antes de que
    # llegue a parse() y el aviso caido desaparece sin dejar rastro. Autocity
    # sirve 404 limpio para avisos vendidos (medido: 123 de 150 URLs pedidas en
    # una corrida del 31/8). Las subclases que necesiten mas codigos amplian
    # esta lista - ML agrega 302, ver mercadolibre_detail.
    handle_httpstatus_list = [404, 410]

    # Codigos que significan inequivocamente "este aviso ya no existe". 404/410
    # aplican a cualquier proveedor; lo que cambia por sitio es lo de arriba.
    DEAD_STATUSES = frozenset({404, 410})

    @property
    def source_slug(self) -> str:
        """Misma convencion que usa LandingZoneMiddleware para el prefijo S3."""
        return self.name.split("_")[0]

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
            yield scrapy.Request(url, callback=self._parse_or_dead)

    def _parse_or_dead(self, response):
        """Filtra avisos caidos antes de delegar en el `parse()` del proveedor.

        wdxtkg3auw: antes cada request iba derecho a `parse()`, y una URL que ya
        no correspondia a un aviso vivo simplemente no producia item. Como
        `run_batch.mark_detailed()` marca igual todo el batch, esos candidatos
        se re-pedian cada DETAIL_TIER_HOURS para siempre sin poder rendir nada.
        """
        if self.is_dead(response):
            yield DeadListingItem(
                source=self.source_slug, url=response.url, item_type="dead_listing"
            )
            return

        follow_to = self.redirect_to_follow(response)
        if follow_to is not None:
            yield response.follow(follow_to, callback=self._parse_or_dead)
            return

        produced = 0
        for item in self.parse(response):
            produced += 1
            yield item

        if produced == 0:
            # Ni item ni señal de muerto reconocida. Antes esto era una perdida
            # 100% silenciosa; ahora deja rastro para poder sumar la señal que
            # falte, en vez de descubrirla auditando Postgres meses despues.
            self.logger.warning(
                "wdxtkg3auw: %s (status=%s) no produjo item ni coincidio con una "
                "señal de baja conocida - revisar si este proveedor usa otra",
                response.url,
                response.status,
            )

    def is_dead(self, response) -> bool:
        """Señal generica de aviso caido. Las subclases la amplian, no la pisan
        (llamar a `super().is_dead(response)` primero)."""
        return response.status in self.DEAD_STATUSES

    def redirect_to_follow(self, response) -> str | None:
        """URL a seguir a mano cuando el proveedor necesita ver el redirect en
        crudo para distinguir baja de simple cambio de URL canonica. `None` =
        no aplica (el comportamiento normal de RedirectMiddleware alcanza)."""
        return None

    @staticmethod
    def _location_of(response) -> str:
        return response.headers.get("Location", b"").decode("latin-1")

    @staticmethod
    def _host_of(url: str) -> str:
        return urlparse(url).netloc.lower()
