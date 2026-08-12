# syntax=docker/dockerfile:1
# Scheduler de wdxtkg35ba: corre run_batch.py (Discovery + Detail) via cron,
# sin depender de que Fabrizio se acuerde de correrlo a mano ni de que su
# laptop este prendida (a diferencia de instalarlo en su crontab local).
FROM python:3.12-slim

RUN apt-get update \
	&& apt-get install -y --no-install-recommends cron \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/output /app/logs

COPY scheduler/crontab /etc/cron.d/car-tracker-scheduler
RUN chmod 0644 /etc/cron.d/car-tracker-scheduler && crontab /etc/cron.d/car-tracker-scheduler

COPY scheduler/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
