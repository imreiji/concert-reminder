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
    ConcertTag,
    Notification,
    ReminderPreset,
    ReminderQueue,
    ReminderRule,
    Tag,
    TagMember,
    TagSubscription,
    User,
    Window,
)
from app.domain.reminders import DayInfo, RuleInfo, WindowInfo, plan_for_rule
from app.domain.types import Anchor, TagKind


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


# ── Tags ─────────────────────────────────────────────────────────────────


async def find_tag_by_name(session: AsyncSession, name: str) -> Tag | None:
    from sqlalchemy import func as sa_func

    res = await session.execute(
        select(Tag).where(sa_func.lower(Tag.name) == name.strip().lower())
    )
    return res.scalar_one_or_none()


async def group_members(session: AsyncSession, group_tag_id: int) -> list[Tag]:
    res = await session.execute(
        select(Tag)
        .join(TagMember, Tag.id == TagMember.member_tag_id)
        .where(TagMember.group_tag_id == group_tag_id)
        .order_by(Tag.name)
    )
    return list(res.scalars())


async def _is_attached(session: AsyncSession, concert_id: int, tag_id: int) -> bool:
    res = await session.execute(
        select(ConcertTag).where(
            ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
        )
    )
    return res.scalar_one_or_none() is not None


async def attach_tag(
    session: AsyncSession, concert_id: int, tag: Tag, expand: bool = True
) -> list[Tag]:
    """Attach a tag to a concert. Returns the list of tags newly attached.

    THE EXPANSION RULE (agreed semantics): attaching a GROUP tag also
    attaches every current member — at this moment only. Editors may then
    remove individual members (not performing); nothing re-adds them unless
    the group tag itself is detached and re-attached. Group membership
    edits never touch existing concerts.

    expand=False is for the creation form, where the editor picks artists
    explicitly (pre-checked from the group) — expansion there would undo
    their unchecks.
    """
    added: list[Tag] = []
    if not await _is_attached(session, concert_id, tag.id):
        session.add(ConcertTag(concert_id=concert_id, tag_id=tag.id))
        added.append(tag)
        if expand and tag.kind is TagKind.GROUP:
            for member in await group_members(session, tag.id):
                if not await _is_attached(session, concert_id, member.id):
                    session.add(ConcertTag(concert_id=concert_id, tag_id=member.id))
                    added.append(member)
    await session.flush()
    return added


async def detach_tag(session: AsyncSession, concert_id: int, tag_id: int) -> None:
    res = await session.execute(
        select(ConcertTag).where(
            ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
        )
    )
    row = res.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


# ── Presets & subscriptions (Phase 10) ───────────────────────────────────


async def apply_preset(
    session: AsyncSession, user_id: int, concert_id: int, preset: ReminderPreset
) -> int:
    """Create this preset's rules on a concert (idempotent per item).

    An item is skipped if the user already has an identical rule
    (same concert, anchor, offsets) — repeated clicks are harmless.
    Returns how many rules were actually created.
    """
    await session.refresh(preset, ["items"])
    existing = await session.execute(
        select(ReminderRule).where(
            ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id
        )
    )
    have = {(r.anchor, r.offset_days, r.offset_hours) for r in existing.scalars()}

    created = 0
    for item in preset.items:
        key = (item.anchor, item.offset_days, item.offset_hours)
        if key in have:
            continue
        rule = ReminderRule(
            user_id=user_id,
            concert_id=concert_id,
            anchor=item.anchor,
            offset_days=item.offset_days,
            offset_hours=item.offset_hours,
        )
        session.add(rule)
        await session.flush()
        await sync_rule(session, rule)
        have.add(key)
        created += 1
    return created


async def handle_newly_tagged(
    session: AsyncSession, concert: Concert, new_tags: list[Tag]
) -> int:
    """The notify-and-apply pipeline. Called when tags are attached to a concert.

    For every user subscribed to any of the newly attached tags:
      * a user who ALREADY has rules on this concert is skipped entirely
        (they know about it; prevents double-apply when a second matching
        tag lands later)
      * otherwise: linked preset auto-applies, and if notify is on, a DM
        notice is queued.
    A user matched by several tags at once (group + members) is handled once;
    if several matched subscriptions carry presets, the earliest-created wins.
    Returns the number of users processed.
    """
    if not new_tags:
        return 0
    res = await session.execute(
        select(TagSubscription)
        .where(TagSubscription.tag_id.in_([t.id for t in new_tags]))
        .order_by(TagSubscription.id)
    )
    subs_by_user: dict[int, list[TagSubscription]] = {}
    for sub in res.scalars():
        subs_by_user.setdefault(sub.user_id, []).append(sub)

    tag_names = ", ".join(t.name for t in new_tags)
    processed = 0
    for user_id, subs in subs_by_user.items():
        already = await session.execute(
            select(ReminderRule.id)
            .where(ReminderRule.user_id == user_id, ReminderRule.concert_id == concert.id)
            .limit(1)
        )
        if already.scalar_one_or_none() is not None:
            continue

        preset = None
        for sub in subs:  # earliest-created subscription with a preset wins
            if sub.preset_id is not None:
                preset = await session.get(ReminderPreset, sub.preset_id)
                if preset is not None:
                    break
        n = 0
        if preset is not None:
            n = await apply_preset(session, user_id, concert.id, preset)

        if any(s.notify for s in subs):
            if preset is not None:
                tail = f"your preset \u201c{preset.name}\u201d set {n} reminder(s)."
            else:
                tail = "no preset linked \u2014 set reminders on the site."
            session.add(Notification(
                user_id=user_id,
                concert_id=concert.id,
                kind="new_event",
                body=(  # plain-text fallback only; normally rendered as an embed
                    f"\U0001f195 New event: **{concert.title}** (tagged: {tag_names}) \u2014 {tail}"
                ),
            ))
        processed += 1
    await session.flush()
    return processed


async def due_notifications(
    session: AsyncSession, limit: int = 100
) -> list[Notification]:
    res = await session.execute(
        select(Notification)
        .where(Notification.sent_at_utc.is_(None))
        .order_by(Notification.created_at)
        .limit(limit)
    )
    return list(res.scalars())


async def mark_notification_sent(session: AsyncSession, notification_id: int) -> None:
    row = await session.get(Notification, notification_id)
    if row is not None:
        row.sent_at_utc = _now()
        await session.flush()


# ── DM button actions (Phase 12) — pure DB logic, discord-free ───────────


async def get_default_preset(session: AsyncSession, user_id: int) -> ReminderPreset | None:
    res = await session.execute(
        select(ReminderPreset).where(
            ReminderPreset.user_id == user_id, ReminderPreset.is_default.is_(True)
        )
    )
    return res.scalar_one_or_none()


async def set_default_preset(session: AsyncSession, user_id: int, preset_id: int) -> None:
    res = await session.execute(
        select(ReminderPreset).where(ReminderPreset.user_id == user_id)
    )
    for p in res.scalars():
        p.is_default = p.id == preset_id
    await session.flush()


async def apply_default_preset(
    session: AsyncSession, user_id: int, concert_id: int
) -> tuple[str, int]:
    """[Set my reminders] button. Returns (status, rules_created):
    'no_default' | 'already_covered' | 'applied'."""
    preset = await get_default_preset(session, user_id)
    if preset is None:
        return "no_default", 0
    existing = await session.execute(
        select(ReminderRule.id)
        .where(ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id)
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return "already_covered", 0
    n = await apply_preset(session, user_id, concert_id, preset)
    return "applied", n


async def remove_user_rules(session: AsyncSession, user_id: int, concert_id: int) -> int:
    """[Remove these reminders] button. Deletes the user's rules on a concert
    (queue rows cascade). Returns how many rules were removed."""
    res = await session.execute(
        select(ReminderRule).where(
            ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id
        )
    )
    rules = list(res.scalars())
    for rule in rules:
        await session.delete(rule)
    await session.flush()
    return len(rules)


async def snooze_reminder(
    session: AsyncSession, queue_id: int, user_id: int, now: datetime | None = None
) -> str:
    """[Snooze 1 day] button. Re-arms a delivered reminder for +24h, capped so
    it can never fire after the deadline it's about.
    Returns: 'snoozed' | 'too_close' | 'not_yours' | 'gone'."""
    from datetime import timedelta

    now = now or _now()
    row = await session.get(ReminderQueue, queue_id)
    if row is None:
        return "gone"
    rule = await session.get(ReminderRule, row.rule_id)
    if rule is None or rule.user_id != user_id:
        return "not_yours"

    new_fire = now + timedelta(hours=24)
    anchor_at: datetime | None = None
    if row.window_id is not None:
        window = await session.get(Window, row.window_id)
        if window is not None:
            anchor_at = (
                window.opens_at_utc if row.anchor is Anchor.OPENS else window.closes_at_utc
            )
    elif row.day_id is not None:
        day = await session.get(ConcertDay, row.day_id)
        anchor_at = day.starts_at_utc if day else None

    if anchor_at is not None and anchor_at > now and new_fire >= anchor_at:
        return "too_close"  # snoozing would sleep through the deadline itself

    row.fire_at_utc = new_fire
    row.sent_at_utc = None  # re-arm
    await session.flush()
    return "snoozed"


@dataclass(frozen=True)
class NoticeContext:
    """Everything needed to render the new-event embed for one recipient."""

    concert_id: int
    title: str
    tags_line: str
    venue: str | None
    first_deadline_label: str | None
    first_deadline_at: datetime | None
    user_timezone: str
    user_has_rules: bool
    user_has_default_preset: bool


async def notice_context(
    session: AsyncSession, concert_id: int, user_id: int
) -> NoticeContext | None:
    concert = await session.get(Concert, concert_id)
    if concert is None:
        return None
    await session.refresh(concert, ["tags", "windows"])
    now = _now()
    upcoming = [
        (w, w.closes_at_utc or w.opens_at_utc)
        for w in concert.windows
        if (w.closes_at_utc or w.opens_at_utc) and (w.closes_at_utc or w.opens_at_utc) > now
    ]
    upcoming.sort(key=lambda pair: pair[1])
    first = upcoming[0] if upcoming else None

    non_venue = [t.name for t in concert.tags if t.kind.value != "venue"]
    venues = [t.name for t in concert.tags if t.kind.value == "venue"]
    user = await session.get(User, user_id)
    has_rules = (await session.execute(
        select(ReminderRule.id)
        .where(ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id)
        .limit(1)
    )).scalar_one_or_none() is not None

    return NoticeContext(
        concert_id=concert_id,
        title=concert.title,
        tags_line=" · ".join(non_venue),
        venue=("Multiple" if len(venues) > 1 else (venues[0] if venues else concert.venue)),
        first_deadline_label=first[0].label if first else None,
        first_deadline_at=first[1] if first else None,
        user_timezone=user.timezone if user else "America/Moncton",
        user_has_rules=has_rules,
        user_has_default_preset=await get_default_preset(session, user_id) is not None,
    )
