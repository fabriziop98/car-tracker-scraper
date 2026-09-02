"""Extraccion de V6 Marketplace (wdxtkg39qx).

Quinta fuente. A diferencia de las anteriores, el catalogo entero se sirve en
un unico JSON publico y sin autenticacion:

    GET https://autoprecios-api.onrender.com/api/db/getPublishedCars

Nada de parsear HTML: reverse-engineering 2026-09-02 (leyendo la Performance
API de un browser real, no adivinando) confirmo que /autos de www.v6.com.ar
(Next.js/Vercel) NO trae los avisos ni en el documento servido ni en su
flight stream - los busca client-side con `fetch()` directo a ese backend (un
servicio Node en Render, separado del front). El array completo (209 avisos
activos al momento de la captura) es la unica fuente de verdad; la ficha HTML
/auto/{slug} no aporta nada que este JSON no tenga.

Por eso esta fuente rompe el patron Discovery-barato/Detail-caro que usan las
otras cuatro (ver CLAUDE.md de este repo): Discovery Y Detail pegan al mismo
endpoint, ninguno de los dos pisa /auto/{slug}. Decision explicita de
Fabrizio, no un atajo tomado sin preguntar.
"""
from __future__ import annotations

import re

_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

PUBLISHED_CARS_URL = "https://autoprecios-api.onrender.com/api/db/getPublishedCars"


def slugify(text: str) -> str:
    """Mismo algoritmo que usa v6.com.ar para armar sus URLs de ficha.

    Confirmado byte a byte contra URLs reales capturadas en vivo (2026-09-02):
    "Ford Fiesta Kinetic Design SE 1.6 MT 5P 2018" -> "ford-fiesta-kinetic-
    design-se-1-6-mt-5p-2018", que es exactamente el path que sirve el sitio
    (incluido el punto de "1.6" volviendose guion, no punto ni nada).
    """
    lowered = text.lower()
    collapsed = _SLUG_NON_ALNUM_RE.sub("-", lowered)
    return collapsed.strip("-")


def detail_path(car: dict) -> str | None:
    """Arma el path /auto/{slug}-{uid} de la ficha, con el mismo slug que usa
    el sitio (ver `slugify`). `uid` es el id de Firestore, siempre presente
    en un registro real - sin el no hay forma de armar una URL valida."""
    uid = car.get("uid")
    if not uid:
        return None
    parts = [car.get("brand"), car.get("model"), car.get("version"), car.get("year")]
    text = " ".join(str(p) for p in parts if p)
    slug = slugify(text)
    return f"/auto/{slug}-{uid}" if slug else f"/auto/{uid}"


def is_zero_km(car: dict) -> bool:
    """0km fuera: el proyecto trackea el mercado de USADOS, mismo criterio e
    incidente real (wdxtkg30xr) que motiva el filtro equivalente en
    Motordil/DeRuedas."""
    try:
        return int(car.get("kilometers") or 0) == 0
    except (TypeError, ValueError):
        return False


def resolve_currency(car: dict) -> str | None:
    """La moneda no esta en un campo plano junto a `price`: hay que leerla de
    la entrada MAS RECIENTE de `priceHistory`. Ese array viene ordenado
    descendente por fecha (confirmado contra datos reales) - el indice 0 es
    el mas nuevo, no el `[-1]`.

    91 de 209 avisos reales (2026-09-02) no tienen `priceHistory` en absoluto
    (nunca bajaron de precio desde que se publicaron) - en ese caso no hay
    señal de moneda y se deja en `None` a proposito: el cascade de
    PriceValidationService del lado Java ya resuelve moneda ambigua
    comparando contra la cohorte de la misma moneda (ver su CLAUDE.md), no
    hace falta duplicar esa heuristica aca con una adivinanza por magnitud.
    """
    history = car.get("priceHistory") or []
    if not history:
        return None
    return history[0].get("currency")


def title(car: dict) -> str | None:
    """V6 no expone un titulo libre en el JSON - se compone solo para tener
    algo legible en el .jsonl de salida y en los logs, mismo criterio que
    Motordil. La normalizacion del lado Java usa brand/model/version sueltos,
    no esto."""
    parts = [
        str(car["year"]) if car.get("year") else None,
        car.get("brand"),
        car.get("model"),
        car.get("version"),
    ]
    parts = [p for p in parts if p]
    return " ".join(parts) if parts else None


def flatten_specs(specs: dict | None) -> list[str]:
    """Aplana specs.{categoria}.{atributo} en pares "atributo: valor" para
    `highlighted_specs_raw`. Descarta valores vacios/None/False: son comunes
    en datos reales (ej. el Peugeot 208 del fixture trae varios campos de
    `carrocería` en "" cuando el concesionario no los cargo)."""
    if not specs:
        return []
    flat = []
    for category in specs.values():
        if not isinstance(category, dict):
            continue
        for key, value in category.items():
            if value in (None, "", False):
                continue
            flat.append(f"{key}: {value}")
    return flat


def agency_seller(car: dict) -> tuple[str | None, str | None, str | None]:
    """(seller_name, seller_type, seller_id) a partir de `agencyUrl`.

    Solo confirmado para avisos con `fromAgency=True` (ver el Toyota Hiace
    real del fixture: `agencyUrl="/agencia/Car-Cash-Argentina"`). Sin ese
    flag no hay ninguna señal de concesionaria vs. particular en el JSON -
    se deja todo en `None`, mismo criterio conservador que Motordil aplica a
    su propio invariante de `dealership` todavia no confirmado al 100%."""
    if not car.get("fromAgency"):
        return None, None, None
    agency_url = car.get("agencyUrl")
    if not agency_url:
        return None, None, "car_dealer"
    slug = agency_url.rsplit("/", 1)[-1]
    name = slug.replace("-", " ").strip()
    return (name or None), "car_dealer", slug
