"""Item pipeline: publica cada ficha completa (ListingDetailItem) al exchange
`listings` como evento ListingObserved (wdxtkg30nn). Los ListingSummaryItem de
Discovery NO se publican - son solo candidatos para que el Detail spider los
visite, no una observacion completa (ver flujo end-to-end, doc de arquitectura
seccion 4.1: "D -->|payload estructurado| MQ", D = Parser de Detail Fetch).
"""
from __future__ import annotations

from itemadapter import ItemAdapter

from car_tracker_scraper.items import ListingDetailItem
from car_tracker_scraper.queue_publish.publisher import ROUTING_KEY_OBSERVED, publisher_from_env

SCHEMA_VERSION = 1


class RabbitMQPublishPipeline:
    def open_spider(self, spider):
        self.publisher = publisher_from_env()

    def close_spider(self, spider):
        self.publisher.close()

    def process_item(self, item, spider):
        if isinstance(item, ListingDetailItem):
            payload = ItemAdapter(item).asdict()
            payload["schema_version"] = SCHEMA_VERSION
            self.publisher.publish(ROUTING_KEY_OBSERVED, payload)
        return item
