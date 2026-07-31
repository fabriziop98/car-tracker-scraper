# Scrapy settings for car_tracker_scraper project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

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
ANTIBLOCK_REDIS_URL = "redis://localhost:6379/0"
ANTIBLOCK_TOKEN_BUCKET_CAPACITY = 5  # burst permitido
ANTIBLOCK_TOKEN_BUCKET_REFILL_PER_SEC = 0.5  # ~1 request cada 2s en regimen estable

# Landing zone (wdxtkg30nm): guardar siempre el HTML crudo en S3/MinIO
# antes de parsearlo. Config real por variable de entorno - ver .env.example.
LANDING_S3_ENDPOINT_URL = "http://localhost:9000"
LANDING_S3_BUCKET = "car-tracker-raw"

DOWNLOADER_MIDDLEWARES = {
    "car_tracker_scraper.antiblocking.middleware.AntiBlockingMiddleware": 350,
    # Prioridad mas baja que AntiBlockingMiddleware a proposito: tiene que
    # ver la response FINAL (post-reintentos), no un 429/503 intermedio -
    # ver el comentario en landing/middleware.py.
    "car_tracker_scraper.landing.middleware.LandingZoneMiddleware": 300,
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
