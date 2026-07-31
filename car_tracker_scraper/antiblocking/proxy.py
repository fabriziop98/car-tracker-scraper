"""Pool de proxies datacenter (wdxtkg30nk). Sin proveedor contratado
todavia (2026-07-31) - queda deshabilitado por default (ANTIBLOCK_PROXY_LIST
vacio) y se activa solo seteando esa variable de entorno, coma-separada.
Sin necesidad de tocar codigo el dia que se contrate un proveedor."""
from __future__ import annotations

import os
import random


def load_proxy_pool() -> list[str]:
    raw = os.environ.get("ANTIBLOCK_PROXY_LIST", "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def random_proxy(pool: list[str]) -> str | None:
    return random.choice(pool) if pool else None
