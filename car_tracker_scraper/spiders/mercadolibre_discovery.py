"""Discovery spider para MercadoLibre.

Recorre los listados paginados de una o mas marcas y descubre avisos nuevos
(usados, sin publicidad). Barato, pensado para correr cada 4-6h (seccion 3.4
del doc de arquitectura). NO trae la ficha completa - eso es el trabajo
separado de mercadolibre_detail.

Filtrado de 0km: el filtro de condicion de ML (`ITEM*CONDITION_2230581`) va
pegado a un segmento `_NoIndex_True` en la URL. En vez de usar ese filtro via
URL, este spider pide la pagina de marca SIN filtrar y descarta los 0km el
mismo, en base al mismo dato que revelo el bug original en Fase 0: el texto
"0 Km" en attributes_list. Es una heuristica de texto, no un campo de
condicion explicito - monitorear si empieza a fallar (ver `_is_zero_km`).

robots.txt: ROBOTSTXT_OBEY esta en False A PROPOSITO (ver settings.py) -
decision de negocio de Fabrizio, no un default. El unico mecanismo de
paginacion que expone ML es `_Desde_{offset}`, que el propio robots.txt de
autos.mercadolibre.com.ar bloquea bajo "User-agent: *" (confirmado a mano en
el navegador que no hay ruta alternativa) - con ROBOTSTXT_OBEY=True el
Discovery quedaria limitado a la pagina 1 de cada marca.

Ojo con `max_pages`: NO es un tope de "cuantas paginas en total". Cada
pagina de ML devuelve una VENTANA de ~10 links de paginacion (no solo
"siguiente"), asi que max_pages es la profundidad de saltos desde la pagina
inicial - y desde wdxtkg398b, es solo un tope de SEGURIDAD (deliberadamente
alto), no el mecanismo real de corte por marca. Confirmado contra el sitio
real (2026-07-31): max_pages=3 para una sola marca termino trayendo 154
items via 17 requests distintos, llegando hasta offset 1969.

wdxtkg398b (2026-08-26, `diagnose_pagination.py` contra Toyota real): la
ventana pagination_nodes_url trae ademas un link "salto al final" (ej. desde
la pagina 10 aparece un nodo value=42 mucho mas alla del rango secuencial
5-15), que apunta directo a la ultima pagina real - por eso alcanzar el
final real toma pocos saltos incluso en marcas grandes. En esa ultima
pagina real (value=42, offset 1969) los resultados NO vienen vacios (48
items) - la señal de "se acabo el inventario" es que NINGUN nodo de la
ventana supera el value de la pagina actual (is_actual_page), no una pagina
vacia. Por eso el corte real ahora es: seguir solo nodos con value mayor al
de la pagina actual, y parar de fanear cuando no queda ninguno - `max_pages`
pasa a ser el backstop por si ese calculo nunca converge (nunca deberia,
en operacion normal).

Uso:
    scrapy crawl mercadolibre_discovery -a marcas=fiat,ford \
        -O output/discovery_%(time)s.jsonl
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import scrapy

from car_tracker_scraper.extraction.common import compact
from car_tracker_scraper.extraction.mercadolibre import (
    extract_nordic_ctx,
    iter_polycards,
    polycard_components,
)
from car_tracker_scraper.spiders.base import landing_meta
from car_tracker_scraper.items import ListingSummaryItem

_ZERO_KM_RE = re.compile(r"^0[.,]?0*\s*km$", re.IGNORECASE)


def _is_zero_km(attributes_raw: list[str] | None) -> bool:
    return any(_ZERO_KM_RE.match(attr.strip()) for attr in (attributes_raw or []))


# wdxtkg30xr: _compact() se movio a extraction/common.py cuando Motordil
# necesito el mismo criterio de comparacion de marcas. Se mantiene el alias
# privado para no tocar los tests que ya lo usaban por este nombre.
_compact = compact


def _resolve_marca(title_raw: str | None, requested_marca: str, known_marcas: list[str]) -> str:
    """Encontrado en produccion 2026-08-26: pedir `https://autos.mercadolibre.com.ar/{marca}`
    para una marca DNRPA sin path de filtro real en ML (ej. 'salto', ruido de
    automotor_marca_descripcion que paso el umbral de volumen) no da 0 resultados -
    ML cae a un listado generico sin filtrar, y sin este fix el spider taggeaba TODOS
    esos items con esa marca invalida (confirmado: 414 items bajo marca=salto en un
    solo Discovery resultaron ser Citroen, Toyota, Mercedes-Benz, Nissan, etc reales).
    Eso los sacaba para siempre de due_for_detail (scheduling/state.py), que filtra
    por marca curada - candidatos de marca curada real quedaban invisibles para Detail.

    En vez de confiar ciegamente en el parametro de busqueda, se intenta reconocer la
    marca real a partir de las primeras palabras del titulo (los titulos de ML siempre
    arrancan con la marca) contra el propio set de marcas pedidas en esta corrida. Esto
    no arriesga romper una pagina de marca donde el filtro si se aplico de verdad: ahi
    el titulo ya arranca con esa misma marca, y matchea igual. Si el titulo no matchea
    ninguna marca conocida (texto libre / caso raro), se conserva requested_marca sin
    tocar - mismo comportamiento que antes del fix, sin regresion."""
    if not title_raw:
        return requested_marca
    compact_known = {_compact(m): m for m in known_marcas}
    words = title_raw.split()
    for n in (3, 2, 1):
        if len(words) < n:
            continue
        candidate = _compact(" ".join(words[:n]))
        if candidate in compact_known:
            return compact_known[candidate]
    return requested_marca


def _parse_page_value(raw: object) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _current_page_value(nodes: list) -> int:
    """Busca el nodo is_actual_page=True en la ventana y devuelve su value.
    Si no aparece (forma inesperada) devuelve 0, que hace que todo nodo
    valido se trate como "hacia adelante" - mismo comportamiento (seguir
    todo) que el codigo tenia antes de wdxtkg398b, sin regresion en ese
    caso raro."""
    for node in nodes:
        if isinstance(node, dict) and node.get("is_actual_page"):
            value = _parse_page_value(node.get("value"))
            if value is not None:
                return value
    return 0


def _normalize_url(url: str | None) -> str | None:
    """metadata.url viene sin esquema en produccion (ej. "auto.mercadolibre.com.ar/MLA-...",
    no "https://auto..."). Confirmado corriendo el spider real 2026-07-31 - sin esto,
    pasarle este valor tal cual a mercadolibre_detail rompe (Scrapy exige URL absoluta)."""
    if url and not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


class MercadolibreDiscoverySpider(scrapy.Spider):
    name = "mercadolibre_discovery"
    allowed_domains = ["autos.mercadolibre.com.ar"]

    def __init__(self, marcas: str = "fiat", max_pages: str = "30", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]
        self.max_pages = int(max_pages)

    async def start(self):
        # start_requests() esta deprecado desde Scrapy 2.13 en favor de este
        # metodo - confirmado ademas que en Scrapy 2.17 (version instalada
        # via requirements.txt sin upper bound) start_requests() no es solo
        # deprecado sino que NO DESPACHA NINGUN REQUEST (el spider abre y
        # cierra en ~12ms, 0 items, sin error visible - encontrado en
        # produccion 2026-08-18, wdxtkg34th/wdxtkg35ba). Verificado aislado
        # contra un server HTTP local (sin tocar mercadolibre.com) que el
        # cambio a async start() resuelve el problema.
        #
        # Sin query string: "?sb=all_mercadolibre" (el sort-order que traia el
        # comando curl original de findings_clickup.md) contiene el substring
        # "mercadolibre", que matchea "Disallow: /*mercadolibre" bajo
        # "User-agent: *" en el robots.txt real de autos.mercadolibre.com.ar
        # (confirmado corriendo el spider real 2026-07-31: bloqueado en
        # silencio via RobotsTxtMiddleware). La URL base sin query, tal como
        # esta documentada en findings_clickup.md ("URL de listado:
        # https://autos.mercadolibre.com.ar/{marca}"), no choca con ninguna
        # regla.
        for marca in self.marcas:
            url = f"https://autos.mercadolibre.com.ar/{marca}"
            yield scrapy.Request(url, callback=self.parse, meta={"marca": marca, "page_count": 1})

    def parse(self, response):
        marca = response.meta["marca"]
        page_count = response.meta["page_count"]

        ctx = extract_nordic_ctx(response.text)
        search = ctx["appProps"]["sharedState"]["search"]

        for polycard in iter_polycards(search.get("results", [])):
            metadata = polycard.get("metadata", {})
            if str(metadata.get("is_pad")).lower() == "true":
                continue  # publicidad, no contaminar agregados

            comp = polycard_components(polycard)
            attributes_raw = (comp.get("attributes_list") or {}).get("texts")
            if _is_zero_km(attributes_raw):
                continue  # 0km, no es el segmento usado que nos interesa

            price = (comp.get("price") or {}).get("current_price") or {}
            # price_complements no tiene forma confirmada todavia (findings_clickup.md
            # solo lo menciona en prosa): en produccion salio como list en vez de
            # dict en al menos un item real - no crashear, solo no sacar el dato.
            price_complements = (comp.get("price") or {}).get("price_complements")

            title_raw = (comp.get("title") or {}).get("text")

            yield ListingSummaryItem(
                source="mercadolibre",
                source_listing_key=metadata.get("id"),
                url=_normalize_url(metadata.get("url")),
                marca=_resolve_marca(title_raw, marca, self.marcas),
                is_ad=False,
                category_id=metadata.get("category_id"),
                domain_id=metadata.get("domain_id"),
                title_raw=title_raw,
                price_amount=price.get("value"),
                price_currency=price.get("currency"),
                attributes_raw=attributes_raw,
                location_raw=(comp.get("location") or {}).get("text"),
                financing_initial_payment=(
                    price_complements.get("initial_payment_amount")
                    if isinstance(price_complements, dict)
                    else None
                ),
                discovered_at=datetime.now(timezone.utc).isoformat(),
                **landing_meta(response),
            )

        if page_count >= self.max_pages:
            self.logger.warning(
                "%s: llego al tope de seguridad max_pages=%d sin que ML dejara de "
                "ofrecer paginas nuevas - cortando de todos modos (ver wdxtkg398b).",
                marca, self.max_pages,
            )
            return

        pagination = search.get("pagination", {})
        nodes = pagination.get("pagination_nodes_url", [])
        current_value = _current_page_value(nodes)

        for node in nodes:
            # Forma real confirmada 2026-07-31 (diagnose_pagination.py contra
            # el sitio real): pagination_nodes_url es una lista de DICTS
            # ({"value": "2", "url": "...", "is_actual_page": False}), no de
            # strings planos - el fix anterior evitaba el crash pero de hecho
            # nunca seguia ninguna pagina (todo se saltaba por no ser str).
            if not isinstance(node, dict):
                self.logger.warning("pagination_nodes_url tiene un item inesperado: %r", node)
                continue
            if node.get("is_actual_page"):
                continue  # es la pagina que ya estamos procesando
            next_url = node.get("url")
            if not isinstance(next_url, str):
                self.logger.warning("pagination_nodes_url.url no es un string: %r", node)
                continue
            node_value = _parse_page_value(node.get("value"))
            if node_value is not None and node_value <= current_value:
                # Pagina ya recorrida (hacia atras) o duplicada - no suma
                # cobertura nueva. Cuando NINGUN nodo de la ventana pasa este
                # filtro (la ultima pagina real, confirmado 2026-08-26 contra
                # Toyota: value=42, resultados no vacios) el for termina sin
                # yieldear ningun Request y la marca queda agotada de verdad.
                continue
            yield response.follow(
                next_url,
                callback=self.parse,
                meta={"marca": marca, "page_count": page_count + 1},
            )
