"""Captura los fixtures de DeRuedas para wdxtkg39pw - CORRELO VOS, no Claude.

Por que no lo corre Claude Code: el robots.txt de deruedas.com.ar bloquea
`ClaudeBot`/`Claude-User` explicitamente (`Disallow: /`), igual que el de
MercadoLibre. Ese bloqueo es sobre la identidad de Claude cuando pide el sitio
directo, no sobre el scraper del proyecto (que usa su propio UA, decision de
negocio ya tomada en settings.py). Con Motordil se hizo una excepcion puntual
porque su robots.txt es permisivo; aca no aplica.

No es parte del pipeline (nada lo importa) - es un one-off para dejar los
fixtures que despues usan los tests del parser.

Captura tres cosas:
  1. busCraw.asp pagina 1  -> forma real de las tarjetas (microdata schema.org)
  2. busCraw.asp pagina 2  -> confirma el tamanio de pagina y que pag= funciona
                              (quedo sin confirmar en el reverse engineering de
                              Fase 0, es uno de los puntos abiertos del ticket)
  3. la ficha de un aviso  -> Fase 0 NUNCA la reverse-engineerio, es formato
                              desconocido. El `cod` sale automatico de la p.1.

Ademas prueba el endpoint SIN el header Referer, que es el otro punto abierto
de la ficha ("confirmar si el server lo exige").

Uso:
    .venv/bin/python capture_deruedas_fixtures.py
"""
from __future__ import annotations

import re
import sys
import time
import urllib.request
from pathlib import Path

from car_tracker_scraper.antiblocking.user_agents import PERSONA_POOL

BASE = "https://www.deruedas.com.ar"
MARCA = "Audi"  # marca chica a proposito: pocas paginas, menos trafico
FIXTURES = Path(__file__).parent / "tests" / "fixtures"


def fetch(url: str, referer: str | None) -> tuple[int, str]:
    headers = dict(PERSONA_POOL[0].headers())
    headers["X-Requested-With"] = "XMLHttpRequest"
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    referer = f"{BASE}/bus.asp?segmento=0&marca={MARCA}"

    # --- 1) pagina 1 del listado
    cb = int(time.time() * 1000)
    url1 = f"{BASE}/busCraw.asp?segmento=0&marca={MARCA}&weNeed=divAll&pag=1&_={cb}"
    status, html1 = fetch(url1, referer)
    print(f"[1/4] listado pag=1 -> HTTP {status}, {len(html1)} bytes")
    if status != 200 or not html1.strip():
        print("      FALLO - no sigo. Pasale esta salida a Claude.")
        return 1
    (FIXTURES / "deruedas_results.html").write_text(html1, encoding="utf-8")
    cards = len(re.findall(r'id="car_(\d+)"', html1))
    print(f"      tarjetas encontradas: {cards}")

    # --- 2) pagina 2, para confirmar la paginacion (punto abierto del ticket)
    time.sleep(3)
    cb = int(time.time() * 1000)
    url2 = f"{BASE}/busCraw.asp?segmento=0&marca={MARCA}&weNeed=divAll&pag=2&_={cb}"
    status2, html2 = fetch(url2, referer)
    ids1 = set(re.findall(r'id="car_(\d+)"', html1))
    ids2 = set(re.findall(r'id="car_(\d+)"', html2))
    print(f"[2/4] listado pag=2 -> HTTP {status2}, {len(html2)} bytes, {len(ids2)} tarjetas")
    print(f"      solapamiento con pag=1: {len(ids1 & ids2)} ids repetidos "
          f"({'OK, paginacion limpia' if not (ids1 & ids2) else 'OJO: se repiten'})")
    if html2.strip():
        (FIXTURES / "deruedas_results_p2.html").write_text(html2, encoding="utf-8")

    # --- 3) el mismo pedido SIN Referer (otro punto abierto del ticket)
    time.sleep(3)
    cb = int(time.time() * 1000)
    status3, html3 = fetch(f"{BASE}/busCraw.asp?segmento=0&marca={MARCA}&weNeed=divAll&pag=1&_={cb}", None)
    n3 = len(re.findall(r'id="car_(\d+)"', html3))
    print(f"[3/4] listado SIN Referer -> HTTP {status3}, {n3} tarjetas "
          f"({'el server NO exige Referer' if n3 else 'el server SI parece exigir Referer'})")

    # --- 4) la ficha de detalle (formato desconocido hasta ahora)
    m = re.search(r'itemprop="url"\s+href="([^"]+)"', html1) or re.search(r'href="(/vendo/[^"]+)"', html1)
    if not m:
        print("[4/4] no pude sacar la URL de una ficha del listado - pasale la salida a Claude.")
        return 1
    detail_url = m.group(1)
    if detail_url.startswith("/"):
        detail_url = BASE + detail_url
    time.sleep(3)
    status4, html4 = fetch(detail_url, referer)
    print(f"[4/4] ficha {detail_url} -> HTTP {status4}, {len(html4)} bytes")
    if status4 == 200 and html4.strip():
        (FIXTURES / "deruedas_detail.html").write_text(html4, encoding="utf-8")

    print("\nFixtures escritos en tests/fixtures/. Pasale esta salida a Claude.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
