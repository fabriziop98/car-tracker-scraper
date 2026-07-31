"""Tests del landing zone (wdxtkg30nm). Usan moto (S3 simulado en memoria)
en vez de un MinIO real - no hay Docker en este entorno de desarrollo."""
import gzip

import boto3
import pytest
from moto import mock_aws
from scrapy.http import HtmlResponse, Request

from car_tracker_scraper.landing.middleware import LandingZoneMiddleware
from car_tracker_scraper.landing.storage import LandingZoneStorage
from car_tracker_scraper.version import PARSER_VERSION


@pytest.fixture
def moto_s3():
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


def _storage() -> LandingZoneStorage:
    # moto solo intercepta el endpoint "real" de AWS que boto3 arma solo -
    # con un endpoint_url custom (MinIO) no lo reconoce e intenta conectar
    # de verdad. En produccion si se pasa (apunta a MinIO); en tests, None.
    return LandingZoneStorage(
        endpoint_url=None,
        access_key="test",
        secret_key="test",
        bucket="car-tracker-raw",
    )


def test_upload_raw_creates_bucket_if_missing(moto_s3):
    storage = _storage()
    key = storage.upload_raw(source="mercadolibre", content=b"<html>hola</html>", extension="html")

    assert key.startswith("mercadolibre/")
    assert key.endswith(".html.gz")


def test_upload_raw_gzips_content(moto_s3):
    storage = _storage()
    original = b"<html>contenido de prueba</html>"
    key = storage.upload_raw(source="mercadolibre", content=original, extension="html")

    obj = storage.client.get_object(Bucket="car-tracker-raw", Key=key)
    stored_bytes = obj["Body"].read()

    assert gzip.decompress(stored_bytes) == original


def test_upload_raw_partitions_by_source_and_date(moto_s3):
    storage = _storage()
    key = storage.upload_raw(source="mercadolibre", content=b"x", extension="html")
    parts = key.split("/")
    assert parts[0] == "mercadolibre"
    assert len(parts[1]) == 10  # YYYY-MM-DD


class _FakeSpider:
    name = "mercadolibre_discovery"


def test_middleware_uploads_response_and_sets_meta(moto_s3, monkeypatch):
    # sin LANDING_S3_ENDPOINT_URL -> boto3 arma el endpoint AWS "real" que
    # moto si sabe interceptar (ver nota en _storage() de mas arriba).
    monkeypatch.delenv("LANDING_S3_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("LANDING_S3_ACCESS_KEY", "test")
    monkeypatch.setenv("LANDING_S3_SECRET_KEY", "test")
    monkeypatch.setenv("LANDING_S3_BUCKET", "car-tracker-raw")

    mw = LandingZoneMiddleware()
    request = Request("https://autos.mercadolibre.com.ar/fiat")
    response = HtmlResponse(url=request.url, body=b"<html>listado</html>", request=request)
    spider = _FakeSpider()

    result = mw.process_response(request, response, spider)

    assert result is response
    assert request.meta["s3_key"].startswith("mercadolibre/")
    assert request.meta["http_status"] == 200
    assert request.meta["parser_version"] == PARSER_VERSION
