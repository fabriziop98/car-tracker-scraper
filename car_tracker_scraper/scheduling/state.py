"""Estado persistente del scheduler (wdxtkg35ba): que URLs se descubrieron y
cuando les toca (re)correr Detail, y cuando corrio Discovery por ultima vez.
Vive en Redis - el mismo store que ya usa la capa anti-bloqueo (wdxtkg30nk) -
no en Postgres: "que URLs mirar y cada cuanto" es estrategia de crawl, no
logica de negocio del lado Java. El scraper nunca escribe a la DB compartida
(mismo principio ya establecido para wdxtkg30nn).

DiscoveryCandidateTracker usa un sorted set: score = timestamp de la ultima
vez que se le corrio Detail (0 = nunca). ZRANGEBYSCORE 0..cutoff da los
candidatos vencidos, los nunca-detallados siempre primero (score 0).

wdxtkg35bb: desde que Discovery corre amplio (todas las marcas DNRPA sobre el
umbral, no solo las curadas), hace falta poder filtrar que candidatos pasan a
Detail (la parte cara - fetch completo + publicacion a RabbitMQ) - correr
Detail sobre una marca no curada solo llena pending_review sin poder
resolverse nunca. Un HASH aparte (url -> marca) guarda esa metadata; el
sorted set de arriba sigue siendo la unica fuente de verdad de "vencido o no".

wdxtkg30xr: con mas de una fuente (MercadoLibre + Motordil) cada una necesita
su propio keyspace - su propio catalogo de candidatos, su propia cadencia de
Discovery y su propio ritmo de Detail. Se resuelve con claves sufijadas por
fuente (ver `keys_for_source`), NO con un campo `source` por candidato: el
filtrado por keyspace no cuesta un HGET extra por URL en el hot path de
due_for_detail, que ya hace uno por marca.

**No renombrar las claves de mercadolibre.** La fuente original quedo con los
nombres SIN sufijo (los de abajo) a proposito: hay decenas de miles de
candidatos vivos ahi, y renombrarlas equivaldria a tirarlos y re-scrapear todo
de cero. `keys_for_source("mercadolibre")` devuelve las legacy justamente por
eso, y hay un test que lo fija.
"""
from __future__ import annotations

import time

CANDIDATES_KEY = "scheduling:discovery_candidates"
CANDIDATE_MARCA_KEY = "scheduling:discovery_candidate_marca"
LAST_DISCOVERY_KEY = "scheduling:last_discovery_at"

# La fuente que existia antes de que el scheduler fuera multi-fuente, y que por
# lo tanto se quedo con las claves sin sufijo (ver docstring del modulo).
LEGACY_UNSUFFIXED_SOURCE = "mercadolibre"


def keys_for_source(source: str) -> tuple[str, str, str]:
    """(candidatos, marcas, ultima-discovery) para una fuente.

    `mercadolibre` devuelve las claves historicas sin sufijo - ver el aviso en
    el docstring del modulo sobre por que no se pueden renombrar.
    """
    if source == LEGACY_UNSUFFIXED_SOURCE:
        return CANDIDATES_KEY, CANDIDATE_MARCA_KEY, LAST_DISCOVERY_KEY
    return (
        f"{CANDIDATES_KEY}:{source}",
        f"{CANDIDATE_MARCA_KEY}:{source}",
        f"{LAST_DISCOVERY_KEY}:{source}",
    )

# Cuanto sobre-pedir a Redis antes de filtrar por marca curada, para que un
# `limit` pedido siga entregando ~esa cantidad aun con muchos candidatos de
# marcas no curadas todavia mezclados en el sorted set.
_FILTER_OVERFETCH_MULTIPLIER = 5


class DiscoveryCandidateTracker:
    def __init__(self, redis_client, key: str = CANDIDATES_KEY, marca_key: str = CANDIDATE_MARCA_KEY):
        self.redis = redis_client
        self.key = key
        self.marca_key = marca_key

    @classmethod
    def for_source(cls, redis_client, source: str) -> "DiscoveryCandidateTracker":
        """Tracker acotado al keyspace de una fuente (wdxtkg30xr)."""
        candidates_key, marca_key, _ = keys_for_source(source)
        return cls(redis_client, key=candidates_key, marca_key=marca_key)

    def record_discovered(self, url: str, marca: str | None = None) -> None:
        """NX: si la URL ya se conocia, no le pisa el score - no perder cuando se detallo por ultima vez.
        La marca si se pisa siempre (HSET plano) - si la misma URL reaparece bajo otra marca en un
        re-scrape, mejor quedarse con el dato mas fresco."""
        self.redis.zadd(self.key, {url: 0}, nx=True)
        if marca:
            self.redis.hset(self.marca_key, url, marca)

    def mark_detailed(self, urls: list[str], when: float | None = None) -> None:
        if not urls:
            return
        when = when if when is not None else time.time()
        self.redis.zadd(self.key, {url: when for url in urls})

    def due_for_detail(self, older_than_seconds: float, limit: int, allowed_marcas: set[str] | None = None) -> list[str]:
        """`allowed_marcas=None` = sin filtrar (compat/tests). Con filtro, sobre-pide a Redis
        (`_FILTER_OVERFETCH_MULTIPLIER * limit`) porque el ZRANGEBYSCORE no sabe de marcas -
        una URL de marca no curada descartada acá sigue con score=0 y se reintenta sola en
        cuanto esa marca se cure, sin lógica extra."""
        cutoff = time.time() - older_than_seconds
        fetch_count = limit if allowed_marcas is None else limit * _FILTER_OVERFETCH_MULTIPLIER
        raw = self.redis.zrangebyscore(self.key, min=0, max=cutoff, start=0, num=fetch_count)
        urls = [u.decode() if isinstance(u, bytes) else u for u in raw]
        if allowed_marcas is None:
            return urls

        result = []
        for url in urls:
            marca = self.redis.hget(self.marca_key, url)
            marca = marca.decode() if isinstance(marca, bytes) else marca
            if marca is None or marca in allowed_marcas:
                result.append(url)
            if len(result) >= limit:
                break
        return result

    def known_count(self) -> int:
        return self.redis.zcard(self.key)


def seconds_since_last_discovery(redis_client, key: str = LAST_DISCOVERY_KEY) -> float:
    raw = redis_client.get(key)
    if raw is None:
        return float("inf")  # nunca corrio - siempre "vencido"
    return time.time() - float(raw)


def mark_discovery_ran(redis_client, when: float | None = None, key: str = LAST_DISCOVERY_KEY) -> None:
    redis_client.set(key, when if when is not None else time.time())


def seconds_since_last_discovery_for(redis_client, source: str) -> float:
    """Cadencia de Discovery por fuente (wdxtkg30xr) - cada una vence sola."""
    return seconds_since_last_discovery(redis_client, key=keys_for_source(source)[2])


def mark_discovery_ran_for(redis_client, source: str, when: float | None = None) -> None:
    mark_discovery_ran(redis_client, when=when, key=keys_for_source(source)[2])
