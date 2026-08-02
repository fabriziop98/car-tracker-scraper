"""Publisher a RabbitMQ para el exchange `listings` (wdxtkg30nn). Este es el
UNICO canal por el que el scraper (Python) le habla a la ingesta (Java) - nunca
DB compartida (doc de arquitectura, seccion 3.6).

La topologia real (cola `listings.observed`, DLQ, politica de reintentos) la
declara y posee el lado Java (com.fabrizio.cartracker.messaging.ListingsQueueConfig).
Este publisher declara el exchange de forma defensiva e idempotente (mismos
parametros que el lado Java) para poder correr el scraper de forma
desacoplada, aunque en la practica docker-compose siempre trae el consumer
arriba primero.
"""
from __future__ import annotations

import json
import logging
import os

import pika

logger = logging.getLogger(__name__)

EXCHANGE = "listings"
ROUTING_KEY_OBSERVED = "listing.observed"


class ListingsQueuePublisher:
    def __init__(self, url: str, exchange: str = EXCHANGE):
        self.exchange = exchange
        self._connection = pika.BlockingConnection(pika.URLParameters(url))
        self._channel = self._connection.channel()
        self._channel.confirm_delivery()
        # Mismos parametros que TopicExchange declarado en ListingsQueueConfig -
        # declarar un exchange existente con los mismos parametros es un no-op.
        self._channel.exchange_declare(exchange=self.exchange, exchange_type="topic", durable=True)

    def publish(self, routing_key: str, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        try:
            self._channel.basic_publish(
                exchange=self.exchange,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=pika.DeliveryMode.Persistent,
                ),
                mandatory=True,
            )
        except pika.exceptions.UnroutableError:
            # mandatory=True + sin cola bindeada todavia (ingesta Java no
            # levantada) - no perder el dato en silencio.
            logger.warning("Mensaje no ruteable en exchange '%s' con routing_key '%s'", self.exchange, routing_key)
        except pika.exceptions.NackError:
            logger.warning("Broker rechazo (nack) la publicacion en '%s' (routing_key '%s')", self.exchange, routing_key)

    def close(self) -> None:
        if self._connection.is_open:
            self._connection.close()


def publisher_from_env() -> ListingsQueuePublisher:
    return ListingsQueuePublisher(
        url=os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F"),
    )
