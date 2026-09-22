"""Tests del scheduler (wdxtkg35ba). fakeredis para el tracker (mismo
criterio que test_antiblocking.py), subprocess.run mockeado - no dispara
crawls reales."""
from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

import run_batch
from car_tracker_scraper.scheduling.state import (
    CANDIDATES_KEY,
    CANDIDATE_MARCA_KEY,
    LAST_DISCOVERY_KEY,
    DiscoveryCandidateTracker,
    keys_for_source,
    mark_discovery_ran,
    mark_discovery_ran_for,
    seconds_since_last_discovery,
    seconds_since_last_discovery_for,
)

# wdxtkg30xr: run_discovery/run_detail son por fuente. Se usa la config real
# de MercadoLibre del registro (no una inventada) para que estos tests sigan
# cubriendo exactamente lo que corre en produccion.
_ML_SOURCE = next(s for s in run_batch.SOURCES if s.slug == "mercadolibre")

# --- DiscoveryCandidateTracker ----------------------------------------------


def test_record_discovered_adds_a_new_candidate_as_never_detailed():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    tracker.record_discovered("https://example.com/a")

    assert tracker.known_count() == 1
    assert tracker.due_for_detail(older_than_seconds=0, limit=10) == ["https://example.com/a"]


def test_record_discovered_does_not_overwrite_an_already_known_url():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a")
    tracker.mark_detailed(["https://example.com/a"], when=time.time())  # recien detallado, ahora mismo

    # se "redescubre" en una corrida de Discovery posterior - no debe resetear a "nunca detallado"
    tracker.record_discovered("https://example.com/a")

    assert tracker.due_for_detail(older_than_seconds=3600, limit=10) == []  # recien detallado, no vencido todavia


def test_due_for_detail_never_detailed_comes_before_recently_expired():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/never")
    tracker.record_discovered("https://example.com/old")
    tracker.mark_detailed(["https://example.com/old"], when=1.0)  # muy en el pasado, vencido

    due = tracker.due_for_detail(older_than_seconds=0, limit=10)

    assert due == ["https://example.com/never", "https://example.com/old"]


def test_due_for_detail_excludes_recently_detailed():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a")
    tracker.mark_detailed(["https://example.com/a"])  # ahora mismo

    due = tracker.due_for_detail(older_than_seconds=3600, limit=10)

    assert due == []


def test_due_for_detail_respects_limit():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    for i in range(5):
        tracker.record_discovered(f"https://example.com/{i}")

    due = tracker.due_for_detail(older_than_seconds=0, limit=2)

    assert len(due) == 2


def test_mark_detailed_with_empty_list_is_a_no_op():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    tracker.mark_detailed([])  # no debe tirar excepcion

    assert tracker.known_count() == 0


def test_mark_dead_removes_the_url_from_due_for_detail():
    # wdxtkg39vm: una vez confirmada muerta, no puede seguir venciendo cada
    # DETAIL_TIER_HOURS para siempre - ese era exactamente el bug.
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/dead", marca="fiat")

    tracker.mark_dead(["https://example.com/dead"])

    assert tracker.due_for_detail(older_than_seconds=0, limit=10) == []
    assert tracker.known_count() == 0
    assert tracker.dead_count() == 1


def test_mark_dead_also_clears_the_marca_metadata():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/dead", marca="fiat")

    tracker.mark_dead(["https://example.com/dead"])

    assert r.hget(tracker.marca_key, "https://example.com/dead") is None


def test_mark_dead_with_empty_list_is_a_no_op():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    tracker.mark_dead([])  # no debe tirar excepcion

    assert tracker.dead_count() == 0


def test_a_revived_url_is_recoverable_via_a_fresh_discovery():
    # Si ML revive el aviso, Discovery lo vuelve a ver: record_discovered()
    # no mira dead_key, asi que la URL vuelve a quedar elegible para Detail
    # sin ningun paso manual (ver docstring de mark_dead).
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/revived", marca="fiat")
    tracker.mark_dead(["https://example.com/revived"])

    tracker.record_discovered("https://example.com/revived", marca="fiat")

    assert tracker.due_for_detail(older_than_seconds=0, limit=10) == ["https://example.com/revived"]


def test_due_for_detail_without_allowed_marcas_ignores_the_filter_entirely():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a", marca="fiat")

    due = tracker.due_for_detail(older_than_seconds=0, limit=10)

    assert due == ["https://example.com/a"]


def test_due_for_detail_with_allowed_marcas_excludes_urls_of_uncurated_brands():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/curada", marca="fiat")
    tracker.record_discovered("https://example.com/no-curada", marca="bmw")

    due = tracker.due_for_detail(older_than_seconds=0, limit=10, allowed_marcas={"fiat"})

    assert due == ["https://example.com/curada"]


def test_due_for_detail_with_allowed_marcas_includes_urls_without_marca_metadata():
    """Compat: URLs registradas antes de wdxtkg35bb (sin marca guardada) no quedan bloqueadas para siempre."""
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/sin-marca")

    due = tracker.due_for_detail(older_than_seconds=0, limit=10, allowed_marcas={"fiat"})

    assert due == ["https://example.com/sin-marca"]


def test_record_discovered_with_the_same_url_updates_the_marca():
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a", marca="bmw")

    tracker.record_discovered("https://example.com/a", marca="fiat")

    due = tracker.due_for_detail(older_than_seconds=0, limit=10, allowed_marcas={"fiat"})
    assert due == ["https://example.com/a"]


# --- last-discovery timestamp -----------------------------------------------


def test_seconds_since_last_discovery_is_infinite_when_never_ran():
    r = fakeredis.FakeRedis()
    assert seconds_since_last_discovery(r) == float("inf")


def test_seconds_since_last_discovery_is_small_right_after_marking():
    r = fakeredis.FakeRedis()
    mark_discovery_ran(r)

    assert 0 <= seconds_since_last_discovery(r) < 5


# --- run_batch.py ------------------------------------------------------------


def test_record_discovered_from_jsonl_skips_ads(tmp_path: Path):
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    jsonl = tmp_path / "discovery.jsonl"
    jsonl.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"url": "https://example.com/real", "is_ad": False},
                {"url": "https://example.com/ad", "is_ad": True},
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    count = run_batch._record_discovered(jsonl, tracker)

    assert count == 1
    assert tracker.known_count() == 1
    assert tracker.due_for_detail(older_than_seconds=0, limit=10) == ["https://example.com/real"]


class _FakeUrlResponse:
    """Standin minimo para lo que urllib.request.urlopen devuelve como context manager - solo lo que json.load necesita (.read())."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


@patch("run_batch.urllib.request.urlopen")
def test_fetch_discovered_marcas_parses_slugs_from_the_api_response(mock_urlopen):
    mock_urlopen.return_value = _FakeUrlResponse(json.dumps([
        {"slug": "volkswagen", "brandName": "VOLKSWAGEN", "volume": 27417},
        {"slug": "ford", "brandName": "FORD", "volume": 21518},
    ]).encode("utf-8"))

    assert run_batch.fetch_discovered_marcas() == ["volkswagen", "ford"]


@patch("run_batch.urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused"))
def test_fetch_discovered_marcas_returns_an_empty_list_when_the_api_is_unreachable(mock_urlopen):
    assert run_batch.fetch_discovered_marcas() == []


@patch("run_batch.fetch_discovered_marcas", return_value=["fiat"])
@patch("run_batch.subprocess.run")
def test_run_discovery_feeds_the_tracker_on_success(mock_run, mock_fetch_marcas, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    mock_run.return_value = MagicMock(returncode=0)
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    # el subprocess mockeado no escribe nada - simulamos la salida esperada de scrapy a mano
    def _fake_run(args, **kwargs):
        output_path = Path(args[args.index("-O") + 1])
        output_path.write_text(json.dumps({"url": "https://example.com/x", "is_ad": False}) + "\n", encoding="utf-8")
        return MagicMock(returncode=0)

    mock_run.side_effect = _fake_run

    from datetime import datetime

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker, _ML_SOURCE)

    assert rc == 0
    assert tracker.known_count() == 1


@patch("run_batch.fetch_discovered_marcas", return_value=["fiat"])
@patch("run_batch.subprocess.run")
def test_run_discovery_does_not_touch_tracker_on_failure(mock_run, mock_fetch_marcas, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    mock_run.return_value = MagicMock(returncode=1)
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    from datetime import datetime

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker, _ML_SOURCE)

    assert rc == 1
    assert tracker.known_count() == 0


@patch("run_batch.fetch_curated_marcas", return_value=[])
@patch("run_batch.fetch_discovered_marcas", return_value=[])
def test_run_discovery_skips_without_crashing_when_no_marcas_are_discovered_yet(mock_fetch_marcas, mock_fetch_curated, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    from datetime import datetime

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker, _ML_SOURCE)

    assert rc == 1
    assert tracker.known_count() == 0


@patch("run_batch.fetch_curated_marcas", return_value=["fiat"])
@patch("run_batch.fetch_discovered_marcas", return_value=[])
@patch("run_batch.subprocess.run")
def test_run_discovery_falls_back_to_curated_marcas_when_brand_discovery_is_empty(mock_run, mock_fetch_discovered, mock_fetch_curated, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    def _fake_run(args, **kwargs):
        output_path = Path(args[args.index("-O") + 1])
        output_path.write_text(json.dumps({"url": "https://example.com/x", "is_ad": False}) + "\n", encoding="utf-8")
        return MagicMock(returncode=0)

    mock_run.side_effect = _fake_run

    from datetime import datetime

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker, _ML_SOURCE)

    assert rc == 0
    assert tracker.known_count() == 1
    assert "-a" in mock_run.call_args[0][0]
    assert "marcas=fiat" in mock_run.call_args[0][0]


@patch("run_batch.fetch_curated_marcas", return_value=["fiat"])
@patch("run_batch.subprocess.run")
def test_run_detail_does_nothing_when_no_candidate_is_due(mock_run, mock_fetch_curated, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    from datetime import datetime

    rc = run_batch.run_detail(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker, _ML_SOURCE)

    assert rc == 0
    mock_run.assert_not_called()


@patch("run_batch.fetch_curated_marcas", return_value=["fiat"])
@patch("run_batch.subprocess.run")
def test_run_detail_marks_attempted_urls_as_detailed_on_a_partial_failure(mock_run, mock_fetch_curated, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a", marca="fiat")

    # el crawl "falla" (returncode=1, ej. un item puntual con error) pero SI produjo
    # output real - se marcan igual (ver docstring de run_detail)
    def _fake_run(args, **kwargs):
        output_path = Path(args[args.index("-O") + 1])
        output_path.write_text(json.dumps({"url": "https://example.com/a"}) + "\n", encoding="utf-8")
        return MagicMock(returncode=1)

    mock_run.side_effect = _fake_run

    from datetime import datetime

    rc = run_batch.run_detail(datetime.now(run_batch.ART), tracker, _ML_SOURCE)

    assert rc == 1
    assert tracker.due_for_detail(older_than_seconds=3600, limit=10) == []


@patch("run_batch.fetch_curated_marcas", return_value=["fiat"])
@patch("run_batch.subprocess.run")
def test_run_detail_marks_a_dead_listing_item_as_dead_not_just_detailed(mock_run, mock_fetch_curated, tmp_path: Path, monkeypatch):
    # wdxtkg39vm: un DeadListingItem en el jsonl (item_type="dead_listing")
    # tiene que sacar esa URL del tracker de candidatos, no solo actualizarle
    # el timestamp de "detallado" (que la dejaria vencer de nuevo en
    # DETAIL_TIER_HOURS y repetir el ciclo para siempre).
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/viva", marca="fiat")
    tracker.record_discovered("https://example.com/muerta", marca="fiat")

    def _fake_run(args, **kwargs):
        output_path = Path(args[args.index("-O") + 1])
        with output_path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"url": "https://example.com/viva", "source_listing_key": "MLA1"}) + "\n")
            fh.write(json.dumps({"url": "https://example.com/muerta", "item_type": "dead_listing", "source": "mercadolibre"}) + "\n")
        return MagicMock(returncode=0)

    mock_run.side_effect = _fake_run

    from datetime import datetime

    run_batch.run_detail(datetime.now(run_batch.ART), tracker, _ML_SOURCE)

    due = tracker.due_for_detail(older_than_seconds=3600, limit=10)
    assert due == []  # ninguna vencida todavia (recien detalladas)
    assert tracker.known_count() == 1  # solo la viva sigue siendo candidato
    assert tracker.dead_count() == 1


@patch("run_batch.fetch_curated_marcas", return_value=["fiat"])
@patch("run_batch.subprocess.run")
def test_run_detail_does_not_mark_candidates_on_a_total_failure(mock_run, mock_fetch_curated, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    # falla total (0 items, ej. la capa anti-bloqueo no llega a Redis) - el crawl
    # puede devolver returncode=0 igual (IgnoreRequest no es un crash de scrapy),
    # asi que el chequeo real es que el archivo de salida haya quedado vacio -
    # incidente real 2026-08-19, ver comentario en run_detail
    mock_run.return_value = MagicMock(returncode=0)
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a", marca="fiat")

    from datetime import datetime

    rc = run_batch.run_detail(datetime.now(run_batch.ART), tracker, _ML_SOURCE)

    assert rc == 0
    assert tracker.due_for_detail(older_than_seconds=3600, limit=10) == ["https://example.com/a"]


@patch("run_batch.fetch_curated_marcas", return_value=["fiat"])
@patch("run_batch.subprocess.run")
def test_run_detail_only_processes_candidates_of_curated_brands(mock_run, mock_fetch_curated, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()

    def _fake_run(args, **kwargs):
        output_path = Path(args[args.index("-O") + 1])
        output_path.write_text(json.dumps({"url": "https://example.com/fiat-curada"}) + "\n", encoding="utf-8")
        return MagicMock(returncode=0)

    mock_run.side_effect = _fake_run
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/fiat-curada", marca="fiat")
    tracker.record_discovered("https://example.com/bmw-no-curada", marca="bmw")

    from datetime import datetime

    run_batch.run_detail(datetime.now(run_batch.ART), tracker, _ML_SOURCE)

    urls_arg = mock_run.call_args[0][0]
    urls_file = Path(urls_arg[urls_arg.index("-a") + 1].split("=", 1)[1])
    detailed_urls = urls_file.read_text(encoding="utf-8").splitlines()
    assert detailed_urls == ["https://example.com/fiat-curada"]
    # la de bmw sigue con score=0 (nunca detallada), se reintenta sola cuando bmw se cure -
    # older_than_seconds=3600 para no confundirla con la de fiat, recien marcada hace instantes
    assert tracker.due_for_detail(older_than_seconds=3600, limit=10) == ["https://example.com/bmw-no-curada"]


@patch("run_batch.fetch_curated_marcas", return_value=[])
@patch("run_batch.subprocess.run")
def test_run_detail_skips_the_tick_instead_of_running_unfiltered_when_curated_marcas_is_unavailable(mock_run, mock_fetch_curated, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a", marca="fiat")

    from datetime import datetime

    rc = run_batch.run_detail(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker, _ML_SOURCE)

    assert rc == 1
    mock_run.assert_not_called()


@patch("run_batch.fetch_curated_marcas", return_value=["fiat"])
@patch("run_batch.fetch_discovered_marcas", return_value=["fiat"])
@patch("run_batch.redis.Redis.from_url")
@patch("run_batch.subprocess.run")
def test_main_runs_regardless_of_the_hour(mock_run, mock_from_url, mock_fetch_discovered, mock_fetch_curated, tmp_path: Path, monkeypatch):
    # wdxtkg30xr (2026-08-25): la ventana horaria 2-7am ART se saco - un tick
    # a mediodia tiene que seguir corriendo Discovery/Detail como cualquier otro.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    r = fakeredis.FakeRedis()
    mock_from_url.return_value = r
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a", marca="fiat")

    def _fake_run(args, **kwargs):
        output_path = Path(args[args.index("-O") + 1])
        output_path.write_text(json.dumps({"url": "https://example.com/x", "is_ad": False}) + "\n", encoding="utf-8")
        return MagicMock(returncode=0)

    mock_run.side_effect = _fake_run

    with patch("run_batch.datetime") as mock_datetime:
        from datetime import datetime as real_datetime

        mock_datetime.now.return_value = real_datetime(2026, 1, 1, 12, 0, tzinfo=run_batch.ART)
        run_batch.main()

    mock_run.assert_called()


# --- multi-fuente (wdxtkg30xr) ------------------------------------------------


def test_mercadolibre_keeps_the_legacy_unsuffixed_redis_keys():
    """El test mas importante de este modulo.

    MercadoLibre tiene decenas de miles de candidatos vivos guardados bajo las
    claves SIN sufijo (las que existian antes de que el scheduler fuera
    multi-fuente). Si `keys_for_source` empezara a devolver
    'scheduling:discovery_candidates:mercadolibre', el scheduler dejaria de
    verlos de un dia para el otro, sin ningun error visible, y habria que
    re-scrapear todo ese inventario de cero.
    """
    assert keys_for_source("mercadolibre") == (
        CANDIDATES_KEY,
        CANDIDATE_MARCA_KEY,
        LAST_DISCOVERY_KEY,
    )


def test_a_new_source_gets_its_own_suffixed_keyspace():
    candidates, marcas, last_discovery = keys_for_source("motordil")

    assert candidates == f"{CANDIDATES_KEY}:motordil"
    assert marcas == f"{CANDIDATE_MARCA_KEY}:motordil"
    assert last_discovery == f"{LAST_DISCOVERY_KEY}:motordil"


def test_candidates_of_different_sources_do_not_leak_into_each_other():
    r = fakeredis.FakeRedis()
    ml = DiscoveryCandidateTracker.for_source(r, "mercadolibre")
    motordil = DiscoveryCandidateTracker.for_source(r, "motordil")

    ml.record_discovered("https://auto.mercadolibre.com.ar/MLA-1", marca="fiat")
    motordil.record_discovered("https://www.motordil.com/auto/x", marca="fiat")

    assert ml.known_count() == 1
    assert motordil.known_count() == 1
    assert ml.due_for_detail(older_than_seconds=0, limit=10) == ["https://auto.mercadolibre.com.ar/MLA-1"]
    assert motordil.due_for_detail(older_than_seconds=0, limit=10) == ["https://www.motordil.com/auto/x"]


def test_a_tracker_for_mercadolibre_reads_the_pre_existing_legacy_candidates():
    # Simula el estado real de produccion: candidatos ya guardados bajo las
    # claves legacy antes de este cambio. El tracker por fuente tiene que
    # seguir viendolos, no arrancar de cero.
    r = fakeredis.FakeRedis()
    legacy = DiscoveryCandidateTracker(r)  # constructor viejo, claves por defecto
    legacy.record_discovered("https://auto.mercadolibre.com.ar/MLA-legacy", marca="ford")

    ml = DiscoveryCandidateTracker.for_source(r, "mercadolibre")

    assert ml.known_count() == 1
    assert ml.due_for_detail(older_than_seconds=0, limit=10, allowed_marcas={"ford"}) == [
        "https://auto.mercadolibre.com.ar/MLA-legacy"
    ]


def test_discovery_cadence_is_tracked_per_source():
    r = fakeredis.FakeRedis()

    mark_discovery_ran_for(r, "mercadolibre")

    # ML acaba de correr, Motordil nunca -> Motordil sigue vencido.
    assert seconds_since_last_discovery_for(r, "mercadolibre") < 5
    assert seconds_since_last_discovery_for(r, "motordil") == float("inf")


def test_mercadolibre_cadence_is_the_same_key_the_legacy_helpers_use():
    r = fakeredis.FakeRedis()

    mark_discovery_ran(r)  # helper legacy, sin fuente

    assert seconds_since_last_discovery_for(r, "mercadolibre") < 5


def test_every_registered_source_has_distinct_spiders_and_a_sane_batch_size():
    slugs = [s.slug for s in run_batch.SOURCES]
    assert len(slugs) == len(set(slugs)), "slugs de fuente duplicados"

    spiders = [s.discovery_spider for s in run_batch.SOURCES] + [s.detail_spider for s in run_batch.SOURCES]
    assert len(spiders) == len(set(spiders)), "dos fuentes comparten un spider"

    # wdxtkg39qx: las fuentes corren en PARALELO, asi que la restriccion es el
    # MAXIMO y no la suma - una fuente lenta ya no obliga a achicarle el batch
    # a las demas. Se pondera por seconds_per_request y no por batch_size a
    # secas, porque una fuente con DOWNLOAD_DELAY propio (DeRuedas: 5s) cuesta
    # varias veces mas por aviso que una que solo respeta el token bucket (1s).
    tick_seconds = 15 * 60
    for source in run_batch.SOURCES:
        assert source.estimated_detail_seconds <= tick_seconds, (
            f"{source.slug} sola no entra en el tick: "
            f"{source.estimated_detail_seconds}s > {tick_seconds}s"
        )


def test_one_failing_source_does_not_stop_the_others(monkeypatch, tmp_path: Path):
    # Todo el punto de tener mas de una fuente es no depender de ninguna: si
    # Motordil explota, MercadoLibre tiene que correr igual (y viceversa).
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    ran = []

    def _fake_run_source(now, redis_client, source):
        ran.append(source.slug)
        if source.slug == "mercadolibre":
            raise RuntimeError("spider roto")
        return 0

    with patch("run_batch.run_source", side_effect=_fake_run_source), \
         patch("run_batch.redis.Redis.from_url", return_value=fakeredis.FakeRedis()):
        rc = run_batch.main()

    assert set(ran) == {s.slug for s in run_batch.SOURCES}, "una fuente caida corto el resto del tick"
    assert rc != 0, "una fuente caida tiene que reflejarse en el exit code"


def test_sources_run_in_parallel_not_sequentially(monkeypatch, tmp_path: Path):
    """wdxtkg39qx: prueba real del paralelismo, no una suposicion.

    Se usa una Barrier del tamanio de SOURCES: solo se libera si TODAS las
    fuentes estan corriendo a la vez. Si main() volviera a ser secuencial, la
    primera se quedaria esperando a las otras dos para siempre y el test corta
    por timeout en vez de pasar en silencio.

    Correrlas en paralelo es seguro porque el token bucket y el circuit breaker
    son por dominio: dos sitios distintos nunca comparten presupuesto.
    """
    import threading

    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    barrier = threading.Barrier(len(run_batch.SOURCES), timeout=5)
    llegaron = []

    def _fake_run_source(now, redis_client, source):
        llegaron.append(source.slug)
        barrier.wait()  # BrokenBarrierError si alguna no llega -> el test falla
        return 0

    with patch("run_batch.run_source", side_effect=_fake_run_source), \
         patch("run_batch.redis.Redis.from_url", return_value=fakeredis.FakeRedis()):
        rc = run_batch.main()

    assert rc == 0
    assert set(llegaron) == {s.slug for s in run_batch.SOURCES}


# ---------------------------------------------------------------------------
# Lock por fuente (2026-08-31): una sola corrida por fuente a la vez.
# ---------------------------------------------------------------------------


def test_una_segunda_corrida_de_la_misma_fuente_saltea_el_tick():
    """El bug medido: Discovery de DeRuedas tarda ~34 min contra un tick de
    cron de 15 min, y `mark_discovery_ran_for` recien corre DESPUES del
    subprocess - asi que los ticks siguientes veian el timestamp viejo y
    lanzaban su propia Discovery encima. Con 3 spiders concurrentes a
    DOWNLOAD_DELAY=5 el sitio recibia un request cada ~1,7s en vez de cada 5s,
    rompiendo el Crawl-delay que respetamos a proposito."""
    from datetime import datetime

    redis_client = fakeredis.FakeRedis()
    source = run_batch.SOURCES[0]
    corridas = []

    with patch("run_batch._run_source_locked", side_effect=lambda *a: corridas.append(1) or 0):
        # la primera toma el lock y corre
        assert run_batch.run_source(datetime.now(), redis_client, source) == 0
        assert len(corridas) == 1

        # simulo una corrida EN CURSO: el lock esta tomado
        assert run_batch._acquire_source_lock(redis_client, source)
        assert run_batch.run_source(datetime.now(), redis_client, source) == 0
        assert len(corridas) == 1, "la segunda corrida no debio ejecutarse"


def test_el_lock_se_libera_aunque_la_corrida_falle():
    """Si una excepcion dejara el lock tomado, la fuente quedaria muerta hasta
    que expire el TTL (1h) - peor que el problema que resuelve."""
    from datetime import datetime

    redis_client = fakeredis.FakeRedis()
    source = run_batch.SOURCES[0]

    with patch("run_batch._run_source_locked", side_effect=RuntimeError("spider roto")):
        with pytest.raises(RuntimeError):
            run_batch.run_source(datetime.now(), redis_client, source)

    assert run_batch._acquire_source_lock(redis_client, source), "el lock quedo huerfano"


def test_el_lock_es_por_fuente_no_global():
    """Que DeRuedas este ocupada no puede frenar a ML: corren contra dominios
    distintos, con presupuestos de request independientes."""
    redis_client = fakeredis.FakeRedis()
    una, otra = run_batch.SOURCES[0], run_batch.SOURCES[1]

    assert run_batch._acquire_source_lock(redis_client, una)
    assert run_batch._acquire_source_lock(redis_client, otra), "el lock de una fuente bloqueo a otra"


def test_el_lock_expira_solo_para_no_dejar_una_fuente_colgada():
    """Ante una muerte dura (contenedor recreado) el `finally` no corre; el TTL
    es la unica red."""
    redis_client = fakeredis.FakeRedis()
    source = run_batch.SOURCES[0]
    run_batch._acquire_source_lock(redis_client, source)

    ttl = redis_client.ttl(f"scheduling:run_lock:{source.slug}")
    assert 0 < ttl <= run_batch.SOURCE_LOCK_TTL_SECONDS
    # tiene que superar la corrida mas larga medida (Discovery de DeRuedas, 34 min)
    assert run_batch.SOURCE_LOCK_TTL_SECONDS > 34 * 60


def test_deruedas_entra_en_el_tick_con_su_crawl_delay():
    """El batch se dimensiona contra el tick de 15 min a 5s por request, no
    contra lo que sobra despues de las otras fuentes (corren en paralelo)."""
    deruedas = next(s for s in run_batch.SOURCES if s.slug == "deruedas")
    segundos = deruedas.detail_batch_size * deruedas.seconds_per_request

    assert segundos <= 900, "un batch mas largo que el tick se apoya solo en el lock"
    assert segundos <= 700, "dejar margen para reintentos y arranque del spider"

    # y tiene que poder recorrer su pool dentro del tier de re-Detail
    por_dia = deruedas.detail_batch_size * (24 * 60 / 15)
    assert por_dia / 8780 >= 1 / (run_batch.DETAIL_TIER_HOURS / 24), \
        "no alcanza a recorrer el pool conocido dentro de DETAIL_TIER_HOURS"


# ---------------------------------------------------------------------------
# wdxtkg39v1: el tuning del corte por modelo vive en SourceConfig y tiene que
# llegar de verdad al comando de scrapy.
# ---------------------------------------------------------------------------


@patch("run_batch.fetch_discovered_marcas", return_value=["toyota"])
@patch("run_batch.subprocess.run")
def test_discovery_args_llegan_al_comando_de_scrapy(mock_run, _marcas, tmp_path: Path, monkeypatch):
    from datetime import datetime

    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    mock_run.return_value = MagicMock(returncode=0)
    ml = next(s for s in run_batch.SOURCES if s.slug == "mercadolibre")
    tracker = DiscoveryCandidateTracker(fakeredis.FakeRedis())

    # subprocess.run esta mockeado, asi que el .jsonl que el spider habria
    # escrito hay que dejarlo a mano: _record_discovered lo lee despues.
    ahora = datetime(2026, 9, 1, 3, 0, tzinfo=run_batch.ART)
    (tmp_path / "output" / f"discovery_mercadolibre_{ahora.strftime('%Y%m%dT%H%M%S')}.jsonl").write_text("")

    run_batch.run_discovery(ahora, tracker, ml)

    cmd = mock_run.call_args[0][0]
    for clave, valor in ml.discovery_args.items():
        assert f"{clave}={valor}" in cmd, f"{clave} no llego al spider: {cmd}"


def test_el_corte_por_modelo_de_ML_llego_al_default_del_spider_con_dato_medido():
    """Arranco en 400/5 (2026-08-28) porque el circuit breaker de ML ya se
    abrio una vez al subir el ritmo de golpe (31% de error). wdxtkg3j80
    (2026-09-06) encontro que max_modelos_por_marca=5 dejaba modelos reales
    sin cobertura propia (VW Golf, 8vo en volumen de su marca, nunca
    scrapeado) - se subio a 10 tras medir el circuit breaker real (0 fallas
    en los ultimos 400 eventos).

    wdxtkg3j80 (2026-09-22, cierre): 16 dias despues, mismo chequeo con mas
    evidencia - circuit breaker cerrado sin interrupcion desde el incidente
    original (`open_until` nunca se volvio a mover de 2026-08-28), 0 fallas
    en los 401 eventos mas recientes en Redis, y el sintoma concreto
    confirmado resuelto en Postgres (VW Golf: 1.446 avisos reales,
    scrapeado hoy mismo). Se subio a 15, el default real del spider -
    dejo de ser un numero mas chico que ese default a proposito, asi que
    este test pasa de "recordatorio de no subir sin medir" a "confirmar que
    no se volvio a angostar sin querer"."""
    ml = next(s for s in run_batch.SOURCES if s.slug == "mercadolibre")
    assert ml.discovery_args["modelo_min_volumen"] >= 400
    assert ml.discovery_args["max_modelos_por_marca"] == 15


def test_las_otras_fuentes_no_heredan_el_corte_por_modelo_de_ML():
    """modelo_min_volumen/max_modelos_por_marca son especificos del corte por
    modelo de ML (test de arriba) - ninguna otra fuente deberia traerlos.

    Correccion 2026-09-14: la version anterior de este test exigia
    discovery_args == {} para toda fuente que no fuera ML, con la premisa de
    que "Motordil y DeRuedas se drenan enteras con la consulta normal". Esa
    premisa resulto falsa - medido en vivo el mismo dia, DISCOVERY_MAX_PAGES=30
    (el backstop de ML, ver su comentario en run_batch.py) le pegaba el techo
    a las 6 marcas de mayor volumen de DeRuedas y al total de Autocosmos/Kavak
    en cada corrida, porque esas fuentes cuentan paginas simple y no tienen el
    corte real propio que tiene ML. Las tres necesitan su propio
    discovery_args={"max_pages": ...} - lo que este test no debe prohibir."""
    claves_de_ml = {"modelo_min_volumen", "max_modelos_por_marca"}
    for source in run_batch.SOURCES:
        if source.slug != "mercadolibre":
            assert not (claves_de_ml & source.discovery_args.keys()), source.slug


def test_release_stale_source_locks_limpia_todos_los_locks(monkeypatch):
    """El release normal es el finally de run_source, que NO corre ante un kill
    duro - y recrear el contenedor en cada rebuild es exactamente eso. Sin esto
    el TTL de 1h deja la fuente parada hasta una hora: paso el 2026-09-01, un
    rebuild a las 10:37 mato una corrida de las 10:30 y el Discovery de ML del
    tick de las 11:15 se salteo solo."""
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(run_batch.redis.Redis, "from_url", staticmethod(lambda *a, **k: fake))
    for source in run_batch.SOURCES:
        run_batch._acquire_source_lock(fake, source)
    assert fake.keys("scheduling:run_lock:*")

    liberados = run_batch.release_stale_source_locks()

    assert len(liberados) == len(run_batch.SOURCES)
    assert fake.keys("scheduling:run_lock:*") == []
    # y despues de limpiarlos, cada fuente puede volver a tomar el suyo
    for source in run_batch.SOURCES:
        assert run_batch._acquire_source_lock(fake, source)


def test_release_stale_source_locks_sin_locks_no_falla(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(run_batch.redis.Redis, "from_url", staticmethod(lambda *a, **k: fake))
    assert run_batch.release_stale_source_locks() == []
