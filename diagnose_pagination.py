"""Diagnostico puntual: que hay realmente en pagination_nodes_url.
No es parte del spider - correr una vez y borrar."""
import urllib.request

from car_tracker_scraper.extraction.mercadolibre import extract_nordic_ctx
from car_tracker_scraper.settings import USER_AGENT

req = urllib.request.Request(
    "https://autos.mercadolibre.com.ar/fiat",
    headers={"User-Agent": USER_AGENT, "Accept-Language": "es-AR,es;q=0.9"},
)
html = urllib.request.urlopen(req).read().decode("utf-8")

ctx = extract_nordic_ctx(html)
pagination = ctx["appProps"]["sharedState"]["search"].get("pagination", {})
nodes = pagination.get("pagination_nodes_url", [])

print("tipo de pagination_nodes_url:", type(nodes))
for i, n in enumerate(nodes):
    print(i, type(n), repr(n))
