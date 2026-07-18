# Cancelled-Leg Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an editor mark an individual concert performance ("leg", i.e. a `ConcertDay` row) cancelled, and have that propagate correctly through reminder planning, display, and the concert's computed date range — without breaking anything that currently assumes every day/round it sees is live.

**Architecture:** One additive boolean column (`ConcertDay.cancelled`). Rounds get no status field of their own — a round is treated as implicitly cancelled when every `ConcertDay` id in its `applies_to` list belongs to a cancelled leg. The pure domain planner (`domain/reminders.py`) never learns the concept of "cancelled"; the service layer filters cancelled legs/rounds out of the candidate lists before handing them to the planner, so the existing "nothing planned → delete the queue row" sync semantics do the reminder-clearing work with zero new suppression logic. A user left with zero remaining reminders on a concert as a direct result of a cancellation gets one DM with a "reinstate" button, following the exact persistent-button pattern already in `bot/views.py`.

**Tech Stack:** FastAPI + Jinja2, SQLAlchemy 2.0 async + Alembic (SQLite), discord.py, pytest-asyncio.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` must both be clean before every commit (project-wide rule, `CLAUDE.md`).
- Never store or compare naive datetimes; the `UTCDateTime` type enforces this at the DB boundary — not touched by this plan, but every test seeding a datetime must use `tzinfo=UTC`.
- After `alembic revision --autogenerate`, review the generated file per `CLAUDE.md`'s migration ritual (replace `app.db.models.UTCDateTime()` with `sa.DateTime()` and drop the `import app.db.models` line) — not expected to apply here since this migration adds only a `Boolean`, but check anyway.
- `domain/reminders.py` must stay pure: no new imports of `sqlalchemy`, `discord`, or the word "cancelled" as a concept. Filtering happens in `db/service.py`, before ORM rows are converted to `RoundInfo`/`DayInfo`.
- Sentence case in all new user-facing copy (existing UI convention).
- Every new page-rendering code path needs at least one logged-in GET render test (existing convention — a missing one shipped a 500 once).
- Spec reference: `docs/superpowers/specs/2026-07-17-cancelled-leg-status-design.md`. Read it before starting if anything below is unclear — it has the full rationale, including the `group_rounds_by_day()` dangling-reference bug this design was built to avoid.

---

## Task 1: `ConcertDay.cancelled` column + migration

**Files:**
- Modify: `src/app/db/models.py` (`ConcertDay` class, line 233)
- Create: one new Alembic revision under `alembic/versions/`
- Test: `tests/test_migration_concert_day_cancelled.py`

**Interfaces:**
- Produces: `ConcertDay.cancelled: bool` (default `False`), read by every later task.

- [ ] **Step 1: Add the column to the model**

In `src/app/db/models.py`, the current `ConcertDay` class (line 233) ends with:

```python
    doors_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    starts_at_utc: Mapped[datetime] = mapped_column(UTCDateTime)

    concert: Mapped[Concert] = relationship(back_populates="days")
```

Change it to:

```python
    doors_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    starts_at_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    # A cancelled leg is never deleted (its rounds' applies_to would dangle --
    # see docs/superpowers/specs/2026-07-17-cancelled-leg-status-design.md)
    # -- only marked. Rounds have no status field of their own: a round is
    # implicitly cancelled when every ConcertDay id in its applies_to is
    # cancelled ("General" rounds with no day association are never
    # auto-cancelled this way).
    cancelled: Mapped[bool] = mapped_column(default=False, server_default="0")

    concert: Mapped[Concert] = relationship(back_populates="days")
```

- [ ] **Step 2: Generate the migration**

Run: `uv run alembic revision --autogenerate -m "concert day cancelled"`

Expected output ends with `Generating ...concert_day_cancelled.py ... done`. Note the generated filename (it starts with a random hex revision id).

- [ ] **Step 3: Review the generated migration**

Open the new file under `alembic/versions/`. It should look like:

```python
"""concert day cancelled

Revision ID: <generated>
Revises: 84977144aad6
Create Date: <generated>
"""
from alembic import op
import sqlalchemy as sa


revision = '<generated>'
down_revision = '84977144aad6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('concert_days', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cancelled', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('concert_days', schema=None) as batch_op:
        batch_op.drop_column('cancelled')
```

This is a pure `Boolean` column — no `app.db.models.UTCDateTime()` reference to fix, no stray `import app.db.models` line. If autogenerate produced something materially different from this shape (e.g. it's not wrapped in `batch_alter_table`, or it invented an index), stop and re-check the model change in Step 1 before continuing — don't hand-edit around a wrong autogenerate.

- [ ] **Step 4: Write the migration test**

Create `tests/test_migration_concert_day_cancelled.py`:

```python
"""Migration test: ConcertDay.cancelled.

Same scratch-DB pattern as every other migration test in this repo
(tests/test_migration_hot_path_indices.py, tests/test_migration_concert_audit.py):
upgrade to the revision right before this one, confirm the column is
absent, upgrade to head, confirm it exists with the right default.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "84977144aad6"  # head immediately before this column


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _columns(con: sqlite3.Connection, table: str) -> dict[str, dict]:
    return {row[1]: {"notnull": row[3], "dflt_value": row[4]} for row in
            con.execute(f"PRAGMA table_info({table})").fetchall()}


def test_concert_day_cancelled_exists_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    assert "cancelled" not in _columns(con, "concert_days")
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    cols = _columns(con, "concert_days")
    assert "cancelled" in cols
    assert cols["cancelled"]["notnull"] == 1
    con.close()
```

- [ ] **Step 5: Run the migration test**

Run: `uv run pytest tests/test_migration_concert_day_cancelled.py -v`
Expected: `1 passed`

- [ ] **Step 6: Apply the migration to the real dev DB**

Run: `uv run alembic upgrade head`
Expected: no output on success (matches this project's existing convention — prior migrations in this session produced no stdout on success either).

- [ ] **Step 7: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing, same count as before plus 1.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/models.py alembic/versions/*_concert_day_cancelled.py tests/test_migration_concert_day_cancelled.py
git commit -m "Add ConcertDay.cancelled column"
```

---

## Task 2: Filter cancelled legs/rounds out of reminder planning

**Files:**
- Modify: `src/app/db/service.py` (`sync_rule`, lines 131–181)
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `ConcertDay.cancelled: bool` (Task 1).
- Produces: `_is_round_cancelled(round_: Round, cancelled_day_ids: set[int]) -> bool` — used again in Task 3.

- [ ] **Step 1: Write the failing tests**

In `tests/test_service.py`, add after the existing `seed()` helper (after line 73, before `async def queue_rows`):

```python
async def seed_two_legs(s) -> tuple[Concert, ConcertDay, ConcertDay, Round, Round, Round]:
    """Two legs (one will be cancelled by the test), three rounds covering
    all three applies_to shapes: tied only to leg A, tied to both legs, and
    General (no day association)."""
    await ensure_user(s, 42, "reiji")
    concert = Concert(title="Two-Leg Tour", event_id="two-leg-tour", created_by=42)
    s.add(concert)
    await s.flush()
    leg_a = ConcertDay(concert_id=concert.id, label="Leg A", starts_at_utc=dt(8, 1, 9))
    leg_b = ConcertDay(concert_id=concert.id, label="Leg B", starts_at_utc=dt(8, 2, 9))
    s.add_all([leg_a, leg_b])
    await s.flush()
    round_a_only = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only",
        closes_at_utc=dt(6, 25), applies_to=[leg_a.id],
    )
    round_both = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Both-legs",
        closes_at_utc=dt(6, 26), applies_to=[leg_a.id, leg_b.id],
    )
    round_general = Round(
        concert_id=concert.id, kind=RoundKind.GENERAL_SALE, label="General",
        closes_at_utc=dt(6, 27),
    )
    s.add_all([round_a_only, round_both, round_general])
    await s.flush()
    return concert, leg_a, leg_b, round_a_only, round_both, round_general
```

Then add these tests right after `test_round_with_all_four_timestamps_syncs_each_anchor_independently` (before the `# ── Concert edit history` section):

```python
# ── Cancelled-leg filtering ──────────────────────────────────────────────


async def test_sync_skips_cancelled_leg_and_its_solely_tied_round(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, concert_id=concert.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    before = {(r.round_id, r.day_id) for r in await queue_rows(session)}
    assert (round_a_only.id, None) in before
    assert (round_both.id, None) in before
    assert (round_general.id, None) in before

    leg_a.cancelled = True
    await session.flush()
    await sync_rule(session, rule, NOW)
    after = {(r.round_id, r.day_id) for r in await queue_rows(session)}
    # A-only is fully cancelled (its one leg is cancelled) -> gone.
    assert (round_a_only.id, None) not in after
    # Both-legs still has leg B live -> untouched.
    assert (round_both.id, None) in after
    # General has no day association -> never affected.
    assert (round_general.id, None) in after


async def test_sync_event_start_rule_skips_cancelled_day(session):
    concert, leg_a, leg_b, _, _, _ = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, concert_id=concert.id, anchor=Anchor.EVENT_START, offset_days=-1)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    before = {r.day_id for r in await queue_rows(session)}
    assert {leg_a.id, leg_b.id} <= before

    leg_a.cancelled = True
    await session.flush()
    await sync_rule(session, rule, NOW)
    after = {r.day_id for r in await queue_rows(session)}
    assert leg_a.id not in after
    assert leg_b.id in after


async def test_sync_round_specific_rule_on_cancelled_round_clears_it(session):
    concert, leg_a, leg_b, round_a_only, _, _ = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert len(await queue_rows(session)) == 1

    leg_a.cancelled = True
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert await queue_rows(session) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_service.py -k "cancelled" -v`
Expected: all 3 FAIL (cancelling `leg_a` currently has no effect on planning — `sync_rule` doesn't know about the `cancelled` column yet).

- [ ] **Step 3: Add the filtering helper and wire it into `sync_rule`**

In `src/app/db/service.py`, the current `sync_rule` (line 131) reads:

```python
async def sync_rule(session: AsyncSession, rule: ReminderRule, now: datetime | None = None) -> None:
    """Reconcile reminder_queue with what this rule currently implies."""
    now = now or _now()

    # Gather the rounds/days in this rule's scope.
    if rule.round_id is not None:
        round_ = await session.get(Round, rule.round_id)
        rounds = [_round_info(round_)] if round_ else []
        days: list[DayInfo] = []
    else:
        rres = await session.execute(select(Round).where(Round.concert_id == rule.concert_id))
        dres = await session.execute(
            select(ConcertDay).where(ConcertDay.concert_id == rule.concert_id)
        )
        rounds = [_round_info(r) for r in rres.scalars()]
        days = [_day_info(d) for d in dres.scalars()]
```

Add this helper right after `_rule_info` (line 125, before the `# ── Queue sync` comment):

```python
def _is_round_cancelled(round_: Round, cancelled_day_ids: set[int]) -> bool:
    """A round is implicitly cancelled when every leg it applies to is
    cancelled. A "General" round (empty/None applies_to) is never
    auto-cancelled this way -- it isn't tied to any specific leg."""
    if not round_.applies_to:
        return False
    return all(day_id in cancelled_day_ids for day_id in round_.applies_to)
```

Then replace the `sync_rule` body shown above with:

```python
async def sync_rule(session: AsyncSession, rule: ReminderRule, now: datetime | None = None) -> None:
    """Reconcile reminder_queue with what this rule currently implies."""
    now = now or _now()

    # Gather the rounds/days in this rule's scope. Cancelled legs and rounds
    # implicitly cancelled by them (see _is_round_cancelled) are filtered
    # out here, before domain/reminders.py ever sees them -- the pure
    # planner never learns the concept of "cancelled"; it just sees fewer
    # candidates, and the existing "nothing planned -> delete" sync
    # semantics clear the reminders with no new suppression logic.
    if rule.round_id is not None:
        round_ = await session.get(Round, rule.round_id)
        if round_ is None:
            rounds = []
        else:
            cancelled_day_ids = set((await session.execute(
                select(ConcertDay.id).where(
                    ConcertDay.concert_id == round_.concert_id,
                    ConcertDay.cancelled.is_(True),
                )
            )).scalars())
            rounds = [] if _is_round_cancelled(round_, cancelled_day_ids) else [_round_info(round_)]
        days: list[DayInfo] = []
    else:
        rres = await session.execute(select(Round).where(Round.concert_id == rule.concert_id))
        dres = await session.execute(
            select(ConcertDay).where(ConcertDay.concert_id == rule.concert_id)
        )
        all_rounds = list(rres.scalars())
        all_days = list(dres.scalars())
        cancelled_day_ids = {d.id for d in all_days if d.cancelled}
        rounds = [
            _round_info(r) for r in all_rounds if not _is_round_cancelled(r, cancelled_day_ids)
        ]
        days = [_day_info(d) for d in all_days if not d.cancelled]
```

The rest of `sync_rule` (the `plan_for_rule(...)` call onward) is unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_service.py -k "cancelled" -v`
Expected: `3 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_service.py
git commit -m "Filter cancelled legs and their implicitly-cancelled rounds out of reminder planning"
```

---

## Task 3: Notification-on-cancel + reinstate service functions

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `_is_round_cancelled` (Task 2), `Notification` model (existing).
- Produces:
  - `async def notify_newly_cancelled_legs(session, concert_id: int, newly_cancelled_day_ids: set[int], now: datetime | None = None) -> int`
  - `async def reinstate_user_rules(session, user_id: int, concert_id: int, now: datetime | None = None) -> int`
  - `@dataclass(frozen=True) class LegCancelledContext: concert_id: int; event_id: str; title: str`
  - `async def leg_cancelled_context(session, concert_id: int) -> LegCancelledContext | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_service.py`, right after the Task 2 tests:

```python
# ── Notification on cancel + reinstate ───────────────────────────────────


async def test_notify_newly_cancelled_legs_notifies_only_users_left_with_nothing(session):
    concert, leg_a, leg_b, round_a_only, round_both, _ = await seed_two_legs(session)
    # user 42: a rule only on the A-only round -- will lose everything.
    rule_a_only = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.CLOSES, offset_days=0)
    # user 43: a concert-wide rule -- still has round_both + round_general on leg B.
    await ensure_user(session, 43, "other-fan")
    rule_concert_wide = ReminderRule(
        user_id=43, concert_id=concert.id, anchor=Anchor.CLOSES, offset_days=0
    )
    session.add_all([rule_a_only, rule_concert_wide])
    await session.flush()
    await sync_rule(session, rule_a_only, NOW)
    await sync_rule(session, rule_concert_wide, NOW)

    leg_a.cancelled = True
    await session.flush()
    n = await notify_newly_cancelled_legs(session, concert.id, {leg_a.id}, NOW)
    await session.commit()

    assert n == 1
    notes = list((await session.execute(select(Notification))).scalars())
    assert len(notes) == 1
    assert notes[0].user_id == 42
    assert notes[0].kind == "leg_cancelled"
    assert notes[0].concert_id == concert.id


async def test_notify_newly_cancelled_legs_noop_when_no_new_cancellations(session):
    concert, leg_a, _, _, _, _ = await seed_two_legs(session)
    n = await notify_newly_cancelled_legs(session, concert.id, set(), NOW)
    assert n == 0
    assert list((await session.execute(select(Notification))).scalars()) == []


async def test_reinstate_user_rules_resyncs_when_uncancelled(session):
    concert, leg_a, leg_b, round_a_only, _, _ = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    leg_a.cancelled = True
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert await queue_rows(session) == []  # cleared

    leg_a.cancelled = False  # editor un-cancels it
    await session.flush()
    n = await reinstate_user_rules(session, 42, concert.id, NOW)
    assert n == 1  # one rule re-synced
    assert len(await queue_rows(session)) == 1  # re-armed


async def test_reinstate_user_rules_is_a_noop_while_still_cancelled(session):
    concert, leg_a, leg_b, round_a_only, _, _ = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    leg_a.cancelled = True
    await session.flush()

    n = await reinstate_user_rules(session, 42, concert.id, NOW)
    assert n == 1  # the rule was re-synced...
    assert await queue_rows(session) == []  # ...but nothing gets planned, still cancelled


async def test_leg_cancelled_context_reads_concert_title(session):
    concert, _, _, _, _, _ = await seed_two_legs(session)
    ctx = await leg_cancelled_context(session, concert.id)
    assert ctx.title == "Two-Leg Tour"
    assert ctx.event_id == "two-leg-tour"
    assert ctx.concert_id == concert.id


async def test_leg_cancelled_context_none_for_missing_concert(session):
    assert await leg_cancelled_context(session, 999) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_service.py -k "notify_newly_cancelled or reinstate_user_rules or leg_cancelled_context" -v`
Expected: all FAIL with `ImportError`/`NameError` (these functions don't exist yet).

- [ ] **Step 3: Implement the three functions**

In `src/app/db/service.py`, add this block right after `sync_concert` (after line 202, before the `# ── Retrieval for the scheduler and /upcoming` comment):

```python
async def notify_newly_cancelled_legs(
    session: AsyncSession,
    concert_id: int,
    newly_cancelled_day_ids: set[int],
    now: datetime | None = None,
) -> int:
    """Call BEFORE sync_concert (which will delete the now-unplanned queue
    rows for these legs). Queues one Notification per user who is about to
    lose EVERY one of their unsent reminders on this concert as a direct
    result of these legs being newly cancelled -- a user with other live
    legs/rounds to fall back on gets nothing. Returns how many notifications
    were queued."""
    if not newly_cancelled_day_ids:
        return 0
    now = now or _now()

    all_cancelled_day_ids = set((await session.execute(
        select(ConcertDay.id).where(
            ConcertDay.concert_id == concert_id, ConcertDay.cancelled.is_(True)
        )
    )).scalars())
    rounds = list(
        (await session.execute(select(Round).where(Round.concert_id == concert_id))).scalars()
    )
    affected_round_ids = {
        r.id for r in rounds
        if set(r.applies_to or []) & newly_cancelled_day_ids
        and _is_round_cancelled(r, all_cancelled_day_ids)
    }

    res = await session.execute(
        select(ReminderRule.user_id, ReminderQueue.id)
        .join(ReminderQueue, ReminderQueue.rule_id == ReminderRule.id)
        .where(
            ReminderQueue.sent_at_utc.is_(None),
            (ReminderQueue.day_id.in_(newly_cancelled_day_ids))
            | (ReminderQueue.round_id.in_(affected_round_ids)),
        )
    )
    doomed_by_user: dict[int, set[int]] = {}
    for user_id, queue_id in res.all():
        doomed_by_user.setdefault(user_id, set()).add(queue_id)
    if not doomed_by_user:
        return 0

    concert = await session.get(Concert, concert_id)
    queued = 0
    for user_id, doomed_ids in doomed_by_user.items():
        other_row = (await session.execute(
            select(ReminderQueue.id)
            .join(ReminderRule, ReminderQueue.rule_id == ReminderRule.id)
            .outerjoin(Round, ReminderRule.round_id == Round.id)
            .where(
                ReminderRule.user_id == user_id,
                (ReminderRule.concert_id == concert_id) | (Round.concert_id == concert_id),
                ReminderQueue.sent_at_utc.is_(None),
                ReminderQueue.id.not_in(doomed_ids),
            )
            .limit(1)
        )).scalar_one_or_none()
        if other_row is not None:
            continue  # other live legs/rounds on this concert -- no notice
        session.add(Notification(
            user_id=user_id,
            body=f"A performance for {concert.title} was cancelled, and your "
                 f"reminder(s) for it were cleared.",
            concert_id=concert_id,
            kind="leg_cancelled",
            created_at=now,
        ))
        queued += 1
    await session.flush()
    return queued


async def reinstate_user_rules(
    session: AsyncSession, user_id: int, concert_id: int, now: datetime | None = None
) -> int:
    """[Reinstate my reminders] button, after a leg-cancellation notice.
    Re-syncs this user's still-existing rules on this concert -- if the
    cancelled leg is still cancelled, sync finds nothing to plan for it and
    this is a no-op; if an editor has since un-cancelled it, the reminder
    re-arms normally. Never deletes or recreates ReminderRule rows -- they
    were never touched by the cancellation in the first place. Returns how
    many rules were re-synced."""
    res = await session.execute(
        select(ReminderRule)
        .outerjoin(Round, ReminderRule.round_id == Round.id)
        .where(
            ReminderRule.user_id == user_id,
            (ReminderRule.concert_id == concert_id) | (Round.concert_id == concert_id),
        )
    )
    rules = list(res.scalars())
    for rule in rules:
        await sync_rule(session, rule, now)
    return len(rules)
```

Then add this dataclass + fetcher right after the `NoticeContext`/`notice_context` pair (search for `async def notice_context` in the file and add this immediately after that function ends):

```python
@dataclass(frozen=True)
class LegCancelledContext:
    """Everything needed to render the leg-cancellation embed."""

    concert_id: int
    event_id: str
    title: str


async def leg_cancelled_context(session: AsyncSession, concert_id: int) -> LegCancelledContext | None:
    concert = await session.get(Concert, concert_id)
    if concert is None:
        return None
    return LegCancelledContext(concert_id=concert.id, event_id=concert.event_id, title=concert.title)
```

Update the test file's imports at the top of `tests/test_service.py` (the `from app.db.service import (...)` block) to add: `leg_cancelled_context`, `notify_newly_cancelled_legs`, `reinstate_user_rules`. Also add `Notification` to the `from app.db.models import (...)` line.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_service.py -k "notify_newly_cancelled or reinstate_user_rules or leg_cancelled_context" -v`
Expected: `6 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_service.py
git commit -m "Add leg-cancellation notification and reinstate-reminders service functions"
```

---

## Task 4: Discord button + scheduler dispatch for the cancellation notice

**Files:**
- Modify: `src/app/bot/views.py`
- Modify: `src/app/bot/messages.py`
- Modify: `src/app/scheduler/loop.py`
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: `reinstate_user_rules`, `leg_cancelled_context`, `LegCancelledContext` (Task 3).
- Produces: `ReinstateRemindersButton` (added to `DYNAMIC_ITEMS`), `build_leg_cancelled_message(ctx) -> tuple[discord.Embed, discord.ui.View]`.

- [ ] **Step 1: Write the failing scheduler test**

Add to `tests/test_presets.py`, right after `test_scheduler_delivers_notifications`:

```python
async def test_scheduler_delivers_leg_cancelled_notice_with_reinstate_button(client):
    """A leg_cancelled notification drains through the same tick as a
    new_event one, but renders the cancellation embed + reinstate button."""
    from app.db.models import Concert, Notification
    from app.scheduler.loop import tick

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Cancelled Tour", "event_id": "cancelled-tour"})
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
        s.add(Notification(
            user_id=FAN_ID, body="fallback text", concert_id=concert.id, kind="leg_cancelled",
        ))
        await s.commit()

    sent = []

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            sent.append((embed.title if embed is not None else body, view))

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    import app.scheduler.loop as loop_mod

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)
    delivered = await tick(FakeBot())

    assert delivered == 1
    title, view = sent[0]
    assert "Cancelled Tour" in title
    # discord.ui.DynamicItem only proxies custom_id (not .label) -- checking
    # custom_id is also the more precise assertion, since it identifies
    # exactly which button this is, not just its display text.
    custom_ids = [getattr(item, "custom_id", None) for item in view.children]
    assert any(cid and cid.startswith("dk:reinstate:") for cid in custom_ids)
    notes = await _all(client.db, Notification)
    assert notes[0].sent_at_utc is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_presets.py -k "leg_cancelled_notice" -v`
Expected: FAIL with `sqlalchemy.exc.IntegrityError` or similar — `Notification.kind = "leg_cancelled"` has nowhere to dispatch to yet, and `ReinstateRemindersButton` doesn't exist.

- [ ] **Step 3: Add the button to `bot/views.py`**

In `src/app/bot/views.py`, add this import to the existing `from app.db.service import (...)` block:

```python
from app.db.service import (
    apply_default_preset,
    reinstate_user_rules,
    remove_user_rules,
    snooze_reminder,
)
```

Add this class right after `RemoveRemindersButton` (before `class ShowDeadlinesButton`):

```python
class ReinstateRemindersButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:reinstate:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Reinstate my reminders",
            style=discord.ButtonStyle.primary,
            custom_id=f"dk:reinstate:{concert_id}",
        ))
        self.concert_id = concert_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["cid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            n = await reinstate_user_rules(session, interaction.user.id, self.concert_id)
            await session.commit()
        msg = (
            f"Reinstated {n} reminder(s) — you'll be notified again per your existing "
            "settings for any that are still active."
            if n else "You had no reminders set up on this event."
        )
        await interaction.response.send_message(msg)
```

Update the `DYNAMIC_ITEMS` list at the bottom of the file:

```python
DYNAMIC_ITEMS = [
    ApplyDefaultButton, RemoveRemindersButton, ReinstateRemindersButton, ShowDeadlinesButton,
    SnoozeButton,
]
```

Also update the module docstring's `custom_id` namespace list at the top of the file to add:

```
    dk:reinstate:{concert_id} re-sync the clicking user's rules on this concert
```

- [ ] **Step 4: Add the message builder to `bot/messages.py`**

Add this function to `src/app/bot/messages.py`, right after `build_new_event_message`:

```python
def build_leg_cancelled_message(ctx) -> tuple:
    """(embed, view) for a leg-cancellation notice. ctx: service.LegCancelledContext."""
    import discord

    from app.bot.views import ReinstateRemindersButton
    from app.config import settings

    embed = discord.Embed(
        title=f"🚫 {ctx.title}",
        description="A performance you had a reminder for was cancelled, and it's been cleared.",
        color=0xB3261E,
    )
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Open on dekimasen.app",
        url=f"{settings.base_url}/concerts/{ctx.event_id}",
    ))
    view.add_item(ReinstateRemindersButton(ctx.concert_id))
    return embed, view
```

- [ ] **Step 5: Teach the scheduler to dispatch on `note.kind`**

In `src/app/scheduler/loop.py`, add `leg_cancelled_context` to the `from app.db.service import (...)` block and `build_leg_cancelled_message` to the `from app.bot.messages import (...)` line:

```python
from app.bot.messages import build_leg_cancelled_message, build_new_event_message, build_reminder_message
from app.db.service import (
    DueReminder,
    NoticeContext,
    due_notifications,
    due_reminders,
    leg_cancelled_context,
    mark_notification_sent,
    mark_sent,
    notice_context,
)
```

Replace `_notification_context` (currently):

```python
async def _notification_context(session, note) -> NoticeContext | None:
    """DB-bound prep for one notification's message payload -- reads the
    session, so callers must run this sequentially, never concurrently."""
    return await notice_context(session, note.concert_id, note.user_id) if note.concert_id else None
```

with:

```python
async def _notification_context(session, note):
    """DB-bound prep for one notification's message payload -- reads the
    session, so callers must run this sequentially, never concurrently.
    Dispatches on note.kind since different notice kinds need different
    context shapes (a leg-cancellation notice doesn't need the new-event
    context's subscriber-state fields, and vice versa)."""
    if note.kind == "leg_cancelled":
        return await leg_cancelled_context(session, note.concert_id) if note.concert_id else None
    return await notice_context(session, note.concert_id, note.user_id) if note.concert_id else None
```

Replace `_send_notification` (currently):

```python
async def _send_notification(bot, note, ctx: NoticeContext | None) -> bool:
    """Send a notice DM. Structured (ctx set) -> rich embed with the
    state-aware buttons; otherwise the plain-text fallback body. Same
    policy as deliver(): only success or a permanent failure clears the
    row. Pure Discord I/O -- no session access, safe to run concurrently."""
    try:
        user = bot.get_user(note.user_id) or await bot.fetch_user(note.user_id)
        if ctx is not None:
            embed, view = build_new_event_message(ctx)
            await user.send(embed=embed, view=view)
        else:
            await user.send(note.body)
        return True
    except discord.Forbidden:
        log.warning("user %s has DMs closed; dropping notification", note.user_id)
        return True
    except discord.HTTPException as e:
        log.error("transient notification failure for user %s: %s", note.user_id, e)
        return False
```

with:

```python
async def _send_notification(bot, note, ctx) -> bool:
    """Send a notice DM. Structured (ctx set) -> rich embed with the
    state-aware buttons; otherwise the plain-text fallback body. Same
    policy as deliver(): only success or a permanent failure clears the
    row. Pure Discord I/O -- no session access, safe to run concurrently."""
    try:
        user = bot.get_user(note.user_id) or await bot.fetch_user(note.user_id)
        if ctx is not None and note.kind == "leg_cancelled":
            embed, view = build_leg_cancelled_message(ctx)
            await user.send(embed=embed, view=view)
        elif ctx is not None:
            embed, view = build_new_event_message(ctx)
            await user.send(embed=embed, view=view)
        else:
            await user.send(note.body)
        return True
    except discord.Forbidden:
        log.warning("user %s has DMs closed; dropping notification", note.user_id)
        return True
    except discord.HTTPException as e:
        log.error("transient notification failure for user %s: %s", note.user_id, e)
        return False
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_presets.py -k "leg_cancelled_notice" -v`
Expected: `1 passed`

- [ ] **Step 7: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/bot/views.py src/app/bot/messages.py src/app/scheduler/loop.py tests/test_presets.py
git commit -m "Add reinstate-reminders Discord button and leg-cancellation notice dispatch"
```

---

## Task 5: Editing UX — `day_cancelled` on the concert edit page

**Files:**
- Modify: `src/app/web/routes/concerts.py` (`apply_day_fields`, `build_day`, `edit_concert`)
- Modify: `src/app/web/templates/concert_edit.html`
- Test: `tests/test_crud.py`

**Interfaces:**
- Consumes: `notify_newly_cancelled_legs` (Task 3), `sync_concert` (existing).
- Produces: `day_cancelled` form field name, `apply_day_fields(..., cancelled: str = "false")`, `build_day(..., cancelled: str = "false")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_crud.py`, after `test_nav_add_link_shown_only_to_editors` (or after the duplicate-concert tests if those come later in the file — add at the end of the file):

```python
# ── Cancelled legs ────────────────────────────────────────────────────────


async def test_edit_page_can_mark_a_day_cancelled(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id

    r = client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        day = await s.get(ConcertDay, day_id)
        assert day.cancelled is True

    # flipping it back to Scheduled un-cancels it
    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["false"],
        },
    )
    async with client.db() as s:
        day = await s.get(ConcertDay, day_id)
        assert day.cancelled is False


async def test_edit_page_defaults_new_day_rows_to_not_cancelled(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["false"],
        },
    )
    async with client.db() as s:
        day = (await s.execute(select(ConcertDay))).scalar_one()
        assert day.cancelled is False


async def test_edit_page_prefills_day_cancelled_select(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    r = client.get("/concerts/c/edit")
    assert r.status_code == 200
    assert '<option value="true" selected>Cancelled</option>' in r.text


async def test_cancelling_the_only_leg_clears_its_reminders_and_notifies(client):
    from app.db.models import Notification

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
        },
    )
    client.post("/concerts/c/rules", data={"anchor": "event_start", "days_before": 7})
    async with client.db() as s:
        assert len((await s.execute(select(ReminderQueue))).scalars().all()) == 1
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    async with client.db() as s:
        assert (await s.execute(select(ReminderQueue))).scalars().all() == []
        notes = (await s.execute(select(Notification))).scalars().all()
        assert len(notes) == 1
        assert notes[0].kind == "leg_cancelled"
        assert notes[0].user_id == EDITOR_ID


async def test_resubmitting_edit_with_same_cancelled_state_does_not_renotify(client):
    from app.db.models import Notification

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],  # already cancelled -- resubmitting the same state
        },
    )
    async with client.db() as s:
        assert (await s.execute(select(Notification))).scalars().all() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_crud.py -k "cancelled" -v`
Expected: FAIL — `day_cancelled` isn't accepted by `edit_concert`/`create_concert` yet, so `day.cancelled` stays `False` regardless, and the prefill/notification assertions fail.

- [ ] **Step 3: Add the `cancelled` parameter to `apply_day_fields`/`build_day`**

In `src/app/web/routes/concerts.py`, replace `apply_day_fields` (line 136):

```python
def apply_day_fields(
    day: ConcertDay,
    label: str,
    starts_at: str,
    city: str = "",
    venue: str = "",
    venue_address: str = "",
    doors_at: str = "",
    cancelled: str = "false",
) -> ConcertDay:
    """The JST->UTC parse + assignment shared by build_day (new rows) and
    the edit page's in-place update of existing rows."""
    starts = parse_jst(starts_at)
    if starts is None:
        raise HTTPException(status_code=422, detail="a day needs a start time")
    day.label = label.strip()
    day.starts_at_utc = starts
    day.city = city.strip() or None
    day.venue = venue.strip() or None
    day.venue_address = venue_address.strip() or None
    day.doors_at_utc = parse_jst(doors_at)
    day.cancelled = cancelled == "true"
    return day


def build_day(
    concert_id: int,
    label: str,
    starts_at: str,
    city: str = "",
    venue: str = "",
    venue_address: str = "",
    doors_at: str = "",
    cancelled: str = "false",
) -> ConcertDay:
    """New-row constructor: the rich creation form, the edit page's new
    rows, and the URL-import commit route."""
    return apply_day_fields(
        ConcertDay(concert_id=concert_id), label, starts_at, city, venue, venue_address,
        doors_at, cancelled,
    )
```

- [ ] **Step 4: Wire `day_cancelled` through `create_concert` and `edit_concert`**

In `create_concert` (line 440), add `day_cancelled: list[str] = Form(default=[])` to the parameter list right after `day_doors_at: list[str] = Form(default=[])`. Then change the day loop (line 494):

```python
    days: list[ConcertDay] = []
    for label, starts_at, city, venue, venue_address, doors_at in zip(
        day_label, day_starts_at, day_city, day_venue, day_venue_address, day_doors_at,
        strict=True,
    ):
        if not any([label.strip(), starts_at.strip(), city.strip(), venue.strip()]):
            continue  # blank trailing row from the repeatable UI
        day = build_day(concert.id, label, starts_at, city, venue, venue_address, doors_at)
        session.add(day)
        days.append(day)
```

to:

```python
    days: list[ConcertDay] = []
    for label, starts_at, city, venue, venue_address, doors_at, cancelled in zip(
        day_label, day_starts_at, day_city, day_venue, day_venue_address, day_doors_at,
        day_cancelled, strict=True,
    ):
        if not any([label.strip(), starts_at.strip(), city.strip(), venue.strip()]):
            continue  # blank trailing row from the repeatable UI
        day = build_day(concert.id, label, starts_at, city, venue, venue_address, doors_at, cancelled)
        session.add(day)
        days.append(day)
```

In `edit_concert` (line 617), add `day_cancelled: list[str] = Form(default=[])` to the parameter list right after `day_doors_at: list[str] = Form(default=[])`. Then change the day-reconciliation block (line 695):

```python
    # -- Days: update existing rows in place by id, insert blank-id rows,
    # delete rows that were dropped.
    await session.refresh(concert, ["days"])
    existing_days = {d.id: d for d in concert.days}
    before_cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
    kept_day_ids: set[int] = set()
    days_for_leg_matching: list[ConcertDay] = []
    for did, label, starts_at, city, venue, venue_address, doors_at, cancelled in zip(
        day_id, day_label, day_starts_at, day_city, day_venue, day_venue_address, day_doors_at,
        day_cancelled, strict=True,
    ):
        if not any([label.strip(), starts_at.strip(), city.strip(), venue.strip()]):
            continue  # blank trailing row from the repeatable UI
        did = did.strip()
        if did.isdigit() and int(did) in existing_days:
            day = apply_day_fields(
                existing_days[int(did)], label, starts_at, city, venue, venue_address,
                doors_at, cancelled,
            )
            kept_day_ids.add(day.id)
        else:
            day = build_day(concert.id, label, starts_at, city, venue, venue_address, doors_at, cancelled)
            session.add(day)
        days_for_leg_matching.append(day)
    for did, day in existing_days.items():
        if did not in kept_day_ids:
            await session.delete(day)
    await session.flush()  # new/kept days have real ids, needed for leg-matching below
    newly_cancelled_day_ids = {
        d.id for d in days_for_leg_matching if d.cancelled
    } - before_cancelled_day_ids
```

Then, at the end of `edit_concert`, change:

```python
    await record_concert_edit(session, concert, user.id, before)
    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
```

to:

```python
    await record_concert_edit(session, concert, user.id, before)
    await session.flush()
    if newly_cancelled_day_ids:
        await notify_newly_cancelled_legs(session, concert.id, newly_cancelled_day_ids)
    await sync_concert(session, concert.id)
    await session.commit()
```

Add `notify_newly_cancelled_legs` to the existing `from app.db.service import (...)` block at the top of the file.

- [ ] **Step 5: Add the `day_cancelled` select to the edit page template**

In `src/app/web/templates/concert_edit.html`, find the day-row block (inside `<div id="day-rows" class="row-list">`). It currently ends each row with the doors/starts labels and the remove button:

```html
      <label>Doors (JST) <input type="datetime-local" name="day_doors_at"
        value="{{ jst(d.doors_at_utc).strftime('%Y-%m-%dT%H:%M') if d.doors_at_utc else '' }}"></label>
      <label>Starts (JST) <input type="datetime-local" name="day_starts_at" required
        value="{{ jst(d.starts_at_utc).strftime('%Y-%m-%dT%H:%M') }}"></label>
      <button type="button" class="x" title="remove" onclick="this.closest('.row-item').remove()">×</button>
```

Add a `day_cancelled` select right before the remove button:

```html
      <label>Doors (JST) <input type="datetime-local" name="day_doors_at"
        value="{{ jst(d.doors_at_utc).strftime('%Y-%m-%dT%H:%M') if d.doors_at_utc else '' }}"></label>
      <label>Starts (JST) <input type="datetime-local" name="day_starts_at" required
        value="{{ jst(d.starts_at_utc).strftime('%Y-%m-%dT%H:%M') }}"></label>
      <select name="day_cancelled">
        <option value="false" {% if not d.cancelled %}selected{% endif %}>Scheduled</option>
        <option value="true" {% if d.cancelled %}selected{% endif %}>Cancelled</option>
      </select>
      <button type="button" class="x" title="remove" onclick="this.closest('.row-item').remove()">×</button>
```

And in the `<template id="day-row-template">` block, add the same select (defaulting to Scheduled, no pre-selection needed) right before its remove button:

```html
    <label>Doors (JST) <input type="datetime-local" name="day_doors_at"></label>
    <label>Starts (JST) <input type="datetime-local" name="day_starts_at" required></label>
    <select name="day_cancelled">
      <option value="false" selected>Scheduled</option>
      <option value="true">Cancelled</option>
    </select>
    <button type="button" class="x" title="remove" onclick="this.closest('.row-item').remove()">×</button>
```

The concert-creation page (`concert_new.html`) does NOT need this select — new concerts always start with un-cancelled legs (`build_day`'s `cancelled: str = "false"` default already covers this), and adding it there would just be a dead control nobody would ever set to Cancelled on a brand-new event.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_crud.py -k "cancelled" -v`
Expected: `5 passed`

- [ ] **Step 7: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/routes/concerts.py src/app/web/templates/concert_edit.html tests/test_crud.py
git commit -m "Add day_cancelled editing UX to the concert edit page"
```

---

## Task 6: Display — Cancelled badge on the concert detail page

**Files:**
- Modify: `src/app/web/templates/_performances.html`
- Modify: `src/app/web/static/style.css`
- Test: `tests/test_crud.py`

**Interfaces:**
- Consumes: `ConcertDay.cancelled` (Task 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_crud.py`:

```python
async def test_detail_page_shows_cancelled_badge_on_cancelled_leg_only(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
        },
    )
    async with client.db() as s:
        days = sorted(
            (await s.execute(select(ConcertDay))).scalars(), key=lambda d: d.label
        )
        day1_id, day2_id = days[0].id, days[1].id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day1_id), str(day2_id)],
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
            "day_cancelled": ["true", "false"],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    day1_section = r.text[r.text.index('leg-heading">Day 1'):r.text.index('leg-heading">Day 2')]
    day2_section = r.text[r.text.index('leg-heading">Day 2'):]
    assert "Cancelled" in day1_section
    assert "Cancelled" not in day2_section


async def test_round_tied_to_cancelled_day_still_renders_not_vanishes(client):
    """Regression guard for the exact bug this whole design was built to
    avoid (see the spec's "bug this design has to avoid" section):
    group_rounds_by_day() looks up round.applies_to ids in a dict keyed by
    concert.days ids. If a cancelled day were ever DELETED rather than just
    flagged, a round referencing it would silently disappear from the page
    entirely -- not fall back to "General", just vanish. Marking (not
    deleting) the day keeps it in concert.days, so the round still resolves
    correctly under that day's now-visibly-cancelled heading."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": ["Day 1"],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
        round_id = (await s.execute(select(Round))).scalar_one().id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],
            "round_id": [str(round_id)], "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": ["Day 1"],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    assert "R1" in r.text  # still rendered -- not silently dropped
    assert "No rounds yet for this performance." not in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_crud.py -k "cancelled_badge or tied_to_cancelled_day" -v`
Expected: both FAIL — `_performances.html` doesn't render a badge yet, so "Cancelled" doesn't appear in day1_section (the second test likely already passes today since nothing deletes the day, but run it now anyway to confirm it's a real regression guard and not a tautology).

- [ ] **Step 3: Add the badge to `_performances.html`**

In `src/app/web/templates/_performances.html`, the day loop currently reads:

```html
  {% for day in concert.days %}
  {% set day_rounds = rounds_by_day.get(day.id, []) %}
  <section class="perf-detail">
    <h3 class="leg-heading{% if day.id in (past_day_ids or []) %} past{% endif %}">{{ day.label }}</h3>
```

Change the `<h3>` line to (kept on ONE line, deliberately — several existing
tests, e.g. `test_detail_page_groups_rounds_by_leg`, search for the exact
substring `leg-heading">Day 1<`; splitting this tag across lines would
insert whitespace between `">` and the label and silently break them):

```html
  {% for day in concert.days %}
  {% set day_rounds = rounds_by_day.get(day.id, []) %}
  <section class="perf-detail">
    <h3 class="leg-heading{% if day.id in (past_day_ids or []) %} past{% endif %}{% if day.cancelled %} cancelled{% endif %}">{{ day.label }}{% if day.cancelled %} <span class="badge cancelled">Cancelled</span>{% endif %}</h3>
```

- [ ] **Step 4: Add the CSS**

In `src/app/web/static/style.css`, find:

```css
/* Past deadlines / days: struck through and dimmed, not hidden */
.past, .past strong, .past a { color: var(--dim); text-decoration: line-through; }
.past .kind, .past .chip { text-decoration: none; }
h3.leg-heading.past { text-decoration: line-through; }
```

Add right after it:

```css
h3.leg-heading.cancelled { text-decoration: line-through; }
.badge.cancelled { color: var(--danger); }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_crud.py -k "cancelled_badge or tied_to_cancelled_day" -v`
Expected: `2 passed`

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/templates/_performances.html src/app/web/static/style.css tests/test_crud.py
git commit -m "Show a Cancelled badge on cancelled legs in the concert detail page"
```

---

## Task 7: `concert_date_range` exclusion + index-page hiding + tile date fix

**Files:**
- Modify: `src/app/web/routes/concerts.py` (`concert_date_range`)
- Modify: `src/app/web/app.py` (index route)
- Modify: `src/app/web/templates/index.html` (tile date display)
- Test: `tests/test_crud.py`, `tests/test_tags.py`

**Interfaces:**
- Consumes: `ConcertDay.cancelled` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_crud.py`:

```python
async def test_concert_date_range_excludes_cancelled_legs(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-09-01T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
        },
    )
    async with client.db() as s:
        days = sorted(
            (await s.execute(select(ConcertDay))).scalars(), key=lambda d: d.label
        )
        day1_id = days[0].id  # the earlier date -- will be cancelled

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day1_id), str(days[1].id)],
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-09-01T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
            "day_cancelled": ["true", "false"],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    # the header date-range summary should reflect Day 2 (Sept), not the
    # cancelled Day 1 (Aug) -- both dates would otherwise appear as the range.
    assert "2099-08-01" not in r.text
    assert "2099-09-01" in r.text
```

Add to `tests/test_tags.py` (which already owns the index-page tests):

```python
async def test_index_hides_concert_whose_only_leg_is_cancelled(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "All Cancelled", "event_id": "all-cancelled",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
        },
    )
    client.post("/concerts", data={"title": "Still Here", "event_id": "still-here"})
    async with client.db() as s:
        from app.db.models import ConcertDay

        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
    client.post(
        "/concerts/all-cancelled/edit",
        data={
            "title": "All Cancelled", "event_id": "all-cancelled",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    r = client.get("/").text
    assert "All Cancelled" not in r
    assert "Still Here" in r
    # still reachable directly by event_id even though hidden from the index
    assert client.get("/concerts/all-cancelled").status_code == 200


async def test_index_keeps_concert_with_zero_days_visible(client):
    """A concert that simply has no legs entered yet (e.g. freshly created,
    or duplicated as a template) is unaffected -- only a concert whose
    EXISTING legs are all cancelled gets hidden."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "No Dates Yet", "event_id": "no-dates-yet"})
    assert "No Dates Yet" in client.get("/").text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_crud.py -k "date_range_excludes" tests/test_tags.py -k "index_hides or index_keeps" -v`
Expected: all FAIL — `concert_date_range` doesn't filter cancelled days yet, and the index route doesn't exclude fully-cancelled concerts yet.

- [ ] **Step 3: Fix `concert_date_range`**

In `src/app/web/routes/concerts.py`, replace (line 308):

```python
def concert_date_range(days: list[ConcertDay]) -> tuple[datetime, datetime] | None:
    """Earliest and latest day.starts_at_utc, for the detail page header's
    date-range summary. None when there are no days yet."""
    if not days:
        return None
    starts = [d.starts_at_utc for d in days]
    return min(starts), max(starts)
```

with:

```python
def concert_date_range(days: list[ConcertDay]) -> tuple[datetime, datetime] | None:
    """Earliest and latest day.starts_at_utc among LIVE (non-cancelled)
    legs, for the detail page header's date-range summary. None when there
    are no days yet, or every existing day is cancelled."""
    live_days = [d for d in days if not d.cancelled]
    if not live_days:
        return None
    starts = [d.starts_at_utc for d in live_days]
    return min(starts), max(starts)
```

- [ ] **Step 4: Exclude fully-cancelled concerts from the default index view**

In `src/app/web/app.py`, add `exists` to the existing `from sqlalchemy import select` line:

```python
from sqlalchemy import exists, select
```

Then change the index route (find `stmt = select(Concert).options(selectinload(Concert.days))`):

```python
            stmt = select(Concert).options(selectinload(Concert.days))
            # No server-side tag filtering: every concert renders into the DOM
            # tagged with its tag ids, and JS toggles tile visibility -- tag
            # filtering was the slowest part of this page when it round-tripped
            # the server on every click.
```

to:

```python
            stmt = select(Concert).options(selectinload(Concert.days))
            # Hide a concert whose every existing leg is cancelled -- it has
            # no valid dates left, same treatment as a concert with zero
            # legs would get if it also had no live rounds, except this is a
            # deliberate exclusion rather than just sorting last. Still
            # reachable directly via /concerts/{event_id}. A concert with NO
            # days at all (e.g. a fresh draft) keeps today's existing
            # behavior of showing up, sorted last -- untouched by this.
            has_any_day = exists().where(ConcertDay.concert_id == Concert.id)
            has_live_day = exists().where(
                ConcertDay.concert_id == Concert.id, ConcertDay.cancelled.is_(False)
            )
            stmt = stmt.where(~has_any_day | has_live_day)
            # No server-side tag filtering: every concert renders into the DOM
            # tagged with its tag ids, and JS toggles tile visibility -- tag
            # filtering was the slowest part of this page when it round-tripped
            # the server on every click.
```

- [ ] **Step 5: Fix the tile date display to skip cancelled legs**

In `src/app/web/templates/index.html`, find:

```html
        {% if c.days %}<span class="when dim">{{ jst(c.days[0].starts_at_utc).strftime("%Y-%m-%d") }} JST</span>{% endif %}
```

Replace with:

```html
        {% set live_days = c.days | rejectattr("cancelled") | list %}
        {% if live_days %}<span class="when dim">{{ jst(live_days[0].starts_at_utc).strftime("%Y-%m-%d") }} JST</span>{% endif %}
```

(`c.days` is ordered by `starts_at_utc` ascending via the model relationship, so `live_days[0]` is still the earliest LIVE day, not necessarily the earliest day overall — this is the fix: without it, a cancelled-but-earliest leg would misrepresent the tile's displayed date.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_crud.py -k "date_range_excludes" tests/test_tags.py -k "index_hides or index_keeps" -v`
Expected: `3 passed`

- [ ] **Step 7: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/routes/concerts.py src/app/web/app.py src/app/web/templates/index.html tests/test_crud.py tests/test_tags.py
git commit -m "Exclude cancelled legs from the concert date range and fully-cancelled concerts from the index"
```

---

## Final step: update CLAUDE.md

The project's `CLAUDE.md` gets a short update after this lands (matching the pattern every prior feature in this project's history has followed — see its own git log): bump the test count, add "a per-leg cancelled status" to the shipped-features sentence in the intro, and add one invariant-style note under a relevant section (e.g. near the Queue Sync invariant) stating: "A cancelled `ConcertDay` is never deleted, only flagged — `group_rounds_by_day()` and every `applies_to` consumer rely on the day row still existing. Rounds have no status of their own; a round counts as cancelled when every day in its `applies_to` is cancelled." This isn't a separate task with its own tests — fold it into whichever of Task 5–7's commits is last, or add one small final commit for it alone.
