# syntax=docker/dockerfile:1
# Scheduler de wdxtkg35ba: corre run_batch.py (Discovery + Detail) via cron,
# sin depender de que Fabrizio se acuerde de correrlo a mano ni de que su
# laptop este prendida (a diferencia de instalarlo en su crontab local).
FROM python:3.12-slim

RUN apt-get update \
	&& apt-get install -y --no-install-recommends cron logrotate \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/output /app/logs

COPY scheduler/logrotate.conf /etc/logrotate.d/car-tracker-batch
RUN chmod 0644 /etc/logrotate.d/car-tracker-batch

# Solo /etc/cron.d, sin `crontab <archivo>`. Se hacian las dos cosas, y la
# segunda instalaba ESTE MISMO archivo como crontab de usuario de root - pero
# el formato de /etc/cron.d lleva un campo de usuario que el de usuario no
# tiene, asi que ahi la linea se interpretaba como el comando
# `root cd /app && python run_batch.py`, que muere en el primer token y corta
# el && . Fallaba en silencio cada 15 min (sin MTA, el mail de cron se
# descarta) mientras el trabajo real lo hacia la copia de /etc/cron.d.
COPY scheduler/crontab /etc/cron.d/car-tracker-scheduler
RUN chmod 0644 /etc/cron.d/car-tracker-scheduler

COPY scheduler/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
