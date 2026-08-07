"""The transition machine turning health checks into admin DMs.

Anti-flap: an observation must be CONFIRMED by a second pass before it alerts,
which is why the pending pair is persisted rather than held in memory.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.core import ensure_user
from app.db.models import (
    Notification,
    OpsCheckState,
    User,
)

# ── Operational health alerts ────────────────────────────────────────────


async def evaluate_and_alert(session: AsyncSession, results, now: datetime) -> int:
    """Fold check results into persisted state; queue an owner DM per confirmed
    change. Returns how many alerts were queued.

    Alerts go through the notifications outbox rather than a direct DM: that is
    invariant 4, and it buys retry, ordering and Forbidden handling for free.
    `kind="ops_alert"` with `concert_id=None` falls through
    `scheduler.loop._notification_context` to the plain-text path, so the send
    code needs no changes.
    """
    # Local import on purpose: app.ops sits ABOVE db/ (it already imports
    # db.models), so importing it at module scope would invert the layering and
    # make db/service.py unimportable on its own.
    from app.domain.health import StoredState, should_alert
    from app.ops import REGISTRY

    alerting = {e.name for e in REGISTRY if e.alerting}
    queued = 0

    for result in results:
        row = await session.get(OpsCheckState, result.name)
        # Keyword arguments in BOTH directions, deliberately: StoredState has
        # two bool|None fields and three datetime|None ones, so a positional
        # copy that drifts out of dataclass order swaps changed_at with
        # last_notified_at silently -- same type, no error, wrong nag timing.
        stored = (
            StoredState(
                ok=row.ok,
                changed_at=row.changed_at,
                last_notified_at=row.last_notified_at,
                pending_ok=row.pending_ok,
                pending_since=row.pending_since,
            )
            if row is not None
            else None
        )
        decision = should_alert(stored, result.ok, now)

        would_notify = decision.notify and result.name in alerting
        # A laptop's disk is not an operational signal; without this, every
        # local dev run would accumulate junk notifications. Evaluated BEFORE
        # the state write, because last_notified_at is the 24h nag clock:
        # advancing it for an alert that was never sent silently swallows the
        # first day of alerts on a server where DISCORD_TOKEN is added later.
        suppressed = would_notify and not settings.bot_enabled

        if row is None:
            row = OpsCheckState(name=result.name)
            session.add(row)
        row.ok = decision.state.ok
        row.changed_at = decision.state.changed_at
        row.last_notified_at = (
            (stored.last_notified_at if stored is not None else None)
            if suppressed
            else decision.state.last_notified_at
        )
        row.pending_ok = decision.state.pending_ok
        row.pending_since = decision.state.pending_since

        if not would_notify or suppressed:
            continue

        status = "recovered" if result.ok else "FAILING"
        for admin_id in settings.admin_ids:
            # An admin who has never logged into the web app has no users row,
            # and Notification.user_id is a FK to it -- queuing without this
            # raises IntegrityError at flush, far from the cause. Guarded on
            # absence rather than calling ensure_user unconditionally: that
            # refreshes the username, which would overwrite a real admin's
            # name with this placeholder every time a check changed state.
            if await session.get(User, admin_id) is None:
                await ensure_user(session, admin_id, str(admin_id))
            session.add(
                Notification(
                    user_id=admin_id,
                    body=f"dekimasen.app check `{result.name}` {status}: {result.detail}",
                    kind="ops_alert",
                )
            )
            queued += 1

    await session.flush()
    return queued
