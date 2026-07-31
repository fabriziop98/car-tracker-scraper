"""Tests de la capa anti-bloqueo (wdxtkg30nk). Usan fakeredis (in-memory)
en vez de un Redis real - no hay Docker en este entorno de desarrollo.
Verificar contra el Redis real del docker-compose queda para Fabrizio."""
import asyncio
import statistics
from datetime import datetime
from unittest.mock import patch

import fakeredis
import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response

from car_tracker_scraper.antiblocking.backoff import exponential_backoff_with_jitter, lognormal_delay
from car_tracker_scraper.antiblocking.circuit_breaker import CircuitBreaker
from car_tracker_scraper.antiblocking.proxy import load_proxy_pool, random_proxy
from car_tracker_scraper.antiblocking.token_bucket import RedisTokenBucket, domain_of
from car_tracker_scraper.antiblocking.user_agents import PERSONA_POOL, random_persona
from run_batch import in_batch_window


# --- token bucket ---------------------------------------------------------


def test_token_bucket_allows_burst_up_to_capacity_then_waits():
    r = fakeredis.FakeRedis()
    bucket = RedisTokenBucket(r, capacity=3, refill_per_sec=1)

    waits = [bucket.seconds_until_available("example.com") for _ in range(3)]
    assert waits == [0, 0, 0]  # las primeras `capacity` requests salen gratis

    wait = bucket.seconds_until_available("example.com")
    assert wait > 0  # ya no quedan tokens


def test_token_bucket_is_per_domain():
    r = fakeredis.FakeRedis()
    bucket = RedisTokenBucket(r, capacity=1, refill_per_sec=1)

    assert bucket.seconds_until_available("a.com") == 0
    assert bucket.seconds_until_available("a.com") > 0
    assert bucket.seconds_until_available("b.com") == 0  # dominio distinto, bucket distinto


def test_domain_of():
    assert domain_of("https://autos.mercadolibre.com.ar/fiat") == "autos.mercadolibre.com.ar"


# --- circuit breaker --------------------------------------------------------


def test_circuit_breaker_stays_closed_below_min_samples():
    r = fakeredis.FakeRedis()
    cb = CircuitBreaker(r, min_samples=10, error_threshold=0.3)
    for _ in range(5):
        cb.record("source-a", success=False)
    assert not cb.is_open("source-a")


def test_circuit_breaker_opens_above_error_threshold():
    r = fakeredis.FakeRedis()
    cb = CircuitBreaker(r, min_samples=10, error_threshold=0.3, pause_seconds=1800)

    just_opened = False
    for i in range(10):
        success = i < 5  # 50% fail, arriba del 30% umbral
        opened = cb.record("source-b", success=success)
        just_opened = just_opened or opened

    assert just_opened
    assert cb.is_open("source-b")


def test_circuit_breaker_close_resets_it():
    r = fakeredis.FakeRedis()
    cb = CircuitBreaker(r, min_samples=2, error_threshold=0.3)
    cb.record("source-c", success=False)
    cb.record("source-c", success=False)
    assert cb.is_open("source-c")

    cb.close("source-c")
    assert not cb.is_open("source-c")


# --- backoff / jitter --------------------------------------------------------


def test_lognormal_delay_is_positive_and_centered_near_median():
    samples = [lognormal_delay(median_seconds=3.0, sigma=0.4) for _ in range(500)]
    assert all(s > 0 for s in samples)
    # la mediana empirica deberia rondar el parametro (lognormal: median = exp(mu))
    assert 2.0 < statistics.median(samples) < 4.5


def test_exponential_backoff_grows_with_retry_times():
    # comparamos medianas empiricas (el jitter individual varia mucho)
    def median_delay(retry_times, n=200):
        return statistics.median(exponential_backoff_with_jitter(retry_times) for _ in range(n))

    assert median_delay(0) < median_delay(2) < median_delay(4)


# --- UA pool coherence --------------------------------------------------------


def test_persona_headers_are_internally_coherent():
    for persona in PERSONA_POOL:
        headers = persona.headers()
        assert "User-Agent" in headers
        assert "Accept-Language" in headers
        is_chromium = "Chrome" in persona.user_agent or "Edg/" in persona.user_agent
        if is_chromium:
            assert "sec-ch-ua" in headers
        else:
            # Firefox/Safari no mandan Client Hints - no deberian aparecer
            assert "sec-ch-ua" not in headers


def test_random_persona_returns_pool_member():
    assert random_persona() in PERSONA_POOL


# --- proxy pool --------------------------------------------------------


def test_load_proxy_pool_empty_by_default(monkeypatch):
    monkeypatch.delenv("ANTIBLOCK_PROXY_LIST", raising=False)
    assert load_proxy_pool() == []
    assert random_proxy([]) is None


def test_load_proxy_pool_parses_csv(monkeypatch):
    monkeypatch.setenv("ANTIBLOCK_PROXY_LIST", "proxy1:8080, proxy2:8080")
    pool = load_proxy_pool()
    assert pool == ["proxy1:8080", "proxy2:8080"]
    assert random_proxy(pool) in pool


# --- batch time window --------------------------------------------------------


def test_in_batch_window_boundaries():
    assert in_batch_window(datetime(2026, 7, 31, 2, 0))
    assert in_batch_window(datetime(2026, 7, 31, 6, 59))
    assert not in_batch_window(datetime(2026, 7, 31, 7, 0))
    assert not in_batch_window(datetime(2026, 7, 31, 1, 59))
    assert not in_batch_window(datetime(2026, 7, 31, 14, 0))


# --- middleware (integracion liviana, redis mockeado) --------------------------------------------------------


class _FakeSpider:
    name = "mercadolibre_discovery"


def _build_middleware():
    from car_tracker_scraper.antiblocking.middleware import AntiBlockingMiddleware

    with patch("redis.Redis.from_url", return_value=fakeredis.FakeRedis()):
        return AntiBlockingMiddleware(redis_url="redis://fake", token_bucket_capacity=5, token_bucket_refill_per_sec=1)


async def _no_sleep(_seconds):
    return None


def test_middleware_process_request_sets_persona_headers():
    mw = _build_middleware()
    request = Request("https://autos.mercadolibre.com.ar/fiat")
    spider = _FakeSpider()

    with patch("car_tracker_scraper.antiblocking.middleware.asyncio.sleep", _no_sleep):
        asyncio.run(mw.process_request(request, spider))

    assert request.headers.get("User-Agent") is not None
    assert request.headers.get("Accept-Language") is not None


def test_middleware_sticky_persona_reused_across_requests():
    mw = _build_middleware()
    spider = _FakeSpider()
    spider.sticky_persona = True

    r1 = Request("https://auto.mercadolibre.com.ar/a")
    r2 = Request("https://auto.mercadolibre.com.ar/b")
    with patch("car_tracker_scraper.antiblocking.middleware.asyncio.sleep", _no_sleep):
        asyncio.run(mw.process_request(r1, spider))
        asyncio.run(mw.process_request(r2, spider))

    assert r1.headers.get("User-Agent") == r2.headers.get("User-Agent")


def test_middleware_raises_ignore_request_when_circuit_open():
    mw = _build_middleware()
    spider = _FakeSpider()
    mw.circuit_breaker.redis.set("antiblock:cb:autos.mercadolibre.com.ar:open_until", 9999999999)

    request = Request("https://autos.mercadolibre.com.ar/fiat")
    with pytest.raises(IgnoreRequest):
        asyncio.run(mw.process_request(request, spider))


def test_middleware_retries_retryable_status_with_backoff():
    mw = _build_middleware()
    spider = _FakeSpider()
    request = Request("https://autos.mercadolibre.com.ar/fiat")
    response = Response(url=request.url, status=429, request=request)

    with patch("car_tracker_scraper.antiblocking.middleware.asyncio.sleep", _no_sleep):
        result = asyncio.run(mw.process_response(request, response, spider))

    assert isinstance(result, Request)
    assert result.meta["antiblock_retry_times"] == 1


def test_middleware_gives_up_after_max_retries():
    mw = _build_middleware()
    spider = _FakeSpider()
    request = Request("https://autos.mercadolibre.com.ar/fiat", meta={"antiblock_retry_times": 3})
    response = Response(url=request.url, status=429, request=request)

    with patch("car_tracker_scraper.antiblocking.middleware.asyncio.sleep", _no_sleep):
        result = asyncio.run(mw.process_response(request, response, spider))

    assert result is response  # ya agoto los reintentos, se devuelve la response tal cual
