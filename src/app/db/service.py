"""Queue synchronization and reminder retrieval.

This is the only module that both touches the database AND calls the domain
planner. Everything here is built around one idea: *re-planning must always
be safe*. Any edit to a concert triggers a full re-sync of affected rules,
and the sync semantics below turn that into upserts, not duplicates.

Sync semantics (per rule):
  * planned & not queued          -> insert
  * planned & queued, unsent      -> update fire_at if it moved
  * planned & queued, ALREADY SENT:
        - if the new fire time is in the future (deadline was postponed),
          re-arm it: clear sent_at and update fire_at. A moved deadline
          deserves a fresh reminder.
        - otherwise leave it alone (delivered, done).
  * queued, unsent, no longer planned -> delete (window removed / now past)
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Concert,
    ConcertDay,
    ReminderQueue,
    ReminderRule,
    User,
    Window,
)
from app.domain.reminders import DayInfo, RuleInfo, WindowInfo, plan_for_rule
from app.domain.types import Anchor


def _now() -> datetime:
    return datetime.now(UTC)


# ── Users ────────────────────────────────────────────────────────────────


async def ensure_user(session: AsyncSession, discord_id: int, username: str) -> User:
    """Get-or-create the user row; refresh the username while we're at it."""
    user = await session.get(User, discord_id)
    if user is None:
        user = User(discord_id=discord_id, username=username)
        session.add(user)
        await session.flush()
    elif user.username != username:
        user.username = username
    return user


# ── Adapters: ORM -> domain dataclasses ──────────────────────────────────


def _window_info(w: Window) -> WindowInfo:
    return WindowInfo(id=w.id, opens_at_utc=w.opens_at_utc, closes_at_utc=w.closes_at_utc)


def _day_info(d: ConcertDay) -> DayInfo:
    return DayInfo(id=d.id, starts_at_utc=d.starts_at_utc)


def _rule_info(r: ReminderRule) -> RuleInfo:
    return RuleInfo(
        id=r.id,
        anchor=r.anchor,
        offset_days=r.offset_days,
        offset_hours=r.offset_hours,
        window_id=r.window_id,
        concert_id=r.concert_id,
    )


# ── Queue sync ───────────────────────────────────────────────────────────


async def sync_rule(session: AsyncSession, rule: ReminderRule, now: datetime | None = None) -> None:
    """Reconcile reminder_queue with what this rule currently implies."""
    now = now or _now()

    # Gather the windows/days in this rule's scope.
    if rule.window_id is not None:
        window = await session.get(Window, rule.window_id)
        windows = [_window_info(window)] if window else []
        days: list[DayInfo] = []
    else:
        wres = await session.execute(select(Window).where(Window.concert_id == rule.concert_id))
        dres = await session.execute(
            select(ConcertDay).where(ConcertDay.concert_id == rule.concert_id)
        )
        windows = [_window_info(w) for w in wres.scalars()]
        days = [_day_info(d) for d in dres.scalars()]

    planned = plan_for_rule(_rule_info(rule), windows, days, now)
    planned_by_key = {(p.window_id or 0, p.day_id or 0, p.anchor): p for p in planned}

    qres = await session.execute(select(ReminderQueue).where(ReminderQueue.rule_id == rule.id))
    existing = list(qres.scalars())
    existing_keys = set()

    for row in existing:
        key = (row.window_id or 0, row.day_id or 0, row.anchor)
        existing_keys.add(key)
        p = planned_by_key.get(key)
        if p is None:
            if row.sent_at_utc is None:
                await session.delete(row)  # no longer planned and never sent
            continue
        if row.sent_at_utc is None:
            row.fire_at_utc = p.fire_at_utc  # cheap even if unchanged
        elif p.fire_at_utc > now:
            # Deadline postponed after we already reminded: re-arm.
            row.fire_at_utc = p.fire_at_utc
            row.sent_at_utc = None

    for key, p in planned_by_key.items():
        if key not in existing_keys:
            session.add(
                ReminderQueue(
                    rule_id=rule.id,
                    window_id=p.window_id,
                    day_id=p.day_id,
                    anchor=p.anchor,
                    fire_at_utc=p.fire_at_utc,
                )
            )
    await session.flush()


async def sync_concert(
    session: AsyncSession, concert_id: int, now: datetime | None = None
) -> int:
    """Re-sync every rule touching this concert (called after any edit).

    Covers concert-scoped rules and window-scoped rules on its windows.
    Returns the number of rules synced.
    """
    res = await session.execute(
        select(ReminderRule)
        .outerjoin(Window, ReminderRule.window_id == Window.id)
        .where(
            (ReminderRule.concert_id == concert_id) | (Window.concert_id == concert_id)
        )
    )
    rules = list(res.scalars())
    for rule in rules:
        await sync_rule(session, rule, now)
    return len(rules)


# ── Retrieval for the scheduler and /upcoming ────────────────────────────


@dataclass(frozen=True)
class DueReminder:
    """Everything the scheduler needs to deliver one reminder."""

    queue_id: int
    discord_id: int
    user_timezone: str
    concert_title: str
    anchor: Anchor
    fire_at_utc: datetime
    # window-anchored:
    window_label: str | None = None
    window_kind: str | None = None
    anchor_time_utc: datetime | None = None
    url: str | None = None
    # day-anchored:
    day_label: str | None = None


async def due_reminders(
    session: AsyncSession, now: datetime | None = None, limit: int = 100
) -> list[DueReminder]:
    now = now or _now()
    res = await session.execute(
        select(ReminderQueue)
        .where(ReminderQueue.sent_at_utc.is_(None), ReminderQueue.fire_at_utc <= now)
        .order_by(ReminderQueue.fire_at_utc)
        .limit(limit)
    )
    # N+1 gets below are deliberate: a due batch is tiny (usually 0-5 rows/minute)
    # and session identity-map caching absorbs repeats. Optimize only if it hurts.
    rows = list(res.scalars())
    out: list[DueReminder] = []
    for row in rows:
        rule = await session.get(ReminderRule, row.rule_id)
        user = await session.get(User, rule.user_id)
        window = await session.get(Window, row.window_id) if row.window_id else None
        day = await session.get(ConcertDay, row.day_id) if row.day_id else None
        parent = window or day
        concert = await session.get(Concert, parent.concert_id) if parent else None
        if concert is None:
            continue  # orphaned row; cascades should prevent this, but never crash the loop
        out.append(
            DueReminder(
                queue_id=row.id,
                discord_id=user.discord_id,
                user_timezone=user.timezone,
                concert_title=concert.title,
                anchor=row.anchor,
                fire_at_utc=row.fire_at_utc,
                window_label=window.label if window else None,
                window_kind=window.kind.value if window else None,
                anchor_time_utc=(
                    (window.opens_at_utc if row.anchor is Anchor.OPENS else window.closes_at_utc)
                    if window
                    else (day.starts_at_utc if day else None)
                ),
                url=window.url if window else None,
                day_label=day.label if day else None,
            )
        )
    return out


async def mark_sent(session: AsyncSession, queue_id: int, now: datetime | None = None) -> None:
    row = await session.get(ReminderQueue, queue_id)
    if row is not None:
        row.sent_at_utc = now or _now()
        await session.flush()


async def upcoming_windows(
    session: AsyncSession, now: datetime | None = None, horizon_days: int = 14
) -> list[tuple[Concert, Window]]:
    """Windows opening or closing within the horizon — powers /upcoming."""
    from datetime import timedelta

    now = now or _now()
    end = now + timedelta(days=horizon_days)
    res = await session.execute(
        select(Concert, Window)
        .join(Window, Window.concert_id == Concert.id)
        .where(
            (Window.opens_at_utc.between(now, end))
            | (Window.closes_at_utc.between(now, end))
        )
        .order_by(Window.closes_at_utc.is_(None), Window.closes_at_utc, Window.opens_at_utc)
    )
    return [(c, w) for c, w in res.all()]
