"""Detail spider para V6 Marketplace (wdxtkg39qx).

Deliberadamente NO hereda de `BaseDetailSpider`: esa base pide UNA URL por
aviso y decide vivo/caido por status HTTP, pero aca no hace falta pisar
/auto/{slug} en absoluto (ver extraction/v6.py) - un unico GET a
`getPublishedCars` ya trae la ficha completa de TODOS los avisos activos,
incluidos los que Discovery encontro. Detail solo cruza los uids pedidos
contra esa respuesta.

Uso (misma interfaz -a urls_file/-a urls que las otras fuentes, aunque el
fetch real sea distinto):
    scrapy crawl v6_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

import scrapy

from car_tracker_scraper.extraction.v6 import (
    PUBLISHED_CARS_URL,
    agency_seller,
    flatten_specs,
    is_zero_km,
    resolve_currency,
    title,
)
from car_tracker_scraper.spiders.base import landing_meta
from car_tracker_scraper.items import DeadListingItem, ListingDetailItem


def uid_from_url(url: str) -> str | None:
    """El uid es el ultimo segmento del slug, despues del ultimo '-' (ver
    `detail_path` en extraction/v6.py - el sitio arma la URL de la misma
    forma, asi que deshacerla alcanza con partir por el ultimo guion)."""
    last_segment = url.rstrip("/").rsplit("/", 1)[-1]
    if "-" not in last_segment:
        return last_segment or None
    return last_segment.rsplit("-", 1)[-1] or None


class V6DetailSpider(scrapy.Spider):
    name = "v6_detail"
    allowed_domains = ["autoprecios-api.onrender.com"]

    # wdxtkg30nk: mismo criterio que BaseDetailSpider, aunque no se herede de
    # ella - un unico request en esta corrida, pero mantiene la persona
    # consistente si en el futuro se suma reintento.
    sticky_persona = True

    def __init__(self, urls_file: str | None = None, urls: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        raw_urls: list[str] = []
        if urls_file:
            with open(urls_file, encoding="utf-8") as fh:
                raw_urls = [line.strip() for line in fh if line.strip()]
        if urls:
            raw_urls += [u.strip() for u in urls.split(",") if u.strip()]
        if not raw_urls:
            raise ValueError("Pasar -a urls_file=path/to/urls.txt o -a urls=url1,url2,...")
        # dict uid->url: si dos URLs distintas resolvieran al mismo uid (no
        # deberia pasar), la ultima gana - no hay forma de que eso pierda un
        # aviso real, solo colisionaria un duplicado exacto.
        self._requested = {uid: url for url in raw_urls if (uid := uid_from_url(url))}

    async def start(self):
        yield scrapy.Request(PUBLISHED_CARS_URL, callback=self.parse)

    def parse(self, response):
        cars = {car.get("uid"): car for car in response.json()}

        for uid, url in self._requested.items():
            car = cars.get(uid)
            if car is None:
                # No esta en el catalogo publicado: vendido o despublicado.
                yield DeadListingItem(source="v6", url=url, item_type="dead_listing")
                continue

            if is_zero_km(car):
                # 0km fuera, igual que en Discovery - se coló en la ventana
                # entre corridas (ej. el concesionario corrigio el odometro).
                # No es un aviso caido, simplemente no emite item (mismo
                # patron que motordil_detail.py con vehiculos NEW).
                self.logger.debug("0km, no es el segmento usado: %s", url)
                continue

            seller_name, seller_type, seller_id = agency_seller(car)
            specs = car.get("specs") or {}
            mecanica = specs.get("mecánica") or {}
            carroceria = specs.get("carrocería") or {}

            yield ListingDetailItem(
                source="v6",
                source_listing_key=uid,
                url=url,
                brand_raw=car.get("brand"),
                model_raw=car.get("model"),
                version_raw=car.get("version"),
                year_raw=car.get("year"),
                km=car.get("kilometers"),
                title_raw=title(car),
                color=None,  # V6 no expone color en ningun campo confirmado
                fuel_type_raw=mecanica.get("combustible"),
                number_of_doors=carroceria.get("cantidad_puertas"),
                transmission_raw=mecanica.get("caja_tipo"),
                item_condition=None,  # sin campo de condicion; el filtro de 0km ya cubre nuevos
                price_amount=car.get("price"),
                price_currency=resolve_currency(car),
                price_valid_until=None,  # V6 no expone vigencia de precio
                breadcrumb_raw=None,  # marca/modelo/version ya vienen sueltos, no hay que parsear texto
                subtitle_raw=None,  # idem
                location_raw=car.get("city"),
                highlighted_specs_raw=flatten_specs(specs),
                seller_name=seller_name,
                seller_type=seller_type,
                seller_id=seller_id,
                province_raw=car.get("province"),
                item_status=None,
                financing_initial_payment=car.get("anticipo"),
                fetched_at=datetime.now(timezone.utc).isoformat(),
                **landing_meta(response),
            )
