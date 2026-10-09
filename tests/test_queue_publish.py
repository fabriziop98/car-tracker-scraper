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
import os
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


def test_publish_reconnects_and_retries_once_on_connection_error(mock_pika_connection):
    """wdxtkg3rpn: en produccion RabbitMQ mato la conexion por heartbeat
    perdido (circuit breaker abierto / racha de 403 sin items dejaba el
    publisher mudo mas de los 60s de timeout) y cada publish() posterior se
    perdia hasta que el spider terminaba. Un error de conexion/canal ahora
    reconecta y reintenta una vez antes de darse por vencido."""
    connection_cls, connection, channel = mock_pika_connection
    channel.basic_publish.side_effect = [pika.exceptions.StreamLostError("boom"), None]
    publisher = ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")

    publisher.publish(ROUTING_KEY_OBSERVED, {"source": "mercadolibre"})  # no debe levantar

    assert channel.basic_publish.call_count == 2
    assert connection_cls.call_count == 2  # conexion inicial + reconexion
    connection.close.assert_called()


def test_publish_raises_after_reconnect_retry_also_fails(mock_pika_connection):
    _, _, channel = mock_pika_connection
    channel.basic_publish.side_effect = pika.exceptions.ChannelWrongStateError("closed")
    publisher = ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")

    with pytest.raises(pika.exceptions.ChannelWrongStateError):
        publisher.publish(ROUTING_KEY_OBSERVED, {"source": "mercadolibre"})

    assert channel.basic_publish.call_count == 2  # intento original + 1 reintento, no mas


def test_keepalive_processes_pending_events_on_open_connection(mock_pika_connection):
    _, connection, _ = mock_pika_connection
    connection.is_open = True
    publisher = ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")

    publisher.keepalive()

    connection.process_data_events.assert_called_once_with(time_limit=0)


def test_keepalive_swallows_amqp_error_on_dead_connection(mock_pika_connection):
    _, connection, _ = mock_pika_connection
    connection.is_open = True
    connection.process_data_events.side_effect = pika.exceptions.StreamLostError("boom")
    publisher = ListingsQueuePublisher(url="amqp://guest:guest@localhost:5672/%2F")

    publisher.keepalive()  # no debe levantar - se repara en el proximo publish()


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


def test_pipeline_starts_and_stops_keepalive_loop(mock_pika_connection):
    """wdxtkg3rpn: sin esto, un hueco largo sin items (circuit breaker
    abierto) deja pika.BlockingConnection sin tocar y RabbitMQ la mata por
    heartbeat perdido."""
    pipeline = RabbitMQPublishPipeline()
    spider = _FakeSpider()

    pipeline.open_spider(spider)
    assert pipeline._keepalive_task.running

    pipeline.close_spider(spider)
    assert not pipeline._keepalive_task.running


# wdxtkg3rpj: RabbitMQ del docker-compose de car-tracker ya no acepta
# guest/guest (credenciales en su .env) - leer RABBITMQ_URL del entorno,
# mismo patron que publisher.py, en vez de hardcodear la URL vieja. El
# fallback guest/guest solo importa para alguien corriendo un RabbitMQ propio
# suelto, no el de este stack.
_RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")


def _rabbitmq_available() -> bool:
    try:
        connection = pika.BlockingConnection(pika.URLParameters(_RABBITMQ_URL))
        connection.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _rabbitmq_available(), reason="RabbitMQ real no disponible (docker compose no levantado, o RABBITMQ_URL sin setear)")
def test_published_message_reaches_real_listings_observed_queue():
    """Integracion real: la cola/binding los declara el lado Java
    (ListingsQueueConfig) - este test asume que car-tracker ya corrio al
    menos una vez contra este mismo broker."""
    publisher = ListingsQueuePublisher(url=_RABBITMQ_URL)
    try:
        payload = {"source": "mercadolibre", "source_listing_key": "MLA_TEST_INTEGRATION", "schema_version": 1}
        publisher.publish(ROUTING_KEY_OBSERVED, payload)
    finally:
        publisher.close()

    connection = pika.BlockingConnection(pika.URLParameters(_RABBITMQ_URL))
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
