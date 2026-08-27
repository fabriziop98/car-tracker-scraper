"""Detail Fetch spider para Motordil (wdxtkg30xr).

Trae la ficha completa de avisos que ya descubrio motordil_discovery. Mismo
rol que mercadolibre_detail: caro, recibe una lista de URLs en vez de
descubrirlas.

La ficha expone el dato por dos vias independientes en el mismo documento:
el objeto `publication` del flight stream (el rico: equipamiento, descripcion
larga, datos del concesionario) y un JSON-LD schema.org. Se usa `publication`
como fuente primaria y el JSON-LD como respaldo de precio/moneda.

OJO con el @type del JSON-LD: Motordil usa "Car", no "Vehicle" como ML
(confirmado contra el fixture real 2026-08-26; el doc de hallazgos de Fase 0
decia "Vehicle" y estaba equivocado).

Uso:
    scrapy crawl motordil_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone


from car_tracker_scraper.extraction.common import extract_json_ld
from car_tracker_scraper.extraction.motordil import extract_rsc_payload, iter_objects_with_key
from car_tracker_scraper.spiders.base import BaseDetailSpider, landing_meta
from car_tracker_scraper.items import ListingDetailItem


class MotordilDetailSpider(BaseDetailSpider):
    name = "motordil_detail"
    allowed_domains = ["www.motordil.com", "motordil.com"]

    def parse(self, response):
        html = response.text

        publications = list(iter_objects_with_key(extract_rsc_payload(html), "publication"))
        if not publications:
            # No es un crash: una ficha dada de baja/expirada puede servir un
            # shell sin publication. Se loguea y no se emite item, que es lo
            # mismo que hace el pipeline con cualquier aviso que ya no esta.
            self.logger.warning("Sin objeto `publication` en %s - aviso caido o cambio de formato", response.url)
            return
        pub = publications[0]

        # 0km fuera, igual que en Discovery - aca con el campo autoritativo
        # (`vehicleStatus`), que la grilla no expone. Doble filtro a proposito:
        # el de Discovery evita gastar el fetch, este ataja lo que igual llego
        # (ej. un aviso que cargo el odometro despues de publicarse).
        if (pub.get("vehicleStatus") or "").upper() == "NEW":
            self.logger.debug("0km, no es el segmento usado: %s", response.url)
            return

        car = extract_json_ld(html, "Car") or {}
        offers = car.get("offers") or {}

        meta = pub.get("metadata") or {}
        details = pub.get("details") or {}
        dealership = pub.get("dealership") or {}
        dealer_location = ((dealership.get("locationData") or {}).get("location") or {})

        yield ListingDetailItem(
            source="motordil",
            source_listing_key=pub.get("id"),
            url=response.url,
            brand_raw=(meta.get("make") or {}).get("make"),
            model_raw=meta.get("model"),
            version_raw=meta.get("version"),
            year_raw=pub.get("year"),
            km=pub.get("odometer"),
            title_raw=pub.get("shortDescription"),
            color=pub.get("color"),
            fuel_type_raw=details.get("fuelType"),
            number_of_doors=details.get("doorCount"),
            transmission_raw=details.get("transmissionType"),
            item_condition=pub.get("vehicleStatus"),
            price_amount=pub.get("price") if pub.get("price") is not None else offers.get("price"),
            price_currency=(pub.get("currency") or {}).get("symbol") or offers.get("priceCurrency"),
            price_valid_until=None,  # Motordil no expone vigencia de precio
            breadcrumb_raw=None,  # no hay breadcrumb: marca/modelo/version ya vienen sueltos
            subtitle_raw=None,  # idem: no hay que parsear texto libre en esta fuente
            location_raw=pub.get("location"),
            highlighted_specs_raw=meta.get("attributes"),
            seller_name=dealership.get("name"),
            # En el fixture real las 24 publicaciones de la grilla tenian
            # `dealership` (ninguna de particular). Se deriva de su presencia,
            # pero NO confirmado todavia que Motordil sea 100% concesionarias -
            # revisar con mas marcas antes de tratarlo como invariante.
            seller_type="car_dealer" if dealership else None,
            seller_id=dealership.get("slug"),
            province_raw=dealer_location.get("state"),
            item_status=pub.get("status"),
            financing_initial_payment=pub.get("downpaymentAmount") or None,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            **landing_meta(response),
        )
