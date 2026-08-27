"""Extraccion de las tarjetas de DeRuedas (wdxtkg39pw).

A diferencia de MercadoLibre (blob JSON en __NORDIC_RENDERING_CTX__) y Motordil
(flight stream de Next.js), DeRuedas es un ASP clasico que devuelve HTML: el
endpoint interno `busCraw.asp` responde un fragmento con una tarjeta por aviso.

Lo importante es que ese HTML NO se parsea por clase CSS: cada tarjeta trae
**microdata schema.org** (`itemscope itemtype="https://schema.org/Car"`) con un
`itemprop` por campo. Eso es un estandar semantico que el sitio tiene incentivo
propio en mantener bien formado (lo usa para los rich snippets de Google), asi
que es mucho mas estable que cualquier heuristica sobre clases.

Se usa `parsel` (ya viene con Scrapy) en vez de BeautifulSoup: no hace falta
sumar una dependencia nueva para esto.

Gotcha confirmado en Fase 0 - **no confiar en `itemCondition` a ciegas**: dos
tarjetas reales sin `mileageFromOdometer` (un Audi A7 2013 y un Q5 2012) venian
marcadas `NewCondition`. El campo parece defaultear a "Nuevo" cuando el vendedor
no cargo los kilometros, asi que no sirve solo para decidir 0km vs usado - hay
que cruzarlo con año y km, mismo criterio que en las otras fuentes.
"""
from __future__ import annotations

import re
from typing import Iterator

from parsel import Selector

_CAR_ID_RE = re.compile(r"car_(\d+)")
_COD_USR_RE = re.compile(r"codUsr=(\d+)")
_DIGITS_RE = re.compile(r"\d+")


def iter_cards(html: str) -> Iterator[Selector]:
    """Cada `<div class="divVehiculo" itemtype=".../Car">` del fragmento."""
    sel = Selector(text=html)
    # Se ancla en el itemtype, no en la clase CSS: si el sitio renombra
    # `divVehiculo` el microdata sigue estando (y al reves no vale).
    yield from sel.css('[itemtype*="schema.org/Car"]')


def prop(card: Selector, name: str) -> str | None:
    """Valor de un `itemprop`, mirando content/href y cayendo al texto.

    El microdata pone casi todo en `<meta content="...">`, pero la URL del
    aviso viene como `<a itemprop="url" href="...">` y algun campo puede venir
    como texto plano - por eso los tres intentos, en ese orden.
    """
    node = card.css(f'[itemprop="{name}"]')
    if not node:
        return None
    for attr in ("content", "href"):
        value = node.attrib.get(attr)
        if value:
            return value.strip()
    text = "".join(node[0].css("::text").getall()).strip()
    return text or None


def nested_prop(card: Selector, scope: str, name: str) -> str | None:
    """Un itemprop dentro de un itemscope anidado (ej. `offers` -> `price`)."""
    scoped = card.css(f'[itemprop="{scope}"]')
    if not scoped:
        return None
    return prop(scoped[0], name)


def card_id(card: Selector) -> str | None:
    """`id="car_778285"` -> "778285". Es el source_listing_key."""
    raw = card.attrib.get("id") or ""
    match = _CAR_ID_RE.search(raw)
    if match:
        return match.group(1)
    # Fallback: el `?cod=` de la URL del aviso dice lo mismo.
    url = prop(card, "url") or ""
    match = re.search(r"[?&]cod=(\d+)", url)
    return match.group(1) if match else None


def seller_id(card: Selector) -> str | None:
    """`?codUsr={N}` en el link al perfil = concesionaria; ausente = particular.

    Se devuelve None para particulares a proposito, en vez de un placeholder:
    el seller_id alimenta el fingerprint de dedup, y un valor compartido
    agruparia como "mismo vendedor" a todos los particulares del pais.
    """
    for href in card.css("a::attr(href)").getall():
        match = _COD_USR_RE.search(href)
        if match:
            return match.group(1)
    return None


def published_price(card: Selector) -> tuple[str | None, str | None]:
    """(monto, moneda) TAL COMO SE PUBLICO, leido del texto de la tarjeta.

    **No usar el microdata `offers/price` para esto.** Confirmado contra el
    fixture real (2026-08-27): DeRuedas declara `priceCurrency = ARS` en las 30
    tarjetas, pero 6 de esas 30 estan publicadas en USD y el microdata trae el
    monto ya CONVERTIDO a pesos con la cotizacion interna del sitio (ej. el
    aviso 802906 se publica "U$ 24.500" y el microdata dice 37852500 ARS,
    ~1545 ARS/USD).

    Tomar el microdata meteria un precio fabricado a la cotizacion de DeRuedas
    en el 20% de los avisos de esta fuente - plausible, indetectable por la
    validacion de precios, y contaminando justo la serie que el proyecto mide.
    El schema es explicito: `current_price_amount` se guarda como se publico,
    sin convertir, y la conversion la hace el lado Java con el `fx_rate` del
    momento de la observacion.
    """
    for raw in card.css("span.titulo::text").getall():
        text = re.sub(r"\s+", " ", raw).strip()
        match = re.match(r"^(U\$S?|US\$|USD|\$)\s*([\d.,]+)$", text)
        if not match:
            continue
        symbol, amount = match.group(1), match.group(2)
        # Formato argentino: '.' separa miles, ',' decimales.
        normalized = amount.replace(".", "").replace(",", ".")
        if not normalized.replace(".", "").isdigit():
            continue
        currency = "ARS" if symbol == "$" else "USD"
        return normalized, currency
    return None, None


def version_text(card: Selector) -> str | None:
    """El trim/version del aviso ('2.0T S-tronic Quattro 225cv').

    Presente en 30/30 tarjetas del fixture real. Es la razon por la que esta
    fuente aporta buen texto de catalogo: marca y modelo vienen del microdata y
    la version de aca, los tres separados, sin tener que partir un titulo libre
    como en MercadoLibre.
    """
    for raw in card.css("a.versionLink::text").getall():
        text = re.sub(r"\s+", " ", raw).strip()
        if text:
            return text
    return None


# --- ficha de detalle ---------------------------------------------------------
# La ficha usa itemtype `schema.org/Vehicle` (NO `Car` como la grilla) y
# `modelDate` (NO `vehicleModelDate`). Confirmado contra el fixture real
# 2026-08-27; Fase 0 nunca habia reverse-engineerado esta pagina.

_DETAIL_PRICE_RE = re.compile(r"Precio:\s*<b>\s*(U\$S?|US\$|USD|\$)\s*([\d.,]+)", re.I)
_DESCRIPTION_RE = re.compile(r"^Encontr\w*\s+tu\s+(.*?)\s+en\s+deRuedas\.?$", re.I)


def detail_vehicle(html: str) -> Selector | None:
    """El bloque `schema.org/Vehicle` de la ficha, o None si no esta."""
    blocks = Selector(text=html).css('[itemtype*="schema.org/Vehicle"]')
    return blocks[0] if blocks else None


def detail_published_price(html: str) -> tuple[str | None, str | None]:
    """(monto, moneda) publicados en la ficha, del HTML plano (`Precio: <b>...`).

    Mismo motivo que published_price() en la grilla: el microdata de la ficha
    tambien trae el precio ya convertido a ARS. En el fixture real el aviso
    802906 se publica "U$ 24.500" y su microdata dice 37852500 ARS.
    """
    match = _DETAIL_PRICE_RE.search(html)
    if not match:
        return None, None
    symbol, amount = match.group(1), match.group(2)
    normalized = amount.replace(".", "").replace(",", ".")
    return normalized, ("ARS" if symbol == "$" else "USD")


def detail_version(description: str | None, brand: str | None, model: str | None) -> str | None:
    """Saca el trim de la `description` de la ficha.

    Forma real: "Encontra tu Audi Q5 2.0T S-tronic Quattro 225cv en deRuedas."
    -> se quita el envoltorio y despues el prefijo "{marca} {modelo}". Si el
    texto no tiene esa forma se devuelve None en vez de arriesgar un trim
    inventado (la ficha no expone la version en un campo propio, a diferencia
    de la grilla, que si tiene `a.versionLink`).
    """
    if not description:
        return None
    match = _DESCRIPTION_RE.match(description.strip())
    if not match:
        return None
    inner = match.group(1).strip()
    for prefix in (f"{brand} {model}", brand, model):
        if prefix and inner.lower().startswith(prefix.lower()):
            inner = inner[len(prefix):].strip()
            break
    return inner or None


def parse_km(value: str | None) -> int | None:
    """"142000 km" -> 142000. Ausente en tarjetas reales (2 de 30 en Fase 0)."""
    if not value:
        return None
    digits = "".join(_DIGITS_RE.findall(value.replace(".", "")))
    return int(digits) if digits else None


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(19|20)\d{2}", value)
    return int(match.group(0)) if match else None
