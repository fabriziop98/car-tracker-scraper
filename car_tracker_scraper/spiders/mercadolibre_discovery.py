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

wdxtkg3j4o (2026-09-06): el tope de ~2.000 por consulta (ver `_abanico_por_modelo`,
wdxtkg39v1) tambien lo puede superar un MODELO individual, no solo una marca -
confirmado contra el sitio real: Ford Ranger tiene 3.160 avisos y `/ranger`
sola solo alcanza 2.000. `_abanico_por_anio` repite el mismo mecanismo un
nivel mas abajo (consulta compuesta `/{anio}/{modelo}`), gateado por
`anio_fanout_min_volumen` para no pagar el costo extra en los modelos chicos
que ya entran enteros en su propia consulta.

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
    iter_model_facet,
    iter_polycards,
    iter_year_facet,
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

    def __init__(
        self,
        marcas: str = "fiat",
        max_pages: str = "30",
        modelo_min_volumen: str = "150",
        max_modelos_por_marca: str = "15",
        anio_min_volumen: str = "100",
        anio_fanout_min_volumen: str = "1500",
        max_anios_por_modelo: str = "40",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.marcas = [m.strip() for m in marcas.split(",") if m.strip()]
        self.max_pages = int(max_pages)
        # wdxtkg39v1: por debajo de este volumen no vale gastar requests - la
        # consulta por marca ya trae esos avisos dentro de su cupo. 0 desactiva
        # el corte por modelo por completo (util para comparar contra la linea
        # base sin tocar codigo).
        self.modelo_min_volumen = int(modelo_min_volumen)
        self.max_modelos_por_marca = int(max_modelos_por_marca)
        # wdxtkg3j4o: mismo problema un nivel mas abajo - un modelo individual
        # puede por si solo superar el tope de ~2.000 de ML (medido 2026-09-06:
        # Ford Ranger, 3.160 avisos reales). anio_fanout_min_volumen es el
        # gate: solo modelos que ya rozan o superan ese tope pagan el costo
        # extra de una consulta por año; anio_min_volumen es el piso POR AÑO
        # dentro de ese abanico (mismo rol que modelo_min_volumen un nivel
        # arriba). 0 en cualquiera de los dos desactiva ese nivel del abanico.
        self.anio_min_volumen = int(anio_min_volumen)
        self.anio_fanout_min_volumen = int(anio_fanout_min_volumen)
        self.max_anios_por_modelo = int(max_anios_por_modelo)

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
            yield scrapy.Request(
                url,
                callback=self.parse,
                meta={"marca": marca, "page_count": 1, "es_pagina_de_marca": True},
            )

    def _abanico_por_modelo(self, marca: str, search: dict):
        """wdxtkg39v1: ademas de la consulta por marca, una consulta por cada
        modelo con volumen propio.

        ML corta la paginacion en el offset ~2.000 por consulta y lo publica en
        `search.pagination.results_limit`. Medido el 2026-09-01: Toyota tiene
        6.746 avisos reales (facet BRAND) y por `/toyota` solo se alcanzan
        2.000 - el 30%. Partiendo en consultas mas chicas cada una recupera su
        propio cupo; se confirmo que el cupo es POR CONSULTA corriendo dos
        modelos seguidos y llegando ambos al offset 1969.

        Se consulta `/{slug}` como TERMINO de busqueda, no como filtro: la url
        que publica el facet (`/{modelo}/{marca}_NoIndex_True`) no aplica nada
        si se la pide sin su fragmento `#applied_...`, que es client-side. ML
        toma el ultimo segmento del path como texto de busqueda.

        **La marca que viaja en meta es la de la PAGINA DE MARCA, no el termino.**
        Es deliberado y es el punto delicado: `_resolve_marca` valida el titulo
        contra `self.marcas`, que tiene que seguir siendo la lista de marcas
        reales. Si se le pasaran nombres de modelo, ninguna resolucion
        matchearia y los items quedarian etiquetados con el modelo como si
        fuera marca - invisibles para `due_for_detail`, que filtra por marca
        curada. Es la misma falla silenciosa del incidente de la marca 'salto'.

        La busqueda por texto trae de mas: `/etios` devuelve un Fiat 600 y
        `/hiace` un Citroen Jumpy entre los primeros resultados. No es un
        problema - son avisos reales, y `_resolve_marca` los reetiqueta con su
        marca verdadera a partir del titulo.
        """
        modelos = [
            m for m in iter_model_facet(search)
            if m["count"] >= self.modelo_min_volumen and m["slug"] != marca
        ]
        modelos.sort(key=lambda m: m["count"], reverse=True)
        modelos = modelos[: self.max_modelos_por_marca]

        if not modelos:
            return
        self.logger.info(
            "wdxtkg39v1 [%s]: %d modelos con volumen propio (>=%d): %s",
            marca,
            len(modelos),
            self.modelo_min_volumen,
            ", ".join(f"{m['slug']}({m['count']})" for m in modelos),
        )
        for modelo in modelos:
            yield scrapy.Request(
                f"https://autos.mercadolibre.com.ar/{modelo['slug']}",
                callback=self.parse,
                meta={
                    "marca": marca,  # la marca real, NO el termino - ver docstring
                    "page_count": 1,
                    "termino_modelo": modelo["slug"],
                    "es_pagina_de_modelo": True,
                    "modelo_count": modelo["count"],
                },
            )

    def _abanico_por_anio(self, marca: str, modelo_slug: str, modelo_count: int, search: dict):
        """wdxtkg3j4o: mismo problema que `_abanico_por_modelo`, un nivel mas
        abajo - un modelo individual puede por si solo superar el tope de
        ~2.000 resultados por consulta de ML. Medido contra el sitio real
        (2026-09-06): Ford Ranger tiene 3.160 avisos reales (facet MODEL de
        `/ford`), y `/ranger` sola solo alcanza los 2.000 de siempre - el 63%.
        Confirmado ademas cruzando avisos reales: de 14 avisos "Ford Ranger
        2019 Limited" que ML muestra hoy, 9 nunca habian sido vistos por
        ningun Discovery de los ultimos 9 dias.

        `modelo_count` (el conteo que ya trajo el facet MODEL de la pagina de
        marca, wdxtkg39v1) es el gate: solo modelos que ya rozan o superan el
        tope de ML pagan el costo extra de abrir una consulta por año -
        gastar ese request extra en los miles de modelos chicos que ya entran
        enteros en su propia consulta seria puro desperdicio.

        Se pide `/{anio}/{modelo}` como termino de busqueda COMPUESTO (dos
        palabras), no un filtro estructurado - mismo criterio que
        `_abanico_por_modelo`: el slug del año sale de la URL real que
        publica el facet VEHICLE_YEAR de la pagina del modelo
        (`/{anio}/{modelo}_NoIndex_True#applied_...`, confirmado contra el
        sitio real 2026-09-06), no de un año inventado a mano.

        No reemplaza a la consulta plana `/{modelo}` (que sigue yendo igual) -
        la complementa. Los años sin volumen propio (por debajo de
        `anio_min_volumen`) ya entran dentro del cupo de esa consulta plana;
        redescubrir los mismos avisos por las dos vias no es un problema, la
        ingesta ya es idempotente por `source_listing_key`.
        """
        if modelo_count < self.anio_fanout_min_volumen:
            return

        anios = [
            a for a in iter_year_facet(search)
            if a["count"] >= self.anio_min_volumen
        ]
        anios.sort(key=lambda a: a["count"], reverse=True)
        anios = anios[: self.max_anios_por_modelo]

        if not anios:
            return
        self.logger.info(
            "wdxtkg3j4o [%s/%s]: %d anios con volumen propio (>=%d) de %d avisos totales: %s",
            marca,
            modelo_slug,
            len(anios),
            self.anio_min_volumen,
            modelo_count,
            ", ".join(f"{a['slug']}({a['count']})" for a in anios),
        )
        for anio in anios:
            yield scrapy.Request(
                f"https://autos.mercadolibre.com.ar/{anio['slug']}/{modelo_slug}",
                callback=self.parse,
                meta={
                    "marca": marca,  # la marca real, NO el termino - mismo criterio que _abanico_por_modelo
                    "page_count": 1,
                    "termino_modelo": modelo_slug,
                    "termino_anio": anio["slug"],
                },
            )

    def parse(self, response):
        marca = response.meta["marca"]
        page_count = response.meta["page_count"]

        ctx = extract_nordic_ctx(response.text)
        search = ctx["appProps"]["sharedState"]["search"]

        if response.meta.get("es_pagina_de_marca") and self.modelo_min_volumen > 0:
            yield from self._abanico_por_modelo(marca, search)

        if response.meta.get("es_pagina_de_modelo") and self.anio_min_volumen > 0:
            yield from self._abanico_por_anio(
                marca, response.meta["termino_modelo"], response.meta.get("modelo_count", 0), search
            )

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
                # es_pagina_de_marca NO se propaga a proposito: el abanico por
                # modelo (wdxtkg39v1) tiene que dispararse una sola vez por
                # marca, en su primera pagina, y no en cada pagina siguiente.
                # es_pagina_de_modelo tampoco: el abanico por año (wdxtkg3j4o)
                # tiene la misma regla, una sola vez por modelo. termino_modelo/
                # termino_anio si viajan, solo para poder leer en el log de que
                # consulta salio cada pagina.
                meta={
                    "marca": marca,
                    "page_count": page_count + 1,
                    "termino_modelo": response.meta.get("termino_modelo"),
                    "termino_anio": response.meta.get("termino_anio"),
                },
            )
