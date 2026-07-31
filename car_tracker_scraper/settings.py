# Scrapy settings for car_tracker_scraper project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = "car_tracker_scraper"

SPIDER_MODULES = ["car_tracker_scraper.spiders"]
NEWSPIDER_MODULE = "car_tracker_scraper.spiders"

ADDONS = {}


# UA de navegador real, igual al que ya se probo funcionando en el reverse
# engineering de Fase 0 (findings_clickup.md). No es una identidad falsa en
# el sentido problematico (no nos hacemos pasar por "Googlebot" ni nada asi),
# es lo que hace cualquier scraper de produccion.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

# Obey robots.txt rules.
# PENDIENTE (ver findings_clickup.md, seccion MercadoLibre): el bloque
# "User-agent: *" del robots.txt de ML no se pudo confirmar completo todavia
# (solo se vio el de Bingbot). Con ROBOTSTXT_OBEY=True en True por defecto,
# si ese bloque generico restringe las URLs de listado/detalle este spider
# se va a frenar solo - confirmar el robots.txt completo en el navegador
# antes de asumir que hay que desactivar esto.
ROBOTSTXT_OBEY = True

# Concurrency and throttling settings — timing humano, sin patrones de reloj
# perfectos (seccion 3.2 del doc de arquitectura).
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = 2
RANDOMIZE_DOWNLOAD_DELAY = True

DEFAULT_REQUEST_HEADERS = {
    "Accept-Language": "es-AR,es;q=0.9",
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

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 2
AUTOTHROTTLE_MAX_DELAY = 30
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
#AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0
#HTTPCACHE_DIR = "httpcache"
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value
FEED_EXPORT_ENCODING = "utf-8"
