"""Token bucket por dominio en Redis (wdxtkg30nk, punto "no negociable,
protege al sitio origen y a vos"). Compartido entre procesos/spiders via
Redis, a diferencia de DOWNLOAD_DELAY/AUTOTHROTTLE de Scrapy que solo
conocen su propio proceso.

Nota de concurrencia: el read-modify-write de tokens no es atomico (no usa
un script Lua). Para un solo proceso de scraper corriendo a la vez esto no
importa - si en el futuro corren varios workers en paralelo contra el mismo
dominio, migrar el calculo a EVAL con un script Lua para que sea atomico.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse


class RedisTokenBucket:
    def __init__(self, redis_client, capacity: float, refill_per_sec: float, key_prefix: str = "antiblock:tb:"):
        self.redis = redis_client
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.key_prefix = key_prefix

    def seconds_until_available(self, domain: str) -> float:
        """Consume un token si hay disponible y devuelve 0. Si no hay,
        devuelve cuantos segundos hay que esperar (sin bloquear el
        thread - quien llama decide como esperar, ej. asyncio.sleep)."""
        key = f"{self.key_prefix}{domain}"
        now = time.time()
        data = self.redis.hgetall(key)
        tokens = float(data.get(b"tokens", self.capacity))
        last = float(data.get(b"last", now))

        elapsed = max(0.0, now - last)
        tokens = min(self.capacity, tokens + elapsed * self.refill_per_sec)

        if tokens >= 1:
            tokens -= 1
            wait = 0.0
        else:
            wait = (1 - tokens) / self.refill_per_sec

        self.redis.hset(key, mapping={"tokens": tokens, "last": now})
        self.redis.expire(key, 3600)
        return wait


def domain_of(url: str) -> str:
    return urlparse(url).netloc
