"""Diagnostico de paginacion real de ML para wdxtkg398b - NO es parte del
pipeline (no lo importa nada), es un one-off para correr a mano.

Camina hacia adelante desde https://autos.mercadolibre.com.ar/{marca} saltando
siempre al nodo de mayor "value" que devuelve pagination_nodes_url (la misma
ventana de ~10 links que ve el spider real), para llegar rapido a la cola real
del inventario de una marca grande sin pedir cada pagina intermedia una por
una. En cada paso imprime lo que necesitamos confirmar antes de tocar la
condicion de corte en mercadolibre_discovery.py::parse (wdxtkg398b):
    - cuantos resultados trae la pagina (search.results, antes de filtrar)
    - la ventana pagination_nodes_url completa (values + is_actual_page)
    - si se repite el mismo nodo/URL que el paso anterior (senal de que ML
      dejo de ofrecer paginas nuevas)

Se corta solo por: resultados vacios, pagination_nodes_url vacio, no hay
avance en el value maximo respecto del paso anterior, o el tope de seguridad
--max-requests (default 40 - alto a proposito para llegar de verdad al final
de una marca grande, pero no infinito). Pausa de --delay segundos entre
requests (default 2s, mas conservador que el token bucket de 1 req/s de
produccion porque esto es un script suelto sin circuit breaker).

Uso (correr vos mismo, no via Claude Code - ver CLAUDE.md "Never run scrapy
crawl... yourself against real MercadoLibre"):
    .venv/bin/python diagnose_pagination.py toyota
    .venv/bin/python diagnose_pagination.py volkswagen --max-requests 60 --delay 2.5
"""
from __future__ import annotations

import argparse
import time
import urllib.request

from car_tracker_scraper.antiblocking.user_agents import PERSONA_POOL
from car_tracker_scraper.extraction.common import compact
from car_tracker_scraper.extraction.mercadolibre import (
    extract_nordic_ctx,
    iter_polycards,
    polycard_components,
)

# Tope duro de paginacion que ML publica en search.pagination.results_limit.
# Confirmado contra el sitio real el 2026-09-01: las consultas por debajo del
# tope reportan ahi su total REAL (yaris=1125, etios=1207, sw4=1044), asi que
# results_limit == TOPE_ML es la señal de "capado, total desconocido".
TOPE_ML = 2000


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=PERSONA_POOL[0].headers())
    with urllib.request.urlopen(req, timeout=15) as response:
        html = response.read().decode("utf-8", errors="replace")
    ctx = extract_nordic_ctx(html)
    return ctx["appProps"]["sharedState"]["search"]


def _scalars(obj: dict, prefix: str) -> list[str]:
    """Campos escalares de un dict, para descubrir sin adivinar si ML expone un
    total de resultados (wdxtkg39v1: saber cuantos avisos DICE tener la consulta
    es lo que separa "el techo nos oculta la cola" de "solo vemos de a 2.000
    pero terminamos viendolos todos por rotacion")."""
    return [
        f"{prefix}.{k} = {v!r}"
        for k, v in sorted(obj.items())
        if not isinstance(v, (list, dict))
    ]


def _titulos_de(results: list) -> list[str]:
    """Reusa el mismo desanidado que el spider real (iter_polycards +
    polycard_components) en vez de adivinar la forma: los items vienen como
    {'id':'POLYCARD','polycard':{...}} y el titulo esta en components[], no en
    una clave de primer nivel. Un intento anterior a mano no encontraba ninguno."""
    titulos = []
    for polycard in iter_polycards(results):
        comp = polycard_components(polycard)
        titulo = (comp.get("title") or {})
        texto = titulo.get("text") if isinstance(titulo, dict) else titulo
        if isinstance(texto, str):
            titulos.append(texto)
    return titulos


def _explorar_filtros(marca: str) -> int:
    """wdxtkg39v1: buscar si ML ya publica sus propios filtros de MODELO en la
    pagina de marca, con slug y conteo.

    Importa para el diseño: si estan ahi, la lista de modelos a partir sale del
    propio sitio (slugs exactos, conteos reales, se mantiene sola cuando ML suma
    un modelo) en vez de armarla desde nuestro catalogo curado - que usa slugs
    nuestros que no tienen por que coincidir con los de ML, y que quedaria
    desactualizado sin que nadie se entere.
    """
    search = fetch(f"https://autos.mercadolibre.com.ar/{marca}")

    # Busqueda dirigida del filtro MODEL en cualquier parte del arbol. El
    # volcado generico de mas abajo encontro el filtro en melidata_track, que es
    # el payload de ANALITICA: trae id/name pero results=None y los valores sin
    # URL. Los facets utiles (con url y conteo por modelo) viven en
    # search_filters/sidebar, con otra forma - por eso se busca por id, no por
    # ruta fija.
    encontrados = []

    def _buscar_model(nodo, camino, prof=0):
        if prof > 6:
            return
        if isinstance(nodo, dict):
            if nodo.get("id") in ("MODEL", "model") and isinstance(nodo.get("values"), list):
                encontrados.append((camino, nodo))
            for k, v in nodo.items():
                _buscar_model(v, f"{camino}.{k}", prof + 1)
        elif isinstance(nodo, list):
            for i, v in enumerate(nodo):
                _buscar_model(v, f"{camino}[{i}]", prof + 1)

    _buscar_model(search, "search")
    print(f"  filtros MODEL encontrados: {len(encontrados)}")

    def _n(v) -> int:
        """results viene como '(1234)' o '(1.234)'."""
        txt = "".join(ch for ch in str(v.get("results") or "") if ch.isdigit())
        return int(txt) if txt else 0

    for camino, filtro in encontrados:
        valores = [v for v in (filtro.get("values") or []) if isinstance(v, dict)]
        if not valores:
            planos = filtro.get("values") or []
            print(f"\n  === {camino} ===  ({len(planos)} valores planos, sin url ni conteo - inservible)")
            continue
        # ORDENADO POR VOLUMEN, no alfabetico: los primeros alfabeticos son ruido
        # de la busqueda por texto (en /toyota aparecen Peugeot 208/2008 porque
        # son avisos cuyo texto menciona "toyota"). Lo que importa es la cola de
        # arriba por conteo.
        valores.sort(key=_n, reverse=True)
        print(f"\n  === {camino} ===  ({len(valores)} valores, ordenados por conteo)")
        print(f"    {'modelo':22} {'avisos':>8}  url")
        for v in valores[:22]:
            print(f"    {str(v.get('name'))[:22]:22} {_n(v):8}  id={str(v.get('id')):10} "
                  f"{str(v.get('url')).split('#')[0]}")
        suma = sum(_n(v) for v in valores)
        print(f"    -- suma de los {len(valores)} valores del facet: {suma}")

    # El BRAND facet importa: si /marca es texto libre, la URL canonica de marca
    # (filtro estructurado) sale de aca.
    marcas_encontradas = []

    def _buscar_brand(nodo, prof=0):
        if prof > 6:
            return
        if isinstance(nodo, dict):
            if nodo.get("id") in ("BRAND", "brand") and isinstance(nodo.get("values"), list):
                marcas_encontradas.append(nodo)
            for v in nodo.values():
                _buscar_brand(v, prof + 1)
        elif isinstance(nodo, list):
            for v in nodo:
                _buscar_brand(v, prof + 1)

    _buscar_brand(search)
    for filtro in marcas_encontradas:
        valores = [v for v in (filtro.get("values") or []) if isinstance(v, dict)]
        if valores:
            print(f"\n  === facet BRAND ({len(valores)} valores) ===")
            for v in sorted(valores, key=_n, reverse=True)[:8]:
                print(f"    {str(v.get('name'))[:22]:22} {_n(v):8}  {str(v.get('url')).split('#')[0]}")

    print(f"\n  claves de search: {sorted(search.keys())}\n")

    def _mostrar(nodo, camino, prof=0):
        if prof > 3:
            return
        if isinstance(nodo, dict):
            claves = sorted(nodo.keys())
            pinta_filtro = {"id", "name", "values"} <= set(claves) or {"id", "name", "results"} <= set(claves)
            if pinta_filtro:
                print(f"  [{camino}] id={nodo.get('id')!r} name={nodo.get('name')!r} "
                      f"results={nodo.get('results')!r}")
                valores = nodo.get("values")
                if isinstance(valores, list):
                    for v in valores[:12]:
                        if isinstance(v, dict):
                            print(f"      id={v.get('id')!r} name={v.get('name')!r} "
                                  f"results={v.get('results')!r} url={str(v.get('url'))[:90]!r}")
                    if len(valores) > 12:
                        print(f"      ... y {len(valores)-12} valores mas")
                return
            for k in claves:
                _mostrar(nodo[k], f"{camino}.{k}", prof + 1)
        elif isinstance(nodo, list):
            for i, v in enumerate(nodo[:25]):
                _mostrar(v, f"{camino}[{i}]", prof + 1)

    # Volcado generico acotado a donde viven los facets reales, para no repetir
    # el ruido de melidata_track.
    for clave in ("search_filters", "search_filter", "sidebar"):
        if clave in search:
            print(f"  --- volcado de search.{clave} ---")
            _mostrar(search[clave], f"search.{clave}")
    return 0


def _tabla_terminos(terminos: list[str], delay: float) -> int:
    """wdxtkg39v1, plan B: consultar el MODELO como termino suelto (`/corolla`),
    sin marca.

    Vale la pena medirlo antes que nada porque es el unico enfoque que NO
    necesita una forma de URL nueva: el spider ya pide `/{marca}` para cada
    marca, asi que alcanza con pasarle tambien nombres de modelo. Y
    `_resolve_marca` ya re-deriva la marca real desde el titulo, que es lo que
    hace seguro consultar por texto sin filtrar por marca.

    Lo que decide si sirve: que el results_limit de cada termino quede POR
    DEBAJO de 2000. Segun el facet de Toyota la mayoria deberia (Etios 766,
    Yaris 698, Corolla Cross 634), y el unico problematico seria `corolla`,
    porque el texto tambien matchea Corolla Cross (1570 + 634 = 2204).
    """
    print(f"{'termino':24} {'query':>16} {'limit':>7} {'pages':>6}  {'capado?':>8}  primer titulo")
    print("-" * 118)
    capados, ok = [], []
    for i, termino in enumerate(terminos):
        url = f"https://autos.mercadolibre.com.ar/{termino}"
        try:
            search = fetch(url)
        except Exception as exc:  # noqa: BLE001 - script a mano
            print(f"{termino:24} ERROR {exc!r}")
            if i < len(terminos) - 1:
                time.sleep(delay)
            continue
        pg = search.get("pagination", {})
        limite = pg.get("results_limit")
        titulos = _titulos_de(search.get("results", []))
        capado = bool(limite and limite >= TOPE_ML)
        (capados if capado else ok).append((termino, limite))
        print(f"{termino:24} {str(search.get('query'))[:16]:>16} {str(limite):>7} "
              f"{str(pg.get('page_count')):>6}  {'SI' if capado else 'no':>8}  "
              f"{(titulos[0][:44] if titulos else '(sin titulos)')}")
        if i < len(terminos) - 1:
            time.sleep(delay)

    print("\n" + "=" * 118)
    if ok:
        print(f"  ALCANZABLES COMPLETOS ({len(ok)}): " + ", ".join(f"{t}={n}" for t, n in ok))
        print(f"    suma recuperable: {sum(n for _, n in ok)}")
    if capados:
        print(f"  TODAVIA CAPADOS ({len(capados)}): " + ", ".join(t for t, _ in capados))
        print("    -> para esos hace falta un corte extra (ej. sumar el año al termino).")
    print("=" * 118)
    return 0


def _probar_formas(marca: str, modelo: str, model_id: str | None, delay: float) -> int:
    """wdxtkg39v1: probar formas candidatas de URL para filtrar por modelo.

    Hace falta porque la URL que publica el facet NO aplica el filtro cuando se
    la pide sin el fragmento: `/corolla/toyota_NoIndex_True` devuelve
    query='toyota' y results_limit=2000, identico a la pagina de marca - el
    filtro vive en el `#applied_filter_id=MODEL...`, que es client-side y nunca
    llega al servidor. El servidor toma el ULTIMO segmento del path como
    busqueda de TEXTO.

    La pista de que igual existe una forma server-side esta en el docstring de
    mercadolibre_discovery: el filtro de condicion se expresa como
    `ITEM*CONDITION_2230581` dentro del path. Si MODEL sigue ese patron, hay una
    forma `_MODEL_{id}` que si filtra de verdad.

    Se mide cada candidata por lo mismo: que `query` entiende ML, cuanto da
    results_limit (si baja de 2000, hay filtro real y ese es el total) y como
    son los primeros titulos.
    """
    candidatas = [
        (f"https://autos.mercadolibre.com.ar/{marca}", "marca sola (linea base)"),
        (f"https://autos.mercadolibre.com.ar/{marca}/{modelo}", "marca/modelo (texto del ultimo segmento)"),
        (f"https://autos.mercadolibre.com.ar/{modelo}/{marca}_NoIndex_True", "la del facet, sin fragmento"),
        (f"https://autos.mercadolibre.com.ar/{marca}/{modelo}_NoIndex_True", "marca/modelo_NoIndex_True"),
    ]
    if model_id:
        candidatas += [
            (f"https://autos.mercadolibre.com.ar/{marca}/_MODEL_{model_id}", "patron _MODEL_{id} (como ITEM*CONDITION)"),
            (f"https://autos.mercadolibre.com.ar/{marca}_MODEL_{model_id}", "marca_MODEL_{id} pegado"),
        ]

    print(f"{'forma':44} {'query':>12} {'limit':>7} {'pages':>6}  primer titulo")
    print("-" * 120)
    for i, (url, etiqueta) in enumerate(candidatas):
        try:
            search = fetch(url)
        except Exception as exc:  # noqa: BLE001 - script a mano
            print(f"{etiqueta:44} ERROR {exc!r}")
            if i < len(candidatas) - 1:
                time.sleep(delay)
            continue
        pg = search.get("pagination", {})
        titulos = _titulos_de(search.get("results", []))
        print(f"{etiqueta:44} {str(search.get('query'))[:12]:>12} "
              f"{str(pg.get('results_limit')):>7} {str(pg.get('page_count')):>6}  "
              f"{(titulos[0][:46] if titulos else '(sin titulos)')}")
        print(f"{'':44} {url}")
        if i < len(candidatas) - 1:
            time.sleep(delay)

    print("\n" + "=" * 120)
    print("  Sirve la forma cuyo results_limit BAJE de 2000 y cuyos titulos sean todos "
          f"{modelo!r}.")
    print("  Si ninguna baja, el filtro por modelo no es alcanzable por URL y hay que")
    print("  evaluar otra via (API publica de ML, o el enfoque por texto de modelo solo).")
    print("=" * 120)
    return 0


def _tabla_por_modelo(marca: str, modelos: list[str], delay: float) -> int:
    """Una sola pagina por modelo: alcanza porque search.pagination ya trae
    page_count y results_limit (wdxtkg39v1, confirmado contra el sitio real el
    2026-09-01 - ML publica results_limit=2000 explicitamente, no hay que
    inferirlo).

    El control anti-'listado generico' es el propio contraste de la tabla: si
    el filtro se aplica de verdad, los modelos chicos TIENEN que dar page_count
    bajo. Que todos den el mismo tope seria la señal de que ML esta ignorando el
    filtro, que es el modo de falla que ya mordio con la marca 'salto'.
    """
    filas = []
    print(f"{'consulta':46} {'query':>14} {'page_count':>11} {'limit':>7} {'results':>8}")
    for i, modelo in enumerate([None] + modelos):
        url = f"https://autos.mercadolibre.com.ar/{marca}"
        if modelo:
            url += f"/{modelo}"
        try:
            search = fetch(url)
        except Exception as exc:  # noqa: BLE001 - script de diagnostico a mano
            print(f"{url[38:]:46} ERROR {exc!r}")
            continue
        pg = search.get("pagination", {})
        fila = (
            modelo or "(solo marca)",
            search.get("query"),
            pg.get("page_count"),
            pg.get("results_limit"),
            len(search.get("results", [])),
        )
        filas.append(fila)
        print(f"{(modelo or '(solo marca)'):46} {str(fila[1]):>14} {str(fila[2]):>11} {str(fila[3]):>7} {fila[4]:>8}")
        if i < len(modelos):
            time.sleep(delay)

    print("\n" + "=" * 78)
    distintos = {f[2] for f in filas if f[2] is not None}
    if len(distintos) <= 1 and len(filas) > 1:
        print("  VEREDICTO: todas las consultas dan el MISMO page_count.")
        print("  Muy probablemente ML ignora el filtro y devuelve un listado generico.")
        print("  NO usar esta forma de URL - probar el filtro con _NoIndex_True.")
    else:
        print(f"  VEREDICTO: el filtro se aplica (page_count varia entre consultas: {sorted(distintos)}).")
        # results_limit NO es una constante: es el total real de la consulta,
        # RECORTADO a 2000 (confirmado 2026-09-01 - yaris=1125, etios=1207,
        # sw4=1044, todos por debajo del tope). Entonces:
        #   == TOPE  -> capado, el total real es >= 2000 y no se conoce
        #   <  TOPE  -> ese ES el total real de la consulta
        capados = [f[0] for f in filas if f[3] and f[3] >= TOPE_ML]
        completos = [(f[0], f[3]) for f in filas if f[3] and f[3] < TOPE_ML]
        print(f"  CAPADOS (total real desconocido, >={TOPE_ML}): {', '.join(capados) if capados else 'ninguno'}")
        print("    -> hay que partirlos mas fino todavia (por año, por ejemplo).")
        if completos:
            print("  COMPLETOS (results_limit ES el total real):")
            for nombre, total in sorted(completos, key=lambda x: -x[1]):
                print(f"    {nombre:20} {total}")
        visible = filas[0][3] if filas else 0
        piso = sum(f[3] or 0 for f in filas[1:])
        if piso and visible:
            print(f"  Suma de los modelos medidos: {piso} contra {visible} visibles por marca "
                  f"-> partiendo se ve al menos {piso/visible:.1f}x mas.")
    print("=" * 78)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("marca", nargs="?", help="ej. toyota (omitir si se pasa --url)")
    parser.add_argument(
        "--modelo",
        help="wdxtkg39v1: acota la consulta a un modelo -> /{marca}/{modelo}. "
             "La forma de esta URL hay que CONFIRMARLA contra el sitio real, no asumirla.",
    )
    parser.add_argument("--url", help="URL completa, para probar cualquier otra forma de filtro")
    parser.add_argument(
        "--terminos",
        help="wdxtkg39v1 (plan B): lista de terminos sueltos separados por comas. Pide "
             "/{termino} de cada uno y tabula results_limit. No necesita ninguna forma "
             "de URL nueva - es lo que el spider ya sabe hacer.",
    )
    parser.add_argument(
        "--formas",
        action="store_true",
        help="wdxtkg39v1: prueba varias formas candidatas de URL para filtrar por modelo "
             "y compara que query entiende ML y cuanto da results_limit en cada una. "
             "Necesita marca y --modelo; con --model-id agrega el patron _MODEL_{id}.",
    )
    parser.add_argument("--model-id", help="id del modelo segun el facet MODEL (ver --filtros)")
    parser.add_argument(
        "--filtros",
        action="store_true",
        help="wdxtkg39v1: pide UNA pagina de la marca y busca si ML publica sus propios "
             "filtros de modelo (slug + conteo). Si estan, la lista de modelos a partir "
             "sale del sitio y no de nuestro catalogo curado.",
    )
    parser.add_argument(
        "--modelos",
        help="wdxtkg39v1: lista separada por comas. Pide UNA pagina de cada "
             "`/{marca}/{modelo}` e imprime una tabla con page_count y results_limit. "
             "Barato (1 request por modelo) y responde las dos preguntas de una: si el "
             "filtro se aplica de verdad (un modelo chico TIENE que dar page_count bajo; "
             "si todos dan el mismo tope, ML esta cayendo a un listado generico) y que "
             "modelos estan capados y hay que partir.",
    )
    parser.add_argument("--max-requests", type=int, default=40)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument(
        "--titulos",
        type=int,
        default=5,
        metavar="N",
        help="Cuantos titulos de muestra mostrar en el veredicto final (default 5). "
             "Los titulos se analizan SIEMPRE: comparar el titulo con el filtro pedido "
             "es la unica forma de distinguir 'el filtro se aplico' de 'ML cayo a un "
             "listado generico sin filtrar' - los contadores dan identico en los dos "
             "casos. Mismo modo de falla que el incidente de la marca 'salto' (ver "
             "_resolve_marca en mercadolibre_discovery.py).",
    )
    args = parser.parse_args()

    if args.terminos:
        return _tabla_terminos([x.strip() for x in args.terminos.split(",") if x.strip()], args.delay)

    if args.formas:
        if not (args.marca and args.modelo):
            parser.error("--formas necesita una marca y --modelo")
        return _probar_formas(args.marca, args.modelo, args.model_id, args.delay)

    if args.filtros:
        if not args.marca:
            parser.error("--filtros necesita una marca")
        return _explorar_filtros(args.marca)

    if args.modelos:
        if not args.marca:
            parser.error("--modelos necesita una marca")
        return _tabla_por_modelo(args.marca, [m.strip() for m in args.modelos.split(",") if m.strip()], args.delay)

    if args.url:
        url = args.url
    elif args.marca and args.modelo:
        url = f"https://autos.mercadolibre.com.ar/{args.marca}/{args.modelo}"
    elif args.marca:
        url = f"https://autos.mercadolibre.com.ar/{args.marca}"
    else:
        parser.error("pasar una marca o --url")

    consulta = url
    seen_urls: set[str] = set()
    last_max_value = -1
    total_results = 0
    max_offset = 0
    primer_paso = True
    titulos_vistos: list[str] = []
    escalares_primer_paso: list[str] = []

    for step in range(1, args.max_requests + 1):
        print(f"\n--- step {step}: GET {url}")
        search = fetch(url)
        results = search.get("results", [])
        pagination = search.get("pagination", {})
        nodes = pagination.get("pagination_nodes_url", [])

        total_results += len(results)
        offset_actual = 0
        if "_Desde_" in url:
            try:
                offset_actual = int(url.split("_Desde_")[1].split("_")[0])
            except (IndexError, ValueError):
                pass
        max_offset = max(max_offset, offset_actual)

        if primer_paso:
            # Solo en el primer paso: descubrir que dice ML del tamanio TOTAL de
            # esta consulta. Si aparece un total, compararlo con lo que se logra
            # recorrer responde de una si el techo esconde inventario.
            primer_paso = False
            escalares_primer_paso = _scalars(search, "search") + _scalars(pagination, "search.pagination")
            print("    --- campos escalares (buscando un total de resultados) ---")
            for linea in escalares_primer_paso:
                print(f"      {linea}")

        print(f"    results: {len(results)}")

        nuevos = _titulos_de(results)
        titulos_vistos.extend(nuevos)
        if not nuevos and results:
            print(f"    <sin titulos reconocibles en {len(results)} results>")
        print(f"    pagination_nodes_url ({len(nodes)} nodos):")
        for node in nodes:
            if isinstance(node, dict):
                print(f"      value={node.get('value')!r} is_actual_page={node.get('is_actual_page')!r} url={node.get('url')!r}")
            else:
                print(f"      <forma inesperada> {node!r}")

        if not results:
            print("\n>>> CORTE: results vacio en este offset.")
            break
        if not nodes:
            print("\n>>> CORTE: pagination_nodes_url vacio.")
            break

        seen_urls.add(url)
        candidates = [
            (node.get("value"), node.get("url"))
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("url"), str) and not node.get("is_actual_page")
        ]
        numeric_candidates = []
        for value, node_url in candidates:
            try:
                numeric_candidates.append((int(value), node_url))
            except (TypeError, ValueError):
                continue

        if not numeric_candidates:
            print("\n>>> CORTE: no hay nodos numericos que no sean is_actual_page.")
            break

        max_value, next_url = max(numeric_candidates, key=lambda pair: pair[0])
        if max_value <= last_max_value:
            print(f"\n>>> CORTE: sin avance (max_value={max_value} <= last_max_value={last_max_value}).")
            break
        if next_url in seen_urls:
            print(f"\n>>> CORTE: {next_url} ya fue visitado (repite nodo).")
            break

        last_max_value = max_value
        url = next_url
        time.sleep(args.delay)
    else:
        print(f"\n>>> Tope de seguridad --max-requests={args.max_requests} alcanzado sin ver corte natural.")

    # wdxtkg39v1: el resumen es el dato que importa. Comparar el `max offset`
    # de dos consultas distintas es lo que dice si el cupo de ~2.000 es POR
    # CONSULTA (cada tajada recupera su propio cupo -> partir por modelo sirve)
    # o POR SESION (la segunda consulta se corta antes -> todo el plan cae).
    print("\n" + "=" * 66)
    print(f"  consulta        : {consulta}")
    print(f"  pasos           : {step}")
    print(f"  offset maximo   : {max_offset}")
    print(f"  results sumados : {total_results}")

    if escalares_primer_paso:
        print("  --- campos escalares del primer paso (total de resultados?) ---")
        for linea in escalares_primer_paso:
            print(f"    {linea}")

    # El veredicto va ACA y no arriba a proposito: es el dato que decide si se
    # puede implementar, y con paginaciones largas la parte de arriba se pierde
    # en el scroll.
    filtro = args.modelo or args.marca or ""
    if titulos_vistos and filtro:
        objetivo = compact(filtro)
        aciertos = sum(1 for t in titulos_vistos if objetivo and objetivo in compact(t))
        pct = 100 * aciertos / len(titulos_vistos)
        print(f"  --- coincidencia con {filtro!r} ---")
        print(f"    titulos analizados : {len(titulos_vistos)}")
        print(f"    coinciden          : {aciertos} ({pct:.0f}%)")
        print("    muestra:")
        for t in titulos_vistos[:5]:
            print(f"      {t}")
        if pct >= 80:
            print(f"    VEREDICTO: el filtro se aplica de verdad ({pct:.0f}% coincide).")
        else:
            print(f"    VEREDICTO: OJO - solo {pct:.0f}% coincide. Muy probablemente ML "
                  "cayo a un listado generico SIN filtrar (mismo modo de falla que la "
                  "marca 'salto'). NO usar esta forma de URL.")
    elif titulos_vistos:
        print(f"  --- muestra de titulos ({len(titulos_vistos)} recolectados) ---")
        for t_ in titulos_vistos[:args.titulos]:
            print(f"    {t_}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
