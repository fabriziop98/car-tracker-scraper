"""Corrida manual puntual de Discovery+Detail, salteando solo el gate de
ventana horaria (2:00-7:00 ART) de run_batch.py sin modificar ese archivo.
Ver CLAUDE.md / guia de testing en ClickUp para el contexto completo.

Uso: .venv/bin/python run_manual_batch.py
"""
from datetime import datetime

import redis

import run_batch


def main() -> None:
    now = datetime.now(run_batch.ART)
    redis_client = redis.Redis.from_url(run_batch.REDIS_URL)
    tracker = run_batch.DiscoveryCandidateTracker(redis_client)

    if run_batch.seconds_since_last_discovery(redis_client) >= run_batch.DISCOVERY_INTERVAL_HOURS * 3600:
        discovery_rc = run_batch.run_discovery(now, tracker)
        if discovery_rc == 0:
            run_batch.mark_discovery_ran(redis_client, when=now.timestamp())
    else:
        print("Discovery corrida hace menos de 5h, no toca todavia.")

    run_batch.run_detail(now, tracker)


if __name__ == "__main__":
    main()
