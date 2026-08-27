"""Extraccion de Autocity (wdxtkg39qx).

Cuarta fuente. Es un WooCommerce sobre WordPress, server-rendered - ni blob
JSON como ML, ni flight stream como Motordil/V6, ni microdata como DeRuedas.

Lo bueno: la ficha expone TODOS los datos del vehiculo como atributos `data-*`
del `<main class="ficha-producto-page">`, que es tan estructurado como un JSON
y no depende de raspar texto:

    data-price="$ 33.200.000" data-ano="2021" data-kms="70.700 km "
    data-brand="Citroen" data-model="C5 Aircross" data-estado="Usado"

La version/trim no esta ahi pero si en el <title>, con la forma
"{version} - {modelo} - {marca} - Catalogo de Autos Usados | Autocity".

Dos particularidades que la hacen mas facil que las fuentes anteriores:

*   **Discovery por sitemap, sin paginacion.** `wp-sitemap-posts-product-1.xml`
    lista las 405 fichas en UNA request. El catalogo paginado tambien existe
    (/catalogo/usados/page/N/) pero da ~13 autos por pagina con solapamiento
    entre paginas - 22 requests y deduplicacion para el mismo resultado.
*   **Filtro de 0km gratis.** La URL separa `/catalogo/usados/` de
    `/catalogo/0km/`, asi que se descarta por path en vez de por heuristica.
    En la ficha ademas esta `data-estado`, que confirma lo mismo. Es mucho mas
    confiable que lo que hubo que armar para Motordil (odometer==0) o DeRuedas
    (odometro explicito, porque su itemCondition miente).
"""
from __future__ import annotations

import re
from typing import Iterator

from parsel import Selector

SITEMAP_URL = "https://autocity.com.ar/wp-sitemap-posts-product-1.xml"
USED_PATH = "/catalogo/usados/"

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_DIGITS_RE = re.compile(r"\d+")
# "/catalogo/usados/m-citroen/m-c5-aircross/1-6-thp-feel-pack-at6-l20/"
_BRAND_IN_URL_RE = re.compile(r"/catalogo/usados/m-([^/]+)/")


def iter_used_urls(sitemap_xml: str) -> Iterator[str]:
    """URLs de fichas de USADOS del sitemap de productos.

    Filtra por el path `/catalogo/usados/`: los 0km viven bajo `/catalogo/0km/`
    y se descartan aca, antes de gastar un solo fetch de Detail. Tambien saltea
    la URL del feed RSS de WordPress, que matchea el prefijo pero no es un auto.
    """
    for url in _LOC_RE.findall(sitemap_xml):
        if USED_PATH not in url:
            continue
        if url.rstrip("/").endswith("/feed"):
            continue
        yield url


def listing_key_from_url(url: str) -> str | None:
    """Clave estable del aviso: el path bajo /catalogo/usados/.

    Ej. '.../usados/m-citroen/m-c5-aircross/1-6-thp-feel-pack-at6-l20/'
        -> 'm-citroen/m-c5-aircross/1-6-thp-feel-pack-at6-l20'

    Se usa el slug y no un id numerico porque el sitemap no expone el post_id de
    WooCommerce, y el slug ya es unico por aviso (marca/modelo/version).
    """
    if USED_PATH not in url:
        return None
    key = url.split(USED_PATH, 1)[1].strip("/")
    return key or None


def brand_from_url(url: str) -> str | None:
    """'/catalogo/usados/m-citroen/...' -> 'citroen'.
    '/catalogo/usados/m-mercedes-benz/...' -> 'mercedes-benz'.

    Autocity prefija las marcas con 'm-' en la URL, pero el resto del segmento
    YA tiene forma de slug. Solo se saca el prefijo: convertir los guiones a
    espacios daria 'mercedes benz', que no es un slug valido y rompe el
    matching contra las marcas curadas (lo detecto el contrato de
    test_source_contract.py). Normalizarlo al slug CURADO sigue siendo
    responsabilidad del spider, via resolve_marca_slug.
    """
    match = _BRAND_IN_URL_RE.search(url)
    return match.group(1) if match else None


def ficha(html: str) -> Selector | None:
    """El `<main class="ficha-producto-page">`, que lleva los data-* del auto."""
    blocks = Selector(text=html).css("main.ficha-producto-page")
    return blocks[0] if blocks else None


def parse_price(raw: str | None) -> tuple[str | None, str | None]:
    """'$ 33.200.000' -> ('33200000', 'ARS').

    Se lee la moneda del simbolo en vez de asumirla: Autocity publica en pesos
    hoy, pero asumir la moneda por contexto de pagina es exactamente el error
    que DeRuedas casi mete en la serie (declaraba ARS para todo y convertia los
    USD con su cotizacion). Si aparece un aviso en dolares, este parser lo
    respeta en vez de registrarlo como pesos.
    """
    if not raw:
        return None, None
    text = re.sub(r"\s+", " ", raw).strip()
    match = re.match(r"^(U\$S?|US\$|USD|\$)\s*([\d.,]+)$", text)
    if not match:
        return None, None
    symbol, amount = match.group(1), match.group(2)
    normalized = amount.replace(".", "").replace(",", ".")
    return normalized, ("ARS" if symbol == "$" else "USD")


def parse_km(raw: str | None) -> int | None:
    """'70.700 km ' -> 70700."""
    if not raw:
        return None
    digits = "".join(_DIGITS_RE.findall(raw.replace(".", "")))
    return int(digits) if digits else None


def parse_year(raw: str | None) -> int | None:
    if not raw:
        return None
    match = re.search(r"(19|20)\d{2}", raw)
    return int(match.group(0)) if match else None


def version_from_title(title: str | None, model: str | None) -> str | None:
    """Saca el trim del <title>.

    Forma real: "1.6 thp feel pack at6 l20 - C5 Aircross - Citroen - Catalogo
    de Autos Usados | Autocity" - el primer segmento es la version. Se valida
    que el segundo segmento sea el modelo antes de confiar, asi un cambio de
    formato del title devuelve None en vez de un trim inventado.
    """
    if not title:
        return None
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) < 3 or not parts[0]:
        return None
    if model and parts[1].lower() != model.strip().lower():
        return None
    return parts[0]
