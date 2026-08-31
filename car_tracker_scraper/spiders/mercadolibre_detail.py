"""Detail Fetch spider para MercadoLibre.

Trae la ficha completa de avisos ya conocidos (via source_listing_key/url que
salieron de mercadolibre_discovery). Caro, priorizado por tier A/B/C (seccion
3.4 del doc de arquitectura) - por eso este spider recibe una lista de URLs
en vez de descubrirlas el mismo.

Uso:
    scrapy crawl mercadolibre_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone


from car_tracker_scraper.extraction.mercadolibre import extract_json_ld, extract_nordic_ctx
from car_tracker_scraper.spiders.base import BaseDetailSpider, landing_meta
from car_tracker_scraper.items import DeadListingItem, ListingDetailItem


class MercadolibreDetailSpider(BaseDetailSpider):
    name = "mercadolibre_detail"
    allowed_domains = ["auto.mercadolibre.com.ar"]

    # wdxtkg3auw: el 302 tiene que llegar a nosotros en crudo en vez de que lo
    # siga RedirectMiddleware, ver is_dead/redirect_to_follow.
    handle_httpstatus_list = BaseDetailSpider.handle_httpstatus_list + [302]

    # ML manda los avisos caidos a la busqueda de la marca/modelo en OTRO
    # subdominio: auto.mercadolibre.com.ar -> autos.mercadolibre.com.ar (con
    # una "s"), con el VIP original colgado en el fragmento #redirectedFromVip.
    _SEARCH_HOST = "autos.mercadolibre.com.ar"

    def is_dead(self, response):
        if super().is_dead(response):
            return True
        if response.status != 302:
            return False
        location = self._location_of(response)
        # Dos formas de reconocerlo, cualquiera alcanza: el salto al subdominio
        # de busqueda, o la marca explicita que ML deja en el fragmento.
        return (
            self._host_of(location) == self._SEARCH_HOST
            or "redirectedFromVip" in location
        )

    def redirect_to_follow(self, response):
        """Un 302 que NO es baja es un cambio de URL canonica (ML reescribe el
        slug del titulo) y hay que seguirlo: el aviso sigue vivo. Medido en la
        corrida de las 20:15 del 31/8: 38 redirects, 37 bajas y 1 de estos."""
        if response.status == 302:
            return self._location_of(response) or None
        return None

    def parse(self, response):
        html = response.text
        vehicle = extract_json_ld(html, "Vehicle") or {}
        offers = vehicle.get("offers") or {}

        # initialState.components confirmado contra un fixture real
        # (tests/fixtures/ml_detail_sample.html, un Fiat Palio real de ML) -
        # ya no es best-effort. Ojo: cuelga de appProps.pageProps, no de
        # appProps directamente como en el listado (paginas distintas, mismo
        # mecanismo __NORDIC_RENDERING_CTX__).
        ctx = extract_nordic_ctx(html)
        components = ctx.get("appProps", {}).get("pageProps", {}).get("initialState", {}).get(
            "components", {}
        )

        if not vehicle and not components:
            # wdxtkg39vm: medido contra 400 HTML reales de la landing zone
            # (2026-08-12 a 2026-08-27, mercadolibre/ en MinIO) - el 15%
            # (60/400) sin JSON-LD Vehicle TAMPOCO tenia initialState.components
            # (0 casos de "sin Vehicle pero con components" en la muestra, el
            # caso que si seria un fallo de parseo genuino). Para el 100% de
            # esos 60, ML sirve HTTP 200 en la MISMA url de detalle pero con el
            # buscador/rescue de la marca en su lugar (initialState trae
            # results/search_filter/pagination en vez de components/id, y el
            # filtro interno "notfinalized" en la query) - no una pagina de
            # "publicacion finalizada" separada como se asumia al abrir el
            # ticket. Por eso el chequeo es "ni vehicle NI components", no solo
            # "sin vehicle": confundir un fallo de parseo real con un aviso
            # muerto tiraria candidatos VIVOS en silencio ante un cambio de
            # formato de ML - la misma clase de riesgo que wdxtkg39v2 ya mostro
            # que no es teorico. No se yield-ea ListingDetailItem para este
            # caso (antes se yieldeaba uno con todos los campos en None, que
            # SI se publicaba a RabbitMQ - un bug de raiz distinto que esto
            # tambien corrige de paso).
            yield DeadListingItem(source="mercadolibre", url=response.url, item_type="dead_listing")
            return

        # El vendedor viene en dos formas distintas segun el tipo (confirmado
        # con 2 fixtures reales, 2026-07-31): concesionaria -> seller_card_motors
        # (con phone_link.track...item_seller_type="car_dealer"); particular ->
        # seller_profile (misma forma anidada seller_name.title.text, sin
        # phone_link). No asumir que seller_card_motors siempre existe.
        dealer_card = components.get("seller_card_motors")
        private_card = components.get("seller_profile")
        if dealer_card:
            seller_card = dealer_card
            # wdxtkg39v2: el fallback a "car_dealer" NO es una suposicion - la
            # presencia misma de `seller_card_motors` ya dice que el vendedor es
            # una concesionaria (los particulares vienen en `seller_profile`).
            # Antes se dependia solo de phone_link.track...item_seller_type, y
            # cuando ese link no venia el seller_type quedaba en None, lo que
            # hacia que ListingUpsertService descartara el aviso ENTERO y en
            # silencio. Medido sobre 60 fichas reales de la landing zone
            # (2026-08-27): 10 de 60 (17%) son concesionarias sin phone_link, o
            # sea que se estaba tirando 1 de cada 6 avisos sabiendo perfectamente
            # que era de concesionaria.
            seller_type = (
                (((dealer_card.get("phone_link") or {}).get("track") or {}).get("melidata_event") or {}).get(
                    "event_data"
                )
                or {}
            ).get("item_seller_type") or "car_dealer"
        elif private_card:
            seller_card = private_card
            seller_type = "particular"
        else:
            seller_card = {}
            seller_type = None

        # seller_id/state/item_status: el path universal components.track
        # (NO anidado en seller_card_motors) trae estos 3 campos para ambos
        # tipos de vendedor - mas robusto que depender de seller_card_motors,
        # que no existe para particulares.
        top_event_data = (
            ((components.get("track") or {}).get("melidata_event") or {}).get("event_data") or {}
        )

        item_proximity_rows = (components.get("item_proximity") or {}).get("content_rows") or []
        location_text = item_proximity_rows[0]["label"]["text"] if item_proximity_rows else None

        financing = components.get("initial_payment_amount") or {}

        yield ListingDetailItem(
            source="mercadolibre",
            source_listing_key=vehicle.get("sku"),
            url=response.url,
            brand_raw=vehicle.get("brand"),
            color=vehicle.get("color"),
            fuel_type_raw=vehicle.get("fuelType"),
            number_of_doors=vehicle.get("numberOfDoors"),
            transmission_raw=vehicle.get("vehicleTransmission"),
            item_condition=vehicle.get("itemCondition"),
            price_amount=offers.get("price"),
            price_currency=offers.get("priceCurrency"),
            price_valid_until=offers.get("priceValidUntil"),
            breadcrumb_raw=_breadcrumb_text(extract_json_ld(html, "BreadcrumbList")),
            subtitle_raw=(components.get("header") or {}).get("subtitle"),
            location_raw=location_text,
            highlighted_specs_raw=(components.get("highlighted_specs_attrs") or {}).get("components"),
            seller_name=(seller_card.get("seller_name") or {}).get("title", {}).get("text"),
            seller_type=seller_type,
            seller_id=top_event_data.get("seller_id"),
            province_raw=top_event_data.get("state"),
            item_status=top_event_data.get("item_status"),
            financing_initial_payment=financing.get("title", {}).get("text"),
            fetched_at=datetime.now(timezone.utc).isoformat(),
            **landing_meta(response),
        )


def _breadcrumb_text(breadcrumb: dict | None) -> str | None:
    if not breadcrumb:
        return None
    items = sorted(breadcrumb.get("itemListElement", []), key=lambda el: el.get("position", 0))
    names = [el.get("name") or (el.get("item") or {}).get("name") for el in items]
    names = [n for n in names if n]
    return " > ".join(names) if names else None
