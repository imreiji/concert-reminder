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
from sqlalchemy.engine import make_url

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
    detail = f"last backup {last.isoformat()}"
    return CheckResult("backup", not backup_is_stale(last, now), detail)


def _db_directory() -> Path:
    """The directory holding the SQLite file -- that is the disk worth watching.

    Parsed with SQLAlchemy's own URL parser rather than by splitting on "///":
    production uses the four-slash absolute form
    (sqlite+aiosqlite:////home/ubuntu/app/app.db) and dev the three-slash
    relative one. Splitting happens to get both right, but hands back the whole
    URL unchanged for an in-memory database, which then reaches disk_usage as a
    bogus path. make_url returns None there instead, which we can handle.
    """
    database = make_url(settings.database_url).database
    if not database:
        return Path(".")  # in-memory DB: report the working directory's disk
    return Path(database).parent


def check_disk() -> CheckResult:
    usage = shutil.disk_usage(_db_directory())
    detail = f"{usage.free / 1_000_000_000:.1f}GB free"
    return CheckResult("disk", not disk_is_low(usage.free, usage.total), detail)


def check_scheduler() -> CheckResult:
    ok, last_tick = heartbeat.status()
    return CheckResult("scheduler", ok, f"last tick {last_tick}")


REGISTRY: list[RegistryEntry] = [
    RegistryEntry("backup", check_backup, alerting=True),
    RegistryEntry("disk", check_disk, alerting=True),
    RegistryEntry("scheduler", check_scheduler, alerting=True),
    # dms is DB-bound, so run_checks handles it separately; the placeholder
    # keeps it in registry order and carries its `alerting` flag.
    RegistryEntry("dms", lambda: CheckResult("dms", True, ""), alerting=False),
]


async def check_dms(session) -> CheckResult:
    blocked = await session.scalar(
        select(func.count()).select_from(User).where(User.dm_blocked_since.is_not(None))
    )
    return CheckResult("dms", True, f"{blocked} users have DMs closed")


async def _safe_run_async(name, fn, session) -> CheckResult:
    try:
        return await fn(session)
    except Exception as e:  # noqa: BLE001 - see safe_run
        log.exception("health check %s raised", name)
        return CheckResult(name, False, f"check raised: {e}")


async def run_checks(session) -> list[CheckResult]:
    """Every check, in registry order. Never raises."""
    results = []
    for entry in REGISTRY:
        if entry.name == "dms":
            results.append(await _safe_run_async("dms", check_dms, session))
        else:
            results.append(safe_run(entry.name, entry.run))
    return results
