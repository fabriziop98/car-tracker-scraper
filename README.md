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
- **robots.txt: resuelto.** El `robots.txt` real de `autos.mercadolibre.com.ar` bloquea
  `*_NoIndex_True` bajo `User-agent: *` (confirmado 2026-07-31), y el filtro de condicion de
  ML iba pegado a ese segmento. Discovery ya no lo usa: pide la pagina de marca sin filtrar
  y descarta 0km el mismo lado del parser (`_is_zero_km`, mismo dato — el texto "0 Km" — que
  revelo el bug original en Fase 0). Es una heuristica de texto, no un campo de condicion
  explicito; si en algun momento empieza a fallar (ML cambia el formato del texto de km), es
  el primer lugar donde mirar.
- Publicar a RabbitMQ (en vez de a un archivo JSONL local) es la tarea separada de
  "persistencia event-driven / cola" del roadmap de Fase 1 — no esta implementado en este repo todavia.

## Tests

```bash
python -m pytest tests/ -v
```

`test_extraction_mercadolibre.py` corre contra un fixture sintetico (estructura documentada).
`test_mercadolibre_detail_spider.py` corre contra el fixture real en `tests/fixtures/`.
