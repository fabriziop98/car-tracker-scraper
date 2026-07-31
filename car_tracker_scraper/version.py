"""Version del parser (wdxtkg30nm): "cada raw_payload registra
parser_version. Cuando arreglas un bug de parseo sabes exactamente que
rango reprocesar - sin volver a scrapear". Subir este numero cada vez que
cambia la logica de extraccion (car_tracker_scraper/extraction/*.py o los
spiders), para poder filtrar por rango afectado despues."""

PARSER_VERSION = "ml_v1"
