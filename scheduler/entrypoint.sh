#!/bin/bash
# cron no hereda las variables de entorno que docker-compose le paso al
# contenedor (limitacion conocida de cron, no un bug nuestro) - las volcamos
# a /app/.env, que run_batch.py ya carga solo via load_dotenv() en cada
# invocacion. Se regenera en cada arranque del contenedor, nunca se commitea
# (el build no copia ningun .env real, ver .dockerignore).
set -euo pipefail
printenv | grep -Ev '^(HOME|PATH|PWD|SHLVL|_)=' > /app/.env

mkdir -p /app/logs
touch /app/logs/batch.log

# Un rebuild mata la corrida en vuelo sin que corra el `finally` que suelta el
# lock de fuente, y el TTL de 1h deja esa fuente parada hasta una hora. Como
# este contenedor recien arranca, ninguna corrida suya puede seguir viva.
cd /app && python -c "
import run_batch
liberados = run_batch.release_stale_source_locks()
print('[entrypoint] locks de corrida liberados:', liberados or 'ninguno')
" || echo "[entrypoint] WARN: no se pudieron liberar los locks (Redis caido?) - siguen con su TTL"

echo "[entrypoint] $(date -u +%FT%TZ) scheduler arrancando - cron cada 15 min, 24/7 (sin ventana horaria)"
exec cron -f
