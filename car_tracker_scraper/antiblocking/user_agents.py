"""Pool de personas de navegador COHERENTES (wdxtkg30nk, punto "UA pool
coherente"): User-Agent + Accept-Language + sec-ch-ua* consistentes entre si.

Un UA de Chrome con Accept-Language en ingles, o un UA de Firefox con
headers sec-ch-ua (que Firefox no manda nunca), es una firma de bot mas
obvia que no rotar UA en absoluto. Por eso esto no es una lista de strings
sueltos: cada Persona es un combo verificado consigo mismo.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Persona:
    user_agent: str
    accept_language: str = "es-AR,es;q=0.9"
    sec_ch_ua: str | None = None  # None = navegador que no manda Client Hints (Firefox/Safari)
    sec_ch_ua_mobile: str | None = None
    sec_ch_ua_platform: str | None = None

    def headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
        }
        if self.sec_ch_ua:
            headers["sec-ch-ua"] = self.sec_ch_ua
            headers["sec-ch-ua-mobile"] = self.sec_ch_ua_mobile or "?0"
            headers["sec-ch-ua-platform"] = self.sec_ch_ua_platform or '"macOS"'
        return headers


_CHROMIUM_SEC_CH_UA = '"Not_A Brand";v="8", "Chromium";v="145", "Google Chrome";v="145"'
_EDGE_SEC_CH_UA = '"Not_A Brand";v="8", "Chromium";v="145", "Microsoft Edge";v="145"'

PERSONA_POOL: list[Persona] = [
    # Chrome / macOS - el mismo UA ya confirmado funcionando en Fase 0/1
    Persona(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        ),
        sec_ch_ua=_CHROMIUM_SEC_CH_UA,
        sec_ch_ua_platform='"macOS"',
    ),
    # Chrome / Windows
    Persona(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        ),
        sec_ch_ua=_CHROMIUM_SEC_CH_UA,
        sec_ch_ua_platform='"Windows"',
    ),
    # Edge / Windows
    Persona(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
        ),
        sec_ch_ua=_EDGE_SEC_CH_UA,
        sec_ch_ua_platform='"Windows"',
    ),
    # Firefox / macOS - Firefox NO manda Client Hints, por eso sec_ch_ua=None
    Persona(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
    ),
    # Safari / macOS - tampoco manda Client Hints
    Persona(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.1 Safari/605.1.15"
        ),
    ),
]


def random_persona() -> Persona:
    return random.choice(PERSONA_POOL)
