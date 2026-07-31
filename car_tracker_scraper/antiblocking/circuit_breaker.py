"""Circuit breaker por fuente (wdxtkg30nk): "si una fuente supera X% de
error en N minutos, pausar 30 min y alertar".

Estado en Redis (compartido entre corridas/procesos):
- `antiblock:cb:{source}:events` - sorted set de eventos (timestamp -> "ok"/"fail"),
  se poda todo lo mas viejo que la ventana N.
- `antiblock:cb:{source}:open_until` - timestamp hasta el que el circuito
  esta abierto (pausado). Ausente/vencido = circuito cerrado (normal).
"""
from __future__ import annotations

import time


def _as_text(value) -> str:
    return value.decode() if isinstance(value, bytes) else value


class CircuitBreaker:
    def __init__(
        self,
        redis_client,
        window_seconds: float = 5 * 60,
        error_threshold: float = 0.3,
        min_samples: int = 10,
        pause_seconds: float = 30 * 60,
        key_prefix: str = "antiblock:cb:",
    ):
        self.redis = redis_client
        self.window_seconds = window_seconds
        self.error_threshold = error_threshold
        self.min_samples = min_samples
        self.pause_seconds = pause_seconds
        self.key_prefix = key_prefix

    def _events_key(self, source: str) -> str:
        return f"{self.key_prefix}{source}:events"

    def _open_until_key(self, source: str) -> str:
        return f"{self.key_prefix}{source}:open_until"

    def is_open(self, source: str) -> bool:
        """True = circuito abierto (pausado), no hacer requests a esta fuente."""
        raw = self.redis.get(self._open_until_key(source))
        if raw is None:
            return False
        return float(raw) > time.time()

    def record(self, source: str, success: bool) -> bool:
        """Registra un resultado. Devuelve True si esto ACABA de abrir el
        circuito (para que quien llama dispare la alerta una sola vez)."""
        now = time.time()
        events_key = self._events_key(source)
        member = f"{now}:{'ok' if success else 'fail'}"
        self.redis.zadd(events_key, {member: now})
        self.redis.zremrangebyscore(events_key, 0, now - self.window_seconds)
        self.redis.expire(events_key, int(self.window_seconds * 2))

        events = self.redis.zrange(events_key, 0, -1)
        total = len(events)
        if total < self.min_samples:
            return False

        fails = sum(1 for e in events if _as_text(e).endswith(":fail"))
        error_rate = fails / total
        if error_rate > self.error_threshold and not self.is_open(source):
            self.redis.set(self._open_until_key(source), now + self.pause_seconds)
            return True
        return False

    def close(self, source: str) -> None:
        """Cierra el circuito manualmente (ej. despues de confirmar recuperacion)."""
        self.redis.delete(self._open_until_key(source))
