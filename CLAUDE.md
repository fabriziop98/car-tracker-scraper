# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`car-tracker-scraper` is the Python/Scrapy scraper for **Car Tracker**, a used-car price-tracking data platform for the Argentine market. Fabrizio is the solo founder/architect; he does not write code himself and relies on Claude Code for all implementation, focusing his own time on architecture-level decisions. Treat every change as something he will only ever audit via `/code-review`, not by reading the diff line by line — real tests against real infra, documented "why" (not just "what"), and consistent conventions matter more here than in a typical repo.

**Fabrizio is a senior Java/Spring backend engineer but is new to Python, Scrapy, and this repo's DevOps side (Docker, Redis, cron).** When explaining anything here, lead with a plain explanation and a Java-world analogy where one helps (e.g. "a Scrapy downloader middleware is like a Servlet Filter", "the circuit breaker here is the same pattern as Resilience4j/Hystrix", "the token bucket is the same idea as Bucket4j/`@RateLimiter`") — don't assume Python/Scrapy familiarity the way you could in the sibling `car-tracker` repo.

The companion repo is **`car-tracker`** (Java/Spring Boot), usually checked out as a sibling directory. By design the two repos **only talk over RabbitMQ** (this repo publishes, that one consumes) plus one narrow read-only HTTP bridge (`CAR_TRACKER_API_URL`, see below) — never a shared database, never a shared repo. See that repo's own `CLAUDE.md` for its side.

**Claude Code may run `scrapy crawl` (Discovery or Detail) and `run_batch.py` directly against the real sites, including MercadoLibre** — this used to be a standing rule requiring Fabrizio to trigger every real run himself; he removed it explicitly (2026-09-02) since in practice validating a fix or a new source always ends up needing a real run anyway. The anti-blocking layer (token bucket, circuit breaker, backoff) is what actually protects the target sites, not a human-in-the-loop gate — keep runs reasonably scoped (e.g. `max_pages`/a handful of URLs) when the goal is validation rather than a production batch.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                                   # then fill in real values, .env is gitignored

python -m pytest tests/ -v                              # full test suite
python -m pytest tests/test_scheduling.py -v             # single test file
python -m pytest tests/test_scheduling.py::test_name -v  # single test

scrapy crawl mercadolibre_discovery -a marcas=fiat,ford -O output/discovery_%(time)s.jsonl
scrapy crawl mercadolibre_detail -a urls_file=urls.txt -O output/detail_%(time)s.jsonl
python run_batch.py                                     # scheduler tick: Discovery refresh + a Detail batch
```

Requires the `car-tracker` repo's `docker compose` infra running alongside it: Redis (token bucket, circuit breaker, discovery-candidate tracking), RabbitMQ (publish target), MinIO (landing zone), Prometheus Pushgateway (metrics), and the `app` service (curated/discovered brand lookups). Nothing in this repo talks to Postgres directly, ever — that's a hard architectural boundary, not an oversight.

**Rebuild after every commit, proactively, not just when asked.** The `scheduler` container (`docker compose build scheduler && docker compose up -d --no-deps scheduler`, run from `car-tracker/`) runs from a built image — recreating it without rebuilding, or not recreating it at all, leaves it silently running stale code with zero visible error. This was the root cause of a real incident (2026-08-18): `scheduler` ran Detail-less for 6 days because the running container predated that day's fixes, and nobody noticed until Postgres was audited directly. After any commit in this repo (or `car-tracker`'s `app`), rebuild+recreate the affected service(s) immediately.

## Architecture

### Discovery vs. Detail — two different costs, two different scopes

This split applies **per source**, not just to MercadoLibre — all five spider pairs (`spiders/{mercadolibre,motordil,deruedas,autocity,v6}_discovery.py`/`*_detail.py`) take the same `marcas` arg and follow the same broad/narrow policy from `run_batch.py`.

- **Discovery** walks a brand's paginated listing pages, filters ads and 0km units, and finds new listing URLs. Cheap, meant to run broad — every brand DNRPA reports selling in Argentina above a volume floor (`GET {CAR_TRACKER_API_URL}/api/brands/discovered`), not just the ones with a built-out canonical catalog.
- **Detail** fetches the full listing page for URLs Discovery already found, and is what actually publishes to RabbitMQ. Expensive, so it's deliberately scoped narrower: only brands promoted into the canonical catalog (`GET {CAR_TRACKER_API_URL}/api/brands/curated`). Running Detail against an uncurated brand just fills the Java side's `pending_review` normalization queue with things that can never resolve. **This Discovery-broad/Detail-narrow split is load-bearing** — it was originally half-implemented (Discovery fixed, Detail still unscoped) and the gap was found and closed; don't reintroduce it when touching brand-related scraping logic. If the curated-marcas fetch fails, Detail skips the tick entirely rather than running unfiltered (fail-safe, same principle as everywhere else in the scheduler).
- Neither list is hardcoded in this repo — `run_batch.py` fetches both from the Java API on every run. This repo never connects to Postgres directly; that HTTP call is the only bridge in that direction, the same way RabbitMQ/S3 are the only bridge in the other direction.

### Anti-blocking layer (`car_tracker_scraper/antiblocking/`)

- **Token bucket per domain in Redis** (`token_bucket.py`) — shared across processes, unlike Scrapy's per-instance `DOWNLOAD_DELAY`. `DOWNLOAD_DELAY`/`AUTOTHROTTLE_ENABLED` are deliberately off in `settings.py` to avoid double-pacing.
- **Exponential backoff with lognormal jitter** (`backoff.py`) on 403/429/503 — never a fixed delay.
- **Coherent User-Agent pool** (`user_agents.py`) — 5 real browser personas, each with internally-consistent UA + Accept-Language + `sec-ch-ua`*. Rotated per-request in Discovery; **sticky** for the whole run in Detail (`spider.sticky_persona = True`).
- **Circuit breaker per domain** (`circuit_breaker.py`) — pauses a domain 30 min on an error-rate spike, alerts Telegram, self-closes after the window (or manually via `CircuitBreaker.close()`).
- Optional proxy pool (`ANTIBLOCK_PROXY_LIST`, empty by default — no provider contracted yet).

### `ROBOTSTXT_OBEY = False` — a deliberate business decision, not a default left on

MercadoLibre's real `robots.txt` blocks `*_Desde_` under `User-agent: *`, and `_Desde_{offset}` is the *only* pagination mechanism the site exposes — obeying it would cap Discovery at page 1 (~37-48 listings) per brand. Fabrizio explicitly chose data coverage over strict compliance with that specific rule and owns that legal/reputational call (see the comment block above `ROBOTSTXT_OBEY` in `settings.py` for the full reasoning).

Separately: `autos.mercadolibre.com.ar/robots.txt` also disallows `ClaudeBot` by name (`Disallow: /`) — that's about Claude Code's own identity when fetching the site directly (e.g. via `curl`/`WebFetch`), not about `ROBOTSTXT_OBEY`, which only governs the scraper's own configured UA pool (never identifies as ClaudeBot/GPTBot/etc.).

### Landing zone (`car_tracker_scraper/landing/`)

`LandingZoneMiddleware` (downloader middleware, priority 300 — lower than `AntiBlockingMiddleware`'s 350 on purpose, so it only sees the *final* post-retry response, never an intermediate 429/503) uploads gzipped raw HTML to S3/MinIO, partitioned `{source}/{YYYY-MM-DD}/...`, before any parsing happens — if a parser bug is found later, affected pages are reprocessed from the landing zone instead of re-scraping. Spiders read `s3_key`/`http_status`/`parser_version` back off `response.meta` and include them on every item; that's the bridge to the Java side's `raw_payload` table. Bump `PARSER_VERSION` (`version.py`) whenever extraction logic changes, so a bug fix knows which date range needs reprocessing.

### Scheduler (`car_tracker_scraper/scheduling/`, `run_batch.py`)

Installed in cron every 15 min, 24/7 (`scheduler/crontab`) — the 2:00-7:00 ART window that used to gate the heavy batch was removed (`wdxtkg30xr`, 2026-08-25): it was redundant with the token bucket and was the tighter real limit on throughput (5h/day of the 24 available) on the way to Fase 1's 100k+ record goal. The token bucket (`ANTIBLOCK_TOKEN_BUCKET_REFILL_PER_SEC`) is the only real aggressiveness limit left — if the circuit breaker starts opening more often, that's the signal to lower the rate, not to reintroduce an hourly gate.

**The scheduler is source-agnostic, not ML-only.** `SOURCES` (`run_batch.py`) is a tuple of `SourceConfig` — five as of 2026-09-02 (`mercadolibre`, `motordil`, `deruedas`, `autocity`, `v6`) — each with its own `discovery_spider`/`detail_spider`, `discovery_interval_hours`, `detail_batch_size`, `seconds_per_request` (a source with its own `DOWNLOAD_DELAY`, e.g. DeRuedas at 5s, costs more per item and has to say so or the tick's time budget is miscalculated), and optional `discovery_args`. `main()` runs all sources **in parallel** (`ThreadPoolExecutor`, one thread per source, `wdxtkg39qx`) — the token bucket is per-domain, so distinct sites never compete, and the tick's time budget is the *max* across sources, not the sum, which is what lets a slow source (Kavak's `Crawl-delay: 20`, once added) coexist without shrinking everyone else's batch. This is also the mechanism behind the doc's "no source over 60% of the dataset" business rule. Each source's tick: Discovery refreshes every `discovery_interval_hours` and every non-ad URL found gets recorded in `DiscoveryCandidateTracker` (Redis sorted set, one keyspace per source — deliberately kept in Redis, never Postgres, since it's not business data); Detail takes a bounded batch (`detail_batch_size`) of candidates overdue by `DETAIL_TIER_HOURS` (72h default), never-detailed candidates always go first. Single tier for now — A/B/C prioritization by real per-model volume is deferred until that data exists; don't block the scheduler on building that first.

**Each source is now guarded by a per-source Redis lock** (`scheduling:run_lock:{slug}`, `run_source()`/`_acquire_source_lock`/`_release_source_lock`, 1h TTL) — this reverses an earlier design ("no lock file, overlap is safe by construction") after a real measured incident (2026-08-31): DeRuedas Discovery took ~34 min (8,029 items at `DOWNLOAD_DELAY=5`) and, with no lock, the same source's Discovery started again on the next two 15-min ticks — three concurrent runs against the same site meant a request every ~1.7s instead of every 5s, silently violating the `Crawl-delay` the delay exists to respect. The lock is **per-source, not global**: one slow source doesn't block the others, which run against different domains with their own budget. Because the lock is only released in `run_source()`'s `finally`, a hard process kill (a container rebuild, which happens on every code deploy) leaves it held for up to the 1h TTL — hit for real on 2026-09-01 when a 10:37 rebuild killed a run started at 10:30 and the 11:15 tick silently skipped Discovery ("already running") with nothing actually running. `release_stale_source_locks()` now runs at container entrypoint to clear every lock unconditionally — safe specifically because compose runs a single `scheduler` container, so a freshly-started one can't be stepping on a legitimately in-flight run.

`run_discovery()` falls back to `fetch_curated_marcas()` when `fetch_discovered_marcas()` (the DNRPA-backed wide list, see `car-tracker`'s brand discovery) comes back empty — bootstraps Discovery before DNRPA's weekly job has ever run, or if it's temporarily down, instead of blocking entirely. `run_detail()` only calls `tracker.mark_detailed()` when the crawl's output file actually has content (checked by file size, not `subprocess.run`'s returncode — Scrapy routinely exits 0 even when every single request failed, e.g. `IgnoreRequest` from the anti-blocking middleware isn't a crash). A real incident (2026-08-19) marking candidates as done on a 100%-failure run silently locked 8,000 of them out of retry for 72h (`DETAIL_TIER_HOURS`) — had to be manually reset in Redis. Don't revert this check to plain returncode.

**Validating a scheduler fix from the host doesn't prove it works in the container.** `ANTIBLOCK_REDIS_URL`/`CAR_TRACKER_API_URL` resolve differently by network context: `localhost` on the host reaches the docker-composed services fine (ports are published), but the exact same `localhost` inside the `scheduler` container resolves to the container itself, not the service — a real bug (see Gotchas) stayed hidden for a while specifically because host-side manual testing kept passing. To actually validate a scheduler-side fix, run it inside the real container: `docker exec car-tracker-scheduler-1 python run_manual_batch.py` (a small helper script, not part of the cron path, that replicates one `main()` tick).

### Metrics (`car_tracker_scraper/observability/metrics.py`)

The scraper runs as short-lived batch jobs (spiders triggered by Discovery/Detail), not a long-running server, so Prometheus can't pull-scrape it — metrics go out via **Pushgateway** instead (`SourceMetricsExtension`, a Scrapy extension hooked to `spider_opened`/`response_received`/`item_scraped`/`spider_closed`, pushes on spider close). `METRICS_PUSHGATEWAY_URL` empty disables it without breaking the spider. Success rate is read from `CircuitBreaker.stats()` (doesn't duplicate that counting); 403/429 rate is counted separately since the circuit breaker only tracks ok/fail booleans; new-vs-known reuses the same `DiscoveryCandidateTracker` Redis set the scheduler already maintains. The corresponding dashboard is a row in `car-tracker`'s existing Grafana dashboard, not a separate one.

### Queue publishing

`queue_publish/pipeline.py` + `publisher.py` is the only channel this repo uses to hand data to the Java side (`ITEM_PIPELINES`, priority 400, runs last). The queue/DLQ/retry topology itself is owned and declared by the Java repo (`car-tracker`'s `messaging/ListingsQueueConfig`) — this repo just publishes into it.

### Main-photo perceptual hash (wdxtkg348c)

`image_phash/pipeline.py` (`ITEM_PIPELINES`, priority 300, runs **before** the queue-publish pipeline) subclasses Scrapy's own `ImagesPipeline` rather than downloading with `requests` — its media requests go through the normal downloader stack, so `AntiBlockingMiddleware`'s per-domain token bucket/circuit breaker apply automatically to the image CDN too (`domain_of()` is generic, not hardcoded to the 5 known site domains). Computes `main_image_phash` (64-bit `imagehash.phash`, 16 hex chars) from `main_image_url`, which each spider has to populate itself (source-specific — currently only `mercadolibre_detail.py` does, from the same `Vehicle.image` JSON-LD field it already parses for other fields). **The image CDN is a different host than the site itself** — `mercadolibre_detail.py`'s `allowed_domains` needed `mlstatic.com` added explicitly, confirmed live 2026-09-03: without it, `OffsiteMiddleware` silently drops the image request (`FileException`, no phash, no error pointing at the real cause) even though `main_image_url` extracts fine. Check this first if a new source's phash always comes back null despite a populated `main_image_url`. No image bytes are persisted anywhere beyond `IMAGES_STORE` (an ephemeral local dir, not the S3/MinIO landing zone) — only the hash string survives past `item_completed`.

## Testing conventions

- `tests/test_scheduling.py`, `tests/test_antiblocking.py` use **fakeredis**, not a real Redis, plus mocked `subprocess.run` — no real crawls or Redis connections triggered by the suite.
- `tests/test_metrics.py` uses fakeredis + a mocked `push_to_gateway` — no real HTTP.
- `tests/test_landing.py` uses **moto** (in-memory simulated S3) — not yet verified against the real docker-composed MinIO.
- `tests/test_mercadolibre_detail_spider.py` runs against a real captured fixture (`tests/fixtures/ml_detail_sample*.html`, real MercadoLibre listings), not a synthetic one — prefer this pattern (real fixture over synthetic HTML) when adding parser tests, since ML's actual markup is what breaks in practice.
- Some things are deliberately verified by hand against real infra instead of in CI (e.g. `DiscoveryCandidateTracker` against the real docker-composed Redis, the anti-blocking layer end-to-end against real MercadoLibre with real Telegram delivery) — check the README's "Estado y pendientes" notes per module before assuming something untested-in-CI is unverified.

## Gotchas

- **`requirements.txt` pins `scrapy>=2.13,<2.14`, not an open upper bound.** An earlier unbounded range let Docker (Python 3.12) resolve `2.17.0`, a version where `start_requests()` silently breaks (0 requests, 0 items, no error) while the local venv (Python 3.9, capped lower) kept working fine — a real, previously-hit incident, not a hypothetical. Raise the pin deliberately, one version at a time, verifying both local and Docker resolve the same line.
- Local dev venv runs **Python 3.9** (`.venv`); Docker builds on a newer Python — keep both in mind when using version-gated stdlib/library features.
- `AntiBlockingMiddleware` (350) must stay higher-priority than `LandingZoneMiddleware` (300) — reversing them would let the landing zone capture an intermediate error response instead of the final retried one.
- **`autos.mercadolibre.com.ar` is not an auto-only domain — it's ML's combined "motors" vertical (autos+motos+otros).** Found via `catalog-normalization` (wdxtkg3hjq, 2026-09-02): two real motorcycles (Honda CBR 1000RR, BMW F 800 GS) turned up in `normalization_alias.pending_review`, published by `mercadolibre_detail` as if they were cars. Root cause: `mercadolibre_discovery._abanico_por_modelo` (wdxtkg39v1) searches each high-volume model slug as free text against this domain, and that search isn't scoped by category — an ambiguous slug can surface a motorcycle result. Fixed in `mercadolibre_detail.py` via `_is_car_category()`, which checks `breadcrumb_raw`'s second segment ("Autos y Camionetas" for a real car; confirmed against a real Volkswagen Suran in production) before yielding. Low volume (2 of 262 `pending_review` rows measured, ~0.8%) — this is a defensive filter at Detail time, not a Discovery-side fix, so a wasted Detail request per stray moto still happens.
- **`settings.py` must read env-configurable values via `os.environ.get(name, default)`, never hardcode the localhost default as a bare literal.** `ANTIBLOCK_REDIS_URL` and `LANDING_S3_ENDPOINT_URL` were both hardcoded to their `localhost` fallback (with a comment claiming "configurable by env var" that the code didn't actually implement) — real incident, 2026-08-19: every Discovery/Detail request inside the `scheduler` container failed with `redis.exceptions.ConnectionError` (the anti-blocking circuit breaker check couldn't reach Redis) for a full overnight window, 100% failure rate, silently. `docker-compose.yml` had the right env vars set the whole time; the Python code just never read them. Any new Scrapy setting backed by an env var needs the same `os.environ.get(...)` pattern already used elsewhere in this file — grep `settings.py` for `os.environ` before adding a new one to copy the pattern, don't hand-copy an existing hardcoded line.

## Medir antes de concluir: el script de análisis es el sospechoso número uno

Mucho de lo que se decide acá sale de medir contra el sitio o contra la base
(rendimiento de Discovery, techo de paginación, cobertura por marca), y esas
mediciones vienen de scripts escritos en el momento. Han estado mal varias
veces, y siempre el bug fue del script, no del dato — dos ejemplos reales de la
misma sesión: se afirmó que MercadoLibre no publica un total de resultados
(sí lo publica, en `search.pagination.results_limit`) y que Corolla e Hilux
iban a necesitar cortarse por año para superar el tope (no lo necesitaban).
Las dos salieron de leer `/toyota/corolla` como un filtro marca+modelo cuando
en realidad es una búsqueda de texto por "corolla".

**Esa conclusión de Corolla/Hilux era correcta para esos dos modelos, pero no
generalizaba** — confirmado el 2026-09-06 con Ford Ranger: `/ranger` sola
publica `results_limit: 2000` con un total real de 3.160 (facet BRAND), y
cruzando avisos reales uno por uno ("Ford Ranger 2019 Limited": 14 en ML, solo
5 en nuestra base) se confirmó que los otros 9 nunca habían sido vistos por
ningún Discovery. La diferencia con el intento anterior: esta vez se cruzó
contra `source_listing_key` reales, no contra un agregado. `_abanico_por_anio`
(wdxtkg3j4o) resuelve esto un nivel más abajo que `_abanico_por_modelo`
(wdxtkg39v1), gateado por `anio_fanout_min_volumen` para no pagar el costo en
los modelos que sí entran enteros en su propia consulta (la inmensa mayoría,
Corolla e Hilux incluidos).

- **Cruzar el total contra un conteo independiente.** Un parser que devuelve 0
  se lee idéntico a "no hay nada". Es el modo de falla más caro porque no
  parece un error.
- **Imprimir ejemplos concretos, no solo agregados.** Un promedio no se puede
  auditar; tres URLs con su respuesta sí.
- **Antes de concluir sobre el comportamiento del sitio, confirmarlo con dos
  consultas que deberían diferir.** Si `/toyota/corolla` y `/toyota/hilux`
  devuelven el mismo `results_limit`, el path no está filtrando lo que se cree.
- El `CLAUDE.md` de `car-tracker` tiene la versión larga de esto, con los tres
  casos del 2026-09-02 que costaron un ticket con la premisa invertida.

## ClickUp (project tracking / documentation)

All roadmap work is tracked in ClickUp, not in this repo's issues. Workspace id `90132882707`, project "Car tracker" id `1000480000001469`, list **"Roadmap Técnico"** (all actionable Fase 0/1/2 tasks) id `1000480000002324`.

Key docs (ClickUp Docs, use `clickup_get_document_pages`/`clickup_list_document_pages`):
- **"Plataforma de Data Analytics - Mercado Automotor Argentino"** — the architecture/business doc, id `2ky5d98k-1599`. A local full-text export is also readable directly (faster than the API): `/Users/fabriziopratici/Downloads/Plataforma de Data Analytics - Mercado Automotor Argentino-20260730131851.md`.
- **"Guía de testing — Car Tracker"** — id `2ky5d98k-1659`, the canonical "how do I test this" runbook.

Tasks that landed in this repo specifically: `wdxtkg30nk` (anti-blocking layer), `wdxtkg30nm` (landing zone), `wdxtkg30nj` (Discovery + Detail spiders, MercadoLibre), `wdxtkg35ba` (scheduler/`run_batch.py`), `wdxtkg34th` (per-source Pushgateway metrics), `wdxtkg35bb` (brand auto-discovery — the Discovery-broad/Detail-curated split described above), `wdxtkg39pw` (DeRuedas, third source), `wdxtkg30xr` (multi-source scheduler: parallel per-source execution + Motordil/Autocity), `wdxtkg39qx` (V6 Marketplace + Autocity sources — Kavak split out separately as `wdxtkg3hc5`, not done yet). Autocosmos (`wdxtkg3hc4`) is in progress as of 2026-09-02 — code for it exists locally but is uncommitted. Fetch by id (`clickup_get_task`) for current status/description rather than trusting this file to stay in sync — it won't.

Al hablar con Fabrizio de una tarea (chat, comentarios de ClickUp, resúmenes),
referite a ella por nombre + fase, nunca por el id crudo a secas — ver la
regla completa en el `CLAUDE.md` de `car-tracker`.
