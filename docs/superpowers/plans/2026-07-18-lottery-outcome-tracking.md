# Per-Round Lottery Outcome Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users record their lottery outcome (applied/didn't apply/won/lost/paid) per round via DM buttons, and have the reminder queue automatically suppress reminders that no longer apply and auto-arm a reminder on the next round after a loss.

**Architecture:** A new `RoundOutcome` table (per user, per round) is the single source of truth. `sync_rule` gains a filtering pass — applied to the same candidate-round list that already filters out cancelled rounds — that drops rounds/anchors an outcome makes irrelevant, before the pure planner (`plan_for_rule`, unchanged) ever sees them. `record_round_outcome` is the one write path (upsert + re-sync), called from five new DM buttons. A loss additionally searches for and auto-arms a real `ReminderRule` on the next round for the same leg, with a `sync_concert` catch-up hook for when that round doesn't exist yet.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, discord.py (including a new `discord.ui.Modal`), Alembic.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` must both be clean before every commit.
- `plan_for_rule` (`domain/reminders.py`) must NOT change at all — it stays completely ignorant of outcomes, exactly as it's already ignorant of cancellation. All filtering happens in `sync_rule` on the ORM `Round` candidate list, before conversion to `RoundInfo`.
- A round is leg-scoped via `Round.applies_to` (a list of `ConcertDay` ids); empty/`None` means "applies to every leg of the concert." Suppression and next-round logic are always leg-scoped, never round-name-scoped — a single named lottery item can be multiple separate `Round` rows, one per leg, resolved independently.
- Cross-round suppression rule: a round is suppressed once **every** leg it applies to has been secured (via `WON`/`PAID`) by this user on some round covering that leg. A round covering even one leg the user hasn't won stays fully active.
- Same-round suppression: `NOT_APPLIED` suppresses that round's RESULTS and PAYMENT; `LOST`/`PAID` suppress that round's PAYMENT; `APPLIED`/`WON` suppress nothing on their own round.
- The permissive sequence: `NOT_APPLIED`/`APPLIED` may only be recorded as the first outcome ever set for a round; `WON`/`LOST` may be recorded regardless of current state (including from nothing); `PAID` may only be recorded when the current state is `WON`.
- Auto-arm on `LOST`: find the next round (earliest `opens_at_utc`, among rounds with `opens_at_utc` set) for the same leg(s), strictly after the lost round's `closes_at_utc` (or `opens_at_utc` if unset). Use the default preset's OPENS-anchor offset if the user has one, else `offset_days=0`. Create a real `ReminderRule`, never an ad-hoc queue row. If no qualifying round exists yet, `sync_concert` catches up later.
- Button/modal callbacks are not independently unit-tested, per this project's established convention (documented in CLAUDE.md) for every `DynamicItem` — reviewed by inspection. Service functions they call are tested directly.
- Spec reference: `docs/superpowers/specs/2026-07-18-lottery-outcome-tracking-design.md`.

---

## Task 1: Data model

**Files:**
- Modify: `src/app/domain/types.py`
- Modify: `src/app/db/models.py`
- Create: `alembic/versions/<generated>_round_outcomes.py`
- Test: `tests/test_migration_round_outcomes.py`

**Interfaces:**
- Produces: `LotteryOutcome` enum (`domain/types.py`) with values `NOT_APPLIED`, `APPLIED`, `WON`, `LOST`, `PAID`.
- Produces: `RoundOutcome` model (`db/models.py`) with `id`, `user_id`, `round_id`, `outcome`, `updated_at`.

- [ ] **Step 1: Add the `LotteryOutcome` enum**

In `src/app/domain/types.py`, add this at the end of the file, right after the existing `Channel` enum:

```python
class LotteryOutcome(enum.StrEnum):
    """A user's recorded progress through one round's lottery, tracked per
    (user, round) in RoundOutcome. Strict sequence enforced in
    record_round_outcome, not at the DB layer:
    APPLIED -> (WON | LOST) -> PAID (PAID only reachable from WON)."""

    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    WON = "won"
    LOST = "lost"
    PAID = "paid"
```

- [ ] **Step 2: Add the `RoundOutcome` model**

In `src/app/db/models.py`, find the import line:

```python
from app.domain.types import Anchor, Channel, ConcertKind, RoundKind, TagKind
```

Replace with:

```python
from app.domain.types import Anchor, Channel, ConcertKind, LotteryOutcome, RoundKind, TagKind
```

Find the end of the `Round` class (its last line, `concert: Mapped[Concert] = relationship(back_populates="rounds")`) and the following blank lines before `class ReminderRule(Base):`. Insert `RoundOutcome` between them:

```python
class RoundOutcome(Base):
    """One user's recorded progress through a specific round's lottery:
    NOT_APPLIED (explicitly opted out) / APPLIED / WON / LOST / PAID.
    Strict sequence enforced in record_round_outcome, not at the DB layer:
    APPLIED -> (WON | LOST) -> PAID (PAID only reachable from WON)."""

    __tablename__ = "round_outcomes"
    __table_args__ = (Index("uq_round_outcome", "user_id", "round_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="CASCADE")
    )
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    outcome: Mapped[LotteryOutcome] = mapped_column(
        Enum(LotteryOutcome, values_callable=lambda e: [m.value for m in e])
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, onupdate=_now)
```

- [ ] **Step 3: Generate and review the migration**

Run: `uv run alembic revision --autogenerate -m "round outcomes"`

This creates `alembic/versions/<hash>_round_outcomes.py` with a head revision hash Alembic generates at run time (the current head is `5ea945b713c4`, so `down_revision` should read that). Edit the generated file to match this project's convention exactly (see `alembic/versions/84977144aad6_concert_audit_log.py` for the identical new-table-with-FKs-and-index shape this mirrors, and `alembic/versions/2f2ad38de5d7_tags.py` for the exact `sa.Enum(...)` shape used for enum columns in this project):

- Replace any `app.db.models.UTCDateTime()` reference with `sa.DateTime()`.
- Remove the `import app.db.models` line if present.
- Confirm the file stays ASCII-only.

The final `upgrade()`/`downgrade()` should read:

```python
def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('round_outcomes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('round_id', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.Enum('not_applied', 'applied', 'won', 'lost', 'paid', name='lotteryoutcome'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], name=op.f('fk_round_outcomes_round_id_rounds'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.discord_id'], name=op.f('fk_round_outcomes_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_round_outcomes'))
    )
    with op.batch_alter_table('round_outcomes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('uq_round_outcome'), ['user_id', 'round_id'], unique=True)

    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('round_outcomes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('uq_round_outcome'))

    op.drop_table('round_outcomes')
    # ### end Alembic commands ###
```

- [ ] **Step 4: Write the failing migration test**

Create `tests/test_migration_round_outcomes.py`:

```python
"""Migration test: the round_outcomes table.

Same pattern as test_migration_concert_audit.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming the table and its
unique index exist after upgrading.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "5ea945b713c4"  # head immediately before this table


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_round_outcomes_table_exists_after_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    assert "round_outcomes" not in _table_names(con)
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    assert "round_outcomes" in _table_names(con)
    columns = {row[1] for row in con.execute("PRAGMA table_info(round_outcomes)").fetchall()}
    assert columns == {"id", "user_id", "round_id", "outcome", "updated_at"}
    indexes = {row[1] for row in con.execute("PRAGMA index_list(round_outcomes)").fetchall()}
    assert "uq_round_outcome" in indexes
    con.close()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_migration_round_outcomes.py -v`
Expected: `1 passed`

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (306).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/domain/types.py src/app/db/models.py alembic/versions/ tests/test_migration_round_outcomes.py
git commit -m "Add RoundOutcome model and migration"
```

---

## Task 2: `sync_rule` suppression

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_lottery_outcomes.py` (new file)

**Interfaces:**
- Consumes: `RoundOutcome`, `LotteryOutcome` (Task 1).
- Produces: `async def _apply_outcome_suppression(session, user_id, rounds, anchor) -> list[Round]` (private helper, called only from `sync_rule`).

- [ ] **Step 1: Create the test file and write the failing tests**

Create `tests/test_lottery_outcomes.py`:

```python
"""Per-round lottery outcome tracking: RoundOutcome suppresses reminders
that no longer apply and (on a loss) auto-arms the next round for the
same leg.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundOutcome,
)
from app.db.service import ensure_user, sync_concert, sync_rule
from app.domain.types import Anchor, LotteryOutcome, RoundKind

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def seed_two_legs(s) -> tuple[Concert, ConcertDay, ConcertDay, Round, Round, Round]:
    """Two legs, three rounds covering all three applies_to shapes: tied
    only to leg A, tied to both legs, and General (no day association)."""
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


async def queue_rows_for(s, rule_id) -> list[ReminderQueue]:
    return list((await s.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule_id)
    )).scalars())


# ── sync_rule suppression ─────────────────────────────────────────────────


async def test_lost_suppresses_this_rounds_payment_reminder(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    session.add(RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.LOST))
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert await queue_rows_for(session, rule.id) == []


async def test_not_applied_suppresses_results_and_payment(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.results_at_utc = dt(6, 27)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    session.add(RoundOutcome(
        user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.NOT_APPLIED,
    ))
    await session.flush()

    results_rule = ReminderRule(
        user_id=42, round_id=round_a_only.id, anchor=Anchor.RESULTS, offset_days=0
    )
    payment_rule = ReminderRule(
        user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0
    )
    session.add_all([results_rule, payment_rule])
    await session.flush()
    await sync_rule(session, results_rule, NOW)
    await sync_rule(session, payment_rule, NOW)

    assert await queue_rows_for(session, results_rule.id) == []
    assert await queue_rows_for(session, payment_rule.id) == []


async def test_won_does_not_suppress_its_own_payment_reminder(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    session.add(RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.WON))
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert len(await queue_rows_for(session, rule.id)) == 1


async def test_paid_suppresses_payment_reminder(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    session.add(RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.PAID))
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert await queue_rows_for(session, rule.id) == []


async def test_winning_one_leg_does_not_suppress_a_round_covering_both_legs(session):
    """Cross-round rule: a round with applies_to=[leg_a, leg_b] must stay
    active until BOTH legs are secured -- winning only leg_a leaves it
    fully planned."""
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    session.add(RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.WON))
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_both.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert len(await queue_rows_for(session, rule.id)) == 1


async def test_winning_both_legs_suppresses_the_shared_round(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_leg_b_only = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="B-only",
        closes_at_utc=dt(6, 24), applies_to=[leg_b.id],
    )
    session.add(round_leg_b_only)
    await session.flush()
    session.add_all([
        RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.WON),
        RoundOutcome(user_id=42, round_id=round_leg_b_only.id, outcome=LotteryOutcome.WON),
    ])
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_both.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert await queue_rows_for(session, rule.id) == []


async def test_losing_one_leg_leaves_its_own_rounds_reminding_as_normal(session):
    """Winning leg_a while losing leg_b must NOT suppress leg_b's own
    general-sale round -- the user still needs that leg."""
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_leg_b_only = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="B-only",
        closes_at_utc=dt(6, 24), applies_to=[leg_b.id],
    )
    session.add(round_leg_b_only)
    await session.flush()
    session.add_all([
        RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.WON),
        RoundOutcome(user_id=42, round_id=round_leg_b_only.id, outcome=LotteryOutcome.LOST),
    ])
    await session.flush()

    # round_general has no applies_to -- covers every leg, including the
    # still-unsecured leg_b -- must stay active.
    rule = ReminderRule(user_id=42, round_id=round_general.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert len(await queue_rows_for(session, rule.id)) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_lottery_outcomes.py -v`
Expected: all 7 FAIL — `sync_rule` doesn't know about `RoundOutcome` yet, so nothing is suppressed.

- [ ] **Step 3: Add the suppression helper and wire it into `sync_rule`**

In `src/app/db/service.py`, find the import line:

```python
from app.db.models import (
    Concert,
    ConcertAudit,
    ConcertDay,
    ConcertTag,
    Notification,
    ReminderPreset,
    ReminderQueue,
    ReminderRule,
    Round,
    Tag,
    TagMember,
    TagSubscription,
    User,
)
from app.domain.reminders import DayInfo, RoundInfo, RuleInfo, anchor_time, plan_for_rule
from app.domain.types import Anchor, TagKind
```

Replace with:

```python
from app.db.models import (
    Concert,
    ConcertAudit,
    ConcertDay,
    ConcertTag,
    Notification,
    ReminderPreset,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundOutcome,
    Tag,
    TagMember,
    TagSubscription,
    User,
)
from app.domain.reminders import DayInfo, RoundInfo, RuleInfo, anchor_time, plan_for_rule
from app.domain.types import Anchor, LotteryOutcome, TagKind
```

Add this helper right before `async def sync_rule`:

```python
async def _apply_outcome_suppression(
    session: AsyncSession, user_id: int, rounds: list[Round], anchor: Anchor
) -> list[Round]:
    """Drop rounds this user's outcomes make irrelevant, before the pure
    planner ever sees them -- same pattern as the cancelled-round
    filtering sync_rule already does. Two passes: cross-round (every leg
    a round covers is already secured via WON/PAID elsewhere on this
    concert) then same-round (this rule's own anchor is moot given this
    round's outcome)."""
    if not rounds:
        return rounds

    concert_id = rounds[0].concert_id
    all_concert_rounds = list((await session.execute(
        select(Round).where(Round.concert_id == concert_id)
    )).scalars())
    all_round_ids = [r.id for r in all_concert_rounds]
    outcomes = {
        o.round_id: o.outcome for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id, RoundOutcome.round_id.in_(all_round_ids)
            )
        )).scalars()
    } if all_round_ids else {}
    all_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.concert_id == concert_id)
    )).scalars())

    secured_legs: set[int] = set()
    for r in all_concert_rounds:
        if outcomes.get(r.id) in (LotteryOutcome.WON, LotteryOutcome.PAID):
            secured_legs |= set(r.applies_to) if r.applies_to else all_day_ids

    survivors = []
    for r in rounds:
        applies = set(r.applies_to) if r.applies_to else all_day_ids
        if applies and applies <= secured_legs:
            continue  # every leg this round covers is already secured
        outcome = outcomes.get(r.id)
        if anchor is Anchor.RESULTS and outcome is LotteryOutcome.NOT_APPLIED:
            continue
        if anchor is Anchor.PAYMENT and outcome in (
            LotteryOutcome.LOST, LotteryOutcome.PAID, LotteryOutcome.NOT_APPLIED
        ):
            continue
        survivors.append(r)
    return survivors
```

Now find `sync_rule`'s current body:

```python
async def sync_rule(session: AsyncSession, rule: ReminderRule, now: datetime | None = None) -> None:
    """Reconcile reminder_queue with what this rule currently implies."""
    now = now or _now()

    # Gather the rounds/days in this rule's scope. Cancelled legs and rounds
    # implicitly cancelled by them (see is_round_cancelled) are filtered
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
            rounds = [] if is_round_cancelled(round_, cancelled_day_ids) else [_round_info(round_)]
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
            _round_info(r) for r in all_rounds if not is_round_cancelled(r, cancelled_day_ids)
        ]
        days = [_day_info(d) for d in all_days if not d.cancelled]

    planned = plan_for_rule(_rule_info(rule), rounds, days, now)
```

Replace with:

```python
async def sync_rule(session: AsyncSession, rule: ReminderRule, now: datetime | None = None) -> None:
    """Reconcile reminder_queue with what this rule currently implies."""
    now = now or _now()

    # Gather the rounds/days in this rule's scope. Cancelled legs and rounds
    # implicitly cancelled by them (see is_round_cancelled) are filtered
    # out here, before domain/reminders.py ever sees them -- the pure
    # planner never learns the concept of "cancelled"; it just sees fewer
    # candidates, and the existing "nothing planned -> delete" sync
    # semantics clear the reminders with no new suppression logic. This
    # user's own RoundOutcome state is filtered the same way (see
    # _apply_outcome_suppression) -- the planner stays equally ignorant
    # of lottery outcomes.
    if rule.round_id is not None:
        round_ = await session.get(Round, rule.round_id)
        if round_ is None:
            live_rounds: list[Round] = []
        else:
            cancelled_day_ids = set((await session.execute(
                select(ConcertDay.id).where(
                    ConcertDay.concert_id == round_.concert_id,
                    ConcertDay.cancelled.is_(True),
                )
            )).scalars())
            live_rounds = [] if is_round_cancelled(round_, cancelled_day_ids) else [round_]
        days: list[DayInfo] = []
    else:
        rres = await session.execute(select(Round).where(Round.concert_id == rule.concert_id))
        dres = await session.execute(
            select(ConcertDay).where(ConcertDay.concert_id == rule.concert_id)
        )
        all_rounds = list(rres.scalars())
        all_days = list(dres.scalars())
        cancelled_day_ids = {d.id for d in all_days if d.cancelled}
        live_rounds = [r for r in all_rounds if not is_round_cancelled(r, cancelled_day_ids)]
        days = [_day_info(d) for d in all_days if not d.cancelled]

    live_rounds = await _apply_outcome_suppression(session, rule.user_id, live_rounds, rule.anchor)
    rounds = [_round_info(r) for r in live_rounds]

    planned = plan_for_rule(_rule_info(rule), rounds, days, now)
```

Everything after this line (`planned_by_key = {...}` onward) is unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_lottery_outcomes.py -v`
Expected: `7 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (313). Confirm the pre-existing cancellation-suppression tests (`tests/test_service.py`'s tests using `seed_two_legs` there, and anything asserting on `sync_rule`) still pass unchanged — they don't seed any `RoundOutcome` rows, so `_apply_outcome_suppression` should be a no-op for them.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_lottery_outcomes.py
git commit -m "Suppress reminders sync_rule plans based on recorded lottery outcomes"
```

---

## Task 3: `record_round_outcome`

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_lottery_outcomes.py`

**Interfaces:**
- Consumes: `_apply_outcome_suppression` (Task 2, via `sync_rule`), `reinstate_user_rules` (already exists).
- Produces: `async def record_round_outcome(session, user_id, round_id, outcome, now=None) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_lottery_outcomes.py`, after the last suppression test:

```python
# ── record_round_outcome ───────────────────────────────────────────────────


async def test_record_round_outcome_upserts_and_resyncs(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert len(await queue_rows_for(session, rule.id)) == 1

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)

    assert await queue_rows_for(session, rule.id) == []
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.LOST


async def test_record_round_outcome_permissively_allows_lost_without_prior_applied(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.LOST


async def test_record_round_outcome_rejects_paid_without_prior_won(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.PAID, NOW)
    assert (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalar_one_or_none() is None


async def test_record_round_outcome_allows_paid_after_won(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.PAID, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.PAID


async def test_record_round_outcome_ignores_repeated_applied(session):
    """A starting state (NOT_APPLIED/APPLIED) only ever applies once --
    once WON is recorded, a stray repeated "I applied" click must not
    revert it."""
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.APPLIED, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.WON
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_lottery_outcomes.py -k "record_round_outcome" -v`
Expected: all 5 FAIL with `ImportError` (the function doesn't exist yet).

- [ ] **Step 3: Implement `record_round_outcome`**

In `src/app/db/service.py`, add this right after `_apply_outcome_suppression` (before `sync_rule`):

```python
async def record_round_outcome(
    session: AsyncSession, user_id: int, round_id: int, outcome: LotteryOutcome,
    now: datetime | None = None,
) -> None:
    """A user's DM-button click recording their lottery progress on one
    round. Permissive sequence: NOT_APPLIED/APPLIED only set the FIRST
    outcome ever recorded for a round; WON/LOST can be set regardless of
    the current state -- clicking them without ever clicking "I applied"
    first just works; PAID only reachable from WON.

    Re-syncs every one of this user's rules for the round's whole
    concert (round-scoped or concert-wide), not just rules on this one
    round -- a concert-wide rule's own candidate list also needs to drop
    a now-suppressed round, and re-running sync_rule is always safe."""
    now = now or _now()
    round_ = await session.get(Round, round_id)
    if round_ is None:
        return

    existing = (await session.execute(
        select(RoundOutcome).where(
            RoundOutcome.user_id == user_id, RoundOutcome.round_id == round_id
        )
    )).scalar_one_or_none()

    if outcome in (LotteryOutcome.NOT_APPLIED, LotteryOutcome.APPLIED) and existing is not None:
        return  # starting states only apply once
    if outcome is LotteryOutcome.PAID and (
        existing is None or existing.outcome is not LotteryOutcome.WON
    ):
        return  # PAID only reachable from WON

    if existing is None:
        session.add(RoundOutcome(user_id=user_id, round_id=round_id, outcome=outcome))
    else:
        existing.outcome = outcome
    await session.flush()

    await reinstate_user_rules(session, user_id, round_.concert_id, now)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_lottery_outcomes.py -k "record_round_outcome" -v`
Expected: `5 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (318).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_lottery_outcomes.py
git commit -m "Add record_round_outcome"
```

---

## Task 4: Auto-arm the next round

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_lottery_outcomes.py`

**Interfaces:**
- Consumes: `record_round_outcome` (Task 3), `get_default_preset` (already exists), `sync_rule` (already exists, now Task 2-modified).
- Produces: `record_round_outcome` gains a final step (auto-arm on `LOST`); `sync_concert` gains a catch-up pass.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_lottery_outcomes.py`, after the `record_round_outcome` tests:

```python
# ── Auto-arm the next round ────────────────────────────────────────────────


async def test_losing_arms_the_next_round_for_the_same_leg(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.opens_at_utc = dt(6, 10)
    next_round = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only round 2",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(next_round)
    await session.flush()

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)

    (rule,) = (await session.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == next_round.id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars()
    assert rule.offset_days == 0 and rule.offset_hours == 0
    assert len(await queue_rows_for(session, rule.id)) == 1


async def test_auto_arm_uses_default_preset_opens_offset(session):
    from app.db.models import PresetItem, ReminderPreset
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.opens_at_utc = dt(6, 10)
    next_round = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only round 2",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(next_round)
    preset = ReminderPreset(user_id=42, name="standard", is_default=True)
    session.add(preset)
    await session.flush()
    session.add(
        PresetItem(preset_id=preset.id, anchor=Anchor.OPENS, offset_days=-2, offset_hours=0)
    )
    await session.flush()

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)

    (rule,) = (await session.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == next_round.id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars()
    assert rule.offset_days == -2


async def test_auto_arm_does_not_duplicate_existing_rule(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.opens_at_utc = dt(6, 10)
    next_round = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only round 2",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(next_round)
    await session.flush()
    existing_rule = ReminderRule(
        user_id=42, round_id=next_round.id, anchor=Anchor.OPENS, offset_days=-5,
    )
    session.add(existing_rule)
    await session.flush()

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)

    rules = list((await session.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == next_round.id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars())
    assert len(rules) == 1
    assert rules[0].offset_days == -5  # untouched, not duplicated/overwritten


async def test_auto_arm_catches_up_when_next_round_added_later(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.opens_at_utc = dt(6, 10)
    await session.flush()

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)
    # nothing to arm yet -- no next round exists

    next_round = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only round 2",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(next_round)
    await session.flush()

    await sync_concert(session, concert.id, NOW)

    (rule,) = (await session.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == next_round.id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars()
    assert len(await queue_rows_for(session, rule.id)) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_lottery_outcomes.py -k "auto_arm or losing_arms" -v`
Expected: all 4 FAIL — losing a round doesn't arm anything yet.

- [ ] **Step 3: Implement the auto-arm helpers and wire them in**

In `src/app/db/service.py`, add these two functions right after `record_round_outcome`:

```python
async def _next_round_for_leg(session: AsyncSession, lost_round: Round) -> Round | None:
    """The next round for the same leg(s) a just-lost round applied to --
    the earliest-opening round (of those with an opens_at_utc set) whose
    applies_to overlaps the lost round's (or is empty -- General rounds
    cover every leg), opening strictly after the lost round's own close
    (falling back to its open time if it has no close)."""
    candidates = list((await session.execute(
        select(Round).where(
            Round.concert_id == lost_round.concert_id,
            Round.id != lost_round.id,
            Round.opens_at_utc.is_not(None),
        )
    )).scalars())
    lost_legs = set(lost_round.applies_to) if lost_round.applies_to else None
    after = lost_round.closes_at_utc or lost_round.opens_at_utc

    def overlaps(r: Round) -> bool:
        if lost_legs is None or not r.applies_to:
            return True  # either side is "every leg" -- always overlaps
        return bool(lost_legs & set(r.applies_to))

    qualifying = [r for r in candidates if overlaps(r) and r.opens_at_utc > after]
    if not qualifying:
        return None
    return min(qualifying, key=lambda r: r.opens_at_utc)


async def _auto_arm_next_round(
    session: AsyncSession, user_id: int, lost_round: Round, now: datetime | None = None
) -> None:
    """After a LOST outcome: find the next round for the same leg and
    auto-create a real ReminderRule for its OPENS anchor, using the
    user's default preset offset if they have one, else immediate."""
    now = now or _now()
    next_round = await _next_round_for_leg(session, lost_round)
    if next_round is None:
        return  # nothing to arm yet -- sync_concert catches up when it's added

    existing = (await session.execute(
        select(ReminderRule.id).where(
            ReminderRule.user_id == user_id,
            ReminderRule.round_id == next_round.id,
            ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return  # already armed

    offset_days, offset_hours = 0, 0
    preset = await get_default_preset(session, user_id)
    if preset is not None:
        await session.refresh(preset, ["items"])
        for item in preset.items:
            if item.anchor is Anchor.OPENS:
                offset_days, offset_hours = item.offset_days, item.offset_hours
                break

    rule = ReminderRule(
        user_id=user_id, round_id=next_round.id, anchor=Anchor.OPENS,
        offset_days=offset_days, offset_hours=offset_hours,
    )
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, now)
```

Find the end of `record_round_outcome`:

```python
    await reinstate_user_rules(session, user_id, round_.concert_id, now)
```

Replace with:

```python
    await reinstate_user_rules(session, user_id, round_.concert_id, now)

    if outcome is LotteryOutcome.LOST:
        await _auto_arm_next_round(session, user_id, round_, now)
```

Find `sync_concert`'s current body:

```python
async def sync_concert(
    session: AsyncSession, concert_id: int, now: datetime | None = None
) -> int:
    """Re-sync every rule touching this concert (called after any edit).

    Covers concert-scoped rules and round-scoped rules on its rounds.
    Returns the number of rules synced.
    """
    res = await session.execute(
        select(ReminderRule)
        .outerjoin(Round, ReminderRule.round_id == Round.id)
        .where(
            (ReminderRule.concert_id == concert_id) | (Round.concert_id == concert_id)
        )
    )
    rules = list(res.scalars())
    for rule in rules:
        await sync_rule(session, rule, now)
    return len(rules)
```

Replace with:

```python
async def sync_concert(
    session: AsyncSession, concert_id: int, now: datetime | None = None
) -> int:
    """Re-sync every rule touching this concert (called after any edit).

    Covers concert-scoped rules and round-scoped rules on its rounds.
    Also catches up any LOST outcome whose auto-armed "next round" didn't
    exist yet at the time of the loss -- now that a round was just added
    or edited on this concert, check again. Returns the number of rules
    synced.
    """
    res = await session.execute(
        select(ReminderRule)
        .outerjoin(Round, ReminderRule.round_id == Round.id)
        .where(
            (ReminderRule.concert_id == concert_id) | (Round.concert_id == concert_id)
        )
    )
    rules = list(res.scalars())
    for rule in rules:
        await sync_rule(session, rule, now)

    lost_outcomes = list((await session.execute(
        select(RoundOutcome, Round)
        .join(Round, RoundOutcome.round_id == Round.id)
        .where(Round.concert_id == concert_id, RoundOutcome.outcome == LotteryOutcome.LOST)
    )).all())
    for outcome, lost_round in lost_outcomes:
        await _auto_arm_next_round(session, outcome.user_id, lost_round, now)

    return len(rules)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_lottery_outcomes.py -k "auto_arm or losing_arms" -v`
Expected: `4 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (322).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_lottery_outcomes.py
git commit -m "Auto-arm the next round for the same leg after a loss"
```

---

## Task 5: `DueReminder` carries `round_id` and `outcome`

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `RoundOutcome`, `LotteryOutcome` (Task 1).
- Produces: `DueReminder.round_id: int | None`, `DueReminder.outcome: LotteryOutcome | None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_service.py`, at the end of the file:

```python
async def test_due_reminder_carries_round_id_and_outcome(session):
    from app.db.models import RoundOutcome
    from app.domain.types import LotteryOutcome

    concert, round_, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    session.add(RoundOutcome(user_id=42, round_id=round_.id, outcome=LotteryOutcome.APPLIED))
    await session.flush()

    (due,) = await due_reminders(session, dt(6, 22))
    assert due.round_id == round_.id
    assert due.outcome == LotteryOutcome.APPLIED
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_service.py -k "carries_round_id_and_outcome" -v`
Expected: FAIL with `TypeError` (`DueReminder` has no `round_id`/`outcome` fields yet).

- [ ] **Step 3: Extend `DueReminder` and `due_reminders`**

In `src/app/db/service.py`, find:

```python
@dataclass(frozen=True)
class DueReminder:
    """Everything the scheduler needs to deliver one reminder."""

    queue_id: int
    discord_id: int
    user_timezone: str
    concert_title: str
    anchor: Anchor
    fire_at_utc: datetime
    # round-anchored:
    round_label: str | None = None
    round_kind: str | None = None
    anchor_time_utc: datetime | None = None
    url: str | None = None
    # day-anchored:
    day_label: str | None = None
```

Replace with:

```python
@dataclass(frozen=True)
class DueReminder:
    """Everything the scheduler needs to deliver one reminder."""

    queue_id: int
    discord_id: int
    user_timezone: str
    concert_title: str
    anchor: Anchor
    fire_at_utc: datetime
    # round-anchored:
    round_id: int | None = None
    round_label: str | None = None
    round_kind: str | None = None
    outcome: LotteryOutcome | None = None
    anchor_time_utc: datetime | None = None
    url: str | None = None
    # day-anchored:
    day_label: str | None = None
```

Find `due_reminders`'s body from the batch-fetch section onward:

```python
    rule_ids = {row.rule_id for row in rows}
    round_ids = {row.round_id for row in rows if row.round_id is not None}
    day_ids = {row.day_id for row in rows if row.day_id is not None}

    rules = {
        r.id: r for r in
        (await session.execute(select(ReminderRule).where(ReminderRule.id.in_(rule_ids)))).scalars()
    }
    user_ids = {r.user_id for r in rules.values()}
    users = {
        u.discord_id: u for u in
        (await session.execute(select(User).where(User.discord_id.in_(user_ids)))).scalars()
    } if user_ids else {}
    rounds = {
        r.id: r for r in
        (await session.execute(select(Round).where(Round.id.in_(round_ids)))).scalars()
    } if round_ids else {}
    days = {
        d.id: d for d in
        (await session.execute(select(ConcertDay).where(ConcertDay.id.in_(day_ids)))).scalars()
    } if day_ids else {}
    concert_ids = {r.concert_id for r in rounds.values()} | {d.concert_id for d in days.values()}
    concerts = {
        c.id: c for c in
        (await session.execute(select(Concert).where(Concert.id.in_(concert_ids)))).scalars()
    } if concert_ids else {}

    out: list[DueReminder] = []
    for row in rows:
        rule = rules.get(row.rule_id)
        user = users.get(rule.user_id) if rule else None
        round_ = rounds.get(row.round_id) if row.round_id is not None else None
        day = days.get(row.day_id) if row.day_id is not None else None
        parent = round_ or day
        concert = concerts.get(parent.concert_id) if parent else None
        if user is None or concert is None:
            continue  # orphaned row; cascades should prevent this, but never crash the loop
        out.append(
            DueReminder(
                queue_id=row.id,
                discord_id=user.discord_id,
                user_timezone=user.timezone,
                concert_title=concert.title,
                anchor=row.anchor,
                fire_at_utc=row.fire_at_utc,
                round_label=round_.label if round_ else None,
                round_kind=round_.kind.value if round_ else None,
                anchor_time_utc=(
                    anchor_time(_round_info(round_), row.anchor)
                    if round_
                    else (day.starts_at_utc if day else None)
                ),
                url=round_.url if round_ else None,
                day_label=day.label if day else None,
            )
        )
    return out
```

Replace with:

```python
    rule_ids = {row.rule_id for row in rows}
    round_ids = {row.round_id for row in rows if row.round_id is not None}
    day_ids = {row.day_id for row in rows if row.day_id is not None}

    rules = {
        r.id: r for r in
        (await session.execute(select(ReminderRule).where(ReminderRule.id.in_(rule_ids)))).scalars()
    }
    user_ids = {r.user_id for r in rules.values()}
    users = {
        u.discord_id: u for u in
        (await session.execute(select(User).where(User.discord_id.in_(user_ids)))).scalars()
    } if user_ids else {}
    rounds = {
        r.id: r for r in
        (await session.execute(select(Round).where(Round.id.in_(round_ids)))).scalars()
    } if round_ids else {}
    days = {
        d.id: d for d in
        (await session.execute(select(ConcertDay).where(ConcertDay.id.in_(day_ids)))).scalars()
    } if day_ids else {}
    concert_ids = {r.concert_id for r in rounds.values()} | {d.concert_id for d in days.values()}
    concerts = {
        c.id: c for c in
        (await session.execute(select(Concert).where(Concert.id.in_(concert_ids)))).scalars()
    } if concert_ids else {}
    # queue + rules + users + rounds + concerts + outcomes -- still a
    # fixed number of round trips regardless of batch size.
    outcomes: dict[tuple[int, int], LotteryOutcome] = {}
    if round_ids:
        outcome_rows = list((await session.execute(
            select(RoundOutcome).where(RoundOutcome.round_id.in_(round_ids))
        )).scalars())
        outcomes = {(o.user_id, o.round_id): o.outcome for o in outcome_rows}

    out: list[DueReminder] = []
    for row in rows:
        rule = rules.get(row.rule_id)
        user = users.get(rule.user_id) if rule else None
        round_ = rounds.get(row.round_id) if row.round_id is not None else None
        day = days.get(row.day_id) if row.day_id is not None else None
        parent = round_ or day
        concert = concerts.get(parent.concert_id) if parent else None
        if user is None or concert is None:
            continue  # orphaned row; cascades should prevent this, but never crash the loop
        out.append(
            DueReminder(
                queue_id=row.id,
                discord_id=user.discord_id,
                user_timezone=user.timezone,
                concert_title=concert.title,
                anchor=row.anchor,
                fire_at_utc=row.fire_at_utc,
                round_id=round_.id if round_ else None,
                round_label=round_.label if round_ else None,
                round_kind=round_.kind.value if round_ else None,
                outcome=outcomes.get((user.discord_id, round_.id)) if round_ else None,
                anchor_time_utc=(
                    anchor_time(_round_info(round_), row.anchor)
                    if round_
                    else (day.starts_at_utc if day else None)
                ),
                url=round_.url if round_ else None,
                day_label=day.label if day else None,
            )
        )
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_service.py -k "carries_round_id_and_outcome" -v`
Expected: `1 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (323). Confirm `test_due_reminders_batches_queries_regardless_of_row_count` still passes — its assertion is `len(queries) <= 6`, and this task adds exactly one more query (outcomes), bringing the count to 6, still within bound.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_service.py
git commit -m "Thread round_id and outcome through DueReminder"
```

---

## Task 6: The four outcome buttons

**Files:**
- Modify: `src/app/bot/views.py`
- Modify: `src/app/bot/messages.py`
- Test: `tests/test_messages.py`

**Interfaces:**
- Consumes: `record_round_outcome` (Task 3), `DueReminder.round_id`/`.outcome` (Task 5).
- Produces: `AppliedButton`, `NotAppliedButton`, `WonButton`, `LostButton`, `PaidButton` (all in `bot/views.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_messages.py`. Find the existing import line:

```python
from app.bot.messages import build_new_event_message, format_reminder, relative_phrase
```

Replace with:

```python
from app.bot.messages import (
    build_new_event_message,
    build_reminder_message,
    format_reminder,
    relative_phrase,
)
```

Find the existing import line:

```python
from app.domain.types import Anchor
```

Replace with:

```python
from app.domain.types import Anchor, LotteryOutcome
```

Then add these tests at the end of the file:

```python
def test_build_reminder_message_shows_apply_buttons_on_closes_with_no_outcome():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.CLOSES, fire_at_utc=dt(6, 22),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), outcome=None,
    )
    _, view = build_reminder_message(item)
    labels = {getattr(c, "label", None) for c in view.children}
    assert "I applied" in labels
    assert "Didn't apply" in labels
    assert "Won" not in labels and "Paid" not in labels


def test_build_reminder_message_shows_won_lost_buttons_on_results_when_applied():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.RESULTS, fire_at_utc=dt(6, 25),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), outcome=LotteryOutcome.APPLIED,
    )
    _, view = build_reminder_message(item)
    labels = {getattr(c, "label", None) for c in view.children}
    assert "Won" in labels
    assert "Lost" in labels


def test_build_reminder_message_shows_paid_button_on_payment_when_won():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.PAYMENT, fire_at_utc=dt(6, 28),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 30), outcome=LotteryOutcome.WON,
    )
    _, view = build_reminder_message(item)
    labels = {getattr(c, "label", None) for c in view.children}
    assert "Paid" in labels


def test_build_reminder_message_shows_no_outcome_buttons_on_payment_when_lost():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.PAYMENT, fire_at_utc=dt(6, 28),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 30), outcome=LotteryOutcome.LOST,
    )
    _, view = build_reminder_message(item)
    labels = {getattr(c, "label", None) for c in view.children}
    assert "Paid" not in labels and "Won" not in labels and "Lost" not in labels
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_messages.py -k "build_reminder_message" -v`
Expected: all 4 FAIL with `TypeError` (`DueReminder` doesn't accept `round_id`/`outcome` as of this task alone... note: Task 5 already added these fields, so this should actually fail because the outcome buttons don't exist yet — the assertions won't find "I applied" etc. in `labels`).

- [ ] **Step 3: Add the five button classes to `bot/views.py`**

In `src/app/bot/views.py`, update the module docstring's `custom_id namespace` list. Find:

```
custom_id namespace:
    dk:apply:{concert_id}     apply the user's default preset
    dk:remove:{concert_id}    remove the user's rules on this concert
    dk:deadlines:{concert_id} reply with the full deadline list
    dk:snooze:{queue_id}      re-arm a delivered reminder for +24h (capped)
    dk:reinstate:{concert_id} re-sync the clicking user's rules on this concert
"""
```

Replace with:

```
custom_id namespace:
    dk:apply:{concert_id}     apply the user's default preset
    dk:remove:{concert_id}    remove the user's rules on this concert
    dk:deadlines:{concert_id} reply with the full deadline list
    dk:snooze:{queue_id}      re-arm a delivered reminder for +24h (capped)
    dk:reinstate:{concert_id} re-sync the clicking user's rules on this concert
    dk:applied:{round_id}     mark this round as applied to
    dk:notapplied:{round_id}  mark this round as not applied to
    dk:won:{round_id}         mark this round as won
    dk:lost:{round_id}        mark this round as lost
    dk:paid:{round_id}        mark this round's payment as done
"""
```

Update the imports. Find:

```python
from app.db.service import (
    apply_default_preset,
    is_round_cancelled,
    reinstate_user_rules,
    remove_user_rules,
    snooze_reminder,
)
from app.db.session import SessionMaker
from app.domain.timezones import fmt_dual
```

Replace with:

```python
from app.db.service import (
    apply_default_preset,
    is_round_cancelled,
    record_round_outcome,
    reinstate_user_rules,
    remove_user_rules,
    snooze_reminder,
)
from app.db.session import SessionMaker
from app.domain.timezones import fmt_dual
from app.domain.types import LotteryOutcome
```

Add these five classes and one shared helper right after `SnoozeButton`'s class body, before `DYNAMIC_ITEMS = [...]`:

```python
async def _handle_outcome_click(
    interaction: discord.Interaction, round_id: int, outcome: LotteryOutcome, success_msg: str
) -> None:
    async with SessionMaker() as session:
        await record_round_outcome(session, interaction.user.id, round_id, outcome)
        await session.commit()
    await interaction.response.send_message(success_msg)


class AppliedButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:applied:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="I applied", style=discord.ButtonStyle.primary,
            custom_id=f"dk:applied:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.APPLIED, "Got it — marked as applied!"
        )


class NotAppliedButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:notapplied:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Didn't apply", style=discord.ButtonStyle.secondary,
            custom_id=f"dk:notapplied:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.NOT_APPLIED,
            "No worries — you won't get results/payment reminders for this one.",
        )


class WonButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:won:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Won", style=discord.ButtonStyle.success,
            custom_id=f"dk:won:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.WON,
            "Congrats! I'll remind you when payment is due.",
        )


class LostButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:lost:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Lost", style=discord.ButtonStyle.secondary,
            custom_id=f"dk:lost:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.LOST,
            "Sorry to hear it — no payment reminder needed, and I'll let you know "
            "when the next round opens if there is one.",
        )


class PaidButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:paid:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Paid", style=discord.ButtonStyle.success,
            custom_id=f"dk:paid:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.PAID, "Marked as paid — all set!"
        )
```

Find:

```python
DYNAMIC_ITEMS = [
    ApplyDefaultButton, RemoveRemindersButton, ReinstateRemindersButton, ShowDeadlinesButton,
    SnoozeButton,
]
```

Replace with:

```python
DYNAMIC_ITEMS = [
    ApplyDefaultButton, RemoveRemindersButton, ReinstateRemindersButton, ShowDeadlinesButton,
    SnoozeButton, AppliedButton, NotAppliedButton, WonButton, LostButton, PaidButton,
]
```

- [ ] **Step 4: Wire the buttons into `build_reminder_message`**

In `src/app/bot/messages.py`, add `LotteryOutcome` to the top-level import. Find:

```python
from app.domain.types import Anchor
```

Replace with:

```python
from app.domain.types import Anchor, LotteryOutcome
```

Find `build_reminder_message`'s current body:

```python
def build_reminder_message(item: DueReminder) -> tuple:
    """(embed, view) for a deadline reminder DM."""
    import discord

    from app.bot.views import SnoozeButton
    from app.config import settings

    subject = item.round_label or item.day_label or "event"
    emoji = KIND_EMOJI.get(item.round_kind or "", "🗓️")
    verb = ANCHOR_VERB[item.anchor]

    embed = discord.Embed(title=f"{emoji} {item.concert_title}", color=0x1A7F4E)
    if item.anchor_time_utc is not None:
        rel = relative_phrase(item.anchor_time_utc, item.fire_at_utc)
        embed.description = (
            f"**{subject}** {verb} {rel}\n{fmt_dual(item.anchor_time_utc, item.user_timezone)}"
        )
    else:
        embed.description = f"**{subject}**"

    view = discord.ui.View(timeout=None)
    if item.url:
        view.add_item(discord.ui.Button(label="Ticket page", url=item.url))
    view.add_item(discord.ui.Button(
        label="Open on dekimasen.app", url=f"{settings.base_url}"
    ))
    view.add_item(SnoozeButton(item.queue_id))
    return embed, view
```

Replace with:

```python
def build_reminder_message(item: DueReminder) -> tuple:
    """(embed, view) for a deadline reminder DM."""
    import discord

    from app.bot.views import (
        AppliedButton,
        LostButton,
        NotAppliedButton,
        PaidButton,
        SnoozeButton,
        WonButton,
    )
    from app.config import settings

    subject = item.round_label or item.day_label or "event"
    emoji = KIND_EMOJI.get(item.round_kind or "", "🗓️")
    verb = ANCHOR_VERB[item.anchor]

    embed = discord.Embed(title=f"{emoji} {item.concert_title}", color=0x1A7F4E)
    if item.anchor_time_utc is not None:
        rel = relative_phrase(item.anchor_time_utc, item.fire_at_utc)
        embed.description = (
            f"**{subject}** {verb} {rel}\n{fmt_dual(item.anchor_time_utc, item.user_timezone)}"
        )
    else:
        embed.description = f"**{subject}**"

    view = discord.ui.View(timeout=None)
    if item.url:
        view.add_item(discord.ui.Button(label="Ticket page", url=item.url))
    view.add_item(discord.ui.Button(
        label="Open on dekimasen.app", url=f"{settings.base_url}"
    ))

    if item.round_id is not None:
        if item.anchor is Anchor.CLOSES and item.outcome is None:
            view.add_item(AppliedButton(item.round_id))
            view.add_item(NotAppliedButton(item.round_id))
        elif item.anchor is Anchor.RESULTS and item.outcome in (None, LotteryOutcome.APPLIED):
            view.add_item(WonButton(item.round_id))
            view.add_item(LostButton(item.round_id))
        elif item.anchor is Anchor.PAYMENT and item.outcome is LotteryOutcome.WON:
            view.add_item(PaidButton(item.round_id))

    view.add_item(SnoozeButton(item.queue_id))
    return embed, view
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_messages.py -k "build_reminder_message" -v`
Expected: `4 passed`

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (327).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/bot/views.py src/app/bot/messages.py tests/test_messages.py
git commit -m "Add DM buttons for recording per-round lottery outcomes"
```

---

## Task 7: Generalized snooze, "Remind me later" modal, "Apply here" relabel

**Files:**
- Modify: `src/app/db/service.py`
- Modify: `src/app/bot/views.py`
- Modify: `src/app/bot/messages.py`
- Test: `tests/test_presets.py`
- Test: `tests/test_messages.py`

**Interfaces:**
- Consumes: `build_reminder_message` (Task 6, extended further here).
- Produces: `snooze_reminder(session, queue_id, user_id, days=1, now=None)`, `RemindLaterButton`, `RemindLaterModal`.

- [ ] **Step 1: Write the failing snooze-generalization tests**

Add to `tests/test_presets.py`, right after `test_snooze_refuses_within_24h_of_deadline`:

```python
async def test_snooze_reminder_accepts_custom_day_count(client):
    from datetime import UTC, datetime, timedelta

    from app.db.service import snooze_reminder

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C2", "event_id": "c2",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post("/concerts/c2/rules", data={"anchor": "closes", "days_before": 3})

    async with client.db() as s:
        (row,) = await _all(client.db, ReminderQueue)
        assert await snooze_reminder(s, row.id, EDITOR_ID, days=10) == "snoozed"
        await s.commit()
    (row,) = await _all(client.db, ReminderQueue)
    assert row.fire_at_utc > datetime.now(UTC) + timedelta(days=9)


async def test_snooze_reminder_default_days_matches_existing_behavior(client):
    from datetime import UTC, datetime, timedelta

    from app.db.service import snooze_reminder

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C3", "event_id": "c3",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post("/concerts/c3/rules", data={"anchor": "closes", "days_before": 3})

    async with client.db() as s:
        (row,) = await _all(client.db, ReminderQueue)
        assert await snooze_reminder(s, row.id, EDITOR_ID) == "snoozed"
        await s.commit()
    (row,) = await _all(client.db, ReminderQueue)
    assert row.fire_at_utc > datetime.now(UTC) + timedelta(hours=23)
    assert row.fire_at_utc < datetime.now(UTC) + timedelta(hours=25)


async def test_snooze_reminder_custom_days_still_capped_at_deadline(client):
    from datetime import UTC, datetime, timedelta

    from app.db.models import Concert, ReminderRule as RR, Round
    from app.db.service import ensure_user, snooze_reminder, sync_rule
    from app.domain.types import Anchor, RoundKind

    async with client.db() as s:
        await ensure_user(s, FAN_ID, "fan")
        c = Concert(title="Soon2", event_id="soon2", created_by=FAN_ID)
        s.add(c)
        await s.flush()
        round_ = Round(concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="R1",
                        closes_at_utc=datetime.now(UTC) + timedelta(hours=10))
        s.add(round_)
        rule = RR(user_id=FAN_ID, concert_id=c.id, anchor=Anchor.CLOSES,
                  offset_days=0, offset_hours=-9)
        s.add(rule)
        await s.flush()
        await sync_rule(s, rule)
        await s.commit()

    async with client.db() as s:
        (row,) = await _all(client.db, ReminderQueue)
        # deadline is only 10h away; a 5-day custom snooze would sleep past it
        assert await snooze_reminder(s, row.id, FAN_ID, days=5) == "too_close"
```

- [ ] **Step 2: Run the tests to verify they fail or pass trivially, then generalize `snooze_reminder`**

Run: `uv run pytest tests/test_presets.py -k "snooze_reminder_accepts or snooze_reminder_default or snooze_reminder_custom" -v`
Expected: `test_snooze_reminder_accepts_custom_day_count` FAILS with `TypeError` (`days` isn't a parameter yet); the other two currently call `snooze_reminder` without `days` so they'd pass against the *old* signature already — that's expected, they're regression guards for after the change, not proof of new behavior on their own.

In `src/app/db/service.py`, find:

```python
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
```

Replace with:

```python
async def snooze_reminder(
    session: AsyncSession, queue_id: int, user_id: int, days: int = 1,
    now: datetime | None = None,
) -> str:
    """[Snooze 1 day] / [Remind me later] buttons. Re-arms a delivered
    reminder for +`days` days, capped so it can never fire after the
    deadline it's about. `days` defaults to 1 -- unchanged behavior for
    every reminder except the CLOSES one, where a modal-driven button
    supplies a user-chosen value instead.
    Returns: 'snoozed' | 'too_close' | 'not_yours' | 'gone'."""
    from datetime import timedelta

    now = now or _now()
    row = await session.get(ReminderQueue, queue_id)
    if row is None:
        return "gone"
    rule = await session.get(ReminderRule, row.rule_id)
    if rule is None or rule.user_id != user_id:
        return "not_yours"

    new_fire = now + timedelta(days=days)
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `uv run pytest tests/test_presets.py -k "snooze_reminder_accepts or snooze_reminder_default or snooze_reminder_custom" -v`
Expected: `3 passed`

- [ ] **Step 4: Write the failing message-building tests for the CLOSES button set**

Add to `tests/test_messages.py`:

```python
def test_build_reminder_message_closes_reminder_uses_remind_later_not_snooze():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.CLOSES, fire_at_utc=dt(6, 22),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), url="https://example.com/apply",
    )
    _, view = build_reminder_message(item)
    labels = {getattr(c, "label", None) for c in view.children}
    assert "Remind me later" in labels
    assert "Snooze 1 day" not in labels
    assert "Apply here" in labels
    assert "Ticket page" not in labels


def test_build_reminder_message_other_anchors_keep_plain_snooze():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.RESULTS, fire_at_utc=dt(6, 25),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25),
    )
    _, view = build_reminder_message(item)
    labels = {getattr(c, "label", None) for c in view.children}
    assert "Snooze 1 day" in labels
    assert "Remind me later" not in labels
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run pytest tests/test_messages.py -k "remind_later or keep_plain_snooze" -v`
Expected: both FAIL — the CLOSES reminder still shows "Ticket page"/"Snooze 1 day" today.

- [ ] **Step 6: Add the modal and button to `bot/views.py`**

Update the module docstring's `custom_id namespace` list one more time. Find:

```
    dk:paid:{round_id}        mark this round's payment as done
"""
```

Replace with:

```
    dk:paid:{round_id}        mark this round's payment as done
    dk:remindlater:{queue_id} open a modal asking how many days to snooze
"""
```

`snooze_reminder` is already imported in this file's `from app.db.service import (...)` block (it was already there for `SnoozeButton`) — no import change needed for the modal.

Add the modal and button right after `PaidButton`'s class body, before `DYNAMIC_ITEMS = [...]`:

```python
class RemindLaterModal(discord.ui.Modal, title="Remind me later"):
    days = discord.ui.TextInput(label="How many days?", placeholder="e.g. 3", max_length=3)

    def __init__(self, queue_id: int) -> None:
        super().__init__()
        self.queue_id = queue_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            n = int(str(self.days))
            if n <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole number of days greater than 0."
            )
            return
        async with SessionMaker() as session:
            status = await snooze_reminder(session, self.queue_id, interaction.user.id, days=n)
            await session.commit()
        msg = {
            "snoozed": f"Got it — I'll remind you again in {n} day(s).",
            "too_close": "Can't snooze that far — the deadline is too close. ⏳",
            "not_yours": "That reminder isn't yours.",
            "gone": "That reminder no longer exists.",
        }[status]
        await interaction.response.send_message(msg)


class RemindLaterButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:remindlater:(?P<qid>\d+)"
):
    def __init__(self, queue_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Remind me later", style=discord.ButtonStyle.secondary,
            custom_id=f"dk:remindlater:{queue_id}",
        ))
        self.queue_id = queue_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["qid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RemindLaterModal(self.queue_id))
```

Find:

```python
DYNAMIC_ITEMS = [
    ApplyDefaultButton, RemoveRemindersButton, ReinstateRemindersButton, ShowDeadlinesButton,
    SnoozeButton, AppliedButton, NotAppliedButton, WonButton, LostButton, PaidButton,
]
```

Replace with:

```python
DYNAMIC_ITEMS = [
    ApplyDefaultButton, RemoveRemindersButton, ReinstateRemindersButton, ShowDeadlinesButton,
    SnoozeButton, AppliedButton, NotAppliedButton, WonButton, LostButton, PaidButton,
    RemindLaterButton,
]
```

- [ ] **Step 7: Update `build_reminder_message`'s CLOSES branch in `bot/messages.py`**

Find:

```python
    from app.bot.views import (
        AppliedButton,
        LostButton,
        NotAppliedButton,
        PaidButton,
        SnoozeButton,
        WonButton,
    )
```

Replace with:

```python
    from app.bot.views import (
        AppliedButton,
        LostButton,
        NotAppliedButton,
        PaidButton,
        RemindLaterButton,
        SnoozeButton,
        WonButton,
    )
```

Find:

```python
    view = discord.ui.View(timeout=None)
    if item.url:
        view.add_item(discord.ui.Button(label="Ticket page", url=item.url))
    view.add_item(discord.ui.Button(
        label="Open on dekimasen.app", url=f"{settings.base_url}"
    ))

    if item.round_id is not None:
        if item.anchor is Anchor.CLOSES and item.outcome is None:
            view.add_item(AppliedButton(item.round_id))
            view.add_item(NotAppliedButton(item.round_id))
        elif item.anchor is Anchor.RESULTS and item.outcome in (None, LotteryOutcome.APPLIED):
            view.add_item(WonButton(item.round_id))
            view.add_item(LostButton(item.round_id))
        elif item.anchor is Anchor.PAYMENT and item.outcome is LotteryOutcome.WON:
            view.add_item(PaidButton(item.round_id))

    view.add_item(SnoozeButton(item.queue_id))
    return embed, view
```

Replace with:

```python
    view = discord.ui.View(timeout=None)
    if item.url:
        link_label = "Apply here" if item.anchor is Anchor.CLOSES else "Ticket page"
        view.add_item(discord.ui.Button(label=link_label, url=item.url))
    view.add_item(discord.ui.Button(
        label="Open on dekimasen.app", url=f"{settings.base_url}"
    ))

    if item.round_id is not None:
        if item.anchor is Anchor.CLOSES and item.outcome is None:
            view.add_item(AppliedButton(item.round_id))
            view.add_item(NotAppliedButton(item.round_id))
        elif item.anchor is Anchor.RESULTS and item.outcome in (None, LotteryOutcome.APPLIED):
            view.add_item(WonButton(item.round_id))
            view.add_item(LostButton(item.round_id))
        elif item.anchor is Anchor.PAYMENT and item.outcome is LotteryOutcome.WON:
            view.add_item(PaidButton(item.round_id))

    if item.anchor is Anchor.CLOSES:
        view.add_item(RemindLaterButton(item.queue_id))
    else:
        view.add_item(SnoozeButton(item.queue_id))
    return embed, view
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_messages.py -k "remind_later or keep_plain_snooze" -v`
Expected: `2 passed`

- [ ] **Step 9: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (332).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py src/app/bot/views.py src/app/bot/messages.py tests/test_presets.py tests/test_messages.py
git commit -m "Add generalized snooze, Remind-me-later modal, and Apply-here relabel"
```

---

## Final step: update CLAUDE.md and WISHLIST.md

**CLAUDE.md:**

- Bump the test count in the intro sentence to 332, and add "per-round lottery outcome tracking (applied/won/lost/paid, with automatic reminder suppression and next-round auto-arming)" to the shipped-features list.
- Add one clause to invariant #2 (Queue sync), extending its existing description of cancelled-round suppression to mention the parallel mechanism this feature adds. Find:

  ```
  A cancelled `ConcertDay` is never deleted, only flagged —
     `group_rounds_by_day()` and every `applies_to` consumer rely on the day
     row still existing. Rounds have no status of their own; a round counts
     as cancelled when every day in its `applies_to` is cancelled.
  ```

  Replace with:

  ```
  A cancelled `ConcertDay` is never deleted, only flagged —
     `group_rounds_by_day()` and every `applies_to` consumer rely on the day
     row still existing. Rounds have no status of their own; a round counts
     as cancelled when every day in its `applies_to` is cancelled.
     `RoundOutcome` (per-user, per-round lottery progress) layers a second,
     per-user suppression pass onto the same `sync_rule` candidate-list
     filtering, orthogonal to cancellation — see
     `db/service.py`'s `_apply_outcome_suppression`.
  ```

**WISHLIST.md:** per CLAUDE.md's "Feature wishlist" maintenance convention (move the shipped entry, then do a full revision pass over what's left):

- Move the "Per-round personal lottery outcome tracking" entry from `## Proposed` to `## Shipped`, with today's date and a one-line note on what shipped (outcome buttons, reminder suppression, next-round auto-arm, the generalized snooze/Remind-me-later modal).
- Re-rank and reconsider the remaining 2 entries (daily digest mode, first-run guided setup). Neither is obviously invalidated or newly enabled by this ship — confirm the order still makes sense rather than leaving it unexamined.
