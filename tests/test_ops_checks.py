"""Registry-level tests. The pure thresholds are covered in
tests/test_domain_health.py; these cover the I/O adapters and, importantly,
that one broken check cannot take down the tick."""
from datetime import UTC, datetime, timedelta

from app import ops


def test_backup_check_reads_marker(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    marker.write_text(datetime.now(UTC).isoformat())
    monkeypatch.setattr(ops.settings, "backup_marker_path", str(marker))
    result = ops.check_backup()
    assert result.ok is True
    assert result.name == "backup"


def test_missing_marker_is_tolerated_during_startup_grace(tmp_path, monkeypatch):
    """Right after a deploy no backup has run yet. Without this grace the very
    first evaluations would page about a backup that was never due."""
    monkeypatch.setattr(ops.settings, "backup_marker_path", str(tmp_path / "nope"))
    monkeypatch.setattr(ops, "_started_at", datetime.now(UTC) - timedelta(hours=2))
    result = ops.check_backup()
    assert result.ok is True
    assert "startup grace" in result.detail


def test_missing_marker_fails_once_grace_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(ops.settings, "backup_marker_path", str(tmp_path / "nope"))
    monkeypatch.setattr(ops, "_started_at", datetime.now(UTC) - timedelta(hours=40))
    result = ops.check_backup()
    assert result.ok is False
    assert "no backup recorded yet" in result.detail


def test_backup_check_reports_stale_marker(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    marker.write_text((datetime.now(UTC) - timedelta(hours=40)).isoformat())
    monkeypatch.setattr(ops.settings, "backup_marker_path", str(marker))
    assert ops.check_backup().ok is False


def test_backup_check_survives_garbage_marker(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    marker.write_text("not a timestamp")
    monkeypatch.setattr(ops.settings, "backup_marker_path", str(marker))
    result = ops.check_backup()
    assert result.ok is False          # unparseable is a problem, not a crash
    assert "unreadable" in result.detail


def test_disk_check_uses_disk_usage(monkeypatch):
    class Usage:
        total, used, free = 20_000_000_000, 19_000_000_000, 1_000_000_000

    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _p: Usage)
    assert ops.check_disk().ok is False


def test_a_raising_check_becomes_a_failure_not_a_crash():
    def boom():
        raise RuntimeError("kaboom")

    result = ops.safe_run("exploder", boom)
    assert result.ok is False
    assert "kaboom" in result.detail


def test_dms_check_is_reported_but_never_alerting():
    entry = next(e for e in ops.REGISTRY if e.name == "dms")
    assert entry.alerting is False
