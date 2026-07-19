"""Operational checks: the I/O half of the health surface.

Each check returns a CheckResult. Two consumers iterate the same registry --
/healthz (pull) and the scheduler (push) -- so adding a signal later is one
function here and it appears in both places.

`alerting` separates "report this" from "wake me for this". Two checks are
reported but never alerted on: `dms`, because a user blocking the bot is their
choice rather than an outage, and paging about someone else's privacy setting
is the noise that trains an operator to ignore alerts; and `scheduler`, because
it cannot say anything true about itself from inside its own tick (see the
comment on its registry entry).
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
from app.db.models import OpsCheckState, User
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
    # A DB-bound check: `run` is an async callable taking the session instead of
    # a plain sync one. run_checks dispatches on this flag rather than on the
    # name, so a test can swap in a sync stub for any check without the
    # dispatcher second-guessing it.
    needs_session: bool = False


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


async def check_disk(session) -> CheckResult:
    """DB-bound only because of hysteresis.

    `disk_is_low` trips at 10%/1GB but clears at 15%/1.5GB, which means it has
    to know whether the disk is ALREADY considered low. The only durable record
    of that is the confirmed verdict the alert machine persists in
    OpsCheckState -- so the read happens here, and domain/health.py stays pure.
    Using the CONFIRMED state (not the raw previous observation) is deliberate:
    it is the same value should_alert reasons about, so the two cannot disagree.
    """
    usage = shutil.disk_usage(_db_directory())
    row = await session.get(OpsCheckState, "disk")
    currently_low = row is not None and row.ok is False
    detail = f"{usage.free / 1_000_000_000:.1f}GB free"
    return CheckResult(
        "disk", not disk_is_low(usage.free, usage.total, currently_low), detail
    )


def check_scheduler() -> CheckResult:
    ok, last_tick = heartbeat.status()
    return CheckResult("scheduler", ok, f"last tick {last_tick}")


async def check_dms(session) -> CheckResult:
    blocked = await session.scalar(
        select(func.count()).select_from(User).where(User.dm_blocked_since.is_not(None))
    )
    # The count stays OUT of the detail: /healthz is public (UptimeRobot polls
    # it anonymously) and this is the one check whose number is derived from the
    # user table rather than being an infrastructure fact. Logged instead, where
    # only the owner can see it.
    log.debug("dms check: %s users have DMs closed", blocked)
    return CheckResult("dms", True, "dm-block state tracked")


REGISTRY: list[RegistryEntry] = [
    RegistryEntry("backup", check_backup, alerting=True),
    RegistryEntry("disk", check_disk, alerting=True, needs_session=True),
    # Reported, never alerted on. heartbeat.beat() fires immediately BEFORE
    # tick(), so when this runs inside the tick the last beat is always seconds
    # old -- structurally always ok=True. The only outcome it could ever produce
    # from in here is a false alarm: a tick that legitimately runs long (a big
    # due batch at SEND_CONCURRENCY=5) twice over would DM "scheduler FAILING"
    # about a scheduler that is merely busy. The scheduler cannot meaningfully
    # report its own liveness from inside itself; this check is only meaningful
    # on the /healthz PULL path, where UptimeRobot is what actually catches
    # scheduler death.
    RegistryEntry("scheduler", check_scheduler, alerting=False),
    RegistryEntry("dms", check_dms, alerting=False, needs_session=True),
]


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
        if entry.needs_session:
            results.append(await _safe_run_async(entry.name, entry.run, session))
        else:
            results.append(safe_run(entry.name, entry.run))
    return results
