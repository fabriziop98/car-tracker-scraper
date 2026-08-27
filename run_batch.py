"""Corredor del batch grueso (wdxtkg30nk, seccion 3.4 del doc de arquitectura).

wdxtkg30xr (2026-08-25): se saco la ventana horaria 2:00-7:00 ART que existia
ademas del token bucket/circuit breaker - dos mecanismos redundantes de
"no pegarle fuerte al sitio origen", y el gate horario era el que mas
limitaba throughput real (5h/dia de las 24 disponibles) camino a la meta de
Fase 1 (100k+ registros). El token bucket (`ANTIBLOCK_TOKEN_BUCKET_REFILL_PER_SEC`,
ahora 1.0 req/s) sigue siendo el limite real de agresividad - decision de
negocio de Fabrizio, igual que ROBOTSTXT_OBEY (ver settings.py). Si el
circuit breaker empieza a abrirse seguido, esa es la señal de bajar el rate
antes que reintroducir una ventana horaria.

wdxtkg35ba: ademas de Discovery, ahora tambien corre Detail - autolimitado
por DiscoveryCandidateTracker (Redis, car_tracker_scraper/scheduling/state.py)
en vez de depender de que alguien extraiga URLs a mano de un .jsonl suelto.
Discovery refresca el catalogo de candidatos cada DISCOVERY_INTERVAL_HOURS;
Detail procesa un batch acotado (DETAIL_BATCH_SIZE) de lo que esta vencido
en cada tick. Pensado para invocarse cada 15-20 min (igual que antes) - la
mayoria de los ticks no hacen nada o un batch chico, el trabajo pesado se
reparte solo a lo largo de la ventana en vez de una corrida gigante.

wdxtkg398b (2026-08-26): Discovery ya no corta en una profundidad fija por
marca, pagina hasta agotar el inventario real de ML (ver
mercadolibre_discovery.py). El runtime de Discovery por tick deja de ser
tan predecible como con max_pages=3, pero el riesgo de que eso choque con
el tick de 15 min de cron es acotado: (1) Discovery solo corre una vez
cada DISCOVERY_INTERVAL_HOURS (5h), no en cada tick, asi que un tick largo
no repite en el siguiente; (2) run_discovery() es sincronico dentro de
main(), asi que si corre mas de 15-20 min si puede solapar con la proxima
invocacion de cron (no hay lock file) - pero seconds_since_last_discovery()
solo se actualiza al terminar OK, asi que el tick solapado no dispara un
segundo Discovery, como mucho corre Detail en paralelo compitiendo por el
mismo token bucket compartido (correcto, no hay condicion de carrera real:
ambos respetan el mismo limite en Redis). Si el circuit breaker empieza a
abrirse mas seguido tras este cambio, esa es la señal de revisar esto, no
de reintroducir un tope chico arbitrario.

wdxtkg30xr (2026-08-26): el scheduler dejo de estar atado a una sola fuente.
`SOURCES` es el registro de fuentes activas (hoy MercadoLibre + Motordil) y
main() itera sobre el; cada una tiene su propio keyspace de candidatos en
Redis, su propia cadencia de Discovery y su propio tamanio de batch de Detail
(ver SourceConfig). Una fuente que falla no aborta las demas - todo el punto
de tener mas de una es no depender de ninguna en particular (regla del doc:
ninguna fuente por encima del 60% del dataset).

wdxtkg39qx: las fuentes corren EN PARALELO (una por thread), no secuencial.
El token bucket es POR DOMINIO, asi que dos sitios distintos nunca compiten
entre si - correrlas en serie era una limitacion autoimpuesta sin beneficio.
El presupuesto del tick pasa a ser el MAXIMO de las fuentes, no la suma, que
es lo que permite sumar sitios lentos (Kavak pide Crawl-delay: 20).

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

Con Discovery corriendo amplio (todas las marcas DNRPA sobre el umbral, no
solo las curadas), Detail - la parte cara, fetch completo + publicacion real
a RabbitMQ - se queda acotado a GET {CAR_TRACKER_API_URL}/api/brands/curated
(`SELECT slug FROM brand`, diseño original confirmado con Fabrizio): correr
Detail sobre una marca sin catalogo solo llena `pending_review` sin poder
resolverse nunca. Si ese endpoint falla, Detail se saltea el tick entero en
vez de correr sin filtro - mismo criterio "fail-safe" que el resto del scheduler.

Instalar en crontab (ejemplo, cada 15-20 min, sin restriccion horaria):
    */15 * * * * cd /ruta/a/car-tracker-scraper && .venv/bin/python run_batch.py >> logs/batch.log 2>&1
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import redis
from dotenv import load_dotenv

from car_tracker_scraper.scheduling.state import (
    DiscoveryCandidateTracker,
    mark_discovery_ran_for,
    seconds_since_last_discovery_for,
)

load_dotenv()  # cron no carga .env solo - a diferencia de scrapy crawl, que lo hace via settings.py

ART = ZoneInfo("America/Argentina/Buenos_Aires")

CAR_TRACKER_API_URL = os.environ.get("CAR_TRACKER_API_URL", "http://localhost:8080")

DISCOVERY_MAX_PAGES = 30  # wdxtkg398b: ya no es el corte real por marca (eso lo
# hace el spider solo, siguiendo pagination_nodes_url hasta que ML no ofrezca
# ninguna pagina con value mayor a la actual - confirmado 2026-08-26 contra
# Toyota real, ver mercadolibre_discovery.py). Esto es solo el backstop de
# seguridad por si ese calculo no converge nunca.

DETAIL_TIER_HOURS = 72

REDIS_URL = os.environ.get("ANTIBLOCK_REDIS_URL", "redis://localhost:6379/0")

OUTPUT_DIR = Path("output")


@dataclass(frozen=True)
class SourceConfig:
    """Una fuente scrapeable (wdxtkg30xr).

    El tamanio de batch de Detail es POR FUENTE, no una constante global.
    Desde wdxtkg39qx las fuentes corren en PARALELO (ver main()), asi que lo
    que tiene que entrar en el tick de cron es el tiempo estimado de CADA
    fuente por separado, no la suma de todas - por eso agregar una fuente
    lenta ya no obliga a achicarle el batch a las demas.
    """

    slug: str
    discovery_spider: str
    detail_spider: str
    discovery_interval_hours: int
    detail_batch_size: int
    # Segundos que cuesta una request de esta fuente. Por defecto 1s, que es el
    # ritmo del token bucket (ANTIBLOCK_TOKEN_BUCKET_REFILL_PER_SEC=1.0, por
    # dominio). Una fuente con DOWNLOAD_DELAY propio cuesta mas y hay que
    # decirlo aca, o el presupuesto del tick queda mal calculado: DeRuedas con
    # delay 5 tarda 5x lo que sugiere su batch_size.
    seconds_per_request: float = 1.0

    @property
    def estimated_detail_seconds(self) -> float:
        return self.detail_batch_size * self.seconds_per_request


SOURCES = (
    SourceConfig(
        slug="mercadolibre",
        discovery_spider="mercadolibre_discovery",
        detail_spider="mercadolibre_detail",
        discovery_interval_hours=5,  # seccion 3.4 del doc: cada 4-6h
        detail_batch_size=500,
    ),
    SourceConfig(
        slug="motordil",
        discovery_spider="motordil_discovery",
        detail_spider="motordil_detail",
        discovery_interval_hours=5,
        # Medido en la primera corrida real (2026-08-27): el inventario entero
        # de Motordil son ~5.400 avisos y se drenaron en una noche. En regimen
        # estable (re-Detail cada DETAIL_TIER_HOURS) necesita ~20 por tick, asi
        # que 200 sobra de lejos.
        detail_batch_size=200,
    ),
    SourceConfig(
        slug="deruedas",
        discovery_spider="deruedas_discovery",
        detail_spider="deruedas_detail",
        discovery_interval_hours=5,
        # El mas chico de los tres a proposito: los spiders de DeRuedas suman
        # DOWNLOAD_DELAY=5 propio para respetar el Crawl-delay que pide su
        # robots.txt, asi que cada request cuesta ~5s en vez de ~1s. Con 40
        # avisos son ~150s: deja ~100s de margen sobre el tick de 15 min una vez
        # descontados ML (500s) y Motordil (200s). El margen es a proposito - el
        # calculo asume paceo perfecto y sin reintentos.
        detail_batch_size=30,
        seconds_per_request=5.0,
    ),
)


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


def fetch_curated_marcas() -> list[str]:
    """GET /api/brands/curated (car-tracker, wdxtkg35bb) - equivalente a
    `SELECT slug FROM brand`, sin conectarse directo a Postgres. Usado por
    run_detail para no correr el fetch caro sobre marcas sin catalogo
    todavia. Vacio (API caida) se trata igual que fetch_discovered_marcas:
    no se cachea ni se asume un fallback, se loguea y se reintenta despues."""
    url = f"{CAR_TRACKER_API_URL}/api/brands/curated"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[run_batch] No se pudo obtener marcas curadas de {url}: {exc}")
        return []
    return list(payload)


def run_discovery(now: datetime, tracker: DiscoveryCandidateTracker, source: SourceConfig) -> int:
    marcas = fetch_discovered_marcas()
    if not marcas:
        # brand_discovery se puebla via BrandDiscoveryJob (car-tracker, Java,
        # semanal - lunes 4am ART). Hasta su primera corrida (o si el job/API
        # esta caido) cae a las marcas curadas en vez de bloquear Discovery
        # por completo - una vez que DNRPA tenga datos, la lista amplia vuelve
        # a tomar precedencia sola (wdxtkg35bb).
        marcas = fetch_curated_marcas()
        if not marcas:
            print(f"[run_batch] {now.isoformat()} sin marcas descubiertas ni curadas "
                  f"todavia ({CAR_TRACKER_API_URL} no responde o ambas APIs vacias) - "
                  "salteo este Discovery, reintento en el proximo tick.")
            return 1
        print(f"[run_batch] {now.isoformat()} brand_discovery vacio (DNRPA todavia no "
              "corrio) - caigo a marcas curadas para este Discovery.")

    print(f"[run_batch] {now.isoformat()} [{source.slug}] Discovery para: {', '.join(marcas)}")
    output_path = OUTPUT_DIR / f"discovery_{source.slug}_{now.strftime('%Y%m%dT%H%M%S')}.jsonl"
    result = subprocess.run([
        sys.executable, "-m", "scrapy", "crawl", source.discovery_spider,
        "-a", f"marcas={','.join(marcas)}",
        "-a", f"max_pages={DISCOVERY_MAX_PAGES}",
        "-O", str(output_path),
    ])
    if result.returncode != 0:
        print(f"[run_batch] [{source.slug}] Discovery termino con returncode={result.returncode}, no actualizo el tracker de candidatos")
        return result.returncode

    discovered = _record_discovered(output_path, tracker)
    print(f"[run_batch] [{source.slug}] {discovered} candidatos nuevos/conocidos registrados (total conocido: {tracker.known_count()})")
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
                tracker.record_discovered(url, marca=item.get("marca"))
                count += 1
    return count


def run_detail(now: datetime, tracker: DiscoveryCandidateTracker, source: SourceConfig) -> int:
    curated_marcas = fetch_curated_marcas()
    if not curated_marcas:
        print(f"[run_batch] {now.isoformat()} [{source.slug}] sin marcas curadas todavia "
              f"({CAR_TRACKER_API_URL} no responde o brand esta vacia) - "
              "salteo Detail este tick en vez de correr sin filtro.")
        return 1

    urls = tracker.due_for_detail(older_than_seconds=DETAIL_TIER_HOURS * 3600, limit=source.detail_batch_size, allowed_marcas=set(curated_marcas))
    if not urls:
        print(f"[run_batch] [{source.slug}] Ningun candidato vencido de marca curada para Detail en este tick.")
        return 0

    urls_path = OUTPUT_DIR / f"detail_batch_{source.slug}_{now.strftime('%Y%m%dT%H%M%S')}.txt"
    urls_path.write_text("\n".join(urls) + "\n", encoding="utf-8")

    print(f"[run_batch] [{source.slug}] Detail sobre {len(urls)} candidatos vencidos.")
    output_path = OUTPUT_DIR / f"detail_{source.slug}_{now.strftime('%Y%m%dT%H%M%S')}.jsonl"
    result = subprocess.run([
        sys.executable, "-m", "scrapy", "crawl", source.detail_spider,
        "-a", f"urls_file={urls_path}",
        "-O", str(output_path),
    ])
    if output_path.exists() and output_path.stat().st_size > 0:
        # Se marcan como "detallados" haya o no fallado algun item puntual - un
        # item que falla se retoma solo en el proximo ciclo del mismo tier
        # (DETAIL_TIER_HOURS), no hace falta tracking de exito/fracaso por URL.
        tracker.mark_detailed(urls, when=now.timestamp())
    else:
        # Falla total (0 items) en vez de fallos puntuales - scrapy suele
        # devolver returncode 0 igual (IgnoreRequest no es un crash), asi
        # que el chequeo real es el archivo de salida, no el returncode.
        # No marcar nada: mejor reintentar el proximo tick que perder estos
        # candidatos 72h (DETAIL_TIER_HOURS) por una falla de infraestructura
        # (incidente real 2026-08-19: settings.py con ANTIBLOCK_REDIS_URL
        # hardcodeado a localhost tumbo el circuit breaker toda la ventana).
        print(f"[run_batch] [{source.slug}] Detail no produjo ningun item - no marco estos "
              "candidatos como detallados, se reintentan en el proximo tick.")
    return result.returncode


def run_source(now: datetime, redis_client, source: SourceConfig) -> int:
    """Un tick completo (Discovery si vencio + Detail) para UNA fuente."""
    tracker = DiscoveryCandidateTracker.for_source(redis_client, source.slug)

    exit_code = 0
    if seconds_since_last_discovery_for(redis_client, source.slug) >= source.discovery_interval_hours * 3600:
        discovery_rc = run_discovery(now, tracker, source)
        exit_code = discovery_rc or exit_code
        if discovery_rc == 0:
            # Solo si salio bien - una falla transitoria reintenta en el proximo
            # tick (15-20 min) en vez de esperar discovery_interval_hours enteras.
            mark_discovery_ran_for(redis_client, source.slug, when=now.timestamp())
    else:
        print(f"[run_batch] [{source.slug}] Discovery corrida hace menos de "
              f"{source.discovery_interval_hours}h, no toca todavia.")

    return run_detail(now, tracker, source) or exit_code


def main() -> int:
    now = datetime.now(ART)
    redis_client = redis.Redis.from_url(REDIS_URL)

    # wdxtkg39qx: las fuentes corren EN PARALELO, una por thread.
    #
    # Antes corrian secuencialmente y eso ponia un techo GLOBAL al tick: la
    # suma de todos los batches tenia que entrar en los 15 min de cron (con 3
    # fuentes ya iba 850s de 900s). Ese techo era una limitacion autoimpuesta
    # SIN beneficio: el token bucket y el circuit breaker son POR DOMINIO (la
    # clave sale de domain_of(url), ver antiblocking/token_bucket.py), asi que
    # dos spiders de sitios distintos nunca compiten por el mismo presupuesto
    # de requests. Correrlas en paralelo no nos hace ni un poco mas agresivos
    # con ningun sitio - solo deja de desperdiciar tiempo de pared esperando a
    # la fuente anterior.
    #
    # Con esto el presupuesto del tick pasa a ser el MAXIMO de las fuentes en
    # vez de la suma, que es lo que hace viable sumar sitios lentos (Kavak pide
    # Crawl-delay: 20, o sea 20s por request).
    #
    # Threads y no procesos: cada run_source es I/O bound (subprocess.run
    # esperando al crawl, mas HTTP y Redis), asi que el GIL se libera. El
    # cliente de redis-py es thread-safe (pool de conexiones) y cada fuente
    # escribe en su propio keyspace y en sus propios archivos de output, asi
    # que no comparten estado mutable.
    exit_code = 0
    with ThreadPoolExecutor(max_workers=len(SOURCES), thread_name_prefix="source") as pool:
        futures = {pool.submit(run_source, now, redis_client, source): source for source in SOURCES}
        for future in as_completed(futures):
            source = futures[future]
            # Una fuente caida (sitio bloqueando, formato cambiado, spider
            # roto) no puede impedir que las demas corran - el objetivo entero
            # de tener mas de una fuente (wdxtkg30xr) es no depender de
            # ninguna en particular.
            try:
                exit_code = future.result() or exit_code
            except Exception as exc:  # noqa: BLE001 - aislamiento deliberado por fuente
                print(f"[run_batch] [{source.slug}] fallo inesperado, sigo con las demas fuentes: {exc!r}")
                exit_code = exit_code or 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
