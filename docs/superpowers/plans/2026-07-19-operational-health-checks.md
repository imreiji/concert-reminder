# Operational Health Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Know when the backup, disk, or scheduler breaks, without going to look.

**Architecture:** A registry of named checks consumed by two independent paths. `/healthz` renders every check (pull; also covers scheduler death, which the scheduler cannot report about itself). The scheduler evaluates the registry every 5th tick and queues owner DMs through the existing `notifications` outbox on *confirmed* state change. Pure threshold and transition logic lives in `domain/health.py`; all I/O lives in `ops.py`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, SQLite, discord.py, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-19-operational-health-checks-design.md`

## Global Constraints

- `uv run pytest -q` must pass and `uv run ruff check .` must be clean before every commit. Both are CI gates.
- Current baseline: **465 passed, 1 failed**. The one failure is `tests/test_crud.py::test_test_dm_when_bot_disabled` — the repo-root `.env` sets a real `DISCORD_TOKEN` while the test assumes empty. Pre-existing, local-only, CI green, **OUT OF SCOPE**. Do not fix it. Each task's target is the running total plus its own new tests, with that same single failure and no others.
- TDD: write the failing test first, run it, confirm it fails for the right reason, then implement.
- `src/app/domain/` must import NO discord, fastapi, or sqlalchemy, and do no I/O.
- Business logic lives in `src/app/db/service.py`. Bot, web, and scheduler are thin shells.
- Notifications go through the `notifications` outbox table — never a direct DM from a web route (invariant 4). This work must not add a second carve-out.
- `/healthz`'s `ok` field must keep tracking the scheduler alone. UptimeRobot keyword-matches `"ok":true`; changing its meaning silently breaks an external alert.
- DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener.
- Config files and shell scripts stay ASCII-only (the owner's Windows machine uses a GBK locale). Verify the *committed blob* for `deploy/backup.sh` is ASCII and LF — the working tree is CRLF via `core.autocrlf`, and a CRLF shell script will not run on Ubuntu.
- Comment the WHY of non-obvious decisions, not the WHAT.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/domain/health.py` (new) | Pure thresholds + the transition machine. No I/O. |
| `src/app/ops.py` (new) | Check registry and I/O adapters (filesystem, disk, DB, heartbeat). |
| `src/app/db/models.py` (modify) | `OpsCheckState` table. |
| `alembic/versions/<rev>_ops_check_state.py` (new) | `CREATE TABLE` migration. |
| `src/app/db/service.py` (modify) | `load_check_states`, `save_check_state`, `queue_ops_alert`. |
| `src/app/config.py` (modify) | `backup_marker_path` setting. |
| `src/app/web/app.py` (modify) | `/healthz` renders the registry. |
| `src/app/scheduler/loop.py` (modify) | Evaluate every 5th tick, queue alerts. |
| `deploy/backup.sh` (modify) | Write the success marker. |

---

### Task 1: Pure health logic

**Files:**
- Create: `src/app/domain/health.py`
- Test: `tests/test_domain_health.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `StoredState`, `AlertDecision`, `backup_is_stale`, `disk_is_low`, `should_alert`, `REALERT_AFTER`, `BACKUP_MAX_AGE`, `MIN_FREE_RATIO`, `MIN_FREE_BYTES`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_domain_health.py
"""Pure health logic. No filesystem, no DB -- that is the point of the split:
the transition machine is the highest-risk code here and the hardest to
exercise through real I/O."""
from datetime import UTC, datetime, timedelta

from app.domain.health import (
    AlertDecision,
    StoredState,
    backup_is_stale,
    disk_is_low,
    should_alert,
)

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def test_backup_stale_when_marker_missing():
    assert backup_is_stale(None, NOW, timedelta(hours=36)) is True


def test_backup_fresh_within_window():
    assert backup_is_stale(NOW - timedelta(hours=20), NOW, timedelta(hours=36)) is False


def test_backup_stale_past_window():
    assert backup_is_stale(NOW - timedelta(hours=37), NOW, timedelta(hours=36)) is True


def test_disk_low_by_ratio():
    # 20GB disk, 1.5GB free: over the 1GB floor but under 10%.
    assert disk_is_low(free_bytes=1_500_000_000, total_bytes=20_000_000_000) is True


def test_disk_low_by_absolute_floor():
    # 4TB disk, 800MB free: 0.02% ratio is fine on a huge disk, the floor is not.
    assert disk_is_low(free_bytes=800_000_000, total_bytes=4_000_000_000_000) is True


def test_disk_ok_when_both_satisfied():
    assert disk_is_low(free_bytes=5_000_000_000, total_bytes=20_000_000_000) is False


def test_first_observation_healthy_is_adopted_silently():
    d = should_alert(None, observed_ok=True, now=NOW)
    assert d.notify is False
    assert d.state.ok is True


def test_first_observation_failing_waits_for_confirmation():
    d = should_alert(None, observed_ok=False, now=NOW)
    assert d.notify is False
    assert d.state.ok is None          # nothing confirmed yet
    assert d.state.pending_ok is False


def test_first_observation_failing_alerts_once_confirmed():
    first = should_alert(None, observed_ok=False, now=NOW)
    second = should_alert(first.state, observed_ok=False, now=NOW + timedelta(minutes=5))
    assert second.notify is True
    assert second.state.ok is False


def test_single_blip_does_not_alert():
    healthy = should_alert(None, observed_ok=True, now=NOW)
    blip = should_alert(healthy.state, observed_ok=False, now=NOW + timedelta(minutes=5))
    recovered = should_alert(blip.state, observed_ok=True, now=NOW + timedelta(minutes=10))
    assert blip.notify is False
    assert recovered.notify is False
    assert recovered.state.ok is True


def test_recovery_alerts_once_confirmed():
    broken = StoredState(ok=False, changed_at=NOW, last_notified_at=NOW,
                         pending_ok=None, pending_since=None)
    first = should_alert(broken, observed_ok=True, now=NOW + timedelta(minutes=5))
    second = should_alert(first.state, observed_ok=True, now=NOW + timedelta(minutes=10))
    assert first.notify is False
    assert second.notify is True
    assert second.state.ok is True


def test_still_broken_realerts_after_24h():
    broken = StoredState(ok=False, changed_at=NOW, last_notified_at=NOW,
                         pending_ok=None, pending_since=None)
    early = should_alert(broken, observed_ok=False, now=NOW + timedelta(hours=23))
    late = should_alert(broken, observed_ok=False, now=NOW + timedelta(hours=25))
    assert early.notify is False
    assert late.notify is True
    assert late.state.last_notified_at == NOW + timedelta(hours=25)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_domain_health.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.health'`

- [ ] **Step 3: Implement**

```python
# src/app/domain/health.py
"""Pure health evaluation: thresholds and the alert transition machine.

No I/O and no sqlalchemy import, per the domain invariant -- `ops.py` reads
the filesystem, the disk and the DB, then hands plain values in here. Keeping
the transition machine pure is deliberate: it is the highest-risk logic in this
feature and the most awkward to exercise through real I/O.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

BACKUP_MAX_AGE = timedelta(hours=36)
REALERT_AFTER = timedelta(hours=24)
# Ratio alone is wrong on a 20GB disk (2GB free is fine); an absolute floor
# alone breaks if the disk is ever resized. Trip on whichever comes first.
MIN_FREE_RATIO = 0.10
MIN_FREE_BYTES = 1_000_000_000


@dataclass(frozen=True)
class StoredState:
    """What ops.py has persisted for one check.

    `ok` is the last CONFIRMED result; None means nothing has been confirmed
    yet. `pending_*` hold an observation that has been seen once but not yet
    repeated -- a state change only counts after two consecutive agreeing
    evaluations, so a flapping check cannot page anyone.
    """

    ok: bool | None
    changed_at: datetime | None
    last_notified_at: datetime | None
    pending_ok: bool | None
    pending_since: datetime | None


@dataclass(frozen=True)
class AlertDecision:
    notify: bool
    state: StoredState


def backup_is_stale(
    last_ok: datetime | None, now: datetime, max_age: timedelta = BACKUP_MAX_AGE
) -> bool:
    """A missing marker counts as stale: no backup has ever been recorded."""
    if last_ok is None:
        return True
    return (now - last_ok) > max_age


def disk_is_low(
    free_bytes: int,
    total_bytes: int,
    min_free_ratio: float = MIN_FREE_RATIO,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> bool:
    if total_bytes <= 0:
        return True  # cannot reason about it; treat as a problem worth seeing
    return (free_bytes / total_bytes) < min_free_ratio or free_bytes < min_free_bytes


def should_alert(
    stored: StoredState | None,
    observed_ok: bool,
    now: datetime,
    realert_after: timedelta = REALERT_AFTER,
) -> AlertDecision:
    """Decide whether this observation warrants a DM, and what to persist."""
    if stored is None:
        stored = StoredState(None, None, None, None, None)

    # Steady: the observation agrees with what is already confirmed.
    if stored.ok is not None and observed_ok == stored.ok:
        state = replace(stored, pending_ok=None, pending_since=None)
        if not observed_ok:
            # Still broken. Nag daily -- transition-only alerting has a hole:
            # you see it, mean to fix it, forget, and it stays broken silently.
            due = (
                stored.last_notified_at is None
                or (now - stored.last_notified_at) >= realert_after
            )
            if due:
                return AlertDecision(True, replace(state, last_notified_at=now))
        return AlertDecision(False, state)

    # Very first observation and it is healthy: adopt as the baseline without
    # announcing it, so a deploy never pages about things that are fine.
    if stored.ok is None and observed_ok:
        return AlertDecision(False, StoredState(True, now, None, None, None))

    # Differs from the confirmed state (or is a first failing observation).
    # Alert only once a second evaluation agrees.
    if stored.pending_since is not None and stored.pending_ok == observed_ok:
        return AlertDecision(True, StoredState(observed_ok, now, now, None, None))
    return AlertDecision(
        False, replace(stored, pending_ok=observed_ok, pending_since=now)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_domain_health.py -q`
Expected: PASS, 12 passed

- [ ] **Step 5: Verify the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: `477 passed, 1 failed` (465 + 12), ruff clean

- [ ] **Step 6: Commit**

```bash
git add src/app/domain/health.py tests/test_domain_health.py
git commit -m "Add pure health thresholds and the alert transition machine"
```

---

### Task 2: `OpsCheckState` table and migration

**Files:**
- Modify: `src/app/db/models.py`
- Create: `alembic/versions/<generated>_ops_check_state.py`
- Test: `tests/test_migration_ops_check_state.py`

**Interfaces:**
- Consumes: nothing from Task 1 (the ORM row is separate from `StoredState` by design).
- Produces: `OpsCheckState` with columns `name` (PK, str), `ok` (bool | None), `changed_at` (datetime | None), `last_notified_at` (datetime | None), `pending_ok` (bool | None), `pending_since` (datetime | None).

- [ ] **Step 1: Add the model**

Add to `src/app/db/models.py`, following the existing model style:

```python
class OpsCheckState(Base):
    """Last confirmed result per operational check, so alerting survives
    restarts. In-memory state would re-announce every problem on every deploy.

    Mirrors domain.health.StoredState, which is a plain dataclass -- domain/
    cannot import sqlalchemy, so ops.py converts between the two.
    """

    __tablename__ = "ops_check_state"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    ok: Mapped[bool | None] = mapped_column(Boolean)
    changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pending_ok: Mapped[bool | None] = mapped_column(Boolean)
    pending_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

Ensure `Boolean` is imported from sqlalchemy at the top of the file if it is not already.

- [ ] **Step 2: Generate the migration**

Run: `uv run alembic revision --autogenerate -m "ops check state"`

- [ ] **Step 3: Edit the generated revision**

Per CLAUDE.md, autogenerate output is never committed as-is:
- Replace any `app.db.models.UTCDateTime()` with `sa.DateTime()`.
- Remove any `import app.db.models` line.
- Add this docstring note under the summary line:

```
Plain CREATE TABLE with no drop_constraint, so unlike 1384cadd692e this does
NOT need the legacy-schema fixture in
tests/test_migration_legacy_anonymous_constraints.py -- there is no existing
constraint to reflect and rename.
```

- [ ] **Step 4: Write the migration test**

```python
# tests/test_migration_ops_check_state.py
"""The migration is a plain CREATE TABLE, so this only has to prove the table
lands with the right columns and round-trips."""
import sqlite3

from alembic import command
from alembic.config import Config


def test_ops_check_state_table_is_created(tmp_path):
    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_check_state)")}
    assert cols == {
        "name", "ok", "changed_at", "last_notified_at", "pending_ok", "pending_since"
    }
    conn.execute(
        "INSERT INTO ops_check_state (name, ok) VALUES ('backup', 0)"
    )
    conn.commit()
    assert conn.execute("SELECT ok FROM ops_check_state").fetchone()[0] == 0
```

If `alembic.ini`'s URL resolution differs from this, read `alembic/env.py` and match how `tests/test_migration_legacy_anonymous_constraints.py` invokes it — that file is the working precedent in this repo.

- [ ] **Step 5: Run migration and tests**

Run: `uv run alembic upgrade head && uv run pytest tests/test_migration_ops_check_state.py -q`
Expected: upgrade succeeds, 1 passed

- [ ] **Step 6: Verify the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: `478 passed, 1 failed`, ruff clean

- [ ] **Step 7: Commit**

```bash
git add src/app/db/models.py alembic/versions/ tests/test_migration_ops_check_state.py
git commit -m "Add ops_check_state so alerting survives restarts"
```

---

### Task 3: Check registry and I/O adapters

**Files:**
- Create: `src/app/ops.py`
- Modify: `src/app/config.py`
- Modify: `.env.example`
- Test: `tests/test_ops_checks.py`

**Interfaces:**
- Consumes: `backup_is_stale`, `disk_is_low` from Task 1.
- Produces: `CheckResult(name: str, ok: bool, detail: str, alerting: bool)`, `async def run_checks(session) -> list[CheckResult]`, `REGISTRY`.

- [ ] **Step 1: Add the setting**

In `src/app/config.py`, alongside the other settings:

```python
    # Written by deploy/backup.sh after a successful upload. The app cannot ask
    # S3 whether a backup landed -- the IAM user is PutObject-only by design --
    # so this marker is the only local evidence a backup ran.
    backup_marker_path: str = "/home/ubuntu/.dekimasen-backup-ok"
```

In `.env.example`, add under a suitable heading:

```
# Only override if the backup marker lives somewhere non-default (tests do).
BACKUP_MARKER_PATH=
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_ops_checks.py
"""Registry-level tests. The pure thresholds are covered in
tests/test_domain_health.py; these cover the I/O adapters and, importantly,
that one broken check cannot take down the tick."""
from datetime import UTC, datetime, timedelta

import pytest

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_ops_checks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ops'`

- [ ] **Step 4: Implement**

```python
# src/app/ops.py
"""Operational checks: the I/O half of the health surface.

Each check returns a CheckResult. Two consumers iterate the same registry --
/healthz (pull) and the scheduler (push) -- so adding a signal later is one
function here and it appears in both places.

`alerting` separates "report this" from "wake me for this". The dms check is
reported but never alerted on: a user blocking the bot is their choice, not an
outage, and paging about someone else's privacy setting is the noise that
trains an operator to ignore alerts.
"""

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.config import settings
from app.db.models import User
from app.domain.health import BACKUP_MAX_AGE, backup_is_stale, disk_is_low
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

# Restart grace, mirroring scheduler/heartbeat.py. A freshly deployed process
# has no marker because no backup has run yet; only once it has been up longer
# than a whole backup cycle is a missing marker evidence of a real problem.
_started_at = datetime.now(UTC)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class RegistryEntry:
    name: str
    run: Callable[[], CheckResult]
    alerting: bool


def safe_run(name: str, fn: Callable[[], CheckResult]) -> CheckResult:
    """A check that cannot run IS a problem -- report it as failing rather than
    letting it propagate. This runs inside the scheduler tick, whose real job
    is delivering reminders; a monitoring bug must never stop that."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - deliberately broad, see docstring
        log.exception("health check %s raised", name)
        return CheckResult(name, False, f"check raised: {e}")


def check_backup() -> CheckResult:
    path = Path(settings.backup_marker_path)
    now = datetime.now(UTC)
    if not path.exists():
        if (now - _started_at) < BACKUP_MAX_AGE:
            return CheckResult("backup", True, "no backup recorded yet (startup grace)")
        return CheckResult("backup", False, "no backup recorded yet")
    try:
        last = datetime.fromisoformat(path.read_text().strip())
    except (OSError, ValueError) as e:
        return CheckResult("backup", False, f"marker unreadable: {e}")
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if backup_is_stale(last, now):
        return CheckResult("backup", False, f"last backup {last.isoformat()}")
    return CheckResult("backup", True, f"last backup {last.isoformat()}")


def check_disk() -> CheckResult:
    usage = shutil.disk_usage(Path(settings.database_url.split("///")[-1]).parent or "/")
    free_gb = usage.free / 1_000_000_000
    if disk_is_low(usage.free, usage.total):
        return CheckResult("disk", False, f"{free_gb:.1f}GB free")
    return CheckResult("disk", True, f"{free_gb:.1f}GB free")


def check_scheduler() -> CheckResult:
    ok, last_tick = heartbeat.status()
    return CheckResult("scheduler", ok, f"last tick {last_tick}")


REGISTRY: list[RegistryEntry] = [
    RegistryEntry("backup", check_backup, alerting=True),
    RegistryEntry("disk", check_disk, alerting=True),
    RegistryEntry("scheduler", check_scheduler, alerting=True),
    # dms is DB-bound, so it is handled separately in run_checks.
    RegistryEntry("dms", lambda: CheckResult("dms", True, ""), alerting=False),
]


async def check_dms(session) -> CheckResult:
    blocked = await session.scalar(
        select(func.count()).select_from(User).where(User.dm_blocked_since.is_not(None))
    )
    return CheckResult("dms", True, f"{blocked} users have DMs closed")


async def run_checks(session) -> list[CheckResult]:
    """Every check, in registry order. Never raises."""
    results = []
    for entry in REGISTRY:
        if entry.name == "dms":
            results.append(await _safe_run_async("dms", check_dms, session))
        else:
            results.append(safe_run(entry.name, entry.run))
    return results


async def _safe_run_async(name, fn, session) -> CheckResult:
    try:
        return await fn(session)
    except Exception as e:  # noqa: BLE001 - see safe_run
        log.exception("health check %s raised", name)
        return CheckResult(name, False, f"check raised: {e}")
```

Note `check_disk` derives the path from `database_url`; if that parsing looks fragile for the configured URL format, read `src/app/db/session.py` and reuse whatever it already does rather than re-deriving.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ops_checks.py -q`
Expected: PASS, 8 passed

- [ ] **Step 6: Verify the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: `486 passed, 1 failed`, ruff clean

- [ ] **Step 7: Commit**

```bash
git add src/app/ops.py src/app/config.py .env.example tests/test_ops_checks.py
git commit -m "Add the operational check registry and its I/O adapters"
```

---

### Task 4: Expose checks on `/healthz`

**Files:**
- Modify: `src/app/web/app.py` (the `healthz` handler)
- Test: `tests/test_healthz_checks.py`

**Interfaces:**
- Consumes: `run_checks` from Task 3.
- Produces: `/healthz` JSON gains a `checks` object: `{"backup": {"ok": bool, "detail": str}, ...}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_healthz_checks.py
"""The `ok` regression test is the important one here: UptimeRobot keyword-
matches '"ok":true', so that field has an external consumer and changing its
meaning silently would defeat the point of this work."""
from fastapi.testclient import TestClient

from app.web.app import create_app


def test_healthz_reports_every_check():
    client = TestClient(create_app())
    body = client.get("/healthz").json()
    assert set(body["checks"]) == {"backup", "disk", "scheduler", "dms"}
    assert set(body["checks"]["backup"]) == {"ok", "detail"}


def test_healthz_ok_still_tracks_scheduler_only(monkeypatch):
    """A failing backup must NOT flip `ok`, or the existing uptime monitor
    starts meaning something different without anyone deciding that."""
    from app import ops

    monkeypatch.setattr(ops.settings, "backup_marker_path", "/nonexistent/marker")
    client = TestClient(create_app())
    body = client.get("/healthz").json()
    assert body["checks"]["backup"]["ok"] is False
    assert body["ok"] == body["scheduler_ok"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_healthz_checks.py -q`
Expected: FAIL with `KeyError: 'checks'`

- [ ] **Step 3: Implement**

Modify the `healthz` handler in `src/app/web/app.py`. It currently reads:

```python
    @app.get("/healthz")
    async def healthz() -> dict:
        scheduler_ok, last_tick = heartbeat.status()
        return {
            "ok": scheduler_ok,  # overall health follows the scheduler on purpose
            "bot_enabled": settings.bot_enabled,
            "scheduler_ok": scheduler_ok,
            "scheduler_last_tick": last_tick,
        }
```

Replace with:

```python
    @app.get("/healthz")
    async def healthz() -> dict:
        scheduler_ok, last_tick = heartbeat.status()
        async with SessionMaker() as session:
            results = await run_checks(session)
        return {
            # `ok` deliberately still follows the scheduler ALONE. UptimeRobot
            # keyword-matches '"ok":true'; folding degraded checks in here would
            # silently redefine an existing external alert. The detail lives in
            # `checks`, and the scheduler DMs on state change.
            "ok": scheduler_ok,
            "bot_enabled": settings.bot_enabled,
            "scheduler_ok": scheduler_ok,
            "scheduler_last_tick": last_tick,
            "checks": {r.name: {"ok": r.ok, "detail": r.detail} for r in results},
        }
```

Add the imports `from app.ops import run_checks` and, if not already present, whatever the module uses for `SessionMaker` (check the existing imports in `web/app.py` and match them).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_healthz_checks.py -q`
Expected: PASS, 2 passed

- [ ] **Step 5: Verify the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: `488 passed, 1 failed`, ruff clean

- [ ] **Step 6: Commit**

```bash
git add src/app/web/app.py tests/test_healthz_checks.py
git commit -m "Report every operational check on /healthz"
```

---

### Task 5: Persist state and queue owner alerts

**Files:**
- Modify: `src/app/db/service.py`
- Modify: `src/app/scheduler/loop.py`
- Test: `tests/test_ops_alerts.py`

**Interfaces:**
- Consumes: `should_alert`, `StoredState` (Task 1); `OpsCheckState` (Task 2); `run_checks`, `REGISTRY` (Task 3).
- Produces: `async def evaluate_and_alert(session, results, now) -> int` in `db/service.py`, returning the number of alerts queued.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ops_alerts.py
"""Transition machine wired to the DB and the outbox.

The fixture registers PRAGMA foreign_keys=ON deliberately: Notification.user_id
is a FK to users.discord_id, and the ensure_user call this code makes is
load-bearing -- without it, queuing an alert for an admin who has never logged
in violates the constraint.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Notification, OpsCheckState
from app.db.service import evaluate_and_alert
from app.ops import CheckResult

ADMIN = 111
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(conn, _rec):
        conn.execute("PRAGMA foreign_keys=ON")  # cascades silently skip without this

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _admins(monkeypatch):
    from app.db import service

    monkeypatch.setattr(service.settings, "admin_whitelist", str(ADMIN))
    monkeypatch.setattr(service.settings, "discord_token", "x")  # bot_enabled


async def _run(session, ok, now):
    return await evaluate_and_alert(
        session, [CheckResult("backup", ok, "detail")], now
    )


async def test_first_failing_observation_is_silent_then_alerts(session):
    assert await _run(session, False, NOW) == 0
    assert await _run(session, False, NOW + timedelta(minutes=5)) == 1
    notes = (await session.execute(select(Notification))).scalars().all()
    assert len(notes) == 1
    assert notes[0].user_id == ADMIN
    assert "backup" in notes[0].body


async def test_healthy_baseline_never_alerts(session):
    assert await _run(session, True, NOW) == 0
    assert await _run(session, True, NOW + timedelta(minutes=5)) == 0
    assert (await session.execute(select(Notification))).scalars().all() == []


async def test_state_is_persisted(session):
    await _run(session, True, NOW)
    row = await session.get(OpsCheckState, "backup")
    assert row is not None and row.ok is True


async def test_alert_queues_for_admin_without_a_user_row(session):
    """ensure_user must run first or the Notification FK fails."""
    await _run(session, False, NOW)
    await _run(session, False, NOW + timedelta(minutes=5))
    await session.commit()  # would raise IntegrityError if ensure_user was skipped


async def test_non_alerting_checks_never_queue(session):
    await evaluate_and_alert(session, [CheckResult("dms", False, "x")], NOW)
    await evaluate_and_alert(
        session, [CheckResult("dms", False, "x")], NOW + timedelta(minutes=5)
    )
    assert (await session.execute(select(Notification))).scalars().all() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ops_alerts.py -q`
Expected: FAIL with `ImportError: cannot import name 'evaluate_and_alert'`

- [ ] **Step 3: Implement the service function**

Add to `src/app/db/service.py`:

```python
async def evaluate_and_alert(session, results, now) -> int:
    """Fold check results into persisted state; queue an owner DM per confirmed
    change. Returns how many alerts were queued.

    Alerts go through the notifications outbox rather than a direct DM: that is
    invariant 4, and it buys retry, ordering and Forbidden handling for free.
    """
    from app.domain.health import StoredState, should_alert
    from app.ops import REGISTRY

    alerting = {e.name for e in REGISTRY if e.alerting}
    queued = 0

    for result in results:
        row = await session.get(OpsCheckState, result.name)
        stored = (
            StoredState(
                row.ok, row.changed_at, row.last_notified_at,
                row.pending_ok, row.pending_since,
            )
            if row is not None
            else None
        )
        decision = should_alert(stored, result.ok, now)

        if row is None:
            row = OpsCheckState(name=result.name)
            session.add(row)
        row.ok = decision.state.ok
        row.changed_at = decision.state.changed_at
        row.last_notified_at = decision.state.last_notified_at
        row.pending_ok = decision.state.pending_ok
        row.pending_since = decision.state.pending_since

        if not decision.notify or result.name not in alerting:
            continue
        # A laptop's disk is not an operational signal; without this, every
        # local dev run would accumulate junk notifications.
        if not settings.bot_enabled:
            continue

        status = "recovered" if result.ok else "FAILING"
        for admin_id in settings.admin_ids:
            await ensure_user(session, admin_id, "admin")
            session.add(Notification(
                user_id=admin_id,
                body=f"dekimasen.app check `{result.name}` {status}: {result.detail}",
                kind="ops_alert",
            ))
            queued += 1

    await session.flush()
    return queued
```

Note `kind="ops_alert"` with `concert_id=None` deliberately falls through `_notification_context` in `scheduler/loop.py` to the plain-text path — no changes to the send code are needed. Verify that by reading `loop.py:89-97`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ops_alerts.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Wire it into the scheduler**

In `src/app/scheduler/loop.py`, inside `tick()`, after the existing notification draining and before `await session.commit()`:

```python
        # Evaluate health every 5th tick (~5 min). Disk stats and file reads do
        # not need per-minute resolution, and the slower cadence damps flapping.
        global _tick_count
        _tick_count += 1
        if _tick_count % HEALTH_EVERY_N_TICKS == 0:
            await evaluate_and_alert(session, await run_checks(session), now)
```

Add near the module's other constants:

```python
HEALTH_EVERY_N_TICKS = 5
_tick_count = 0
```

and the imports `from app.db.service import evaluate_and_alert` (add to the existing service import block) and `from app.ops import run_checks`.

- [ ] **Step 6: Verify the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: `493 passed, 1 failed`, ruff clean

- [ ] **Step 7: Commit**

```bash
git add src/app/db/service.py src/app/scheduler/loop.py tests/test_ops_alerts.py
git commit -m "Queue owner alerts through the outbox on confirmed check changes"
```

---

### Task 6: Write the backup marker, and document it

**Files:**
- Modify: `deploy/backup.sh`
- Modify: `docs/deploy.md`

**Interfaces:**
- Consumes: the `backup_marker_path` setting from Task 3 (default `/home/ubuntu/.dekimasen-backup-ok`).
- Produces: the marker file the `backup` check reads.

- [ ] **Step 1: Add the marker write**

In `deploy/backup.sh`, after the `aws s3 cp` line and before the `echo "backup ok"` line:

```bash
# Success marker for the app's health check. The IAM user is PutObject-only by
# design, so the app cannot ask S3 whether this landed -- this file is the only
# local evidence a backup ran. Written only after the upload succeeds, so a
# failed run leaves it stale rather than lying.
date -u +%Y-%m-%dT%H:%M:%S+00:00 > /home/ubuntu/.dekimasen-backup-ok
```

- [ ] **Step 2: Verify the script**

Run: `bash -n deploy/backup.sh`
Expected: no output (clean)

Then confirm the committed blob is ASCII and LF:

Run: `git show :deploy/backup.sh | python -c "import sys;b=sys.stdin.buffer.read();print('ASCII' if b.decode().isascii() else 'NON-ASCII', 'CRLF' if b'\r\n' in b else 'LF')"`
Expected: `ASCII LF`

Do NOT execute `deploy/backup.sh` — it targets a production path and a real S3 bucket.

- [ ] **Step 3: Document it**

In `docs/deploy.md`, in the backup section, add:

```
`backup.sh` writes `/home/ubuntu/.dekimasen-backup-ok` on success. `/healthz`
reads it and reports the `backup` check, and the scheduler DMs admins when it
goes stale (36h) or recovers. The app cannot verify the object reached S3 --
the IAM user is PutObject-only -- so this marker proves the script succeeded,
which is close to but not the same as the backup existing.
```

- [ ] **Step 4: Verify the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: `493 passed, 1 failed`, ruff clean (no test changes in this task)

- [ ] **Step 5: Commit**

```bash
git add deploy/backup.sh docs/deploy.md
git commit -m "Write a backup success marker for the health check to read"
```

---

## Deployment notes

This ships with a migration, so use the migration path added to `docs/deploy.md`: stop the service, take a verified backup, then upgrade.

After deploying, `backup` will report `"no backup recorded yet"` until the first nightly run at 09:00 UTC writes the marker. That is expected and does not alert immediately — the 36h window plus the confirmation rule means a genuinely broken backup surfaces roughly a day and a half after deploy, not at deploy time.

To sanity-check the whole path without waiting, run `~/app/deploy/backup.sh` manually and confirm `/healthz` flips `checks.backup.ok` to true.
