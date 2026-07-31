# car-tracker-scraper

Scraper en Python (Scrapy) para Car Tracker. Repo separado del backend Java a
proposito: la arquitectura del proyecto dice que Java y Python solo se
comunican por la cola de mensajes, nunca por repo o DB compartida.

Fuente cubierta por ahora: **MercadoLibre**. DeRuedas y Motordil quedan para
cuando se repita este mismo patron con sus fuentes ya reverse-engineered (ver
`/Users/fabriziopratici/Downloads/findings_clickup.md`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # completar TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTIBLOCK_REDIS_URL
```

Requiere un Redis corriendo (el mismo del `docker-compose.yml` del repo `car-tracker`) para el
token bucket y el circuit breaker de la capa anti-bloqueo — ver mas abajo.

## Discovery

Recorre el listado paginado de una o mas marcas, filtra publicidad y 0km
(filtro de condicion `ITEM*CONDITION_2230581`, confirmado con datos reales en
Fase 0), y descubre avisos nuevos. Pensado para correr cada 4-6h.

```bash
scrapy crawl mercadolibre_discovery -a marcas=fiat,ford -a max_pages=3 \
    -O output/discovery_%(time)s.jsonl
```

## Detail Fetch

Trae la ficha completa de avisos ya conocidos (las URLs que salieron de
Discovery). Caro, priorizado por tier — no descubre nada nuevo el mismo.

```bash
scrapy crawl mercadolibre_detail -a urls_file=urls.txt \
    -O output/detail_%(time)s.jsonl
```

## Estado y pendientes

- **Discovery**: extraccion verificada — reusa el mismo parsing (`__NORDIC_RENDERING_CTX__`,
  brace-balancing, desanidado de polycards) que ya se probo con datos reales contra
  MercadoLibre durante el reverse engineering de Fase 0.
- **Detail**: verificado contra un fixture real (`tests/fixtures/ml_detail_sample.html`, un
  Fiat Palio real de ML) — tanto el JSON-LD (`Vehicle`/`BreadcrumbList`) como
  `__NORDIC_RENDERING_CTX__` (`seller_card_motors`, `highlighted_specs_attrs`, `item_proximity`,
  `initial_payment_amount`, etc.). Ya no es best-effort.
- **robots.txt: resuelto.** El `robots.txt` real de `autos.mercadolibre.com.ar` bloquea
  `*_NoIndex_True` bajo `User-agent: *` (confirmado 2026-07-31), y el filtro de condicion de
  ML iba pegado a ese segmento. Discovery ya no lo usa: pide la pagina de marca sin filtrar
  y descarta 0km el mismo lado del parser (`_is_zero_km`, mismo dato — el texto "0 Km" — que
  revelo el bug original en Fase 0). Es una heuristica de texto, no un campo de condicion
  explicito; si en algun momento empieza a fallar (ML cambia el formato del texto de km), es
  el primer lugar donde mirar.
- Publicar a RabbitMQ (en vez de a un archivo JSONL local) es la tarea separada de
  "persistencia event-driven / cola" del roadmap de Fase 1 — no esta implementado en este repo todavia.

## Capa anti-bloqueo (`car_tracker_scraper/antiblocking/`)

Implementa wdxtkg30nk completo:

- **Token bucket por dominio en Redis** — compartido entre procesos, no solo por-instancia como
  `DOWNLOAD_DELAY` de Scrapy.
- **Backoff exponencial con jitter lognormal** en reintentos (403/429/503) — nunca un delay fijo.
- **UA pool coherente** (`user_agents.py`): 5 personas de navegador reales, cada una con su
  User-Agent + Accept-Language + `sec-ch-ua`* consistentes entre si (Firefox/Safari no mandan
  Client Hints, y no se les agregan). Rotado por request en Discovery; **sticky** (misma persona
  toda la corrida) en Detail via `spider.sticky_persona = True`.
- **Proxy pool opcional** (`ANTIBLOCK_PROXY_LIST`) — vacio por default, sin proveedor contratado
  todavia; se activa solo seteando la variable de entorno, sin tocar codigo.
- **Circuit breaker por dominio**: si la tasa de error supera el umbral en una ventana de tiempo,
  pausa ese dominio 30 min y manda una alerta a Telegram. Se cierra solo cuando pasan los 30 min
  (o manualmente via `CircuitBreaker.close()`).
- **Ventana horaria 2:00-7:00 ART** (`run_batch.py`): pensado para invocarse via cron: si se corre
  fuera de la ventana, no hace nada. Instalar en crontab (ver el docstring del script).

**Verificado end-to-end 2026-07-31**: corrida real contra MercadoLibre con el Redis del
docker-compose levantado (12 items reales, cero errores) y confirmado por `redis-cli` que las
keys `antiblock:tb:*` / `antiblock:cb:*` se crean de verdad. Mensaje de Telegram real tambien
confirmado.

## Landing zone (`car_tracker_scraper/landing/`)

Implementa wdxtkg30nm: guarda **siempre** el HTML crudo (gzip) en S3/MinIO, particionado
`{fuente}/{YYYY-MM-DD}/...`, antes de que el spider intente parsearlo — si el parser tiene un
bug, se reprocesa desde ahi, sin volver a scrapear.

- `LandingZoneMiddleware` (downloader middleware, prioridad 300 — mas baja que
  `AntiBlockingMiddleware` a proposito, ver comentario en el modulo) sube cada response FINAL
  (post-reintentos) y le agrega `s3_key`/`http_status`/`parser_version` al `request.meta`.
- Los spiders leen esos 3 campos de `response.meta` y los incluyen en cada item — terminan
  siendo el puente hacia la tabla `raw_payload` de Postgres del lado de la ingesta Spring Boot.
- `PARSER_VERSION` (`car_tracker_scraper/version.py`) — subirlo cada vez que cambia la logica de
  extraccion, para saber que rango reprocesar si se encuentra un bug despues.
- MinIO agregado al `docker-compose.yml` del repo `car-tracker` (puerto 9000 API, 9001 consola
  web) — habla la misma API que S3 real, migrar a AWS S3/R2 despues es solo cambiar el
  endpoint, no el codigo.

Probado con `moto` (S3 simulado en memoria) — **sin verificar todavia** contra el MinIO real del
docker-compose.

## Tests

```bash
python -m pytest tests/ -v
```

`test_extraction_mercadolibre.py` corre contra un fixture sintetico (estructura documentada).
`test_mercadolibre_detail_spider.py` corre contra el fixture real en `tests/fixtures/`.
