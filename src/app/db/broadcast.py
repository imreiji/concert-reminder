"""The admin broadcast: the one path putting admin-authored text into user DMs.

Still goes through the notification outbox (invariant 4) -- queued HELD via
`Notification.send_after_utc` so it can be cancelled inside the hold.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import _now
from app.db.models import (
    Broadcast,
    DeliveryLog,
    Notification,
    User,
)
from app.domain.types import (
    BroadcastMode,
)
from app.i18n import N_, gettext_in

# ── Admin broadcast ──────────────────────────────────────────────────────

# Long enough to reread what you sent and see the mistake; short enough that a
# real incident remedy is not uselessly delayed. A constant, not a setting --
# one fewer thing to get wrong at 3am (owner ruling, 2026-07-28).
HOLD_SECONDS = 120
# Discord's hard ceiling is 2000 characters and the localized frame costs some
# of them. Rejected at the boundary rather than truncated on send: a broadcast
# that silently says less than what was approved is the failure this feature
# exists to avoid.
BROADCAST_BODY_MAX = 1900
# Above this many recipients, the admin must type the count to proceed. Keyed
# on SIZE, not mode, so a 400-person explicit list is gated exactly like ALL.
TYPED_CONFIRM_THRESHOLD = 10
_DUPLICATE_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class Recipients:
    """`ids` is (discord_id, language) pairs -- the language is needed at queue
    time, because the localized frame is applied per recipient there rather
    than at send time, which is what keeps the scheduler's send code
    unchanged."""

    ids: tuple[tuple[int, str], ...]
    unmatched: tuple[str, ...]


async def resolve_recipients(
    session: AsyncSession, mode: BroadcastMode, param: str | None
) -> Recipients:
    """Resolve a mode + param to a concrete recipient set.

    Every mode is RESOLVED, never derived: the set cannot change between the
    preview an admin approved and the send that executes. Modes that would be
    derived -- everyone tracking a concert, followers of a tag -- were
    rejected for exactly that reason, since the count an admin confirmed
    would then be a lie by the time the send ran.
    """
    if mode is BroadcastMode.ALL:
        res = await session.execute(select(User.discord_id, User.language))
        return Recipients(ids=tuple((i, lang) for i, lang in res.all()), unmatched=())

    if mode is BroadcastMode.BATCH:
        if not param:
            return Recipients(ids=(), unmatched=())
        try:
            batch_at = datetime.fromisoformat(param)
        except ValueError:
            # `mode_param` is a free-text field on the compose form, so a
            # typo'd timestamp is ordinary user error, not a server fault.
            # Reported rather than raised, for the same reason an unmatched
            # EXPLICIT id is: this function stays total, the preview shows
            # "0 recipients" plus the offending text, and neither route needs
            # its own try/except to avoid a 500.
            return Recipients(ids=(), unmatched=(param,))
        res = await session.execute(
            select(User.discord_id, User.language)
            .join(DeliveryLog, DeliveryLog.user_id == User.discord_id)
            .where(DeliveryLog.batch_at_utc == batch_at)
            .distinct()
        )
        return Recipients(ids=tuple((i, lang) for i, lang in res.all()), unmatched=())

    # EXPLICIT: report what did not match rather than dropping it silently.
    # Quietly discarding a mistyped id is how you conclude you messaged
    # someone you did not.
    tokens = [t.strip() for t in (param or "").replace(",", " ").split() if t.strip()]
    wanted: list[int] = []
    unmatched: list[str] = []
    for token in tokens:
        if token.isdigit():
            wanted.append(int(token))
        else:
            unmatched.append(token)
    found: dict[int, str] = {}
    if wanted:
        res = await session.execute(
            select(User.discord_id, User.language).where(User.discord_id.in_(wanted))
        )
        found = {i: lang for i, lang in res.all()}
    unmatched += [str(i) for i in wanted if i not in found]
    return Recipients(ids=tuple(found.items()), unmatched=tuple(unmatched))


def _framed_body(body: str, language: str) -> str:
    """The recipient-facing frame, resolved in THEIR language.

    Applied here, at queue time, rather than at send time: one Notification is
    written per recipient anyway and their language is already in hand, so
    pre-framing means the scheduler's plain-text path (`await
    user.send(note.body)`) needs no changes at all.

    The brand itself is never translated, exactly as the language names
    EN/中文/日本語 are not. Only the frame around it is.

    The N_() is load-bearing despite being the identity function: `pybabel
    extract` matches on the CALLED NAME, and `gettext_in` is not one of the
    keywords in babel.cfg, so without the marker this msgid is invisible to the
    ritual -- the next `pybabel update` would file the hand-added entry as
    obsolete and the frame would silently fall back to English for everyone.
    (`gettext_in(locale, "Multiple")` gets away with a bare literal only
    because "Multiple" is independently extracted from three templates.)
    """
    return f"**{gettext_in(language, N_('From dekimasen.app'))}**\n\n{body}"


async def queue_broadcast(
    session: AsyncSession,
    created_by: int,
    mode: BroadcastMode,
    param: str | None,
    body: str,
    now: datetime | None = None,
) -> Broadcast:
    """Write the audit row and one held Notification per recipient.

    Through the outbox, never a direct send (invariant 4). Recipients are
    re-resolved here rather than trusted from the preview form, so
    recipient_count records what was actually queued.
    """
    now = now or _now()
    body = body.strip()
    if not body:
        raise ValueError("broadcast body is empty")
    if len(body) > BROADCAST_BODY_MAX:
        raise ValueError(f"broadcast body exceeds {BROADCAST_BODY_MAX} characters")

    recipients = await resolve_recipients(session, mode, param)
    send_after = now + timedelta(seconds=HOLD_SECONDS)
    broadcast = Broadcast(
        created_by=created_by,
        created_at_utc=now,
        mode=mode,
        mode_param=param,
        body=body,
        recipient_count=len(recipients.ids),
        send_after_utc=send_after,
    )
    session.add(broadcast)
    await session.flush()

    session.add_all(
        [
            Notification(
                user_id=discord_id,
                body=_framed_body(body, language),
                kind="admin_broadcast",
                send_after_utc=send_after,
                broadcast_id=broadcast.id,
            )
            for discord_id, language in recipients.ids
        ]
    )
    await session.flush()
    return broadcast


async def cancel_broadcast(
    session: AsyncSession, broadcast_id: int, now: datetime | None = None
) -> tuple[int, int]:
    """Delete this broadcast's UNSENT notifications. Returns
    (cancelled, already_delivered).

    Both numbers are returned because a tick can drain rows between the click
    and this call. Reporting "cancelled -- 12 of 40 had already been delivered"
    is the point: a rail that lies about what it undid is worse than no rail.
    """
    now = now or _now()
    delivered = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.broadcast_id == broadcast_id,
                Notification.sent_at_utc.is_not(None),
            )
        )
    ).scalar_one()
    res = await session.execute(
        delete(Notification).where(
            Notification.broadcast_id == broadcast_id,
            Notification.sent_at_utc.is_(None),
        )
    )
    row = await session.get(Broadcast, broadcast_id)
    if row is not None and row.cancelled_at_utc is None:
        row.cancelled_at_utc = now
    await session.flush()
    return (res.rowcount or 0, delivered)


async def recent_broadcasts(session: AsyncSession, limit: int = 50) -> list[Broadcast]:
    res = await session.execute(
        select(Broadcast).order_by(Broadcast.created_at_utc.desc()).limit(limit)
    )
    return list(res.scalars())


async def duplicate_body_recently(
    session: AsyncSession, body: str, now: datetime | None = None
) -> bool:
    """Has this exact text gone out in the last hour? Catches the stale-tab
    resubmit and answers "did I already send this?" during an incident."""
    now = now or _now()
    res = await session.execute(
        select(Broadcast.id).where(
            Broadcast.body == body.strip(),
            Broadcast.created_at_utc >= now - _DUPLICATE_WINDOW,
        )
    )
    return res.first() is not None
