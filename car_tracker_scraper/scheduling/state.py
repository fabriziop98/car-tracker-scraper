"""Estado persistente del scheduler (wdxtkg35ba): que URLs se descubrieron y
cuando les toca (re)correr Detail, y cuando corrio Discovery por ultima vez.
Vive en Redis - el mismo store que ya usa la capa anti-bloqueo (wdxtkg30nk) -
no en Postgres: "que URLs mirar y cada cuanto" es estrategia de crawl, no
logica de negocio del lado Java. El scraper nunca escribe a la DB compartida
(mismo principio ya establecido para wdxtkg30nn).

DiscoveryCandidateTracker usa un sorted set: score = timestamp de la ultima
vez que se le corrio Detail (0 = nunca). ZRANGEBYSCORE 0..cutoff da los
candidatos vencidos, los nunca-detallados siempre primero (score 0).
"""
from __future__ import annotations

import time

CANDIDATES_KEY = "scheduling:discovery_candidates"
LAST_DISCOVERY_KEY = "scheduling:last_discovery_at"


class DiscoveryCandidateTracker:
    def __init__(self, redis_client, key: str = CANDIDATES_KEY):
        self.redis = redis_client
        self.key = key

    def record_discovered(self, url: str) -> None:
        """NX: si la URL ya se conocia, no le pisa el score - no perder cuando se detallo por ultima vez."""
        self.redis.zadd(self.key, {url: 0}, nx=True)

    def mark_detailed(self, urls: list[str], when: float | None = None) -> None:
        if not urls:
            return
        when = when if when is not None else time.time()
        self.redis.zadd(self.key, {url: when for url in urls})

    def due_for_detail(self, older_than_seconds: float, limit: int) -> list[str]:
        cutoff = time.time() - older_than_seconds
        raw = self.redis.zrangebyscore(self.key, min=0, max=cutoff, start=0, num=limit)
        return [u.decode() if isinstance(u, bytes) else u for u in raw]

    def known_count(self) -> int:
        return self.redis.zcard(self.key)


def seconds_since_last_discovery(redis_client, key: str = LAST_DISCOVERY_KEY) -> float:
    raw = redis_client.get(key)
    if raw is None:
        return float("inf")  # nunca corrio - siempre "vencido"
    return time.time() - float(raw)


def mark_discovery_ran(redis_client, when: float | None = None, key: str = LAST_DISCOVERY_KEY) -> None:
    redis_client.set(key, when if when is not None else time.time())
