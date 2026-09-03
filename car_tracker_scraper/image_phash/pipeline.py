"""Item pipeline: descarga la foto principal y calcula su hash perceptual
(wdxtkg348c, Dedup Nivel 2 - "el discriminador mas fuerte" segun wdxtkg30nr).

Subclasea `scrapy.pipelines.images.ImagesPipeline` en vez de bajar la imagen
a mano con `requests`: las requests que arma `get_media_requests` pasan por
el downloader normal de Scrapy, o sea que `AntiBlockingMiddleware` las ve
igual que cualquier otra - el CDN de imagenes (`http2.mlstatic.com`, un host
DISTINTO de `auto.mercadolibre.com.ar`) recibe su propio token bucket y
circuit breaker por dominio automaticamente (`domain_of(request.url)` es
generico, no hardcodea las 5 fuentes conocidas). Bajar la imagen con un
cliente HTTP aparte hubiera esquivado toda esa infraestructura.

Solo se calcula el hash - no se persiste la imagen en ningun lado mas alla
del archivo temporal que `ImagesPipeline` escribe en `IMAGES_STORE` (un
directorio local del contenedor, no un volumen ni landing zone). Si mas
adelante hace falta reprocesar fotos historicas, eso es un alcance nuevo
(subir tambien a MinIO como el HTML), no lo que pide este ticket.
"""
from __future__ import annotations

import logging
import os

import imagehash
from PIL import Image
from itemadapter import ItemAdapter
from scrapy import Request
from scrapy.pipelines.images import ImagesPipeline

from car_tracker_scraper.items import ListingDetailItem

logger = logging.getLogger(__name__)


class ImagePhashPipeline(ImagesPipeline):

    def get_media_requests(self, item, info):
        if not isinstance(item, ListingDetailItem):
            return []
        url = ItemAdapter(item).get("main_image_url")
        return [Request(url)] if url else []

    def item_completed(self, results, item, info):
        if not isinstance(item, ListingDetailItem):
            return item

        for ok, result in results:
            if not ok:
                logger.warning("No se pudo descargar la foto principal de %s: %s",
                                ItemAdapter(item).get("url"), result.getErrorMessage())
                continue
            # FSFilesStore (IMAGES_STORE local): result["path"] es relativo a
            # self.store.basedir, no absoluto - ImagesPipeline lo documenta asi.
            full_path = os.path.join(self.store.basedir, result["path"])
            try:
                with Image.open(full_path) as img:
                    item["main_image_phash"] = str(imagehash.phash(img))
            except Exception:
                logger.exception("Fallo calculando el phash de la foto principal de %s",
                                  ItemAdapter(item).get("url"))
            break  # una sola foto principal - el primer resultado exitoso alcanza

        return item
