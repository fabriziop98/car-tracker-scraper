# Scrapy settings for car_tracker_scraper project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

import os

from dotenv import load_dotenv

load_dotenv()  # carga .env (gitignored) si existe - ver .env.example

BOT_NAME = "car_tracker_scraper"

SPIDER_MODULES = ["car_tracker_scraper.spiders"]
NEWSPIDER_MODULE = "car_tracker_scraper.spiders"

ADDONS = {}


# El User-Agent/Accept-Language/sec-ch-ua ya NO se fijan aca: los pone
# AntiBlockingMiddleware por request, desde el pool de personas coherentes
# de car_tracker_scraper/antiblocking/user_agents.py (wdxtkg30nk).

# Obey robots.txt rules.
# DESACTIVADO A PROPOSITO - decision de negocio de Fabrizio (2026-07-31), no
# un default silencioso. El robots.txt real de autos.mercadolibre.com.ar
# bloquea, bajo "User-agent: *", TANTO "*_NoIndex_True" (evitado cambiando
# la URL de entrada) COMO "*_Desde_" - y "_Desde_{offset}" es el UNICO
# mecanismo de paginacion que ML expone (confirmado a mano en el navegador:
# la pagina 2 carga la misma URL "_Desde_49_NoIndex_True", no hay ruta
# alternativa por query param). Con ROBOTSTXT_OBEY=True, el Discovery queda
# limitado a la pagina 1 de cada marca (~37-48 avisos), sin poder paginar
# mas hondo. Fabrizio eligio priorizar cobertura de datos sobre compliance
# estricto con esa regla puntual - el riesgo legal/reputacional de esto es
# de el, no una decision tecnica unilateral. Los bloqueos nombrados
# (ClaudeBot/GPTBot/etc. con Disallow: /) nunca aplicaron a este scraper de
# todos modos, porque el USER_AGENT configurado abajo no se identifica como
# ninguno de esos bots.
ROBOTSTXT_OBEY = False

CONCURRENT_REQUESTS_PER_DOMAIN = 2

# El pacing (delay + jitter) y el backoff en errores ya NO los maneja
# DOWNLOAD_DELAY/RANDOMIZE_DOWNLOAD_DELAY/AUTOTHROTTLE (delay fijo o uniforme
# - justo lo que wdxtkg30nk pide evitar): los maneja AntiBlockingMiddleware
# via token bucket en Redis + jitter lognormal. Dejarlos prendidos a la vez
# duplicaria/pisaria el pacing.
DOWNLOAD_DELAY = 0
AUTOTHROTTLE_ENABLED = False

# Capa anti-bloqueo (wdxtkg30nk): token bucket por dominio en Redis,
# UA pool coherente, proxy pool opcional, circuit breaker, backoff con
# jitter lognormal. Config real (Redis URL, Telegram, proxies) por
# variable de entorno - ver .env.example.
ANTIBLOCK_REDIS_URL = os.environ.get("ANTIBLOCK_REDIS_URL", "redis://localhost:6379/0")
ANTIBLOCK_TOKEN_BUCKET_CAPACITY = 5  # burst permitido
ANTIBLOCK_TOKEN_BUCKET_REFILL_PER_SEC = float(os.environ.get("ANTIBLOCK_TOKEN_BUCKET_REFILL_PER_SEC", "0.5"))

# Landing zone (wdxtkg30nm): guardar siempre el HTML crudo en S3/MinIO
# antes de parsearlo. Config real por variable de entorno - ver .env.example.
LANDING_S3_ENDPOINT_URL = os.environ.get("LANDING_S3_ENDPOINT_URL", "http://localhost:9000")
LANDING_S3_BUCKET = "car-tracker-raw"

# Mensajeria (wdxtkg30nn): unico canal por el que este scraper le habla a la
# ingesta Spring Boot - nunca DB compartida. Topologia real (cola, DLQ,
# reintentos) la posee y declara el lado Java. Config real por variable de
# entorno - ver .env.example.
ITEM_PIPELINES = {
    # wdxtkg348c: tiene que correr ANTES que RabbitMQPublishPipeline (numero
    # mas bajo = mas temprano) para que main_image_phash ya este seteado
    # cuando el mensaje se arma - ItemAdapter(item).asdict() en el publish
    # pipeline no sabe esperar a un campo que todavia no existe.
    "car_tracker_scraper.image_phash.pipeline.ImagePhashPipeline": 300,
    "car_tracker_scraper.queue_publish.pipeline.RabbitMQPublishPipeline": 400,
}

# wdxtkg348c: directorio local efimero para ImagePhashPipeline - se lee una
# sola vez para calcular el hash perceptual y despues no se vuelve a tocar.
# No es landing zone (no se sube a MinIO/S3, a diferencia del HTML/JSON via
# LandingZoneMiddleware) - si mas adelante hace falta reprocesar fotos
# historicas, eso es alcance nuevo, no lo que pide este ticket.
IMAGES_STORE = os.environ.get("IMAGE_PHASH_STORE", "/tmp/car-tracker-image-phash")

DOWNLOADER_MIDDLEWARES = {
    "car_tracker_scraper.antiblocking.middleware.AntiBlockingMiddleware": 350,
    # Prioridad mas baja que AntiBlockingMiddleware a proposito: tiene que
    # ver la response FINAL (post-reintentos), no un 429/503 intermedio -
    # ver el comentario en landing/middleware.py.
    "car_tracker_scraper.landing.middleware.LandingZoneMiddleware": 300,
}

# Metricas por fuente hacia Prometheus Pushgateway (wdxtkg34th): success
# rate, 403/429, latencia p95, items/min, null% por campo, nuevos vs
# conocidos. METRICS_PUSHGATEWAY_URL vacio = deshabilitado, no rompe el
# spider. Config real por variable de entorno - ver .env.example.
#
# 2026-09-14: el "" hardcodeado nunca leia la variable de entorno a pesar
# de lo que dice el comentario de arriba - Settings.get(name, default) solo
# usa el default cuando la CLAVE esta ausente, no cuando esta presente pero
# vacia, asi que el fallback a os.environ que tiene metrics.py nunca se
# ejecutaba. Confirmado en vivo: METRICS_PUSHGATEWAY_URL estaba bien seteado
# en el contenedor scheduler pero las 7 fuentes registraban "no configurado"
# en cada corrida - cero metricas empujadas a Pushgateway desde que existe
# esta linea.
METRICS_PUSHGATEWAY_URL = os.environ.get("METRICS_PUSHGATEWAY_URL", "")
EXTENSIONS = {
    "car_tracker_scraper.observability.metrics.SourceMetricsExtension": 500,
}

# Disable cookies (enabled by default)
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
#TELNETCONSOLE_ENABLED = False

# Override the default request headers:
#DEFAULT_REQUEST_HEADERS = {
#    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
#    "Accept-Language": "en",
#}

# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
#SPIDER_MIDDLEWARES = {
#    "car_tracker_scraper.middlewares.CarTrackerScraperSpiderMiddleware": 543,
#}

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#DOWNLOADER_MIDDLEWARES = {
#    "car_tracker_scraper.middlewares.CarTrackerScraperDownloaderMiddleware": 543,
#}

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
#EXTENSIONS = {
#    "scrapy.extensions.telnet.TelnetConsole": None,
#}

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
#ITEM_PIPELINES = {
#    "car_tracker_scraper.pipelines.CarTrackerScraperPipeline": 300,
#}

# AutoThrottle deshabilitado a proposito - ver comentario junto a
# AUTOTHROTTLE_ENABLED mas arriba (AntiBlockingMiddleware maneja el pacing).

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0
#HTTPCACHE_DIR = "httpcache"
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value
FEED_EXPORT_ENCODING = "utf-8"

# Scrapy corre el root logger en DEBUG (su default), y boto3/botocore heredan
# ese nivel. Como LandingZoneMiddleware sube el HTML de CADA pagina scrapeada a
# MinIO, botocore emitia ~20 lineas por request - firma AWS, StringToSign,
# CanonicalRequest, headers completos de ida y vuelta, eventos de hooks. A ~250
# MB/dia con 4 fuentes, batch.log llego a 4,86 GB (incidente 2026-08-31) y dejo
# de ser leible justo cuando hacia falta para diagnosticar una caida de 20h: no
# entraba en memoria y habia que rasparlo con `tail -c` de a cientos de MB.
#
# Se bajan SOLO estos loggers, no el LOG_LEVEL global: las lineas DEBUG propias
# de Scrapy ("Crawled (200) <GET ...>") son las que sirven de verdad para
# diagnosticar - con ellas se identificaron los 404 de Autocity (wdxtkg3auw).
# Nada de lo que botocore dice en DEBUG se uso nunca; sus errores reales siguen
# saliendo, porque son WARNING/ERROR.
import logging  # noqa: E402  (al final del archivo a proposito, junto a lo que configura)

for _noisy_logger in ("botocore", "boto3", "s3transfer", "urllib3", "pika"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

# El volcado del item completo de scrapy.core.scraper (el 99% del log) NO se
# puede apagar con setLevel: configure_logging() de Scrapy resetea los loggers
# scrapy.* a NOTSET despues de que este archivo corre. Se apaga con un
# LogFormatter, ver car_tracker_scraper/logformatter.py.
LOG_FORMATTER = "car_tracker_scraper.logformatter.QuietItemLogFormatter"
