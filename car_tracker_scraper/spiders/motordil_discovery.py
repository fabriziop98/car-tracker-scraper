"""Discovery spider para Motordil (wdxtkg30xr).

Segunda fuente del proyecto, despues de MercadoLibre. Motivo de que sea esta y
no DeRuedas: su robots.txt es permisivo (no bloquea bots de IA, a diferencia
de ML y DeRuedas), su paginacion es exacta y su extraccion ya se habia
verificado contra el sitio real en Fase 0.

Diferencias importantes contra el Discovery de ML:

*   NO hace falta el heuristico `_resolve_marca` (wdxtkg3980): cada aviso trae
    `metadata.make.make` explicito, asi que la marca sale del ITEM y no del
    parametro de busqueda. El bug de ML (pedir una marca invalida devolvia un
    listado generico taggeado con esa marca) no puede pasar aca.
*   `priceCurrency=USD` en la URL es preferencia de VISUALIZACION, no filtro -
    en Fase 0 aparecieron avisos en ARS igual. Siempre leer `currency.symbol`
    del propio aviso, nunca asumir por el parametro.
*   No hay filtro de 0km por URL: se descarta por `vehicleStatus`/`year` igual
    que en ML se descarta por el texto "0 Km".

Uso:
    scrapy crawl motordil_discovery -a marcas=alfa-romeo,ford \
        -O output/discovery_%(time)s.jsonl
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

import scrapy

from car_tracker_scraper.extraction.common import resolve_marca_slug
from car_tracker_scraper.extraction.motordil import (
    detail_path,
    extract_rsc_payload,
    iter_objects_with_key,
)
from car_tracker_scraper.spiders.base import landing_meta
from car_tracker_scraper.items import ListingSummaryItem

BASE_URL = "https://www.motordil.com"


def _listing_url(listing: dict) -> str | None:
    path = detail_path(listing)
    return f"{BASE_URL}{path}" if path else None


# wdxtkg39pw: la normalizacion de marca al slug curado se movio a
# extraction/common.py cuando DeRuedas (tercera fuente) necesito exactamente lo
# mismo. Se mantiene el alias privado para no tocar los tests que ya lo usaban
# por este nombre.
_resolve_marca_slug = resolve_marca_slug


def _title(listing: dict) -> str | None:
    """Motordil no expone un titulo libre en la grilla (a diferencia de ML) -
    se compone desde los campos estructurados solo para tener algo legible en
    el .jsonl de salida y en los logs. La normalizacion del lado Java NO usa
    esto: usa make/model/version por separado, que es justamente la ventaja de
    esta fuente."""
    meta = listing.get("metadata") or {}
    parts = [
        str(listing["year"]) if listing.get("year") else None,
        ((meta.get("make") or {}).get("make")),
        meta.get("model"),
        meta.get("version"),
    ]
    parts = [p for p in parts if p]
    return " ".join(parts) if parts else None


class MotordilDiscoverySpider(scrapy.Spider):
    name = "motordil_discovery"
    allowed_domains = ["www.motordil.com", "motordil.com"]

    def __init__(self, marcas: str = "alfa-romeo", max_pages: str = "60", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]
        # Tope de SEGURIDAD, no el corte real (mismo criterio que quedo en el
        # Discovery de ML tras wdxtkg398b): se pagina hasta que una pagina no
        # traiga avisos nuevos. A 24 avisos por pagina, 60 paginas son ~1440
        # avisos de una sola marca - holgado para el inventario real de
        # Motordil, que es un sitio mucho mas chico que ML.
        self.max_pages = int(max_pages)

    def _page_url(self, marca: str, page: int) -> str:
        # Sin el parametro `_rsc`: con el, la respuesta es solo el shell de la
        # pagina y no trae ningun aviso (ver extraction/motordil.py).
        query = urlencode(
            {"priceCurrency": "USD", "sellerType": "ALL", "v": marca, "page": page}
        )
        return f"{BASE_URL}/results?{query}"

    async def start(self):
        # async start() en vez de start_requests(): ver el comentario largo en
        # mercadolibre_discovery.py (start_requests() no despacha nada en
        # Scrapy 2.17, incidente real de wdxtkg34th/wdxtkg35ba).
        for marca in self.marcas:
            yield scrapy.Request(
                self._page_url(marca, 1),
                callback=self.parse,
                meta={"marca": marca, "page": 1},
            )

    def parse(self, response):
        marca = response.meta["marca"]
        page = response.meta["page"]

        listings = list(iter_objects_with_key(extract_rsc_payload(response.text), "listing"))

        emitted = 0
        for listing in listings:
            url = _listing_url(listing)
            if not url:
                self.logger.warning("Aviso sin slug ni id, no se puede armar URL: %r", listing.get("id"))
                continue

            # 0km fuera: el proyecto trackea el mercado de USADOS. El Discovery
            # de ML ya descarta 0km por el texto "0 Km"; Motordil no expone la
            # condicion en la grilla (`vehicleStatus` solo esta en la ficha),
            # asi que aca se usa el odometro. Incidente real 2026-08-27: sin
            # este filtro entraron 114 autos nuevos (0 km, modelos 2024-2026,
            # titulos "0KM SIN RODAR A PATENTAR") a la serie de precios de
            # usados, donde distorsionan cualquier cohorte que toquen.
            if listing.get("odometer") == 0:
                continue

            meta = listing.get("metadata") or {}
            price = listing.get("price")
            currency = (listing.get("currency") or {}).get("symbol")

            yield ListingSummaryItem(
                source="motordil",
                source_listing_key=listing.get("id"),
                url=url,
                # La marca real del propio aviso, no la pedida en la busqueda,
                # pero normalizada al slug curado (ver _resolve_marca_slug).
                marca=_resolve_marca_slug((meta.get("make") or {}).get("make"), marca, self.marcas),
                is_ad=False,  # Motordil no mezcla publicidad en la grilla de /results
                category_id=meta.get("vehicleType"),
                domain_id=None,
                title_raw=_title(listing),
                price_amount=price,
                price_currency=currency,
                attributes_raw=[
                    str(listing.get("year")) if listing.get("year") else None,
                    f"{listing['odometer']} km" if listing.get("odometer") is not None else None,
                ],
                location_raw=(
                    ((listing.get("dealership") or {}).get("locationData") or {}).get("location") or {}
                ).get("state"),
                financing_initial_payment=listing.get("downpaymentAmount") or None,
                discovered_at=datetime.now(timezone.utc).isoformat(),
                **landing_meta(response),
            )
            emitted += 1

        # Corte real: una pagina sin avisos = se agoto el inventario de la
        # marca. Confirmado en Fase 0 que la paginacion es limpia (page=1 -> 24,
        # page=2 -> 17, total == resultCount, sin solapamiento), asi que no hace
        # falta deduplicar por id entre paginas para saber cuando parar.
        if emitted == 0:
            return
        if page >= self.max_pages:
            self.logger.warning(
                "%s: llego al tope de seguridad max_pages=%d sin agotar el inventario "
                "(ver wdxtkg30xr).",
                marca, self.max_pages,
            )
            return

        yield response.follow(
            self._page_url(marca, page + 1),
            callback=self.parse,
            meta={"marca": marca, "page": page + 1},
        )
