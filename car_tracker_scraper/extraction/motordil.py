"""Extraccion de datos embebidos en las paginas de Motordil (wdxtkg30xr).

Motordil es un Next.js (App Router) sobre Vercel: el dato no viene ni en un
blob JSON unico (como el __NORDIC_RENDERING_CTX__ de ML) ni en microdata, sino
en el "RSC flight stream" - una serie de <script>self.__next_f.push([1,"..."])
</script> donde cada chunk es un STRING JSON-encoded que hay que decodificar y
concatenar para reconstruir el stream completo. Recien sobre ese texto
reconstruido aparecen los objetos que interesan.

Dos gotchas confirmados contra fixtures reales (2026-08-26):

1. La clave raiz CAMBIA segun la pagina: en /results los avisos vienen como
   {"listing": {...}} (24 en la primera pagina), y en /auto/{slug} el aviso
   viene como {"publication": {...}} (uno solo). Por eso `iter_objects_with_key`
   recibe la clave en vez de asumirla.

2. Hay que pedir las paginas SIN el parametro `_rsc` (o sea, como navegacion
   normal, no como prefetch de Next.js). Con `_rsc` la respuesta es solo el
   shell de la pagina, sin el listado - ya documentado en el reverse
   engineering de Fase 0.
"""
from __future__ import annotations

import json
import re
from typing import Iterator

from car_tracker_scraper.extraction.common import extract_balanced_json

_FLIGHT_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.S)


def extract_rsc_payload(html: str) -> str:
    """Reconstruye el flight stream de Next.js concatenando todos sus chunks.

    Cada chunk es un string JSON-encoded (con sus escapes propios), asi que hay
    que pasarlo por json.loads ANTES de concatenar - concatenar los literales
    crudos y decodificar despues no funciona, los escapes quedan mal cortados
    en el borde entre chunks.
    """
    chunks = _FLIGHT_CHUNK_RE.findall(html)
    decoded = []
    for chunk in chunks:
        try:
            decoded.append(json.loads(chunk))
        except json.JSONDecodeError:
            # Un chunk ilegible no invalida el resto del stream: los objetos
            # que buscamos pueden estar enteros en los demas.
            continue
    return "".join(decoded)


def iter_objects_with_key(payload: str, key: str) -> Iterator[dict]:
    """Devuelve cada objeto `{"<key>": {...}}` del stream, ya desenvuelto.

    Busca la apertura literal `{"<key>":` y de ahi corta por balance de llaves
    (el stream no es un JSON valido en si mismo, es una concatenacion de
    fragmentos, asi que no se puede parsear entero de una).
    """
    needle = '{"%s":' % key
    for match in re.finditer(re.escape(needle), payload):
        try:
            wrapper = extract_balanced_json(payload, match.start())
        except (ValueError, json.JSONDecodeError):
            # Objeto truncado en el borde del stream - saltarlo en vez de
            # perder los que si estan completos.
            continue
        value = wrapper.get(key)
        if isinstance(value, dict):
            yield value


def detail_path(listing: dict) -> str | None:
    """Path de la ficha de un aviso de /results.

    `slug` viene null en datos reales (3 de 24 en el fixture capturado), asi
    que hay fallback por id. OJO: la forma /auto/{id} NO esta verificada
    contra el sitio real todavia - se dedujo del patron de la URL con slug.
    Si en la primera corrida real aparecen 404s de Detail, este es el primer
    lugar a mirar.
    """
    slug = listing.get("slug") or listing.get("id")
    return f"/auto/{slug}" if slug else None
