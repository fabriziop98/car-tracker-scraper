"""Tests de la mensajeria hacia RabbitMQ (wdxtkg30nn).

Unit tests: pika mockeado, para la logica de pipeline (que items se publican)
y del publisher (routing key, delivery_mode, manejo de UnroutableError) sin
depender de un broker.

Integracion: contra el RabbitMQ real de docker-compose (mismo que ya declara
la topologia el lado Java) - confirma que un mensaje publicado ac llega tal
cual a la cola `listings.observed` real. Se saltea sola si no hay broker
corriendo (entorno sin Docker).
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pika
import pytest

from car_tracker_scraper.items import ListingDetailItem, ListingSummaryItem
from car_tracker_scraper.queue_publish.pipeline import RabbitMQPublishPipeline
from car_tracker_scraper.queue_publish.publisher import EXCHANGE, ROUTING_KEY_OBSERVED, ListingsQueuePublisher


class _FakeSpider:
    name = "mercadolibre_detail"


@pytest.fixture
def mock_pika_connection():
    with patch("car_tracker_scraper.queue_publish.publisher.pika.BlockingConnection") as connection_cls:
        connection = MagicMock()
        channel = MagicMock()
        connection.channel.return_value = channel
        connection_cls.return_value = connection
        yield connection_cls, connection, channel


def test_publisher_declares_exchange_on_connect(mock_pika_connection):
    _, _, channel = mock_pika_connection
    ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")

    channel.exchange_declare.assert_called_once_with(exchange=EXCHANGE, exchange_type="topic", durable=True)
    channel.confirm_delivery.assert_called_once()


def test_publish_sends_persistent_json_message(mock_pika_connection):
    _, _, channel = mock_pika_connection
    publisher = ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")

    publisher.publish(ROUTING_KEY_OBSERVED, {"source": "mercadolibre", "price_amount": 7700})

    assert channel.basic_publish.call_count == 1
    _, kwargs = channel.basic_publish.call_args
    assert kwargs["exchange"] == EXCHANGE
    assert kwargs["routing_key"] == ROUTING_KEY_OBSERVED
    assert kwargs["mandatory"] is True
    assert kwargs["properties"].delivery_mode == 2  # persistente
    assert kwargs["properties"].content_type == "application/json"
    assert json.loads(kwargs["body"]) == {"source": "mercadolibre", "price_amount": 7700}


def test_publish_swallows_unroutable_error(mock_pika_connection):
    _, _, channel = mock_pika_connection
    channel.basic_publish.side_effect = pika.exceptions.UnroutableError([])
    publisher = ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")

    publisher.publish(ROUTING_KEY_OBSERVED, {"source": "mercadolibre"})  # no debe levantar


def test_close_closes_open_connection(mock_pika_connection):
    _, connection, _ = mock_pika_connection
    connection.is_open = True
    publisher = ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")

    publisher.close()

    connection.close.assert_called_once()


def test_pipeline_publishes_only_detail_items(mock_pika_connection):
    _, _, channel = mock_pika_connection
    pipeline = RabbitMQPublishPipeline()
    pipeline.open_spider(_FakeSpider())

    summary = ListingSummaryItem(source="mercadolibre", source_listing_key="MLA1")
    detail = ListingDetailItem(source="mercadolibre", source_listing_key="MLA1", price_amount=7700)

    result_summary = pipeline.process_item(summary, _FakeSpider())
    result_detail = pipeline.process_item(detail, _FakeSpider())

    assert result_summary is summary
    assert result_detail is detail
    assert channel.basic_publish.call_count == 1  # solo el detail
    _, kwargs = channel.basic_publish.call_args
    body = json.loads(kwargs["body"])
    assert body["source_listing_key"] == "MLA1"
    assert body["schema_version"] == 1


def _rabbitmq_available() -> bool:
    try:
        connection = pika.BlockingConnection(pika.URLParameters("amqp://guest:guest@localhost:5672/%2F"))
        connection.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _rabbitmq_available(), reason="RabbitMQ real no disponible (docker compose no levantado)")
def test_published_message_reaches_real_listings_observed_queue():
    """Integracion real: la cola/binding los declara el lado Java
    (ListingsQueueConfig) - este test asume que car-tracker ya corrio al
    menos una vez contra este mismo broker."""
    publisher = ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")
    try:
        payload = {"source": "mercadolibre", "source_listing_key": "MLA_TEST_INTEGRATION", "schema_version": 1}
        publisher.publish(ROUTING_KEY_OBSERVED, payload)
    finally:
        publisher.close()

    connection = pika.BlockingConnection(pika.URLParameters("amqp://guest:guest@localhost:5672/%2F"))
    try:
        channel = connection.channel()
        try:
            channel.queue_declare(queue="listings.observed", passive=True)
        except Exception:
            pytest.skip("listings.observed no existe todavia - correr la app Java (ListingsQueueConfig) al menos una vez")

        found = False
        for _ in range(50):
            method_frame, properties, body = channel.basic_get(queue="listings.observed", auto_ack=True)
            if method_frame is not None and json.loads(body).get("source_listing_key") == "MLA_TEST_INTEGRATION":
                assert properties.content_type == "application/json"
                assert properties.delivery_mode == 2
                found = True
                break
            time.sleep(0.05)
        assert found, "el mensaje publicado no aparecio en listings.observed"
    finally:
        connection.close()
