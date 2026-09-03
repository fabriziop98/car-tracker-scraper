"""Extraccion de Kavak (wdxtkg3hc5).

Igual que Motordil, Kavak es un Next.js (App Router) que sirve el dato en el
**RSC flight stream** (`self.__next_f.push([1,"..."])`) - no hay microdata
schema.org por aviso (solo aparece para el breadcrumb) ni una API JSON aparte.
Se reusa `extraction.motordil.extract_rsc_payload()` para reconstruir el
stream: es generico, no tiene nada especifico de Motordil.

**A diferencia de Autocosmos, acá NO hay hallazgo de precio falso que
mitigar.** Verificado contra HTML real: "Precio contado" y "Precio
financiando" son dos precios de venta REALES del mismo auto (un descuento
legitimo por financiar, no un anticipo parcial) - los dos representan el
valor total del vehiculo. `card_price()`/`detail_price()` siempre devuelven el
precio de CONTADO (el mas alto de los dos cuando hay descuento por
financiacion), nunca el financiado, para que la serie de precios sea
comparable entre avisos con y sin promocion.

**Kavak es un revendedor unico** (reacondiciona y revende su propio stock, no
un clasificado multi-vendedor) - `seller_type`/`seller_name` se hardcodean en
el spider en vez de extraerse, mismo criterio que Autocity con `seller_id`
(ver comentario en car-tracker/CLAUDE.md sobre V6 vs Autocity).

Dos formas de la tarjeta segun de donde salga:

1. **Grilla** (`/ar/usados`, `/ar/usados/{marca}`): cada tarjeta es un objeto
   `{"id": "543535", "url": "...", "title": "Renault • Kangoo",
   "subtitle": "2024 • 68.500 km • 1.6 2A • Manual",
   "mainPrice": "23.960.000", "analytics": {"car_price": "23960000", ...}}`.
   El precio de CONTADO no siempre es `mainPrice` (que muestra el financiado
   cuando hay descuento) - se usa `analytics.car_price`, que sale limpio y sin
   descuento en las tarjetas verificadas.
2. **Ficha de detalle**: un evento de analytics `{"track": {"event":
   "vip_viewed", "car_price": 30640000, "car_pricefinal": 30640000,
   "car_price_financing_final": 30312000, ...}}` con los mismos campos pero
   como NUMEROS, no strings, y con mas detalle (color, combustible,
   transmision, sucursal).
"""
from __future__ import annotations

import json
import re
from typing import Iterator

from car_tracker_scraper.extraction.common import extract_balanced_json
from car_tracker_scraper.extraction.motordil import extract_rsc_payload

BASE_URL = "https://www.kavak.com"

_CARD_START_RE = re.compile(r'\{"id":"\d+","url":')
_TRACK_START_RE = re.compile(r'"track":\{')
_DIGITS_RE = re.compile(r"\d+")


def card_detail_url(card: dict) -> str | None:
    """La URL de la tarjeta CON `?id=` agregado.

    **Hallazgo critico verificado contra el sitio real (2026-09-02)**: la
    `url` que trae la tarjeta (ej. `.../volkswagen-gol_trend-16_pack_i-
    hatchback-2012`) NO resuelve de forma determinista al auto de esa
    tarjeta. Se probo en vivo: Discovery vio el id 542257 ("Gol Trend 1.6
    PACK I") en esa URL: pedirla TAL CUAL (sin `?id=`) devolvio el aviso
    543615 ("Gol Trend 1.6 TRENDLINE") - un auto completamente distinto, ni
    siquiera la misma version. El slug de la URL es cosmetico/SEO; el server
    solo identifica el stock especifico por el query param `id`. Sin este
    fix, Detail traeria sistematicamente el auto equivocado para practicamente
    todos los avisos."""
    url = card.get("url")
    listing_id = card.get("id")
    if not url or not listing_id:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}id={listing_id}"


def iter_cards(payload: str) -> Iterator[dict]:
    """Cada objeto de tarjeta del stream reconstruido de una pagina de grilla."""
    for match in _CARD_START_RE.finditer(payload):
        try:
            card = extract_balanced_json(payload, match.start())
        except (ValueError, json.JSONDecodeError):
            continue
        if "analytics" in card:
            yield card


def card_price(card: dict) -> tuple[str | None, str | None]:
    """(price_amount, price_currency) de CONTADO - nunca el financiado.

    **OJO - primer intento fallido, documentado a proposito**: la primera
    version de esta funcion devolvia `analytics.car_price` directo, asumiendo
    que ese campo era siempre el de contado. Verificado contra fixture real
    (Toyota Yaris, CON promocion "Precio financiando 50%"): `analytics.
    car_price` da "30312000" (el FINANCIADO, mas bajo), no "30640000" (el de
    contado real, confirmado independientemente contra la ficha de detalle de
    ese mismo aviso). El campo que si es el de contado en la tarjeta es
    `priceTop` cuando esta presente (el precio "antes" tachado que se muestra
    junto al financiado) - si `priceTop` viene vacio es porque no hay
    promocion, y ahi `mainPrice` YA es el de contado (unico precio mostrado).
    """
    price_top = (card.get("priceTop") or "").strip()
    raw = price_top.replace("$$", "").strip() if price_top else card.get("mainPrice")
    if not raw:
        return None, None
    normalized = raw.replace(".", "")
    if not normalized.isdigit():
        return None, None
    # Todos los avisos verificados publican en ARS (simbolo "$$" en el
    # payload, que no es un codigo de moneda real - no se vio ningun aviso en
    # USD en Kavak Argentina). Asumir ARS a proposito, no dejar la moneda sin
    # setear.
    return normalized, "ARS"


def card_year(card: dict) -> int | None:
    return (card.get("analytics") or {}).get("car_year")


def card_km(card: dict) -> int | None:
    """El km NO esta en `analytics` de la tarjeta de grilla (si en la ficha) -
    hay que parsearlo del `subtitle` ('2024 • 68.500 km • ...')."""
    subtitle = card.get("subtitle") or ""
    parts = [p.strip() for p in subtitle.split("•")]
    for part in parts:
        if part.endswith("km"):
            digits = "".join(_DIGITS_RE.findall(part))
            return int(digits) if digits else None
    return None


def card_version(card: dict) -> str | None:
    """Tercer segmento del subtitle ('2024 • 68.500 km • 1.6 2A • Manual')."""
    parts = [p.strip() for p in (card.get("subtitle") or "").split("•")]
    return parts[2] if len(parts) > 2 and parts[2] else None


def card_transmission(card: dict) -> str | None:
    parts = [p.strip() for p in (card.get("subtitle") or "").split("•")]
    return parts[3] if len(parts) > 3 and parts[3] else None


# --- ficha de detalle ---------------------------------------------------------


def detail_analytics(payload: str) -> dict | None:
    """El evento `vip_viewed` con todos los campos del auto - hay mas de una
    copia identica en el stream (confirmado contra fixture real), se toma la
    primera que tenga `car_id`.

    **Ojo con avisos caidos**: Kavak devuelve HTTP 200 (no 404) para un `id`
    inexistente, y la pagina de "empty state" IGUAL trae un evento
    `vip_viewed` con la clave `car_id` presente pero en `None` (confirmado
    contra el sitio real, `?id=999999999`) - por eso el chequeo es
    `obj.get("car_id")` (valor truthy), no `"car_id" in obj` (la clave
    siempre esta, viva o muerta la ficha)."""
    for match in _TRACK_START_RE.finditer(payload):
        try:
            obj = extract_balanced_json(payload, match.end() - 1)
        except (ValueError, json.JSONDecodeError):
            continue
        if obj.get("car_id"):
            return obj
    return None


def detail_price(analytics: dict) -> tuple[str | None, str | None]:
    """El de CONTADO, nunca `car_price_financing_final` (el financiado con
    descuento).

    **Ojo, la misma clave significa otra cosa en la grilla**: en el evento
    `vip_viewed` de la FICHA, `car_price` SI es directamente el de contado
    (confirmado: 30640000 para el Yaris, igual al `priceTop` de su propia
    tarjeta de grilla) - a diferencia de `analytics.car_price` DENTRO de una
    tarjeta de grilla, que es el precio financiado cuando hay promocion (ver
    el docstring de card_price(), el bug que motivo esa aclaracion)."""
    amount = analytics.get("car_price")
    if not amount:
        return None, None
    return str(amount), "ARS"


def detail_breadcrumb(payload: str) -> str | None:
    """Los nombres del `BreadcrumbList` (schema.org, unico microdata que trae
    esta fuente) aparecen en orden de documento como `itemProp":"name",
    "children":"TEXTO"` - confirmado contra fixture real."""
    names = re.findall(r'itemProp":"name","children":"([^"]+)"', payload)
    return " > ".join(names) if names else None


_DYNAMIC_START_RE = re.compile(r'"dynamic":\{')


def detail_dynamic(payload: str) -> dict | None:
    """El objeto `dynamic` de la ficha: precio + promocion + sucursal, todo en
    un solo lugar limpio.

    **OJO - primer intento descartado antes de escribir esto**: la primera
    forma de sacar la ubicacion fue una regex posicional buscando el texto
    "Ciudad" en el arbol serializado (el valor se renderiza ANTES que la
    etiqueta en ese componente) - funcionaba pero era fragil a cualquier
    cambio de layout. Este objeto `dynamic` es mucho mas confiable: `price` y
    `promotion.promotionPrice` coinciden exactamente con lo que ya daba
    `detail_analytics()` (triple confirmado contra el mismo fixture), y de
    paso trae `warehouse.region`/`warehouse.name` limpios en vez de tener que
    parsear texto. Unico en la pagina (confirmado contra fixture real)."""
    match = _DYNAMIC_START_RE.search(payload)
    if not match:
        return None
    try:
        return extract_balanced_json(payload, match.end() - 1)
    except (ValueError, json.JSONDecodeError):
        return None


def detail_province(dynamic: dict) -> str | None:
    """`warehouse.region` - ej. 'Buenos Aires'. Viene de la sucursal fisica
    asignada al auto, no de una direccion de vendedor (no hay vendedores
    distintos, Kavak es revendedor unico)."""
    return (dynamic.get("warehouse") or {}).get("region") or None


def detail_sucursal(dynamic: dict) -> str | None:
    return (dynamic.get("warehouse") or {}).get("name") or None
