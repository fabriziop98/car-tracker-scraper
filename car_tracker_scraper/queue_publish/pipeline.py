"""Item pipeline: publica cada ficha completa (ListingDetailItem) al exchange
`listings` como evento ListingObserved (wdxtkg30nn). Los ListingSummaryItem de
Discovery NO se publican - son solo candidatos para que el Detail spider los
visite, no una observacion completa (ver flujo end-to-end, doc de arquitectura
seccion 4.1: "D -->|payload estructurado| MQ", D = Parser de Detail Fetch).
"""
from __future__ import annotations

from itemadapter import ItemAdapter
from twisted.internet import task

from car_tracker_scraper.items import ListingDetailItem
from car_tracker_scraper.queue_publish.publisher import ROUTING_KEY_OBSERVED, publisher_from_env

SCHEMA_VERSION = 1

# Bien por debajo de los 60s de heartbeat timeout de RabbitMQ (confirmado en
# produccion, wdxtkg3rpn) - un circuit breaker abierto o una racha de 403 sin
# items puede dejar el pipeline sin publicar nada por minutos, y
# pika.BlockingConnection no manda heartbeats por si sola sin que se la toque.
KEEPALIVE_INTERVAL_SECONDS = 20


class RabbitMQPublishPipeline:
    def open_spider(self, spider):
        self.publisher = publisher_from_env()
        self._keepalive_task = task.LoopingCall(self.publisher.keepalive)
        self._keepalive_task.start(KEEPALIVE_INTERVAL_SECONDS, now=False)

    def close_spider(self, spider):
        if self._keepalive_task.running:
            self._keepalive_task.stop()
        self.publisher.close()

    def process_item(self, item, spider):
        if isinstance(item, ListingDetailItem):
            payload = ItemAdapter(item).asdict()
            payload["schema_version"] = SCHEMA_VERSION
            self.publisher.publish(ROUTING_KEY_OBSERVED, payload)
        return item
