"""Detail Fetch spider para DeRuedas (wdxtkg39pw).

Fase 0 nunca reverse-engineerio esta pagina (a diferencia de ML y Motordil, que
si tienen su seccion "Detail Fetch - RESUELTO" en findings_clickup.md) - el
formato se confirmo contra un fixture real recien el 2026-08-27. Diferencias
con la grilla que hay que tener presentes:

*   El itemtype es `schema.org/Vehicle`, NO `Car`.
*   El año viene en `modelDate`, no `vehicleModelDate`.
*   La marca esta ANIDADA en un itemscope `Brand` (`[itemprop=brand] [itemprop=name]`).
*   Los kilometros vienen como "KMT 109000" (unitCode + valor pegados).
*   NO hay `a.versionLink`: el trim solo aparece dentro de `description`.
*   No hay JSON-LD en toda la pagina.

Uso:
    scrapy crawl deruedas_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

from parsel import Selector

from car_tracker_scraper.extraction.deruedas import (
    detail_published_price,
    detail_version,
    detail_vehicle,
    parse_km,
    parse_year,
    prop,
    seller_id,
)
from car_tracker_scraper.spiders.base import BaseDetailSpider, landing_meta
from car_tracker_scraper.items import ListingDetailItem


class DeruedasDetailSpider(BaseDetailSpider):
    name = "deruedas_detail"
    allowed_domains = ["www.deruedas.com.ar", "deruedas.com.ar"]
    custom_settings = {
        # Mismo respeto al Crawl-delay: 5 del robots.txt que en Discovery.
        "DOWNLOAD_DELAY": 5,
    }

    # wdxtkg3auw: el 302 tiene que llegar en crudo, no seguirlo el middleware.
    handle_httpstatus_list = BaseDetailSpider.handle_httpstatus_list + [302]

    def is_dead(self, response):
        """DeRuedas manda los avisos caidos a la HOME del sitio con un 302
        (no 404 como Autocity, ni al buscador de la marca como ML - cada sitio
        lo hace distinto, por eso ninguna señal se asume).

        Identificado el 2026-08-31 con el warning que introdujo wdxtkg3auw
        justamente para esto: en el batch de las 18:45 aparecio
        `https://www.deruedas.com.ar/ (status=200) no produjo item`, o sea
        RedirectMiddleware siguiendo el 302 hasta la portada, que obviamente no
        parsea como ficha.

        Se exige que el destino sea la RAIZ, no cualquier redirect del mismo
        host: un 302 a otra ficha seria un cambio de URL canonica con el aviso
        vivo, y marcarlo muerto tiraria un candidato bueno."""
        if super().is_dead(response):
            return True
        if response.status != 302:
            return False
        location = self._location_of(response)
        if not location:
            return False
        from urllib.parse import urlparse

        destino = urlparse(location)
        return destino.path in ("", "/") and (
            destino.netloc == "" or destino.netloc.lower() in self.allowed_domains
        )

    def parse(self, response):
        html = response.text
        vehicle = detail_vehicle(html)
        if vehicle is None:
            # Aviso dado de baja o cambio de formato: no es un crash, no hay
            # item que emitir. Mismo criterio que el Detail de Motordil.
            self.logger.warning("Sin bloque schema.org/Vehicle en %s - aviso caido o cambio de formato", response.url)
            return

        # 0km fuera, mismo criterio que en Discovery: por odometro explicito en
        # 0, no por itemCondition (esta fuente lo marca NewCondition cuando
        # falta el kilometraje - ver el hallazgo de Fase 0).
        if parse_km(prop(vehicle, "mileageFromOdometer")) == 0:
            self.logger.debug("0km, no es el segmento usado: %s", response.url)
            return

        brand = (
            vehicle.css('[itemprop="brand"] [itemprop="name"]::attr(content)').get()
            or vehicle.css('[itemprop="brand"] [itemprop="name"]::text').get()
        )
        brand = brand.strip() if brand else None
        model = prop(vehicle, "model")
        description = prop(vehicle, "description")

        # Precio del HTML plano, NO del microdata: DeRuedas convierte a ARS con
        # su propia cotizacion (17 de 30 avisos del fixture real estan
        # publicados en USD y el microdata los declara ARS igual).
        price_amount, price_currency = detail_published_price(html)

        # "Cordoba, Capital" -> "Cordoba". La provincia alimenta el fingerprint
        # de dedup, asi que se queda con el primer componente, que es el que
        # tiene granularidad de provincia.
        address = prop(vehicle, "address")
        province = address.split(",")[0].strip() if address else None

        # OJO: el link con `codUsr` esta FUERA del itemscope Vehicle (esta en el
        # bloque del vendedor), asi que se busca sobre la pagina entera. Buscarlo
        # dentro del bloque Vehicle devolvia None siempre.
        dealer_id = seller_id(Selector(text=html))

        yield ListingDetailItem(
            source="deruedas",
            source_listing_key=prop(vehicle, "sku"),
            url=response.url,
            brand_raw=brand,
            model_raw=model,
            version_raw=detail_version(description, brand, model),
            year_raw=parse_year(prop(vehicle, "modelDate")),
            km=parse_km(prop(vehicle, "mileageFromOdometer")),
            title_raw=prop(vehicle, "name"),
            color=None,
            fuel_type_raw=None,  # la ficha no lo expone; la grilla si (fuelType)
            number_of_doors=None,
            transmission_raw=None,
            item_condition=prop(vehicle, "availability"),
            price_amount=price_amount,
            price_currency=price_currency,
            price_valid_until=prop(vehicle, "priceValidUntil"),
            breadcrumb_raw=None,
            subtitle_raw=description,
            location_raw=address,
            highlighted_specs_raw=None,
            seller_name=Selector(text=html).css('[itemtype*="schema.org/Person"] [itemprop="name"]::attr(content)').get(),
            # codUsr presente = concesionaria; ausente = particular. Se deja
            # seller_type en None cuando no hay codUsr en vez de asumir
            # 'particular', porque en la ficha el link puede no renderizarse.
            seller_type="car_dealer" if dealer_id else None,
            seller_id=dealer_id,
            province_raw=province,
            item_status=None,
            financing_initial_payment=None,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            **landing_meta(response),
        )
