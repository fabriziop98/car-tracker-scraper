"""Tests del scheduler (wdxtkg35ba). fakeredis para el tracker (mismo
criterio que test_antiblocking.py), subprocess.run mockeado - no dispara
crawls reales."""
from __future__ import annotations

import json
import time
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


@patch("run_batch.subprocess.run")
def test_run_discovery_feeds_the_tracker_on_success(mock_run, tmp_path: Path, monkeypatch):
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


@patch("run_batch.subprocess.run")
def test_run_discovery_does_not_touch_tracker_on_failure(mock_run, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    mock_run.return_value = MagicMock(returncode=1)
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    from datetime import datetime

    rc = run_batch.run_discovery(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker)

    assert rc == 1
    assert tracker.known_count() == 0


@patch("run_batch.subprocess.run")
def test_run_detail_does_nothing_when_no_candidate_is_due(mock_run, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)

    from datetime import datetime

    rc = run_batch.run_detail(datetime(2026, 1, 1, 3, 0, tzinfo=run_batch.ART), tracker)

    assert rc == 0
    mock_run.assert_not_called()


@patch("run_batch.subprocess.run")
def test_run_detail_marks_attempted_urls_as_detailed_even_if_the_crawl_fails(mock_run, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    mock_run.return_value = MagicMock(returncode=1)  # el crawl "falla" pero igual se marcan (ver docstring de run_detail)
    r = fakeredis.FakeRedis()
    tracker = DiscoveryCandidateTracker(r)
    tracker.record_discovered("https://example.com/a")

    from datetime import datetime

    rc = run_batch.run_detail(datetime.now(run_batch.ART), tracker)

    assert rc == 1
    assert tracker.due_for_detail(older_than_seconds=3600, limit=10) == []


@patch("run_batch.subprocess.run")
def test_main_skips_everything_outside_the_window(mock_run):
    with patch("run_batch.datetime") as mock_datetime:
        from datetime import datetime as real_datetime

        mock_datetime.now.return_value = real_datetime(2026, 1, 1, 12, 0, tzinfo=run_batch.ART)
        rc = run_batch.main()

    assert rc == 0
    mock_run.assert_not_called()
