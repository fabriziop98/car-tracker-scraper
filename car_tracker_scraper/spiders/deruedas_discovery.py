"""Discovery spider para DeRuedas (wdxtkg39pw).

Tercera fuente. A diferencia de ML y Motordil, DeRuedas no embebe JSON: es un
ASP clasico con un endpoint interno (`busCraw.asp`) que devuelve un fragmento
HTML con una tarjeta por aviso, cada una con microdata schema.org. Ver
extraction/deruedas.py para el detalle del formato.

robots.txt: DeRuedas bloquea explicitamente ClaudeBot/GPTBot/CCBot y usa
Content-Signal (`ai-train=no`). Ese bloqueo es sobre bots de IA, no sobre el
scraper del proyecto, que corre con su propio UA - misma situacion que
MercadoLibre y la misma decision de negocio ya tomada en settings.py
(ROBOTSTXT_OBEY=False). Su regla general para `User-agent: *` pide
`Crawl-delay: 5`; el token bucket compartido va a ~1 req/s, asi que para
respetarlo esta fuente usa su propio DOWNLOAD_DELAY (ver custom_settings).

Uso:
    scrapy crawl deruedas_discovery -a marcas=audi,ford \
        -O output/discovery_%(time)s.jsonl
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import scrapy

from car_tracker_scraper.extraction.common import resolve_marca_slug
from car_tracker_scraper.extraction.deruedas import (
    card_id,
    iter_cards,
    parse_km,
    parse_year,
    prop,
    published_price,
    seller_id,
    version_text,
)
from car_tracker_scraper.spiders.base import landing_meta
from car_tracker_scraper.items import ListingSummaryItem

BASE_URL = "https://www.deruedas.com.ar"
SEGMENTO_AUTOS = 0


def marca_param(slug: str) -> str:
    """slug curado -> valor del parametro `marca` de DeRuedas.

    Fase 0 solo confirmo el formato con 'Audi' (una palabra, capitalizada). Para
    marcas de varias palabras ('mercedes-benz') la forma exacta que espera el
    sitio NO esta verificada. No es critico: la marca con la que se taggea cada
    aviso sale del propio microdata de la tarjeta, no de este parametro (ver
    parse()), asi que si el filtro no aplica y DeRuedas devuelve un listado
    generico, los avisos igual quedan bien clasificados - que es exactamente el
    modo en que fallo MercadoLibre en wdxtkg3980.
    """
    return "-".join(part.capitalize() for part in slug.split("-"))


class DeruedasDiscoverySpider(scrapy.Spider):
    name = "deruedas_discovery"
    allowed_domains = ["www.deruedas.com.ar", "deruedas.com.ar"]
    custom_settings = {
        # robots.txt de DeRuedas pide Crawl-delay: 5 bajo User-agent: *. El
        # token bucket global (1 req/s) es mas agresivo que eso, asi que se
        # suma un delay propio SOLO para esta fuente. No reemplaza al token
        # bucket, se acumula con el.
        "DOWNLOAD_DELAY": 5,
    }

    def __init__(self, marcas: str = "audi", max_pages: str = "80", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]
        # Tope de seguridad, no el corte real: se pagina hasta que una pagina
        # no traiga tarjetas nuevas. A 30 tarjetas por pagina, 80 paginas son
        # 2400 avisos de una sola marca.
        self.max_pages = int(max_pages)

    def _page_url(self, marca: str, page: int) -> str:
        query = urlencode({
            "segmento": SEGMENTO_AUTOS,
            "marca": marca_param(marca),
            "weNeed": "divAll",
            "pag": page,
            # cachebuster: el sitio lo manda en cada XHR real
            "_": int(time.time() * 1000),
        })
        return f"{BASE_URL}/busCraw.asp?{query}"

    def _headers(self, marca: str) -> dict:
        # busCraw.asp es un endpoint XHR: se piden los mismos headers que manda
        # el sitio. Si el server exige el Referer quedo sin confirmar en Fase 0,
        # asi que se manda igual (no cuesta nada y evita un 403 sorpresa).
        return {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/bus.asp?segmento={SEGMENTO_AUTOS}&marca={marca_param(marca)}",
        }

    async def start(self):
        # async start() en vez de start_requests(): ver el comentario en
        # mercadolibre_discovery.py (start_requests() no despacha nada en
        # Scrapy 2.17).
        for marca in self.marcas:
            yield scrapy.Request(
                self._page_url(marca, 1),
                callback=self.parse,
                headers=self._headers(marca),
                meta={"marca": marca, "page": 1},
            )

    def parse(self, response):
        marca = response.meta["marca"]
        page = response.meta["page"]
        seen_ids = response.meta.get("seen_ids", set())

        emitted = 0
        page_ids = set()
        for card in iter_cards(response.text):
            listing_id = card_id(card)
            url = prop(card, "url")
            if not listing_id or not url:
                self.logger.warning("Tarjeta sin id o sin URL, la salteo: %r", listing_id)
                continue
            page_ids.add(listing_id)

            year = parse_year(prop(card, "vehicleModelDate"))
            km = parse_km(prop(card, "mileageFromOdometer"))

            # 0km fuera (proyecto = mercado de usados). Se filtra por odometro
            # EXPLICITO en 0 y NO por `itemCondition`: el hallazgo de Fase 0 es
            # que esta fuente marca NewCondition cuando el vendedor no cargo los
            # kilometros, asi que filtrar por condicion tiraria usados reales.
            # km ausente (None) NO se descarta por la misma razon.
            if km == 0:
                continue
            # Precio del TEXTO, no del microdata: DeRuedas declara ARS siempre
            # y convierte los avisos en USD con su propia cotizacion. Ver
            # published_price() para el detalle del hallazgo.
            price_amount, price_currency = published_price(card)
            version = version_text(card)

            yield ListingSummaryItem(
                source="deruedas",
                source_listing_key=listing_id,
                url=response.urljoin(url),
                # La marca del propio aviso (microdata), normalizada al slug
                # curado - no la del parametro de busqueda.
                marca=resolve_marca_slug(prop(card, "brand"), marca, self.marcas),
                is_ad=False,  # el fragmento de busCraw.asp no mezcla publicidad
                category_id=None,
                domain_id=None,
                title_raw=" ".join(
                    p for p in (prop(card, "brand"), prop(card, "model"), version) if p
                ) or None,
                price_amount=price_amount,
                price_currency=price_currency,
                attributes_raw=[
                    str(year) if year else None,
                    f"{km} km" if km is not None else None,
                ],
                location_raw=prop(card, "addressLocality") or prop(card, "address"),
                financing_initial_payment=None,
                discovered_at=datetime.now(timezone.utc).isoformat(),
                **landing_meta(response),
            )
            emitted += 1

        # Corte real: una pagina sin tarjetas, o que no aporta ningun id nuevo,
        # significa que se agoto el inventario de la marca. El chequeo de ids
        # nuevos (y no solo "vino vacia") es a proposito: quedo SIN CONFIRMAR
        # en Fase 0 si `pag` mas alla del ultimo repite la ultima pagina en vez
        # de devolver vacio - con esto el spider se banca las dos formas.
        if emitted == 0 or not (page_ids - seen_ids):
            return
        if page >= self.max_pages:
            self.logger.warning(
                "%s: llego al tope de seguridad max_pages=%d sin agotar el inventario "
                "(ver wdxtkg39pw).", marca, self.max_pages,
            )
            return

        yield response.follow(
            self._page_url(marca, page + 1),
            callback=self.parse,
            headers=self._headers(marca),
            meta={"marca": marca, "page": page + 1, "seen_ids": seen_ids | page_ids},
        )
