"""wdxtkg348c: sin descargar nada real - la logica del pipeline (que URL pide,
que hace con el resultado) es independiente del contenido de la foto. Se usa
una imagen sintetica minima (PIL) solo para que Image.open()/imagehash.phash()
tengan algo real que abrir, mismo criterio que otros tests de este repo que
mockean la infraestructura externa (RabbitMQ, S3) y prueban la orquestacion."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from twisted.python.failure import Failure

from car_tracker_scraper.image_phash.pipeline import ImagePhashPipeline
from car_tracker_scraper.items import DeadListingItem, ListingDetailItem, ListingSummaryItem


@pytest.fixture
def pipeline(tmp_path):
    return ImagePhashPipeline(store_uri=str(tmp_path))


def test_get_media_requests_yields_the_main_image_url(pipeline):
    item = ListingDetailItem(source="mercadolibre", main_image_url="https://http2.mlstatic.com/foo.webp")

    requests = pipeline.get_media_requests(item, None)

    assert len(requests) == 1
    assert requests[0].url == "https://http2.mlstatic.com/foo.webp"


def test_get_media_requests_returns_nothing_without_an_image_url(pipeline):
    item = ListingDetailItem(source="mercadolibre")

    assert pipeline.get_media_requests(item, None) == []


def test_get_media_requests_ignores_non_detail_items(pipeline):
    assert pipeline.get_media_requests(ListingSummaryItem(source="mercadolibre"), None) == []
    assert pipeline.get_media_requests(DeadListingItem(source="mercadolibre"), None) == []


def test_item_completed_sets_a_16_char_hex_phash_from_the_downloaded_image(pipeline, tmp_path):
    relative_path = "full/test.jpg"
    absolute_path = Path(pipeline.store.basedir) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=(120, 40, 200)).save(absolute_path, "JPEG")

    item = ListingDetailItem(source="mercadolibre", main_image_url="https://http2.mlstatic.com/foo.webp")
    results = [(True, {"url": item["main_image_url"], "path": relative_path, "checksum": "x"})]

    completed = pipeline.item_completed(results, item, SimpleNamespace())

    assert completed is item
    assert isinstance(item["main_image_phash"], str)
    assert len(item["main_image_phash"]) == 16
    int(item["main_image_phash"], 16)  # hex valido


def test_item_completed_leaves_phash_unset_when_the_download_failed(pipeline):
    item = ListingDetailItem(source="mercadolibre", main_image_url="https://http2.mlstatic.com/foo.webp")
    failure = Failure(Exception("connection refused"))
    results = [(False, failure)]

    completed = pipeline.item_completed(results, item, SimpleNamespace())

    assert completed is item
    assert "main_image_phash" not in item


def test_item_completed_ignores_non_detail_items(pipeline):
    item = ListingSummaryItem(source="mercadolibre")

    completed = pipeline.item_completed([], item, SimpleNamespace())

    assert completed is item
