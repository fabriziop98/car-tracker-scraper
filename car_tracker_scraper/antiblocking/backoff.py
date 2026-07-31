"""Backoff exponencial con jitter lognormal (wdxtkg30nk): "NO delay fijo:
2,0 s exactos es la firma mas obvia de un bot". Un delay uniforme tampoco
alcanza - una lognormal tiene cola larga hacia arriba y se agrupa cerca
de la mediana, mas parecido a timing humano real que uniform() o un
`* random()` lineal."""
from __future__ import annotations

import math
import random

RETRYABLE_STATUS_CODES = {403, 429, 503}


def lognormal_delay(median_seconds: float = 3.0, sigma: float = 0.5) -> float:
    mu = math.log(median_seconds)
    return random.lognormvariate(mu, sigma)


def exponential_backoff_with_jitter(retry_times: int, base_seconds: float = 5.0, max_seconds: float = 300.0) -> float:
    """retry_times=0 -> ~base_seconds, retry_times=1 -> ~2x, retry_times=2 -> ~4x, etc.,
    con jitter lognormal aplicado sobre la mediana exponencial (no un delay fijo)."""
    median = min(base_seconds * (2**retry_times), max_seconds)
    return lognormal_delay(median_seconds=median, sigma=0.4)
