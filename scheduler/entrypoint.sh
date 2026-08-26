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

echo "[entrypoint] $(date -u +%FT%TZ) scheduler arrancando - cron cada 15 min, 24/7 (sin ventana horaria)"
exec cron -f
