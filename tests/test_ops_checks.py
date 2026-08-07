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


class _FakeSession:
    """Just enough session for check_disk: it only ever does a PK get."""

    def __init__(self, disk_state=None):
        self._disk_state = disk_state

    async def get(self, _model, _pk):
        return self._disk_state


class _State:
    def __init__(self, ok):
        self.ok = ok


def _usage(free, total=20_000_000_000):
    class Usage:
        pass

    Usage.total, Usage.free, Usage.used = total, free, total - free
    return Usage


async def test_disk_check_uses_disk_usage(monkeypatch):
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _p: _usage(1_000_000_000))
    assert (await ops.check_disk(_FakeSession())).ok is False


async def test_disk_check_reads_confirmed_state_for_hysteresis(monkeypatch):
    """12% free: below the 15% CLEAR threshold, above the 10% TRIP one.

    From healthy that reads as fine; from an already-low state it must stay
    low. Without the DB read there would be nothing to distinguish the two, and
    a disk hovering right at 10% would flap F,T,F,T -- which alerts in neither
    direction, because should_alert needs two consecutive agreeing sightings.
    """
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _p: _usage(2_400_000_000))

    from_healthy = await ops.check_disk(_FakeSession(_State(ok=True)))
    from_low = await ops.check_disk(_FakeSession(_State(ok=False)))

    assert from_healthy.ok is True
    assert from_low.ok is False


async def test_disk_check_treats_missing_state_as_not_low(monkeypatch):
    """First-ever run has no OpsCheckState row: use the trip threshold, so a
    fresh process never reports low on a disk that merely sits under 15%."""
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _p: _usage(2_400_000_000))
    assert (await ops.check_disk(_FakeSession(None))).ok is True


def test_a_raising_check_becomes_a_failure_not_a_crash():
    def boom():
        raise RuntimeError("kaboom")

    result = ops.safe_run("exploder", boom)
    assert result.ok is False
    assert "RuntimeError" in result.detail


def test_a_raising_check_does_not_leak_its_message():
    """str() on a SQLAlchemy error carries the full statement and column
    names. /healthz shows `checks` to an admin only now, so this is defence in
    depth -- but a CheckResult is a value two consumers already render and a
    third could be less careful, so a detail that never contains a statement
    cannot leak one wherever it ends up."""

    def boom():
        raise RuntimeError("SELECT secret FROM users WHERE token = 'hunter2'")

    result = ops.safe_run("exploder", boom)
    assert "hunter2" not in result.detail
    assert "SELECT" not in result.detail
    assert result.detail == "check raised: RuntimeError"


def test_dms_check_is_reported_but_never_alerting():
    entry = next(e for e in ops.REGISTRY if e.name == "dms")
    assert entry.alerting is False


def test_scheduler_check_is_reported_but_never_alerting():
    """heartbeat.beat() runs immediately before tick(), so evaluated from
    inside the tick this check is structurally always ok -- the only thing it
    could ever produce from there is a false alarm about a busy scheduler. It
    stays in the registry because /healthz (the pull path) is where it means
    something."""
    entry = next(e for e in ops.REGISTRY if e.name == "scheduler")
    assert entry.alerting is False


async def test_dms_detail_carries_no_user_derived_count():
    """Infrastructure facts (disk, backup) are admin-gated on /healthz;
    "N users have DMs closed" is derived from the USER table and stays out of
    the detail string entirely, one layer further in. Naming who is affected
    belongs to /admin/deliveries, which owns that responsibility explicitly."""

    class CountingSession:
        async def scalar(self, _query):
            return 7

    result = await ops.check_dms(CountingSession())
    assert result.ok is True
    assert not any(ch.isdigit() for ch in result.detail)
