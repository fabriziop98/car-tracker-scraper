"""Downloader middleware que junta las piezas de wdxtkg30nk (Capa
anti-bloqueo): token bucket, UA pool coherente (rotado por request en
Discovery, sticky en Detail via `spider.sticky_persona = True`), proxy
pool opcional, circuit breaker por dominio, y backoff exponencial con
jitter lognormal en reintentos.

Requiere Redis (ANTIBLOCK_REDIS_URL, default redis://localhost:6379/0 -
el mismo que ya levanta el docker-compose del repo car-tracker).
"""
from __future__ import annotations

import asyncio
import logging
import os

import redis
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request

from car_tracker_scraper.antiblocking.backoff import (
    RETRYABLE_STATUS_CODES,
    exponential_backoff_with_jitter,
    lognormal_delay,
)
from car_tracker_scraper.antiblocking.circuit_breaker import CircuitBreaker
from car_tracker_scraper.antiblocking.proxy import load_proxy_pool, random_proxy
from car_tracker_scraper.antiblocking.telegram_alerts import send_telegram_alert
from car_tracker_scraper.antiblocking.token_bucket import RedisTokenBucket, domain_of
from car_tracker_scraper.antiblocking.user_agents import random_persona

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class AntiBlockingMiddleware:
    def __init__(self, redis_url: str, token_bucket_capacity: float, token_bucket_refill_per_sec: float):
        self.redis = redis.Redis.from_url(redis_url)
        self.bucket = RedisTokenBucket(self.redis, token_bucket_capacity, token_bucket_refill_per_sec)
        self.circuit_breaker = CircuitBreaker(self.redis)
        self.proxy_pool = load_proxy_pool()

    @classmethod
    def from_crawler(cls, crawler):
        settings = crawler.settings
        return cls(
            redis_url=settings.get("ANTIBLOCK_REDIS_URL", os.environ.get("ANTIBLOCK_REDIS_URL", "redis://localhost:6379/0")),
            token_bucket_capacity=settings.getfloat("ANTIBLOCK_TOKEN_BUCKET_CAPACITY", 5),
            token_bucket_refill_per_sec=settings.getfloat("ANTIBLOCK_TOKEN_BUCKET_REFILL_PER_SEC", 0.5),
        )

    async def process_request(self, request: Request, spider):
        domain = domain_of(request.url)

        if self.circuit_breaker.is_open(domain):
            raise IgnoreRequest(f"Circuit breaker abierto para {domain}, pausando requests")

        # UA pool: sticky en Detail (misma persona toda la corrida del spider),
        # rotado por request en Discovery (default).
        if getattr(spider, "sticky_persona", False):
            persona = getattr(spider, "_antiblock_persona", None)
            if persona is None:
                persona = random_persona()
                spider._antiblock_persona = persona
        else:
            persona = random_persona()
        request.headers.update(persona.headers())

        proxy = random_proxy(self.proxy_pool)
        if proxy:
            request.meta["proxy"] = proxy

        wait = self.bucket.seconds_until_available(domain)
        # jitter humano ademas del token bucket, aunque haya token disponible ya
        # (evita que, con el bucket lleno, las requests salgan todas seguidas
        # a "velocidad de maquina" - seccion 3.2 del doc: "sin patrones de
        # reloj perfectos").
        wait += lognormal_delay(median_seconds=1.0, sigma=0.4)
        if wait > 0:
            await asyncio.sleep(wait)

    async def process_response(self, request: Request, response, spider):
        domain = domain_of(response.url)
        success = response.status < 400

        just_opened = self.circuit_breaker.record(domain, success)
        if just_opened:
            logger.error("Circuit breaker ABIERTO para %s - pausando 30 min", domain)
            send_telegram_alert(
                f"⚠️ Circuit breaker ABIERTO para {domain}\nTasa de error alta detectada - pausando requests 30 min."
            )

        if response.status in RETRYABLE_STATUS_CODES:
            retry_times = request.meta.get("antiblock_retry_times", 0)
            if retry_times < MAX_RETRIES:
                delay = exponential_backoff_with_jitter(retry_times)
                logger.warning(
                    "HTTP %s en %s, reintento %d/%d en %.1fs",
                    response.status,
                    request.url,
                    retry_times + 1,
                    MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                new_request = request.copy()
                new_request.meta["antiblock_retry_times"] = retry_times + 1
                new_request.dont_filter = True
                return new_request

        return response
