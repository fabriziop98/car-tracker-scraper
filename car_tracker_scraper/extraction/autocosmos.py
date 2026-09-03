"""Extraccion de Autocosmos (wdxtkg3hc4).

Al igual que DeRuedas, la grilla y la ficha traen **microdata schema.org**
(`itemtype="http://schema.org/Car"` en la grilla, envoltorio equivalente en la
ficha) con un `itemprop` por campo - se prioriza sobre heuristica de texto por
la misma razon: es un estandar que el sitio tiene incentivo propio en mantener
bien formado (rich snippets de Google).

**El hallazgo que justifica este modulo (investigacion 2026-09-02, ver
wdxtkg3hc4)**: una parte de los avisos usados de Autocosmos son "financiados en
cuotas" y en esos el numero grande que se ve como precio es el ANTICIPO de un
plan de financiacion, no el precio del auto. Confirmado contra HTML real: un
Chevrolet Tracker (2025, `seccion=financiados`) trae

    <div itemprop="priceSpecification" itemscope
         itemtype="http://schema.org/PriceSpecification"
         class="listing-card__price m-anticipo m-alone">
        <meta itemprop="priceCurrency" content="ARS" />
        <span class="listing-card__price-title">Anticipo: </span>
        <span class="listing-card__price-value" itemprop="price"
              content="4000000">$4.000.000</span>
    </div>

mientras que un aviso de precio real (Nissan March, `seccion=precio-final`)
trae el `itemprop="price"` colgando DIRECTO de `offers`, sin el envoltorio
`PriceSpecification`:

    <span class="listing-card__price ">
        <meta itemprop="priceCurrency" content="ARS" />
        <span class="listing-card__price-value" itemprop="price"
              content="13900000">$13.900.000</span>
    </span>

**OJO - intento fallido documentado a proposito**: la primera version de este
modulo detectaba el anticipo por `itemtype*="PriceSpecification"`, razonando
que el itemtype era mas estable que una clase CSS. Un chequeo cruzado contra
el fixture de detalle real (`autocosmos_detail_contado.html`, precio NORMAL)
lo reventó: en la FICHA de detalle (a diferencia de la grilla) **el precio
normal TAMBIEN viene envuelto en `itemtype="PriceSpecification"`** -

    <p class="car-specifics__price  " itemprop="priceSpecification"
       itemscope itemtype="http://schema.org/PriceSpecification">
        <meta itemprop="priceCurrency" content="ARS" />
        <strong itemprop="price" content="13900000">$13.900.000</strong>
    </p>

- asi que el itemtype NO discrimina ahi y un Nissan March de precio real
quedaba clasificado como anticipo. La clase `m-anticipo` SI aparece en los dos
formatos (grilla y ficha) solo cuando es una cuota inicial, asi que es la
señal que se usa - la generalizacion "el estandar semantico es mas confiable
que la clase CSS" que funciono para DeRuedas no vale aca sin verificarla
primero contra un fixture de cada caso.

Con esto, Discovery NO necesita filtrar por `?seccion=precio-final` (como se
penso en la investigacion inicial): puede recorrer el catalogo completo
(`/auto/usado`) y clasificar cada aviso vos misma - un aviso "en cuotas" se
emite igual, con `price_amount=None` y `financing_initial_payment` seteado, en
vez de perderse. Eso sube la cobertura de 5.330 (solo "Precio final") a los
5.690 usados reales.
"""
from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin

from parsel import Selector

_DIGITS_RE = re.compile(r"\d+")
_HASH_ID_RE = re.compile(r"/([0-9a-f]{32})/?$")

BASE_URL = "https://www.autocosmos.com.ar"


def iter_cards(html: str) -> Iterator[Selector]:
    """Cada `<article itemtype=".../Car">` de la grilla de resultados."""
    sel = Selector(text=html)
    yield from sel.css('[itemtype*="schema.org/Car"]')


def card_url(card: Selector) -> str | None:
    href = card.css('[itemprop="url"]::attr(href)').get()
    return urljoin(BASE_URL, href) if href else None


def listing_key_from_url(url: str | None) -> str | None:
    """El hash de 32 hex al final de la URL canonica, ej.

    '/auto/usado/toyota/corolla-cross-hybrid/hibrida-18-seg-ecvt/526c...b93'
    -> '526c...b93'. Es el mismo id en la tarjeta de grilla y en la ficha de
    detalle (confirmado contra HTML real), asi que sirve de source_listing_key
    estable entre Discovery y Detail.
    """
    if not url:
        return None
    match = _HASH_ID_RE.search(url)
    return match.group(1) if match else None


def card_brand(card: Selector) -> str | None:
    text = card.css('[itemprop="brand"]::text').get()
    return text.strip() if text else None


def card_model(card: Selector) -> str | None:
    """El modelo puro, SIN la version.

    El `itemprop="model"` envuelve un div que a su vez tiene DOS spans propios
    por clase CSS (`.listing-card__model` y `.listing-card__version`) - leer
    el itemprop entero devuelve "Corolla Cross Hybrid Active" pegado, sin poder
    separar donde termina el modelo y empieza la version.
    """
    text = card.css(".listing-card__model::text").get()
    return text.strip() if text else None


def card_version(card: Selector) -> str | None:
    text = card.css(".listing-card__version::text").get()
    return text.strip() if text else None


def card_year(card: Selector) -> int | None:
    text = card.css('[itemprop="modelDate"]::text').get()
    return parse_year(text)


def card_km(card: Selector) -> int | None:
    content = card.css('[itemprop="mileageFromOdometer"]::attr(content)').get()
    return parse_km(content)


def card_location(card: Selector) -> tuple[str | None, str | None]:
    """(ciudad, provincia). La ciudad viene con un ' | ' colgado en el HTML
    real (es el separador visual con la provincia, que el sitio deja pegado al
    texto del span en vez de ponerlo en el markup) - se limpia aca."""
    city = card.css('[itemprop="addressLocality"]::text').get()
    province = card.css('[itemprop="addressRegion"]::text').get()
    city = city.strip(" |\xa0") if city else None
    province = province.strip() if province else None
    return city or None, province


def is_financed_price(card_or_vehicle: Selector) -> bool:
    """True si el precio de este bloque es un ANTICIPO de financiacion.

    Se ancla en la clase CSS `m-anticipo` - ver el docstring del modulo para
    por que NO se usa el itemtype `PriceSpecification` (aparece en los dos
    casos dentro de la ficha de detalle, asi que ahi no discrimina nada)."""
    return bool(card_or_vehicle.css(".m-anticipo"))


def card_price(card: Selector) -> tuple[str | None, str | None, str | None]:
    """(price_amount, price_currency, financing_initial_payment).

    Si el bloque es un anticipo, price_amount/price_currency quedan en None a
    proposito - no hay forma de derivar el precio real del auto a partir de
    una cuota inicial, y NO hacerlo es la mitigacion completa del hallazgo de
    wdxtkg3hc4."""
    amount = card.css('[itemprop="price"]::attr(content)').get()
    if amount is None:
        return None, None, None
    if is_financed_price(card):
        return None, None, amount
    currency = card.css('[itemprop="priceCurrency"]::attr(content)').get()
    return amount, currency, None


def parse_km(value: str | None) -> int | None:
    """'KMT 110000' -> 110000. Mismo formato (unitCode+valor pegados) que la
    ficha de DeRuedas, pero aca aparece tambien en la GRILLA."""
    if not value:
        return None
    digits = "".join(_DIGITS_RE.findall(value))
    return int(digits) if digits else None


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(19|20)\d{2}", value)
    return int(match.group(0)) if match else None


# --- ficha de detalle ---------------------------------------------------------
# Misma logica de itemprops que la grilla, con dos diferencias confirmadas
# contra HTML real: el nombre/marca/modelo vienen en <meta> sueltos en vez de
# spans visibles, y el color y el tipo de vendedor SOLO aparecen aca (la
# grilla no los expone).

_SELLER_TYPE_RE = re.compile(r'name="dfp_privado"\s+content="([^"]+)"')
_SELLER_TYPE_MAP = {"empresa": "car_dealer", "particular": "particular"}


def detail_vehicle(html: str) -> Selector | None:
    """El bloque `schema.org/Car` de la ficha - confirmado que el precio
    (`offers`/`priceSpecification`) esta ANIDADO adentro de este mismo
    itemscope, igual que en la grilla, asi que `card_price()` sirve tal cual
    sobre lo que devuelve esta funcion (no hace falta una variante `detail_*`
    separada para precio)."""
    blocks = Selector(text=html).css('[itemtype*="schema.org/Car"]')
    return blocks[0] if blocks else None


def detail_brand(vehicle: Selector) -> str | None:
    return vehicle.css('[itemprop="brand"]::attr(content)').get()


def detail_model(vehicle: Selector) -> str | None:
    """A diferencia de la grilla (donde el modelo esta partido en dos spans
    por clase CSS), la ficha trae el modelo puro directo en el meta."""
    return vehicle.css('[itemprop="model"]::attr(content)').get()


def detail_version(vehicle: Selector) -> str | None:
    """La ficha NO tiene itemprop para la version - solo aparece en el H1
    (`.car-specifics__version`), como texto suelto sin marcar semanticamente."""
    text = vehicle.css(".car-specifics__version::text").get()
    return text.strip() if text else None


def detail_color(vehicle: Selector) -> str | None:
    return vehicle.css('[itemprop="color"]::text').get()


def detail_title(vehicle: Selector) -> str | None:
    """El nombre completo ya viene armado por el sitio en un unico meta -
    'Toyota Corolla Cross Hybrid Hibrida 1.8 SEG eCVT' - no hace falta unir
    brand+model+version a mano como en otras fuentes."""
    return vehicle.css('[itemprop="name"]::attr(content)').get()


def detail_seller_type(html: str) -> str | None:
    """`<meta name="dfp_privado" content="empresa|particular">` - tag interno
    de segmentacion publicitaria (DoubleClick For Publishers), pero es una
    señal por-aviso limpia de tipo de vendedor, sin heuristica. Confirmado:
    "empresa" en un aviso de concesionaria (Toyota Corolla Cross Hybrid,
    financiado), "particular" en un aviso de dueño directo (Nissan March).
    Ausente => None, no se asume nada."""
    match = _SELLER_TYPE_RE.search(html)
    if not match:
        return None
    return _SELLER_TYPE_MAP.get(match.group(1).lower())


def detail_breadcrumb(html: str) -> str | None:
    """El nav de breadcrumb vive ANTES del `<article itemtype=".../Car">` en
    el HTML (fuera de su subtree), asi que toma el HTML entero y no el
    Selector ya escopeado por `detail_vehicle()`."""
    parts = [t.strip() for t in Selector(text=html).css(".breadcrumbs a::text").getall() if t.strip()]
    return " > ".join(parts) if parts else None
