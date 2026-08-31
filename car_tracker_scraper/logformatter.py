"""LogFormatter propio para no volcar el item entero al log (wdxtkg3auv).

Por que un LogFormatter y no `logging.getLogger("scrapy.core.scraper")
.setLevel(INFO)` en settings.py, que seria lo obvio: **no funciona**.
`scrapy.utils.log.configure_logging()` resetea los loggers `scrapy.*` a
NOTSET despues de que settings.py corrio, con lo que el nivel puesto a mano se
pierde. Verificado en el contenedor:

    tras cargar settings.py:      scrapy.core.scraper level=INFO
    tras configure_logging():     scrapy.core.scraper level=NOTSET, efectivo=DEBUG

A los loggers de terceros (botocore, pika) no los toca - por eso ahi si alcanza
con setLevel, y settings.py lo hace asi.

Que se pierde: la linea "Scraped from <200 url>" con el item pretty-printeado.
Nada, en la practica: el item ya se escribe COMPLETO en el .jsonl de output/
(`-O output/detail_*.jsonl`), que ademas es consultable con jq. Lo que se gana:
esas lineas eran el 99% del log de una corrida (8,78 MB de 8,89 MB medidos el
2026-08-31), porque highlighted_specs_raw de ML es el arbol entero de ficha
tecnica y ocupa cientos de lineas por item.

El resto de los mensajes (dropped, item_error, download_error) se dejan tal
cual: son de bajo volumen y ahi si hace falta el detalle.
"""
from __future__ import annotations

from scrapy import logformatter


class QuietItemLogFormatter(logformatter.LogFormatter):
    def scraped(self, item, response, spider):
        """Devolver None hace que Scrapy saltee el mensaje por completo."""
        return None
