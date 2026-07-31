"""Corredor del batch grueso, respetando la ventana horaria 2:00-7:00 ART
(wdxtkg30nk, seccion 3.4 del doc de arquitectura: "correr el grueso del
batch entre 2:00 y 7:00 ART - menor carga en el sitio origen, menor
agresividad de las defensas anti-bot").

Pensado para invocarse via cron cada 15-30 min; si se corre fuera de la
ventana, no hace nada (exit 0) en vez de esperar bloqueando - mas simple
de operar y de loguear que un proceso long-running con sleep interno.

Instalar en crontab (ejemplo, cada 20 min):
    */20 * * * * cd /ruta/a/car-tracker-scraper && .venv/bin/python run_batch.py >> logs/batch.log 2>&1

No hay nada desplegado todavia (sin servidor, sin Temporal) - este script
es el mecanismo real, listo para instalar el dia que haya donde correrlo.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ART = ZoneInfo("America/Argentina/Buenos_Aires")
WINDOW_START_HOUR = 2
WINDOW_END_HOUR = 7

MARCAS = ["fiat"]  # ampliar a medida que se sumen mas marcas/fuentes


def in_batch_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(ART)
    return WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR


def main() -> int:
    now = datetime.now(ART)
    if not in_batch_window(now):
        print(f"[run_batch] {now.isoformat()} fuera de la ventana {WINDOW_START_HOUR}-{WINDOW_END_HOUR} ART, no corro nada.")
        return 0

    print(f"[run_batch] {now.isoformat()} dentro de la ventana, corriendo Discovery para: {', '.join(MARCAS)}")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scrapy",
            "crawl",
            "mercadolibre_discovery",
            "-a",
            f"marcas={','.join(MARCAS)}",
            "-a",
            "max_pages=3",
            "-O",
            f"output/discovery_{now.strftime('%Y%m%dT%H%M%S')}.jsonl",
        ]
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
