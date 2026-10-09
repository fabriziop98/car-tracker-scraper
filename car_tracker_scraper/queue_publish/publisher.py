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
        self._url = url
        self.exchange = exchange
        self._connect()

    def _connect(self) -> None:
        self._connection = pika.BlockingConnection(pika.URLParameters(self._url))
        self._channel = self._connection.channel()
        self._channel.confirm_delivery()
        # Mismos parametros que TopicExchange declarado en ListingsQueueConfig -
        # declarar un exchange existente con los mismos parametros es un no-op.
        self._channel.exchange_declare(exchange=self.exchange, exchange_type="topic", durable=True)

    def _reconnect(self) -> None:
        try:
            if self._connection.is_open:
                self._connection.close()
        except pika.exceptions.AMQPError:
            pass  # la conexion ya esta en un estado roto - nada que cerrar prolijo
        self._connect()

    def publish(self, routing_key: str, payload: dict, _retry: bool = True) -> None:
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
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.AMQPChannelError) as exc:
            # Conexion/canal caidos (visto en produccion: RabbitMQ cierra la
            # conexion por "missed heartbeats" cuando el scraper pasa 60s+ sin
            # publicar nada - circuit breaker abierto o racha de 403 sin items -
            # porque pika.BlockingConnection solo procesa/envia heartbeats
            # cuando se lo llama explicitamente; ver keepalive() mas abajo, que
            # ataca la causa). Sin este reintento, cada item posterior se
            # perdia en silencio (salvo el ERROR de log) hasta que el spider
            # terminaba y abria una conexion nueva - wdxtkg3rpn, 2026-10-08/09.
            if not _retry:
                logger.error(
                    "Publish perdido tras reconectar: no se pudo reenviar routing_key '%s' en '%s' (%s)",
                    routing_key,
                    self.exchange,
                    exc,
                )
                raise
            logger.warning("Conexion RabbitMQ caida (%s) - reconectando y reintentando publish", exc)
            self._reconnect()
            self.publish(routing_key, payload, _retry=False)

    def keepalive(self) -> None:
        """Procesa eventos pendientes (y envia/recibe heartbeats) aunque no haya
        ningun item para publicar en este momento. Llamar periodicamente (ver
        RabbitMQPublishPipeline) durante huecos largos sin items - circuit
        breaker abierto, racha de errores del sitio origen - para que la
        conexion no quede muda mas alla del timeout de heartbeat de RabbitMQ
        (60s por defecto) y el broker la mate. No reconecta por si sola: una
        conexion caida se repara recien en el proximo publish()."""
        try:
            if self._connection.is_open:
                self._connection.process_data_events(time_limit=0)
        except pika.exceptions.AMQPError as exc:
            logger.warning("Keepalive sobre conexion RabbitMQ fallo (%s) - se reconectara en el proximo publish()", exc)

    def close(self) -> None:
        if self._connection.is_open:
            self._connection.close()


def publisher_from_env() -> ListingsQueuePublisher:
    return ListingsQueuePublisher(
        url=os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F"),
    )
