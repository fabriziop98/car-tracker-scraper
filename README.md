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
```

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
- **robots.txt — DECISION PENDIENTE, no solo verificacion:** el `robots.txt` real de
  `autos.mercadolibre.com.ar` (el subdominio que pega Discovery) tiene, en el bloque generico
  `User-agent: *`, la regla `Disallow: /*_NoIndex_True`. La URL que arma Discovery
  (`.../fiat_ITEM*CONDITION_2230581_NoIndex_True?...`) matchea esa regla. Con
  `ROBOTSTXT_OBEY=True` (default actual), Scrapy va a bloquear el 100% de los pedidos de
  Discovery en silencio (0 resultados, sin error visible). Ver la nota en `settings.py` y
  decidir como seguir antes de intentar correr Discovery de verdad.
- Publicar a RabbitMQ (en vez de a un archivo JSONL local) es la tarea separada de
  "persistencia event-driven / cola" del roadmap de Fase 1 — no esta implementado en este repo todavia.

## Tests

```bash
python -m pytest tests/ -v
```

`test_extraction_mercadolibre.py` corre contra un fixture sintetico (estructura documentada).
`test_mercadolibre_detail_spider.py` corre contra el fixture real en `tests/fixtures/`.
