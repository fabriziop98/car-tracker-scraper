"""Corredor del batch grueso, respetando la ventana horaria 2:00-7:00 ART
(wdxtkg30nk, seccion 3.4 del doc de arquitectura: "correr el grueso del
batch entre 2:00 y 7:00 ART - menor carga en el sitio origen, menor
agresividad de las defensas anti-bot").

wdxtkg35ba: ademas de Discovery, ahora tambien corre Detail - autolimitado
por DiscoveryCandidateTracker (Redis, car_tracker_scraper/scheduling/state.py)
en vez de depender de que alguien extraiga URLs a mano de un .jsonl suelto.
Discovery refresca el catalogo de candidatos cada DISCOVERY_INTERVAL_HOURS;
Detail procesa un batch acotado (DETAIL_BATCH_SIZE) de lo que esta vencido
en cada tick. Pensado para invocarse cada 15-20 min (igual que antes) - la
mayoria de los ticks no hacen nada o un batch chico, el trabajo pesado se
reparte solo a lo largo de la ventana en vez de una corrida gigante.

Tier unico por ahora (DETAIL_TIER_HOURS): la diferenciacion A/B/C por
volumen real (seccion 3.4 del doc) queda para cuando haya datos reales de
que modelos tienen mas volumen - no bloquear el scheduler en resolver eso
primero (ver wdxtkg35ba).

wdxtkg35bb: las marcas de Discovery ya no estan hardcodeadas aca - se piden
en cada corrida a GET {CAR_TRACKER_API_URL}/api/brands/discovered (Java,
`car-tracker`), que a su vez las persiste desde transferencias reales DNRPA
(datos.gob.ar), no desde un facet de MercadoLibre. El scraper sigue sin
conectarse nunca directo a Postgres - este endpoint es el unico puente en
esa direccion, mismo principio que RabbitMQ/S3 en la direccion opuesta.

Instalar en crontab (ejemplo, cada 20 min):
    */20 * * * * cd /ruta/a/car-tracker-scraper && .venv/bin/python run_batch.py >> logs/batch.log 2>&1

No hay nada desplegado todavia (sin servidor, sin Temporal) - este script
es el mecanismo real, listo para instalar el dia que haya donde correrlo.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import redis
from dotenv import load_dotenv

from car_tracker_scraper.scheduling.state import (
    DiscoveryCandidateTracker,
    mark_discovery_ran,
    seconds_since_last_discovery,
)

load_dotenv()  # cron no carga .env solo - a diferencia de scrapy crawl, que lo hace via settings.py

ART = ZoneInfo("America/Argentina/Buenos_Aires")
WINDOW_START_HOUR = 2
WINDOW_END_HOUR = 7

CAR_TRACKER_API_URL = os.environ.get("CAR_TRACKER_API_URL", "http://localhost:8080")

DISCOVERY_MAX_PAGES = 3
DISCOVERY_INTERVAL_HOURS = 5  # seccion 3.4 del doc: cada 4-6h

DETAIL_TIER_HOURS = 72
DETAIL_BATCH_SIZE = 400  # lo que entra en un tick de ~15-20 min al ritmo del token bucket (~0.5 req/s)

REDIS_URL = os.environ.get("ANTIBLOCK_REDIS_URL", "redis://localhost:6379/0")

OUTPUT_DIR = Path("output")


def in_batch_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(ART)
    return WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR


def fetch_discovered_marcas() -> list[str]:
    """GET /api/brands/discovered (car-tracker, wdxtkg35bb) - unico puente hacia
    Postgres en esta direccion, nunca una conexion directa a la DB desde aca.
    Vacio (API caida, o brand_discovery todavia sin datos del job DNRPA) se
    trata igual que cualquier fuente caida en este proyecto: no se cachea ni
    se hardcodea un fallback, se loguea y se reintenta en el proximo tick."""
    url = f"{CAR_TRACKER_API_URL}/api/brands/discovered"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[run_batch] No se pudo obtener marcas descubiertas de {url}: {exc}")
        return []
    return [item["slug"] for item in payload]


def run_discovery(now: datetime, tracker: DiscoveryCandidateTracker) -> int:
    marcas = fetch_discovered_marcas()
    if not marcas:
        print(f"[run_batch] {now.isoformat()} sin marcas descubiertas todavia "
              f"({CAR_TRACKER_API_URL} no responde o brand_discovery esta vacia) - "
              "salteo este Discovery, reintento en el proximo tick.")
        return 1

    print(f"[run_batch] {now.isoformat()} Discovery para: {', '.join(marcas)}")
    output_path = OUTPUT_DIR / f"discovery_{now.strftime('%Y%m%dT%H%M%S')}.jsonl"
    result = subprocess.run([
        sys.executable, "-m", "scrapy", "crawl", "mercadolibre_discovery",
        "-a", f"marcas={','.join(marcas)}",
        "-a", f"max_pages={DISCOVERY_MAX_PAGES}",
        "-O", str(output_path),
    ])
    if result.returncode != 0:
        print(f"[run_batch] Discovery termino con returncode={result.returncode}, no actualizo el tracker de candidatos")
        return result.returncode

    discovered = _record_discovered(output_path, tracker)
    print(f"[run_batch] {discovered} candidatos nuevos/conocidos registrados (total conocido: {tracker.known_count()})")
    return 0


def _record_discovered(jsonl_path: Path, tracker: DiscoveryCandidateTracker) -> int:
    count = 0
    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("is_ad"):
                continue
            url = item.get("url")
            if url:
                tracker.record_discovered(url)
                count += 1
    return count


def run_detail(now: datetime, tracker: DiscoveryCandidateTracker) -> int:
    urls = tracker.due_for_detail(older_than_seconds=DETAIL_TIER_HOURS * 3600, limit=DETAIL_BATCH_SIZE)
    if not urls:
        print("[run_batch] Ningun candidato vencido para Detail en este tick.")
        return 0

    urls_path = OUTPUT_DIR / f"detail_batch_{now.strftime('%Y%m%dT%H%M%S')}.txt"
    urls_path.write_text("\n".join(urls) + "\n", encoding="utf-8")

    print(f"[run_batch] Detail sobre {len(urls)} candidatos vencidos.")
    result = subprocess.run([
        sys.executable, "-m", "scrapy", "crawl", "mercadolibre_detail",
        "-a", f"urls_file={urls_path}",
        "-O", str(OUTPUT_DIR / f"detail_{now.strftime('%Y%m%dT%H%M%S')}.jsonl"),
    ])
    # Se marcan como "detallados" haya o no fallado algun item puntual - un
    # item que falla se retoma solo en el proximo ciclo del mismo tier
    # (DETAIL_TIER_HOURS), no hace falta tracking de exito/fracaso por URL.
    tracker.mark_detailed(urls, when=now.timestamp())
    return result.returncode


def main() -> int:
    now = datetime.now(ART)
    if not in_batch_window(now):
        print(f"[run_batch] {now.isoformat()} fuera de la ventana {WINDOW_START_HOUR}-{WINDOW_END_HOUR} ART, no corro nada.")
        return 0

    redis_client = redis.Redis.from_url(REDIS_URL)
    tracker = DiscoveryCandidateTracker(redis_client)

    exit_code = 0
    if seconds_since_last_discovery(redis_client) >= DISCOVERY_INTERVAL_HOURS * 3600:
        discovery_rc = run_discovery(now, tracker)
        exit_code = discovery_rc or exit_code
        if discovery_rc == 0:
            # Solo si salio bien - una falla transitoria reintenta en el proximo
            # tick (15-20 min) en vez de esperar DISCOVERY_INTERVAL_HOURS enteras.
            mark_discovery_ran(redis_client, when=now.timestamp())
    else:
        print(f"[run_batch] Discovery corrida hace menos de {DISCOVERY_INTERVAL_HOURS}h, no toca todavia.")

    exit_code = run_detail(now, tracker) or exit_code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
