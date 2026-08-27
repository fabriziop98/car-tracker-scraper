"""Corrida manual puntual de Discovery+Detail para todas las fuentes.

Replica un tick de run_batch.main() sin pasar por cron. No es parte del camino
de produccion - existe para validar un fix DENTRO del container real, que es
donde ANTIBLOCK_REDIS_URL/CAR_TRACKER_API_URL resuelven distinto que en el host
(ver CLAUDE.md). Nunca editar run_batch.py para saltearse algo: agregarlo aca.

wdxtkg30xr: antes salteaba el gate de ventana horaria 2:00-7:00 ART, que dejo
de existir en wdxtkg30xr. Hoy es simplemente "corre el tick ahora", con el
mismo respeto por el gate de DISCOVERY_INTERVAL por fuente que tiene el tick
real - si Discovery de una fuente no vencio todavia, no se fuerza.

Uso: .venv/bin/python run_manual_batch.py
"""
from datetime import datetime

import redis

import run_batch


def main() -> None:
    now = datetime.now(run_batch.ART)
    redis_client = redis.Redis.from_url(run_batch.REDIS_URL)

    for source in run_batch.SOURCES:
        print(f"\n===== {source.slug} =====")
        try:
            run_batch.run_source(now, redis_client, source)
        except Exception as exc:  # noqa: BLE001 - mismo aislamiento por fuente que run_batch.main
            print(f"[run_manual_batch] [{source.slug}] fallo inesperado: {exc!r}")


if __name__ == "__main__":
    main()
