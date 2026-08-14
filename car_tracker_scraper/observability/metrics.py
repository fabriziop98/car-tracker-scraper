"""Metricas por-fuente del scraper hacia Prometheus Pushgateway (wdxtkg34th,
seccion 3.3 del doc de arquitectura): success rate, tasa de 403/429,
latencia p95, items/min, % de campos nulos por campo, nuevos vs conocidos.

Por que Pushgateway y no /metrics normal: el scraper corre como jobs batch
de corta duracion (spiders de Scrapy disparados por Discovery/Detail), no
como un proceso servidor de larga vida - Prometheus no puede hacer pull
scraping de un proceso que ya termino cuando llega el proximo scrape. Cada
spider empuja sus metricas acumuladas al cerrar (signal `spider_closed`).

Fuente de verdad reusada para success rate: `CircuitBreaker.stats()` (misma
ventana/eventos que ya usa el circuit breaker en Redis, wdxtkg30nk) - no se
duplica el conteo ok/fail. La tasa de 403/429 SI se cuenta aca de forma
local (via signal `response_received`), porque el circuit breaker solo
guarda ok/fail booleano, no el status code especifico.
"""
from __future__ import annotations

import logging
import math
import os
import time
from collections import Counter, defaultdict

import redis
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
from scrapy import signals

from car_tracker_scraper.antiblocking.circuit_breaker import CircuitBreaker
from car_tracker_scraper.antiblocking.token_bucket import domain_of
from car_tracker_scraper.scheduling.state import CANDIDATES_KEY

logger = logging.getLogger(__name__)

BLOCK_STATUSES = (403, 429)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(pct * len(ordered)) - 1))
    return ordered[idx]


class SourceMetricsExtension:
    """Extension de Scrapy: junta metricas durante la corrida de un spider
    y las empuja a Pushgateway al cerrar. Deshabilitada (no-op silencioso,
    no rompe el spider) si METRICS_PUSHGATEWAY_URL no esta configurado."""

    def __init__(self, redis_client, circuit_breaker: CircuitBreaker, pushgateway_url: str):
        self.redis = redis_client
        self.circuit_breaker = circuit_breaker
        self.pushgateway_url = pushgateway_url
        self.enabled = bool(pushgateway_url)

        self._started_at: float | None = None
        self._domain: str | None = None
        self._status_counts: Counter = Counter()
        self._latencies: list[float] = []
        self._items_total = 0
        self._field_totals: dict = defaultdict(int)
        self._null_counts: dict = defaultdict(int)
        self._new_items = 0
        self._known_items = 0

    @classmethod
    def from_crawler(cls, crawler):
        settings = crawler.settings
        pushgateway_url = settings.get("METRICS_PUSHGATEWAY_URL", os.environ.get("METRICS_PUSHGATEWAY_URL", ""))
        redis_url = settings.get("ANTIBLOCK_REDIS_URL", os.environ.get("ANTIBLOCK_REDIS_URL", "redis://localhost:6379/0"))
        redis_client = redis.Redis.from_url(redis_url)
        ext = cls(
            redis_client=redis_client,
            circuit_breaker=CircuitBreaker(redis_client),
            pushgateway_url=pushgateway_url,
        )
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        crawler.signals.connect(ext.item_scraped, signal=signals.item_scraped)
        crawler.signals.connect(ext.response_received, signal=signals.response_received)
        return ext

    def spider_opened(self, spider) -> None:
        self._started_at = time.time()

    def response_received(self, response, request, spider) -> None:
        if self._domain is None:
            self._domain = domain_of(response.url)
        self._status_counts[response.status] += 1
        latency = response.meta.get("download_latency")
        if latency is not None:
            self._latencies.append(latency)

    def item_scraped(self, item, response, spider) -> None:
        self._items_total += 1
        for field_name in item.fields:
            self._field_totals[field_name] += 1
            value = item.get(field_name)
            if value is None or value == "":
                self._null_counts[field_name] += 1

        url = item.get("url")
        if url:
            if self.redis.zscore(CANDIDATES_KEY, url) is not None:
                self._known_items += 1
            else:
                self._new_items += 1

    def spider_closed(self, spider, reason) -> None:
        if not self.enabled:
            logger.info("METRICS_PUSHGATEWAY_URL no configurado - no se empujan metricas de %s", spider.name)
            return
        if not self._status_counts and self._items_total == 0:
            return  # el spider no llego a hacer ninguna request (ej. ventana horaria vacia) - nada que reportar

        source = self._domain or "unknown"
        elapsed_minutes = max(time.time() - (self._started_at or time.time()), 1e-9) / 60

        registry = CollectorRegistry()

        total_responses = sum(self._status_counts.values())
        if total_responses:
            blocked = sum(count for status, count in self._status_counts.items() if status in BLOCK_STATUSES)
            Gauge(
                "car_tracker_scraper_blocked_rate",
                "Proporcion de responses HTTP 403/429 (senal de bloqueo)",
                ["source", "spider"],
                registry=registry,
            ).labels(source, spider.name).set(blocked / total_responses)

            Gauge(
                "car_tracker_scraper_latency_p95_seconds",
                "Latencia p95 de descarga de esta corrida",
                ["source", "spider"],
                registry=registry,
            ).labels(source, spider.name).set(_percentile(self._latencies, 0.95))

        cb_total, _cb_fails, cb_error_rate = self.circuit_breaker.stats(source)
        if cb_total:
            Gauge(
                "car_tracker_scraper_success_rate",
                "Tasa de exito reciente de la fuente - misma ventana que el circuit breaker (wdxtkg30nk)",
                ["source", "spider"],
                registry=registry,
            ).labels(source, spider.name).set(1 - cb_error_rate)

        if self._items_total:
            Gauge(
                "car_tracker_scraper_items_per_minute",
                "Items scrapeados por minuto en esta corrida",
                ["spider"],
                registry=registry,
            ).labels(spider.name).set(self._items_total / elapsed_minutes)

            null_gauge = Gauge(
                "car_tracker_scraper_field_null_ratio",
                "Proporcion de items con este campo nulo o vacio en esta corrida",
                ["spider", "field"],
                registry=registry,
            )
            for field_name, total in self._field_totals.items():
                null_gauge.labels(spider.name, field_name).set(self._null_counts[field_name] / total)

            new_known_total = self._new_items + self._known_items
            if new_known_total:
                Gauge(
                    "car_tracker_scraper_new_item_ratio",
                    "Proporcion de items nuevos (URL no vista en corridas previas) vs conocidos",
                    ["spider"],
                    registry=registry,
                ).labels(spider.name).set(self._new_items / new_known_total)

        try:
            push_to_gateway(self.pushgateway_url, job=f"car_tracker_scraper_{spider.name}", registry=registry)
        except OSError:
            logger.exception(
                "No se pudieron empujar las metricas de %s a Pushgateway (%s) - se pierden las de esta corrida",
                spider.name,
                self.pushgateway_url,
            )
