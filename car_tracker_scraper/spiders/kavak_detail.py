"""Detail Fetch spider para Kavak (wdxtkg3hc5).

**Aviso caido = HTTP 200 con analytics vacio, NO 404.** Confirmado contra el
sitio real (`?id=999999999`): Kavak siempre devuelve 200, y la pagina de
"empty state" trae igual un evento `vip_viewed` pero con `car_id: null` (ver
`detail_analytics()` en extraction/kavak.py). El default de BaseDetailSpider
(404/410) NO alcanza aca - hace falta `is_dead()` propio basado en contenido,
o el candidato nunca se marca muerto en el tracker y se re-pide cada
DETAIL_TIER_HOURS para siempre (wdxtkg3auw).

Kavak es un revendedor unico (ver docstring de extraction/kavak.py) -
seller_type/seller_name se hardcodean, no se extraen.

Uso:
    scrapy crawl kavak_detail -a urls_file=path/to/urls.txt \
        -O output/detail_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone

from car_tracker_scraper.extraction.kavak import (
    detail_analytics,
    detail_breadcrumb,
    detail_dynamic,
    detail_price,
    detail_province,
    detail_sucursal,
)
from car_tracker_scraper.extraction.motordil import extract_rsc_payload
from car_tracker_scraper.spiders.base import BaseDetailSpider, landing_meta
from car_tracker_scraper.items import ListingDetailItem


class KavakDetailSpider(BaseDetailSpider):
    name = "kavak_detail"
    allowed_domains = ["www.kavak.com", "kavak.com"]
    custom_settings = {
        # Mismo Crawl-delay: 20 que Discovery.
        "DOWNLOAD_DELAY": 20,
    }

    def is_dead(self, response) -> bool:
        if super().is_dead(response):
            return True
        payload = extract_rsc_payload(response.text)
        return detail_analytics(payload) is None

    def parse(self, response):
        payload = extract_rsc_payload(response.text)
        analytics = detail_analytics(payload)
        if analytics is None:
            # is_dead() ya filtro este caso antes de llegar aca en una corrida
            # real - esto solo cubre un cambio de formato que is_dead() no
            # haya detectado, mismo criterio defensivo que el resto de las
            # fuentes (log + return, nunca crashear).
            self.logger.warning(
                "Sin evento vip_viewed valido en %s - aviso caido o cambio de formato",
                response.url,
            )
            return

        km = analytics.get("car_mileage")
        if km == 0:
            self.logger.debug("0km, no es el segmento usado: %s", response.url)
            return

        price_amount, price_currency = detail_price(analytics)
        dynamic = detail_dynamic(payload) or {}
        province = detail_province(dynamic)
        sucursal = detail_sucursal(dynamic)

        yield ListingDetailItem(
            source="kavak",
            source_listing_key=str(analytics["car_id"]),
            url=response.url,
            brand_raw=analytics.get("car_make"),
            model_raw=analytics.get("car_model"),
            version_raw=analytics.get("car_version"),
            year_raw=analytics.get("car_year"),
            km=km,
            title_raw=" ".join(
                p for p in (analytics.get("car_make"), analytics.get("car_model"), analytics.get("car_version")) if p
            ) or None,
            color=analytics.get("car_color") or None,
            fuel_type_raw=analytics.get("car_fuel") or None,
            number_of_doors=None,
            transmission_raw=analytics.get("car_transmission") or None,
            item_condition=analytics.get("car_condition"),
            price_amount=price_amount,
            price_currency=price_currency,
            price_valid_until=None,
            breadcrumb_raw=detail_breadcrumb(payload),
            subtitle_raw=None,
            location_raw=", ".join(p for p in (sucursal, province) if p) or None,
            highlighted_specs_raw=None,
            # Revendedor unico (ver docstring del modulo) - no hay vendedores
            # distintos que extraer, a diferencia de un clasificado.
            seller_name="Kavak",
            seller_type="car_dealer",
            seller_id=None,
            province_raw=province,
            item_status=analytics.get("car_status") or None,
            financing_initial_payment=None,  # no aplica, ver docstring de extraction/kavak.py
            fetched_at=datetime.now(timezone.utc).isoformat(),
            **landing_meta(response),
        )
