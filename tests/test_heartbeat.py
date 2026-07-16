"""Heartbeat semantics: fresh start is healthy, recent tick is healthy, silence is not."""

from datetime import UTC, datetime, timedelta

from app.scheduler import heartbeat


def test_fresh_process_is_healthy(monkeypatch):
    monkeypatch.setattr(heartbeat, "last_tick", None)
    monkeypatch.setattr(heartbeat, "_started_at", datetime.now(UTC))
    ok, last = heartbeat.status()
    assert ok and last is None


def test_stale_start_without_ticks_is_unhealthy(monkeypatch):
    monkeypatch.setattr(heartbeat, "last_tick", None)
    monkeypatch.setattr(heartbeat, "_started_at", datetime.now(UTC) - timedelta(minutes=10))
    ok, _ = heartbeat.status()
    assert not ok


def test_recent_tick_is_healthy(monkeypatch):
    monkeypatch.setattr(heartbeat, "last_tick", datetime.now(UTC) - timedelta(seconds=60))
    ok, last = heartbeat.status()
    assert ok and last is not None


def test_silent_scheduler_is_unhealthy(monkeypatch):
    monkeypatch.setattr(heartbeat, "last_tick", datetime.now(UTC) - timedelta(minutes=10))
    ok, _ = heartbeat.status()
    assert not ok


def test_beat_updates(monkeypatch):
    monkeypatch.setattr(heartbeat, "last_tick", None)
    heartbeat.beat()
    ok, last = heartbeat.status()
    assert ok and last is not None
