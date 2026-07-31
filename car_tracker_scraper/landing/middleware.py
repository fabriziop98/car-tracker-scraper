"""Downloader middleware que sube CADA response final (post-reintentos) al
landing zone en S3/MinIO, antes de que el spider intente parsearla. Si el
parser tiene un bug, el raw ya esta guardado - no hay que volver a scrapear
(wdxtkg30nm).

Prioridad mas baja que AntiBlockingMiddleware (350) a proposito: tiene que
correr DESPUES de que AntiBlockingMiddleware ya decidio si reintentar o no,
para guardar la response que realmente llega al spider, no un 429/503
intermedio que todavia se va a reintentar.
"""
from __future__ import annotations

from car_tracker_scraper.landing.storage import storage_from_env
from car_tracker_scraper.version import PARSER_VERSION


class LandingZoneMiddleware:
    def __init__(self):
        self.storage = storage_from_env()

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def process_response(self, request, response, spider):
        source = spider.name.split("_")[0]  # "mercadolibre_discovery" -> "mercadolibre"
        s3_key = self.storage.upload_raw(source=source, content=response.body, extension="html")
        request.meta["s3_key"] = s3_key
        request.meta["http_status"] = response.status
        request.meta["parser_version"] = PARSER_VERSION
        return response
