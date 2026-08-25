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
    DiscoveryCandidateTracker,
    mark_discovery_ran,
    seconds_since_last_discovery,
)

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


def test_in_batch_window_boundaries_still_hold():
    from datetime import datetime

    from run_batch import ART, in_batch_window

    assert in_batch_window(datetime(2026, 1, 1, 2, 0, tzinfo=ART)) is True
    assert in_batch_window(datetime(2026, 1, 1, 6, 59, tzinfo=ART)) is True
    assert in_batch_window(datetime(2026, 1, 1, 7, 0, tzinfo=ART)) is False
    assert in_batch_window(datetime(2026, 1, 1, 1, 59, tzinfo=ART)) is False


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

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker)

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

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker)

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

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker)

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

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker)

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

    rc = run_batch.run_detail(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker)

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

    rc = run_batch.run_detail(datetime.now(run_batch.ART), tracker)

    assert rc == 1
    assert tracker.due_for_detail(older_than_seconds=3600, limit=10) == []


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

    rc = run_batch.run_detail(datetime.now(run_batch.ART), tracker)

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

    run_batch.run_detail(datetime.now(run_batch.ART), tracker)

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

    rc = run_batch.run_detail(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker)

    assert rc == 1
    mock_run.assert_not_called()


@patch("run_batch.subprocess.run")
def test_main_skips_everything_outside_the_window(mock_run):
    with patch("run_batch.datetime") as mock_datetime:
        from datetime import datetime as real_datetime

        mock_datetime.now.return_value = real_datetime(2026, 1, 1, 12, 0, tzinfo=run_batch.ART)
        rc = run_batch.main()

    assert rc == 0
    mock_run.assert_not_called()
