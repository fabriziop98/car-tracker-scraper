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
from car_tracker_scraper.extraction.mercadolibre import extract_nordic_ctx


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=PERSONA_POOL[0].headers())
    with urllib.request.urlopen(req, timeout=15) as response:
        html = response.read().decode("utf-8", errors="replace")
    ctx = extract_nordic_ctx(html)
    return ctx["appProps"]["sharedState"]["search"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("marca")
    parser.add_argument("--max-requests", type=int, default=40)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    url = f"https://autos.mercadolibre.com.ar/{args.marca}"
    seen_urls: set[str] = set()
    last_max_value = -1

    for step in range(1, args.max_requests + 1):
        print(f"\n--- step {step}: GET {url}")
        search = fetch(url)
        results = search.get("results", [])
        pagination = search.get("pagination", {})
        nodes = pagination.get("pagination_nodes_url", [])

        print(f"    results: {len(results)}")
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
