"""Alertas de circuit breaker via Telegram (wdxtkg30nk, punto "circuit
breaker: si una fuente supera X% de error en N minutos, pausar 30 min y
alertar"). Credenciales por variable de entorno, nunca hardcodeadas -
ver .env.example."""
from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def send_telegram_alert(text: str) -> bool:
    """Manda `text` al chat configurado. Devuelve False (y loguea, no
    levanta excepcion) si falta config o falla el request - una alerta
    caida no debe tumbar el spider."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configurados - alerta no enviada: %s", text)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as resp:
            return resp.status == 200
    except urllib.error.URLError:
        logger.exception("No se pudo enviar la alerta de Telegram")
        return False
