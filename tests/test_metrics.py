"""Tests de la extension de metricas por fuente (wdxtkg34th). Usan
fakeredis (in-memory) y mockean push_to_gateway - no hay Pushgateway real
en este entorno de desarrollo, y no queremos requests HTTP reales en los
tests. Verificar contra el Pushgateway real del docker-compose queda para
Fabrizio."""
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
from scrapy.http import Request, Response

from car_tracker_scraper.antiblocking.circuit_breaker import CircuitBreaker
from car_tracker_scraper.items import ListingSummaryItem
from car_tracker_scraper.observability.metrics import SourceMetricsExtension
from car_tracker_scraper.scheduling.state import CANDIDATES_KEY

SPIDER = SimpleNamespace(name="mercadolibre_discovery")


def _make_extension(redis_client=None, pushgateway_url="http://pushgateway:9091"):
    r = redis_client or fakeredis.FakeRedis()
    return SourceMetricsExtension(
        redis_client=r,
        circuit_breaker=CircuitBreaker(r),
        pushgateway_url=pushgateway_url,
    ), r


def _response(url="https://autos.mercadolibre.com.ar/fiat", status=200, latency=0.2):
    request = Request(url=url, meta={"download_latency": latency})
    return Response(url=url, status=status, request=request)


def test_disabled_without_pushgateway_url_does_not_push():
    ext, _ = _make_extension(pushgateway_url="")
    ext.spider_opened(SPIDER)
    ext.response_received(_response(), request=None, spider=SPIDER)

    with patch("car_tracker_scraper.observability.metrics.push_to_gateway") as mock_push:
        ext.spider_closed(SPIDER, reason="finished")
    mock_push.assert_not_called()


def test_nothing_to_report_skips_push():
    ext, _ = _make_extension()
    ext.spider_opened(SPIDER)
    with patch("car_tracker_scraper.observability.metrics.push_to_gateway") as mock_push:
        ext.spider_closed(SPIDER, reason="finished")
    mock_push.assert_not_called()


def test_blocked_rate_counts_403_and_429_against_all_responses():
    ext, _ = _make_extension()
    ext.spider_opened(SPIDER)
    for status in (200, 200, 403, 429):
        ext.response_received(_response(status=status), request=None, spider=SPIDER)

    pushed = {}

    def _capture(url, job, registry):
        pushed["registry"] = registry

    with patch("car_tracker_scraper.observability.metrics.push_to_gateway", side_effect=_capture):
        ext.spider_closed(SPIDER, reason="finished")

    metrics = {m.name: m for m in pushed["registry"].collect()}
    assert metrics["car_tracker_scraper_blocked_rate"].samples[0].value == 0.5


def test_latency_p95_uses_download_latency_from_response_meta():
    ext, _ = _make_extension()
    ext.spider_opened(SPIDER)
    for latency in [0.1, 0.2, 0.3, 0.4, 1.0]:  # p95 de 5 valores -> el mas alto
        ext.response_received(_response(latency=latency), request=None, spider=SPIDER)

    pushed = {}
    with patch(
        "car_tracker_scraper.observability.metrics.push_to_gateway",
        side_effect=lambda url, job, registry: pushed.update(registry=registry),
    ):
        ext.spider_closed(SPIDER, reason="finished")

    metrics = {m.name: m for m in pushed["registry"].collect()}
    assert metrics["car_tracker_scraper_latency_p95_seconds"].samples[0].value == 1.0


def test_success_rate_reads_from_circuit_breaker_not_local_count():
    r = fakeredis.FakeRedis()
    ext, _ = _make_extension(redis_client=r)
    cb = CircuitBreaker(r, min_samples=1)
    # el circuit breaker ya tiene estado de OTRAS corridas para este dominio
    cb.record("autos.mercadolibre.com.ar", success=True)
    cb.record("autos.mercadolibre.com.ar", success=True)
    cb.record("autos.mercadolibre.com.ar", success=False)

    ext.spider_opened(SPIDER)
    ext.response_received(_response(status=200), request=None, spider=SPIDER)  # 100% local, pero no debe usarse

    pushed = {}
    with patch(
        "car_tracker_scraper.observability.metrics.push_to_gateway",
        side_effect=lambda url, job, registry: pushed.update(registry=registry),
    ):
        ext.spider_closed(SPIDER, reason="finished")

    metrics = {m.name: m for m in pushed["registry"].collect()}
    # 2 ok / 3 total en el circuit breaker, no 1/1 local
    assert abs(metrics["car_tracker_scraper_success_rate"].samples[0].value - (2 / 3)) < 1e-9


def test_null_ratio_per_field_counts_none_and_empty_string_as_null():
    ext, _ = _make_extension()
    ext.spider_opened(SPIDER)

    item_a = ListingSummaryItem(url="https://x/a", price_amount=1000, title_raw="Fiat Palio")
    item_b = ListingSummaryItem(url="https://x/b", price_amount=None, title_raw="")
    for item in (item_a, item_b):
        ext.item_scraped(item, response=None, spider=SPIDER)

    pushed = {}
    with patch(
        "car_tracker_scraper.observability.metrics.push_to_gateway",
        side_effect=lambda url, job, registry: pushed.update(registry=registry),
    ):
        ext.spider_closed(SPIDER, reason="finished")

    metrics = {m.name: m for m in pushed["registry"].collect()}
    by_field = {s.labels["field"]: s.value for s in metrics["car_tracker_scraper_field_null_ratio"].samples}
    assert by_field["price_amount"] == 0.5  # 1 de 2 nulo
    assert by_field["title_raw"] == 0.5  # 1 de 2 vacio (cuenta como nulo)
    assert by_field["source"] == 1.0  # nunca seteado en ningun item -> 100% nulo


def test_items_per_minute_is_positive_for_a_run_with_items():
    ext, _ = _make_extension()
    ext.spider_opened(SPIDER)
    ext.item_scraped(ListingSummaryItem(url="https://x/a"), response=None, spider=SPIDER)
    ext.item_scraped(ListingSummaryItem(url="https://x/b"), response=None, spider=SPIDER)

    pushed = {}
    with patch(
        "car_tracker_scraper.observability.metrics.push_to_gateway",
        side_effect=lambda url, job, registry: pushed.update(registry=registry),
    ):
        ext.spider_closed(SPIDER, reason="finished")

    metrics = {m.name: m for m in pushed["registry"].collect()}
    assert metrics["car_tracker_scraper_items_per_minute"].samples[0].value > 0


def test_new_item_ratio_distinguishes_urls_already_known_in_redis():
    r = fakeredis.FakeRedis()
    r.zadd(CANDIDATES_KEY, {"https://x/already-seen": 0})
    ext, _ = _make_extension(redis_client=r)
    ext.spider_opened(SPIDER)

    ext.item_scraped(ListingSummaryItem(url="https://x/already-seen"), response=None, spider=SPIDER)
    ext.item_scraped(ListingSummaryItem(url="https://x/brand-new-1"), response=None, spider=SPIDER)
    ext.item_scraped(ListingSummaryItem(url="https://x/brand-new-2"), response=None, spider=SPIDER)

    pushed = {}
    with patch(
        "car_tracker_scraper.observability.metrics.push_to_gateway",
        side_effect=lambda url, job, registry: pushed.update(registry=registry),
    ):
        ext.spider_closed(SPIDER, reason="finished")

    metrics = {m.name: m for m in pushed["registry"].collect()}
    # 2 nuevos / 3 total
    assert abs(metrics["car_tracker_scraper_new_item_ratio"].samples[0].value - (2 / 3)) < 1e-9


def test_pushgateway_failure_is_logged_not_raised():
    ext, _ = _make_extension()
    ext.spider_opened(SPIDER)
    ext.response_received(_response(), request=None, spider=SPIDER)

    with patch("car_tracker_scraper.observability.metrics.push_to_gateway", side_effect=OSError("connection refused")):
        ext.spider_closed(SPIDER, reason="finished")  # no debe levantar
