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
# Hysteresis. A single hard edge at 10%/1GB is exactly the value that
# oscillates -- SQLite's WAL and the nightly gzipped backup churn across it --
# and an oscillating check alerts in NEITHER direction: should_alert needs two
# consecutive agreeing observations, and F,T,F,T never supplies them, so the
# check goes quiet precisely when the disk is most interesting. Once low, stay
# low until there is real headroom back.
CLEAR_FREE_RATIO = 0.15
CLEAR_FREE_BYTES = 1_500_000_000


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
    currently_low: bool = False,
    min_free_ratio: float = MIN_FREE_RATIO,
    min_free_bytes: int = MIN_FREE_BYTES,
    clear_free_ratio: float = CLEAR_FREE_RATIO,
    clear_free_bytes: int = CLEAR_FREE_BYTES,
) -> bool:
    """Trip at 10%/1GB; once tripped, clear only at 15%/1.5GB.

    `currently_low` is the caller's last CONFIRMED verdict for this check --
    the state lives in the DB, so ops.py reads it and passes it in rather than
    this module reaching for it (domain stays pure).
    """
    if total_bytes <= 0:
        return True  # cannot reason about it; treat as a problem worth seeing
    ratio_floor = clear_free_ratio if currently_low else min_free_ratio
    bytes_floor = clear_free_bytes if currently_low else min_free_bytes
    return (free_bytes / total_bytes) < ratio_floor or free_bytes < bytes_floor


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
