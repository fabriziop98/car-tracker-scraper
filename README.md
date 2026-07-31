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
- **Detail**: la parte de JSON-LD (`@type: Vehicle`, `BreadcrumbList`) esta bien fundada
  (mismo mecanismo schema.org, con ejemplo real en los findings). La parte de
  `__NORDIC_RENDERING_CTX__` (`seller_card_motors`, `highlighted_specs_attrs`, etc.) es
  **best-effort**: los paths exactos solo estan descriptos en prosa en los findings, no
  verificados contra un HTML de detalle real todavia. Antes de confiar en produccion,
  guardar un detalle real (`curl` corrido por vos, no por el asistente — el robots.txt de
  ML bloquea `ClaudeBot`) en `tests/fixtures/` y ajustar `mercadolibre_detail.py` contra eso.
- **robots.txt**: `ROBOTSTXT_OBEY=True` (default de Scrapy). El bloque generico
  `User-agent: *` de ML no se pudo confirmar completo — revisarlo en el navegador antes
  de un crawl grande, por si frena mas de lo esperado.
- Publicar a RabbitMQ (en vez de a un archivo JSONL local) es la tarea separada de
  "persistencia event-driven / cola" del roadmap de Fase 1 — no esta implementado en este repo todavia.

## Tests

```bash
python -m pytest tests/ -v
```

Corren contra un fixture **sintetico** (construido en base a la estructura confirmada
en los findings), no contra HTML real de ML — ver el docstring de
`tests/test_extraction_mercadolibre.py`.
