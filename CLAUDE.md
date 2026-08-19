# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`car-tracker-scraper` is the Python/Scrapy scraper for **Car Tracker**, a used-car price-tracking data platform for the Argentine market. Fabrizio is the solo founder/architect; he does not write code himself and relies on Claude Code for all implementation, focusing his own time on architecture-level decisions. Treat every change as something he will only ever audit via `/code-review`, not by reading the diff line by line — real tests against real infra, documented "why" (not just "what"), and consistent conventions matter more here than in a typical repo.

**Fabrizio is a senior Java/Spring backend engineer but is new to Python, Scrapy, and this repo's DevOps side (Docker, Redis, cron).** When explaining anything here, lead with a plain explanation and a Java-world analogy where one helps (e.g. "a Scrapy downloader middleware is like a Servlet Filter", "the circuit breaker here is the same pattern as Resilience4j/Hystrix", "the token bucket is the same idea as Bucket4j/`@RateLimiter`") — don't assume Python/Scrapy familiarity the way you could in the sibling `car-tracker` repo.

The companion repo is **`car-tracker`** (Java/Spring Boot), usually checked out as a sibling directory. By design the two repos **only talk over RabbitMQ** (this repo publishes, that one consumes) plus one narrow read-only HTTP bridge (`CAR_TRACKER_API_URL`, see below) — never a shared database, never a shared repo. See that repo's own `CLAUDE.md` for its side.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                                   # then fill in real values, .env is gitignored

python -m pytest tests/ -v                              # full test suite
python -m pytest tests/test_scheduling.py -v             # single test file
python -m pytest tests/test_scheduling.py::test_name -v  # single test

scrapy crawl mercadolibre_discovery -a marcas=fiat,ford -a max_pages=3 -O output/discovery_%(time)s.jsonl
scrapy crawl mercadolibre_detail -a urls_file=urls.txt -O output/detail_%(time)s.jsonl
python run_batch.py                                     # scheduler tick: Discovery refresh + a Detail batch
```

Requires the `car-tracker` repo's `docker compose` infra running alongside it: Redis (token bucket, circuit breaker, discovery-candidate tracking), RabbitMQ (publish target), MinIO (landing zone), Prometheus Pushgateway (metrics), and the `app` service (curated/discovered brand lookups). Nothing in this repo talks to Postgres directly, ever — that's a hard architectural boundary, not an oversight.

## Architecture

### Discovery vs. Detail — two different costs, two different scopes

- **Discovery** (`spiders/mercadolibre_discovery.py`) walks a brand's paginated listing pages, filters ads and 0km units, and finds new listing URLs. Cheap, meant to run broad — every brand DNRPA reports selling in Argentina above a volume floor (`GET {CAR_TRACKER_API_URL}/api/brands/discovered`), not just the ones with a built-out canonical catalog.
- **Detail** (`spiders/mercadolibre_detail.py`) fetches the full listing page for URLs Discovery already found, and is what actually publishes to RabbitMQ. Expensive, so it's deliberately scoped narrower: only brands promoted into the canonical catalog (`GET {CAR_TRACKER_API_URL}/api/brands/curated`). Running Detail against an uncurated brand just fills the Java side's `pending_review` normalization queue with things that can never resolve. **This Discovery-broad/Detail-narrow split is load-bearing** — it was originally half-implemented (Discovery fixed, Detail still unscoped) and the gap was found and closed; don't reintroduce it when touching brand-related scraping logic. If the curated-marcas fetch fails, Detail skips the tick entirely rather than running unfiltered (fail-safe, same principle as everywhere else in the scheduler).
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

Meant to be installed in cron every 15-20 min (`run_batch.py`'s docstring has the exact line). Only runs the heavy batch inside the 2:00-7:00 ART window (lower site load, less aggressive anti-bot defenses) — outside it, a tick is a no-op. Each tick: Discovery refreshes every `DISCOVERY_INTERVAL_HOURS` (5h default) and every non-ad URL found gets recorded in `DiscoveryCandidateTracker` (Redis sorted set `scheduling:discovery_candidates` — this is crawl strategy, deliberately kept in Redis, never Postgres, since it's not business data); Detail takes a bounded batch (`DETAIL_BATCH_SIZE`, 400 default) of candidates overdue by `DETAIL_TIER_HOURS` (72h default), never-detailed candidates always go first. Single tier for now — A/B/C prioritization by real per-model volume is deferred until that data exists; don't block the scheduler on building that first.

### Metrics (`car_tracker_scraper/observability/metrics.py`)

The scraper runs as short-lived batch jobs (spiders triggered by Discovery/Detail), not a long-running server, so Prometheus can't pull-scrape it — metrics go out via **Pushgateway** instead (`SourceMetricsExtension`, a Scrapy extension hooked to `spider_opened`/`response_received`/`item_scraped`/`spider_closed`, pushes on spider close). `METRICS_PUSHGATEWAY_URL` empty disables it without breaking the spider. Success rate is read from `CircuitBreaker.stats()` (doesn't duplicate that counting); 403/429 rate is counted separately since the circuit breaker only tracks ok/fail booleans; new-vs-known reuses the same `DiscoveryCandidateTracker` Redis set the scheduler already maintains. The corresponding dashboard is a row in `car-tracker`'s existing Grafana dashboard, not a separate one.

### Queue publishing

`queue_publish/pipeline.py` + `publisher.py` is the only channel this repo uses to hand data to the Java side (`ITEM_PIPELINES`, priority 400, runs last). The queue/DLQ/retry topology itself is owned and declared by the Java repo (`car-tracker`'s `messaging/ListingsQueueConfig`) — this repo just publishes into it.

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

## ClickUp (project tracking / documentation)

All roadmap work is tracked in ClickUp, not in this repo's issues. Workspace id `90132882707`, project "Car tracker" id `1000480000001469`, list **"Roadmap Técnico"** (all actionable Fase 0/1/2 tasks) id `1000480000002324`.

Key docs (ClickUp Docs, use `clickup_get_document_pages`/`clickup_list_document_pages`):
- **"Plataforma de Data Analytics - Mercado Automotor Argentino"** — the architecture/business doc, id `2ky5d98k-1599`. A local full-text export is also readable directly (faster than the API): `/Users/fabriziopratici/Downloads/Plataforma de Data Analytics - Mercado Automotor Argentino-20260730131851.md`.
- **"Guía de testing — Car Tracker"** — id `2ky5d98k-1659`, the canonical "how do I test this" runbook.

Tasks that landed in this repo specifically: `wdxtkg30nk` (anti-blocking layer), `wdxtkg30nm` (landing zone), `wdxtkg30nj` (Discovery + Detail spiders), `wdxtkg35ba` (scheduler/`run_batch.py`), `wdxtkg34th` (per-source Pushgateway metrics), `wdxtkg35bb` (brand auto-discovery — the Discovery-broad/Detail-curated split described above). Fetch by id (`clickup_get_task`) for current status/description rather than trusting this file to stay in sync — it won't.
