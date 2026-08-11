# Round watch (quiet ladders) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the owner a live admin worklist of tracked concerts whose ladder
holds no future anchor, and DM him once when a concert newly joins it.

**Architecture:** A pure predicate over the catalogue (`db/quiet_ladders.py`)
reusing the read API's existing `next_anchor_at` signal; two nullable stamp
columns on `concerts`; a self-idempotent reconciliation pass in the scheduler
tick that stamps newcomers and queues one digest notice; an admin page that
derives membership live so it never depends on the pass having run.

**Tech Stack:** Python 3.14, SQLAlchemy async + Alembic (SQLite), FastAPI +
Jinja2, discord.py, pytest/pytest-asyncio.

**Design spec:** `docs/superpowers/specs/2026-08-11-round-watch-design.md` —
read it before Task 1. Every ruling below traces to a section of it.

## Global Constraints

- **The plan's code is UNVERIFIED.** It was written from reading the codebase,
  not from running it. Treat every snippet as a strong suggestion about shape
  and naming, not as correct code. If a test the plan gives you does not fail
  the way the plan says it will, STOP and report — do not adjust the test until
  it passes.
- `uv run pytest -q` and `uv run ruff check .` MUST both be clean before every
  commit. Use `uv run --isolated` — an external process holds a lock on `.venv`
  on this machine.
- Run test commands in the FOREGROUND. A backgrounded suite run stalls.
- `src/app/domain/` is pure: no discord, fastapi or sqlalchemy imports, ever.
- Feature modules under `src/app/db/` import `core`, NEVER the facade
  (`service.py`). The facade imports them. A feature module importing the facade
  is a cycle.
- Every public name added to a `db/` module MUST also be added to
  `db/service.py` — both the `from .module import (...)` list and `__all__`.
  `tests/test_service_facade.py` fails if they disagree.
- The DB stores aware UTC only; `UTCDateTime` rejects naive datetimes
  (invariant 1).
- Admin pages are English-only and NOT wrapped in `_()`.
  `tests/test_i18n_catalogues.py` would demand ja/zh translations for any msgid
  you add.
- Never interpolate user-controlled text into an inline `on*` handler. Use a
  `data-` attribute read via `dataset` (invariant 7).
- Commit messages end with the two trailer lines used throughout this repo:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH`.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/db/core.py` (modify) | Promote `_next_anchor_iso` to `next_anchor_at` |
| `src/app/db/models.py` (modify) | Two stamp columns on `Concert` |
| `alembic/versions/<rev>_quiet_ladder_stamps.py` (create) | Columns + blanket backfill |
| `src/app/db/quiet_ladders.py` (create) | Predicate, row query, reconciliation, checked-writer |
| `src/app/db/service.py` (modify) | Re-export the above |
| `src/app/domain/quiet_ladder_message.py` (create) | Pure DM body and copy block |
| `src/app/scheduler/loop.py` (modify) | The per-tick pass and the notice |
| `src/app/web/routes/quiet_ladders.py` (create) | `GET /admin/quiet-ladders`, `POST .../checked` |
| `src/app/web/templates/admin_quiet_ladders.html` (create) | The page |
| `src/app/web/app.py` (modify) | Register the router |

---

### Task 1: Promote `next_anchor_at`

The predicate and the read API must answer "does this concert hold a future
moment" with the same code. Today only an ISO-string form exists.

**Files:**
- Modify: `src/app/db/core.py:3790` (`_next_anchor_iso`)
- Modify: `src/app/db/service.py` (import list and `__all__`)
- Test: `tests/test_api_reads.py`

**Interfaces:**
- Produces: `next_anchor_at(concert: Concert, now: datetime) -> datetime | None`
  — the earliest future moment among live rounds, catalogue-level (never
  per-viewer). `None` means the ladder holds no future anchor.
  `_next_anchor_iso(concert, now) -> str | None` stays, as a wrapper.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_reads.py`:

```python
async def test_next_anchor_at_returns_the_datetime_the_iso_form_stringifies(session):
    """The ISO form is a wrapper. One definition of 'future anchor', so this
    surface and the quiet-ladders predicate cannot drift apart."""
    from app.db.service import _next_anchor_iso, next_anchor_at

    await ensure_user(session, 42, "reiji")
    concert = Concert(title="Anchor", event_id="anchor", created_by=42)
    session.add(concert)
    await session.flush()
    soon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="1次",
        closes_at_utc=soon,
    ))
    await session.flush()
    await session.refresh(concert, ["rounds", "days"])

    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    assert next_anchor_at(concert, now) == soon
    assert _next_anchor_iso(concert, now) == soon.isoformat()

    after = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert next_anchor_at(concert, after) is None
    assert _next_anchor_iso(concert, after) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --isolated pytest tests/test_api_reads.py::test_next_anchor_at_returns_the_datetime_the_iso_form_stringifies -q`
Expected: FAIL — `ImportError: cannot import name 'next_anchor_at'`.

- [ ] **Step 3: Split the function**

In `src/app/db/core.py`, rename the existing `_next_anchor_iso` body to
`next_anchor_at`, change its final line to return the datetime, and leave a
wrapper. Keep the whole existing docstring on `next_anchor_at` — it records why
this is catalogue-level and why `all_legs_cancelled` is asked here — and add
the one-line note below.

```python
def next_anchor_at(concert: Concert, now: datetime) -> datetime | None:
    """<keep the existing _next_anchor_iso docstring verbatim>

    Returns the datetime rather than a string because two callers want two
    shapes of the same fact: the agent read API serialises it, and
    db/quiet_ladders.py compares it to None. One definition, two renderings.
    """
    if all_legs_cancelled(concert.days):
        return None
    cancelled = {d.id for d in concert.days if d.cancelled}
    moments: list[datetime] = []
    for r in concert.rounds:
        if is_round_cancelled(r, cancelled):
            continue
        for at in (
            r.opens_at_utc, r.closes_at_utc, r.results_at_utc, r.payment_deadline_at_utc,
        ):
            if at is not None and at > now:
                moments.append(at)
    return min(moments) if moments else None


def _next_anchor_iso(concert: Concert, now: datetime) -> str | None:
    """The API's serialised form of `next_anchor_at`."""
    at = next_anchor_at(concert, now)
    return at.isoformat() if at is not None else None
```

- [ ] **Step 4: Export it from the facade**

In `src/app/db/service.py`, add `next_anchor_at` to the `from .core import (...)`
list and `"next_anchor_at"` to `__all__`, both in alphabetical position.

- [ ] **Step 5: Run the test and the facade test**

Run: `uv run --isolated pytest tests/test_api_reads.py tests/test_service_facade.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`
Expected: all pass, ruff clean. The API's own tests must be unaffected — that
is the point of the wrapper.

- [ ] **Step 7: Commit**

```bash
git add src/app/db/core.py src/app/db/service.py tests/test_api_reads.py
git commit -m "refactor(core): promote _next_anchor_iso to next_anchor_at

The quiet-ladders predicate needs the same 'does this concert hold a future
moment' answer the agent read API already computes. Two definitions free to
drift is the bug this avoids, so the ISO form becomes a wrapper over a
datetime-returning function rather than a second implementation.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH"
```

---

### Task 2: The two stamp columns and the migration

**Files:**
- Modify: `src/app/db/models.py` (`Concert`, after `created_at`)
- Create: `alembic/versions/<rev>_quiet_ladder_stamps.py`
- Test: `tests/test_quiet_ladder_migration.py`

**Interfaces:**
- Produces: `Concert.quiet_since_utc: datetime | None` (system-owned) and
  `Concert.ladder_rechecked_at_utc: datetime | None` (human-owned), both
  `UTCDateTime`, both nullable.

- [ ] **Step 1: Add the columns to the model**

In `src/app/db/models.py`, inside `class Concert`, immediately after the
`created_at` line:

```python
    # Round watch (see docs/superpowers/specs/2026-08-11-round-watch-design.md).
    # Two stamps with two owners, deliberately not one column: "how long has
    # this ladder been quiet" and "have I looked at it" are different
    # questions, and one column would have to lie about one of them.
    #
    # quiet_since_utc is SYSTEM-owned -- written only by the scheduler's
    # reconcile pass -- and means FIRST OBSERVED QUIET, not "went quiet": the
    # migration blanket-stamps every row so the first pass after deploy
    # announces nothing, and under that name the backfilled value is honest.
    quiet_since_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # ladder_rechecked_at_utc is the OWNER's, written only by the Checked
    # button. Cleared with quiet_since_utc when a concert leaves the list:
    # both belong to the CURRENT quiet spell, so a concert that goes quiet
    # again arrives unchecked.
    ladder_rechecked_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

- [ ] **Step 2: Generate the migration**

Run: `uv run --isolated alembic revision --autogenerate -m "quiet ladder stamps"`

- [ ] **Step 3: Edit the generated migration**

Two mandatory edits plus the backfill. Open the new file in
`alembic/versions/` and make it read:

```python
"""quiet ladder stamps

Revision ID: <keep generated>
Revises: <keep generated>
"""
import sqlalchemy as sa
from alembic import op

revision = "<keep generated>"
down_revision = "<keep generated>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("concerts") as batch:
        batch.add_column(sa.Column("quiet_since_utc", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("ladder_rechecked_at_utc", sa.DateTime(), nullable=True))
    # BLANKET stamp, not a predicate backfill, and the difference is the whole
    # point. The reconcile pass clears quiet_since_utc for every concert NOT on
    # the list on its very first run, so stamping everything reaches the same
    # steady state as stamping exactly the quiet ones -- without reimplementing
    # a Python predicate (next_anchor_at, is_round_cancelled) in SQL, where it
    # would be free to disagree with the real one.
    #
    # What it buys: no concert is a NEWCOMER on the first pass, so the first
    # tick after deploy DMs nothing instead of announcing the entire back
    # catalogue at once.
    #
    # CURRENT_TIMESTAMP is SQLite's naive UTC 'YYYY-MM-DD HH:MM:SS', which is
    # exactly the on-disk form UTCDateTime writes (it stores naive UTC and
    # re-attaches tzinfo on read), so these rows read back as aware UTC like
    # any other.
    op.execute("UPDATE concerts SET quiet_since_utc = CURRENT_TIMESTAMP")


def downgrade() -> None:
    with op.batch_alter_table("concerts") as batch:
        batch.drop_column("ladder_rechecked_at_utc")
        batch.drop_column("quiet_since_utc")
```

Required checks before moving on:
- No `import app.db.models` line remains, and no `app.db.models.UTCDateTime()`
  — both must be `sa.DateTime()`.
- This migration ADDS columns and touches no constraint, so it does NOT need
  `naming_convention=NAMING_CONVENTION`. That requirement applies only to
  migrations calling `drop_constraint` against legacy tables. Leave a comment
  saying so if you like, but do not add the parameter.

- [ ] **Step 4: Write the migration test**

Create `tests/test_quiet_ladder_migration.py`:

```python
"""The blanket backfill: every existing concert is stamped, so the first
reconcile pass after deploy has no newcomers and therefore sends no DM."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Concert
from app.db.service import ensure_user


async def test_every_concert_carries_a_quiet_stamp_after_migration(session):
    """The test DB is built from Base.metadata, so this asserts the model half:
    the columns exist, are nullable, and default to NULL for a NEW row. The
    backfill itself is asserted by the reconcile test in Task 4 -- a concert
    with a stamp is not a newcomer."""
    await ensure_user(session, 42, "reiji")
    concert = Concert(title="Fresh", event_id="fresh", created_by=42)
    session.add(concert)
    await session.flush()

    assert concert.quiet_since_utc is None
    assert concert.ladder_rechecked_at_utc is None

    concert.quiet_since_utc = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    await session.flush()
    stored = (await session.execute(
        select(Concert).where(Concert.id == concert.id)
    )).scalar_one()
    assert stored.quiet_since_utc == datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    assert stored.quiet_since_utc.tzinfo is not None  # invariant 1
```

- [ ] **Step 5: Run the migration and the test**

Run: `uv run --isolated alembic upgrade head`
Then: `uv run --isolated pytest tests/test_quiet_ladder_migration.py -q`
Expected: migration applies cleanly, test passes.

- [ ] **Step 6: Full suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`

- [ ] **Step 7: Commit**

```bash
git add src/app/db/models.py alembic/versions tests/test_quiet_ladder_migration.py
git commit -m "feat(db): two quiet-ladder stamps on concerts

quiet_since_utc is system-owned and means FIRST OBSERVED QUIET; the migration
blanket-stamps every existing row so the first reconcile pass after deploy has
no newcomers and announces nothing. Stamping everything reaches the same steady
state as a predicate backfill -- the pass clears non-quiet rows on its first
run -- without restating next_anchor_at in SQL where it could disagree.

ladder_rechecked_at_utc is the owner's. Both belong to the current quiet spell.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH"
```

---

### Task 3: The predicate and the row query

**Files:**
- Create: `src/app/db/quiet_ladders.py`
- Modify: `src/app/db/service.py`
- Test: `tests/test_quiet_ladders.py`

**Interfaces:**
- Consumes: `next_anchor_at` (Task 1), the two columns (Task 2).
- Produces:
  - `@dataclass(frozen=True) QuietRound(label: str, kind: str, opens_at_utc, closes_at_utc, results_at_utc, payment_deadline_at_utc)` — all four `datetime | None`.
  - `@dataclass(frozen=True) QuietLadder(concert_id: int, event_id: str, title: str, title_en: str | None, leg_dates: tuple[date, ...], official_url: str | None, eventernote_url: str | None, source_url: str | None, rounds: tuple[QuietRound, ...], quiet_since_utc: datetime | None, rechecked_at_utc: datetime | None)`
  - `async def quiet_ladder_rows(session, now: datetime | None = None) -> list[QuietLadder]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quiet_ladders.py`:

```python
"""The quiet-ladders predicate: which tracked concerts have no future anchor.

Case-by-case against the design spec's table. The last case is the one that
pins the reuse of next_anchor_at -- if this surface and the agent read API ever
grow two definitions of "future anchor", it fails."""

from datetime import UTC, date, datetime

from app.db.models import Concert, ConcertDay, Round
from app.db.service import ensure_user, quiet_ladder_rows
from app.domain.types import RoundKind

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def at(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


async def _concert(session, event_id, *, legs=(), rounds=()):
    await ensure_user(session, 42, "reiji")
    c = Concert(title=event_id, event_id=event_id, created_by=42)
    session.add(c)
    await session.flush()
    for starts, cancelled in legs:
        session.add(ConcertDay(
            concert_id=c.id, starts_at_utc=starts, cancelled=cancelled,
        ))
    for label, closes in rounds:
        session.add(Round(
            concert_id=c.id, kind=RoundKind.LOTTERY_ROUND,
            label=label, closes_at_utc=closes,
        ))
    await session.flush()
    return c


async def _ids(session):
    return {row.event_id for row in await quiet_ladder_rows(session, NOW)}


async def test_dateless_concert_with_no_rounds_is_quiet(session):
    """The canonical case: imported because the page says 詳細は後日発表."""
    await _concert(session, "dateless")
    assert "dateless" in await _ids(session)


async def test_future_legs_with_every_round_closed_is_quiet(session):
    """最速先行 ran and closed; 一般発売 has not been announced."""
    await _concert(
        session, "closed-ladder",
        legs=[(at(12, 1), False)],
        rounds=[("最速先行", at(7, 1))],
    )
    assert "closed-ladder" in await _ids(session)


async def test_a_future_anchor_keeps_a_concert_off_the_list(session):
    """Pins the next_anchor_at reuse. A round still to close is not quiet."""
    await _concert(
        session, "live-ladder",
        legs=[(at(12, 1), False)],
        rounds=[("一般発売", at(9, 1))],
    )
    assert "live-ladder" not in await _ids(session)


async def test_a_concert_already_past_is_not_quiet(session):
    """Its ladder is finished, not quiet. The list drains itself."""
    await _concert(
        session, "past",
        legs=[(at(3, 1), False)],
        rounds=[("最速先行", at(2, 1))],
    )
    assert "past" not in await _ids(session)


async def test_a_fully_cancelled_concert_is_not_quiet(session):
    await _concert(
        session, "dead",
        legs=[(at(12, 1), True)],
        rounds=[("最速先行", at(7, 1))],
    )
    assert "dead" not in await _ids(session)


async def test_the_latest_live_leg_decides_a_multi_leg_run(session):
    """A tour whose first night has passed but whose last has not is still
    quiet -- there is a show ahead with no deadline in front of it.

    NOTE: there is no 'undated leg' case to test. ConcertDay.starts_at_utc
    compiles to DATETIME NOT NULL, so 'dateless' can only mean a concert with
    ZERO legs, which test_dateless_concert_with_no_rounds_is_quiet covers.
    """
    await _concert(
        session, "tour",
        legs=[(at(3, 1), False), (at(12, 1), False)],
        rounds=[("最速先行", at(2, 1))],
    )
    assert "tour" in await _ids(session)


async def test_rows_carry_what_a_re_check_needs(session):
    c = await _concert(
        session, "payload",
        legs=[(at(12, 1), False)],
        rounds=[("最速先行", at(7, 1))],
    )
    c.official_url = "https://example.jp/live"
    c.title_en = "Payload Live"
    await session.flush()

    row = next(r for r in await quiet_ladder_rows(session, NOW) if r.event_id == "payload")
    assert row.title_en == "Payload Live"
    assert row.official_url == "https://example.jp/live"
    assert row.leg_dates == (date(2026, 12, 1),)
    assert [r.label for r in row.rounds] == ["最速先行"]


async def test_never_checked_sorts_before_checked(session):
    a = await _concert(session, "checked")
    b = await _concert(session, "never")
    a.quiet_since_utc = at(8, 1)
    a.ladder_rechecked_at_utc = at(8, 10)
    b.quiet_since_utc = at(8, 1)
    await session.flush()

    order = [row.event_id for row in await quiet_ladder_rows(session, NOW)]
    assert order.index("never") < order.index("checked")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --isolated pytest tests/test_quiet_ladders.py -q`
Expected: every test FAILS with `ImportError: cannot import name 'quiet_ladder_rows'`.

- [ ] **Step 3: Write the module**

Create `src/app/db/quiet_ladders.py`:

```python
"""Round watch: which tracked concerts have a ladder that has gone quiet.

Discovery's sweep answers "what exists that you are not tracking". This answers
"what changed about what you already track" -- a round announced after a concert
was imported is otherwise invisible, and a user who followed the right artist
can still miss the lottery.

Design: docs/superpowers/specs/2026-08-11-round-watch-design.md.

THE PREDICATE, in one place and only here:

    not all_legs_cancelled(days)
    and (a live dated leg is in the future  or  no live leg is dated at all)
    and next_anchor_at(concert, now) is None

Candidates are narrowed in SQL and the anchor clause is applied in Python,
because `is_round_cancelled` is Python. The catalogue is small enough that a
scan is cheaper than a second SQL transliteration of a Python predicate -- and
a transliteration would be free to drift from the original, which is exactly
what promoting `next_anchor_at` was meant to prevent.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.core import _jst_date, _now, all_legs_cancelled, next_anchor_at
from app.db.models import Concert


@dataclass(frozen=True)
class QuietRound:
    """One round the concert DOES carry, so a re-check does not re-propose it."""

    label: str
    kind: str
    opens_at_utc: datetime | None
    closes_at_utc: datetime | None
    results_at_utc: datetime | None
    payment_deadline_at_utc: datetime | None


@dataclass(frozen=True)
class QuietLadder:
    """One row of the worklist."""

    concert_id: int
    event_id: str
    title: str
    title_en: str | None
    leg_dates: tuple[date, ...]
    official_url: str | None
    eventernote_url: str | None
    source_url: str | None
    rounds: tuple[QuietRound, ...]
    quiet_since_utc: datetime | None
    rechecked_at_utc: datetime | None


def _not_yet_performed(concert: Concert, now: datetime) -> bool:
    """Has this concert still got a night ahead of it -- or no nights at all?

    `ConcertDay.starts_at_utc` is DATETIME NOT NULL, so there is no such thing
    as an undated leg: an empty list here means a concert with ZERO legs, which
    is exactly a skeleton import or a `duplicate_concert` clone, and exactly the
    case this feature exists for. The LATEST live leg decides, so a tour whose
    first night has passed and whose last has not is still awaiting a deadline.
    """
    live = [d.starts_at_utc for d in concert.days if not d.cancelled]
    return not live or max(live) > now


def is_quiet(concert: Concert, now: datetime) -> bool:
    """The predicate. `concert` must arrive with `days` and `rounds` loaded."""
    if all_legs_cancelled(concert.days):
        return False
    if not _not_yet_performed(concert, now):
        return False
    return next_anchor_at(concert, now) is None


def _row(concert: Concert) -> QuietLadder:
    return QuietLadder(
        concert_id=concert.id,
        event_id=concert.event_id,
        title=concert.title,
        title_en=concert.title_en,
        leg_dates=tuple(sorted(
            _jst_date(d.starts_at_utc) for d in concert.days if not d.cancelled
        )),
        official_url=concert.official_url,
        eventernote_url=concert.eventernote_url,
        source_url=concert.source_url,
        rounds=tuple(
            QuietRound(
                label=r.label,
                kind=r.kind.value,
                opens_at_utc=r.opens_at_utc,
                closes_at_utc=r.closes_at_utc,
                results_at_utc=r.results_at_utc,
                payment_deadline_at_utc=r.payment_deadline_at_utc,
            )
            for r in concert.rounds
        ),
        quiet_since_utc=concert.quiet_since_utc,
        rechecked_at_utc=concert.ladder_rechecked_at_utc,
    )


async def _quiet_concerts(session: AsyncSession, now: datetime) -> list[Concert]:
    """Every concert the predicate matches, ORM rows with days/rounds loaded.

    selectinload, not lazy access: ConcertDay.venue_tag is lazy="raise" and the
    surrounding code runs outside a greenlet-friendly context often enough that
    an accidental lazy load is a 500 rather than a slow query.
    """
    concerts = (await session.execute(
        select(Concert).options(
            selectinload(Concert.days), selectinload(Concert.rounds)
        )
    )).scalars().all()
    return [c for c in concerts if is_quiet(c, now)]


async def quiet_ladder_rows(
    session: AsyncSession, now: datetime | None = None
) -> list[QuietLadder]:
    """The worklist, longest-unattended first.

    Sort: never checked before ever checked, then oldest check, then longest
    quiet. A row is never hidden -- the stamp answers "have I looked at this",
    and hiding would silently promote it to "is this resolved", which it cannot
    answer.

    Derived live on every call, so the page never depends on the scheduler's
    reconcile pass having run.
    """
    now = now or _now()
    rows = [_row(c) for c in await _quiet_concerts(session, now)]
    far_past = datetime.min.replace(tzinfo=now.tzinfo)
    rows.sort(key=lambda r: (
        r.rechecked_at_utc is not None,
        r.rechecked_at_utc or far_past,
        r.quiet_since_utc or far_past,
    ))
    return rows
```

- [ ] **Step 4: Export from the facade**

In `src/app/db/service.py` add a `from .quiet_ladders import (...)` block in
the same style as the other feature modules, importing `QuietLadder`,
`QuietRound`, `is_quiet` and `quiet_ladder_rows`, and add all four names to
`__all__` in alphabetical position.

- [ ] **Step 5: Run the tests**

Run: `uv run --isolated pytest tests/test_quiet_ladders.py tests/test_service_facade.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite and lint, then commit**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`

```bash
git add src/app/db/quiet_ladders.py src/app/db/service.py tests/test_quiet_ladders.py
git commit -m "feat(db): the quiet-ladders predicate and worklist query

One definition of 'this ladder holds no future anchor', reusing next_anchor_at
so this surface and the agent read API cannot drift. The LATEST live leg decides
whether a show is still ahead, a concert with no legs at all is in, a concert
whose last night has passed is out, and the list drains itself.

Derived live, so the page never depends on the scheduler pass having run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH"
```

---

### Task 4: The reconciliation pass and the Checked writer

**Files:**
- Modify: `src/app/db/quiet_ladders.py`
- Modify: `src/app/db/service.py`
- Test: `tests/test_quiet_ladders.py`

**Interfaces:**
- Consumes: `quiet_ladder_rows`, `is_quiet` (Task 3).
- Produces:
  - `async def reconcile_quiet_ladders(session, now=None) -> list[QuietLadder]` — stamps newcomers, clears leavers, returns ONLY the newcomers.
  - `async def record_ladder_checked(session, event_id: str, now=None) -> bool` — True if a concert was found and stamped.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_quiet_ladders.py`:

```python
from app.db.service import reconcile_quiet_ladders, record_ladder_checked


async def test_reconcile_stamps_newcomers_and_returns_them(session):
    await _concert(session, "newcomer")
    newcomers = await reconcile_quiet_ladders(session, NOW)
    assert [r.event_id for r in newcomers] == ["newcomer"]

    rows = await quiet_ladder_rows(session, NOW)
    assert rows[0].quiet_since_utc == NOW


async def test_an_immediate_second_run_announces_nothing(session):
    """The idempotency the per-tick cadence rests on. If this fails, someone
    has reintroduced a 24h clock instead of relying on the stamp."""
    await _concert(session, "newcomer")
    assert len(await reconcile_quiet_ladders(session, NOW)) == 1
    assert await reconcile_quiet_ladders(session, NOW) == []


async def test_a_stamped_concert_is_never_a_newcomer(session):
    """What the migration's blanket backfill buys: nothing announces on the
    first pass after deploy."""
    c = await _concert(session, "backfilled")
    c.quiet_since_utc = at(8, 1)
    await session.flush()
    assert await reconcile_quiet_ladders(session, NOW) == []


async def test_leaving_the_list_clears_both_stamps(session):
    c = await _concert(session, "recovers")
    await reconcile_quiet_ladders(session, NOW)
    await record_ladder_checked(session, "recovers", NOW)

    session.add(Round(
        concert_id=c.id, kind=RoundKind.LOTTERY_ROUND,
        label="一般発売", closes_at_utc=at(9, 1),
    ))
    await session.flush()
    await reconcile_quiet_ladders(session, NOW)
    await session.refresh(c)

    assert c.quiet_since_utc is None
    assert c.ladder_rechecked_at_utc is None


async def test_a_second_quiet_spell_arrives_unchecked(session):
    """Both stamps belong to the CURRENT spell, so an earlier check does not
    carry over to a question it was not asked about."""
    c = await _concert(session, "again")
    await reconcile_quiet_ladders(session, NOW)
    await record_ladder_checked(session, "again", NOW)

    round_ = Round(
        concert_id=c.id, kind=RoundKind.LOTTERY_ROUND,
        label="一般発売", closes_at_utc=at(9, 1),
    )
    session.add(round_)
    await session.flush()
    await reconcile_quiet_ladders(session, NOW)

    later = at(10, 1)
    newcomers = await reconcile_quiet_ladders(session, later)
    assert [r.event_id for r in newcomers] == ["again"]
    await session.refresh(c)
    assert c.ladder_rechecked_at_utc is None


async def test_record_ladder_checked_reports_a_missing_concert(session):
    assert await record_ladder_checked(session, "nope", NOW) is False
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --isolated pytest tests/test_quiet_ladders.py -q`
Expected: the six new tests FAIL with `ImportError: cannot import name
'reconcile_quiet_ladders'`; the Task 3 tests still pass.

- [ ] **Step 3: Implement**

Append to `src/app/db/quiet_ladders.py`:

```python
async def reconcile_quiet_ladders(
    session: AsyncSession, now: datetime | None = None
) -> list[QuietLadder]:
    """Bring the stamps in line with the predicate; return the NEWCOMERS.

    SELF-IDEMPOTENT, and that is what lets this run every tick instead of on
    the sweep's 24-hour clock: a stamped concert is no longer a newcomer, so a
    re-run announces nothing. The sweep needs a clock because its work is 86
    third-party fetches ending in a DM; this is a query and a diff.

    The CALLER commits, and must commit the queued notice in the SAME
    transaction as these stamps -- that pairing is what makes the notice
    exactly-once. Committing the stamp first would drop the DM on a crash;
    committing the notice first would repeat it.
    """
    now = now or _now()
    concerts = (await session.execute(
        select(Concert).options(
            selectinload(Concert.days), selectinload(Concert.rounds)
        )
    )).scalars().all()

    newcomers: list[Concert] = []
    for concert in concerts:
        if is_quiet(concert, now):
            if concert.quiet_since_utc is None:
                concert.quiet_since_utc = now
                newcomers.append(concert)
        elif concert.quiet_since_utc is not None:
            # Both stamps belong to the current quiet spell.
            concert.quiet_since_utc = None
            concert.ladder_rechecked_at_utc = None
    await session.flush()
    return [_row(c) for c in newcomers]


async def record_ladder_checked(
    session: AsyncSession, event_id: str, now: datetime | None = None
) -> bool:
    """Stamp "I have re-checked this one". False when no such concert exists.

    Sorts and dims; never hides. A concert whose ladder you checked in March
    genuinely does grow a 一般発売 in July.
    """
    concert = (await session.execute(
        select(Concert).where(Concert.event_id == event_id)
    )).scalar_one_or_none()
    if concert is None:
        return False
    concert.ladder_rechecked_at_utc = now or _now()
    await session.flush()
    return True
```

- [ ] **Step 4: Export from the facade**

Add `reconcile_quiet_ladders` and `record_ladder_checked` to the
`from .quiet_ladders import (...)` block and to `__all__`.

- [ ] **Step 5: Run tests, suite, lint**

Run: `uv run --isolated pytest tests/test_quiet_ladders.py tests/test_service_facade.py -q`
then `uv run --isolated pytest -q` then `uv run --isolated ruff check .`

- [ ] **Step 6: Commit**

```bash
git add src/app/db/quiet_ladders.py src/app/db/service.py tests/test_quiet_ladders.py
git commit -m "feat(db): reconcile quiet-ladder stamps, and record a re-check

reconcile_quiet_ladders stamps newcomers, clears both stamps for leavers and
returns only the newcomers. Self-idempotent by construction -- a stamped
concert is not a newcomer -- which is what lets the scheduler run it every tick
rather than on a clock it would gain nothing from.

A pinned test asserts an immediate second run announces nothing, so a later
'optimisation' that reintroduces a cadence clock fails loudly.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH"
```

---

### Task 5: The pure message and copy block

**Files:**
- Create: `src/app/domain/quiet_ladder_message.py`
- Test: `tests/test_quiet_ladder_message.py`

**Interfaces:**
- Consumes: nothing from `db/` — this module is PURE. The caller adapts
  `QuietLadder` rows into its own `QuietEntry`.
- Produces:
  - `@dataclass(frozen=True) QuietEntry(title, event_id, leg_dates: tuple[date, ...], round_labels: tuple[str, ...], official_url: str | None, eventernote_url: str | None)`
  - `def build_quiet_ladder_dm(entries, total, *, base_url, budget=DM_CHAR_BUDGET) -> str`
  - `def build_quiet_ladder_block(entries) -> str`

**Why two functions and not one.** `build_discovery_dm` deliberately serves both
its DM and its copy block through ONE formatter with a `budget` parameter, under
the ruling that "a second one would drift". That ruling is honored here in the
form that fits: both functions live in ONE module over ONE input dataclass, so
a field added to `QuietEntry` is visibly unhandled in both. They are not two
renderings of the same content — the DM is a nudge ("these three went quiet,
here is the page") and the block is an agent prompt (URLs and known rounds).
Forcing one function would either bloat the DM with URLs or starve the block of
them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quiet_ladder_message.py`:

```python
"""The quiet-ladder DM and copy block. Pure: no DB, no Discord."""

from datetime import date

from app.domain.quiet_ladder_message import (
    QuietEntry,
    build_quiet_ladder_block,
    build_quiet_ladder_dm,
)

BASE = "https://dekimasen.app"


def entry(n: int, **over) -> QuietEntry:
    fields = dict(
        title=f"Concert {n}",
        event_id=f"concert-{n}",
        leg_dates=(date(2026, 12, n),),
        round_labels=("最速先行",),
        official_url=f"https://example.jp/{n}",
        eventernote_url=None,
    )
    fields.update(over)
    return QuietEntry(**fields)


def test_no_entries_is_silence():
    """Running every tick makes this load-bearing: a 'nothing found' message
    at this cadence would be 1,440 DMs a day."""
    assert build_quiet_ladder_dm([], total=0, base_url=BASE) == ""


def test_the_dm_names_the_concerts_and_links_the_page():
    body = build_quiet_ladder_dm([entry(1)], total=1, base_url=BASE)
    assert "Concert 1" in body
    assert f"{BASE}/admin/quiet-ladders" in body


def test_the_dm_reports_the_real_total_when_it_cannot_name_them_all():
    entries = [entry(i) for i in range(1, 21)]
    body = build_quiet_ladder_dm(entries, total=20, base_url=BASE)
    assert "20" in body
    assert len(body) <= 1900


def test_the_block_carries_what_a_re_check_needs():
    block = build_quiet_ladder_block([entry(1)])
    assert "concert-1" in block
    assert "https://example.jp/1" in block
    assert "最速先行" in block


def test_the_block_says_when_a_concert_has_no_rounds_at_all():
    """A concert with a closed 最速先行 reads differently from one with
    nothing -- the agent needs to know which it is."""
    block = build_quiet_ladder_block([entry(1, round_labels=())])
    assert "no rounds" in block.lower()


def test_the_block_says_when_a_concert_has_no_dates():
    block = build_quiet_ladder_block([entry(1, leg_dates=())])
    assert "no dates" in block.lower()
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run --isolated pytest tests/test_quiet_ladder_message.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.quiet_ladder_message'`.

- [ ] **Step 3: Implement**

Create `src/app/domain/quiet_ladder_message.py`:

```python
"""The quiet-ladder digest DM, and the copy block the admin page renders.

Pure (no DB, no Discord), like domain/discovery_message.py, which this mirrors.

TWO functions over ONE dataclass in ONE module. `build_discovery_dm` serves its
DM and its copy block through a single formatter with a `budget` parameter,
under the ruling that a second formatter would drift; the spirit of that ruling
is what the shared `QuietEntry` and the shared module buy here. They stay two
functions because they answer different questions -- the DM is a nudge, the
block is a prompt an agent acts on -- and folding them would either bloat the
DM with URLs or starve the block of them.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.domain.discovery_message import DM_CHAR_BUDGET

DM_LIST_LIMIT = 10


@dataclass(frozen=True)
class QuietEntry:
    """One concert whose ladder holds no future anchor."""

    title: str
    event_id: str
    leg_dates: tuple[date, ...]
    round_labels: tuple[str, ...]
    official_url: str | None
    eventernote_url: str | None


def _dates(entry: QuietEntry) -> str:
    if not entry.leg_dates:
        return "no dates announced"
    return ", ".join(d.strftime("%d %b") for d in entry.leg_dates)


def _rounds(entry: QuietEntry) -> str:
    if not entry.round_labels:
        return "no rounds recorded"
    return ", ".join(entry.round_labels)


def build_quiet_ladder_dm(
    entries: Sequence[QuietEntry],
    total: int,
    *,
    base_url: str,
    budget: int | None = DM_CHAR_BUDGET,
) -> str:
    """The digest, or "" when there is nothing to say.

    Silence is the NORMAL output. This pass runs every tick, so a "nothing
    found" message would be 1,440 DMs a day -- the same mistake discovery's
    daily note warns about, an order of magnitude louder.

    Shrinks the whole message until it fits, like build_discovery_dm, so the
    named concerts and the "+N more" count never disagree. `total` is the real
    backlog, whatever gets rendered.
    """
    if not entries:
        return ""

    kept = list(entries[:DM_LIST_LIMIT])
    while True:
        body = _compose(kept, total, base_url)
        if budget is None or len(body) <= budget or len(kept) <= 1:
            return body
        kept.pop()


def _compose(kept: Sequence[QuietEntry], total: int, base_url: str) -> str:
    head = (
        f"**{total} concert{'' if total == 1 else 's'} went quiet** — no future "
        "deadline left on the ladder, and the show has not happened yet."
    )
    lines = [f"• {e.title} ({_dates(e)})" for e in kept]
    dropped = total - len(kept)
    if dropped > 0:
        lines.append(f"…and {dropped} more.")
    tail = f"Re-check them: {base_url}/admin/quiet-ladders"
    return "\n".join([head, "", *lines, "", tail])


def build_quiet_ladder_block(entries: Sequence[QuietEntry]) -> str:
    """The paste-ready block: everything an agent needs to re-check a ladder.

    The rounds already known are the load-bearing part. Without them the agent
    re-proposes rounds the catalogue already holds, which is the failure this
    block exists to avoid.

    NO budget: a web page has no character cap, and a block that silently
    dropped concerts on the very page the DM points at would leave them
    reachable from nowhere. Same reasoning as build_discovery_dm's `budget=None`
    call site.
    """
    if not entries:
        return ""
    out: list[str] = []
    for e in entries:
        out.append(f"- {e.event_id}: {e.title}")
        out.append(f"  dates: {_dates(e)}")
        out.append(f"  rounds held: {_rounds(e)}")
        if e.official_url:
            out.append(f"  official: {e.official_url}")
        if e.eventernote_url:
            out.append(f"  eventernote: {e.eventernote_url}")
    return "\n".join(out)
```

- [ ] **Step 4: Run tests, suite, lint, commit**

Run: `uv run --isolated pytest tests/test_quiet_ladder_message.py -q`, then the
full suite and ruff.

```bash
git add src/app/domain/quiet_ladder_message.py tests/test_quiet_ladder_message.py
git commit -m "feat(domain): the quiet-ladder digest and copy block

Two functions over one dataclass in one module. The DM is a nudge and the block
is an agent prompt, so they render different payloads -- but a field added to
QuietEntry is visibly unhandled in both, which is what discovery_message's
one-formatter ruling was protecting.

Silence on an empty list is load-bearing here rather than tasteful: the pass
runs every tick, so a 'nothing found' note would be 1,440 DMs a day.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH"
```

---

### Task 6: The scheduler pass and the notice

**Files:**
- Modify: `src/app/scheduler/loop.py` (after the discovery sweep block, near line 300)
- Test: `tests/test_quiet_ladder_notice.py`

**Interfaces:**
- Consumes: `reconcile_quiet_ladders` (Task 4), `build_quiet_ladder_dm` (Task 5).
- Produces: `Notification` rows with `kind="quiet_ladder"`, `concert_id=None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quiet_ladder_notice.py`:

```python
"""The quiet-ladder notice: one digest per pass, to admins, never per concert."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert, Notification, User
from app.db.service import ensure_user
from app.scheduler.loop import run_quiet_ladder_pass

ADMIN_ID = 42
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))


async def _quiet(session, event_id):
    await ensure_user(session, 99, "editor")
    session.add(Concert(title=event_id, event_id=event_id, created_by=99))
    await session.flush()


async def _notices(session):
    return list((await session.execute(
        select(Notification).where(Notification.kind == "quiet_ladder")
    )).scalars())


async def test_one_digest_names_every_newcomer(session):
    await _quiet(session, "a")
    await _quiet(session, "b")
    await run_quiet_ladder_pass(session, NOW)

    notices = await _notices(session)
    assert len(notices) == 1, "one digest per pass, not one per concert"
    assert "a" in notices[0].body and "b" in notices[0].body
    assert notices[0].concert_id is None  # plain body, not a per-concert embed


async def test_no_newcomers_sends_nothing(session):
    await run_quiet_ladder_pass(session, NOW)
    assert await _notices(session) == []


async def test_a_second_pass_sends_nothing(session):
    await _quiet(session, "a")
    await run_quiet_ladder_pass(session, NOW)
    await run_quiet_ladder_pass(session, NOW)
    assert len(await _notices(session)) == 1


async def test_an_admin_who_never_signed_in_gets_a_user_row_first(session):
    """Notification.user_id is an FK to users.discord_id, so a queued notice
    for an admin with no row raises IntegrityError at flush, far from here."""
    await _quiet(session, "a")
    await run_quiet_ladder_pass(session, NOW)
    assert await session.get(User, ADMIN_ID) is not None


async def test_an_existing_admin_keeps_their_username(session):
    """ensure_user refreshes the username, so calling it unconditionally would
    overwrite a real admin's name with the placeholder on every tick."""
    await ensure_user(session, ADMIN_ID, "reiji")
    await _quiet(session, "a")
    await run_quiet_ladder_pass(session, NOW)

    admin = await session.get(User, ADMIN_ID)
    assert admin.username == "reiji"
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run --isolated pytest tests/test_quiet_ladder_notice.py -q`
Expected: FAIL — `ImportError: cannot import name 'run_quiet_ladder_pass'`.

- [ ] **Step 3: Implement the pass**

In `src/app/scheduler/loop.py`, add to the imports:

```python
from app.db.service import reconcile_quiet_ladders
from app.domain.quiet_ladder_message import QuietEntry, build_quiet_ladder_dm
```

(`Notification`, `User`, `ensure_user` and `settings` are already imported by
this module; verify before adding duplicates.)

Then add this function above `tick`:

```python
async def run_quiet_ladder_pass(session, now: datetime) -> int:
    """Stamp concerts whose ladder just went quiet, and DM the admins once.

    Returns how many concerts newly went quiet.

    NO CADENCE CLOCK, unlike the discovery sweep beneath which this sits, and
    that is deliberate rather than an oversight. The sweep's 24-hour clock
    protects 86 third-party fetches ending in a DM. This is a query and a diff
    over the local catalogue, and reconcile_quiet_ladders is self-idempotent --
    a stamped concert is not a newcomer -- so a clock would protect nothing and
    would delay a notice by up to a day.

    The stamps and the queued notice are left for the CALLER to commit together
    in one transaction. That pairing is what makes the notice exactly-once: a
    crash between them would otherwise either lose the DM or repeat it.
    """
    newcomers = await reconcile_quiet_ladders(session, now)
    if not newcomers:
        # Silence is the normal output. At this cadence a "nothing found"
        # note would be 1,440 DMs a day.
        return 0

    body = build_quiet_ladder_dm(
        [
            QuietEntry(
                title=row.title,
                event_id=row.event_id,
                leg_dates=row.leg_dates,
                round_labels=tuple(r.label for r in row.rounds),
                official_url=row.official_url,
                eventernote_url=row.eventernote_url,
            )
            for row in newcomers
        ],
        total=len(newcomers),
        base_url=settings.base_url,
    )
    if not body:
        return len(newcomers)

    for admin_id in sorted(settings.admin_ids):
        # Guarded on absence, never unconditional: ensure_user refreshes the
        # username, which would overwrite a real admin's name with this
        # placeholder on every single tick.
        if await session.get(User, admin_id) is None:
            await ensure_user(session, admin_id, str(admin_id))
        session.add(Notification(
            user_id=admin_id,
            body=body,
            kind="quiet_ladder",
            # NULL means "render the plain body, not a per-concert embed", and
            # already makes record_deliveries skip the title lookup. A digest
            # naming several concerts could not be one concert's embed anyway.
            concert_id=None,
        ))
    return len(newcomers)
```

- [ ] **Step 4: Wire it into the tick**

In `tick`, AFTER the discovery sweep's `try`/`except` block, add:

```python
        # Round watch: its own try/except and its own commit, for the same
        # reason every block above has them -- the least important operation in
        # the tick must never be able to roll back the most important one.
        # Every tick, not on the sweep's clock: see run_quiet_ladder_pass.
        try:
            went_quiet = await run_quiet_ladder_pass(session, now)
            await session.commit()
            if went_quiet:
                log.info("round watch: %d concert(s) newly quiet", went_quiet)
        except Exception:
            log.exception("quiet ladder pass failed; delivery was unaffected")
            await session.rollback()
```

Note the difference from the sweep's handler: there is nothing to re-stamp on
failure. The sweep re-stamps because a rolled-back `last_run_at` would re-run it
every 60 seconds forever; this pass has no clock to restore, and re-running it
next tick is exactly what should happen.

- [ ] **Step 5: Confirm the notice kind is NOT in `UNREPORTED_NOTE_KINDS`**

Read `UNREPORTED_NOTE_KINDS` in `src/app/db/service.py` (or the module it comes
from) and confirm `"quiet_ladder"` is absent. It must stay absent: that set is
only for notices that REPORT ON deliveries, and this reports on the catalogue.
It belongs in `delivery_log` like any other notice — the same call the
Eventernote discovery notice makes. **Do not add it.** This step is a read and a
confirmation, not an edit.

- [ ] **Step 6: Run tests, suite, lint, commit**

Run: `uv run --isolated pytest tests/test_quiet_ladder_notice.py -q`, then the
full suite and ruff.

```bash
git add src/app/scheduler/loop.py tests/test_quiet_ladder_notice.py
git commit -m "feat(scheduler): DM the admins once when a concert goes quiet

One digest per pass, never one per concert, queued through the outbox like
every other notice (invariant 4). concert_id NULL so the drain renders the
plain body -- a digest naming several concerts is not one concert's embed.

Runs every tick with no cadence clock: reconcile_quiet_ladders is
self-idempotent, so a clock would protect nothing and delay a notice by up to a
day. Its own try/except and its own commit, below reminder delivery.

NOT added to UNREPORTED_NOTE_KINDS: that set is for notices reporting on
deliveries, and this one reports on the catalogue.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH"
```

---

### Task 7: The admin page

**Files:**
- Create: `src/app/web/routes/quiet_ladders.py`
- Create: `src/app/web/templates/admin_quiet_ladders.html`
- Modify: `src/app/web/app.py` (import + registration, near lines 40 and 366)
- Test: `tests/test_admin_quiet_ladders.py`

**Interfaces:**
- Consumes: `quiet_ladder_rows`, `record_ladder_checked` (Tasks 3-4),
  `build_quiet_ladder_block` (Task 5).
- Produces: `GET /admin/quiet-ladders`,
  `POST /admin/quiet-ladders/{event_id}/checked`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_quiet_ladders.py`. Copy the `client` fixture, the
`login_as` helper and the `ADMIN_ID, EDITOR_ID = 42, 77` line VERBATIM from
`tests/test_admin_discoveries.py:30-64` — same shape, same reasons. Its imports
are a superset of what you need; the ones these tests actually use are:

```python
from fastapi.testclient import TestClient
from sqlalchemy import select
import pytest

from app.config import settings
from app.db.models import Concert
from app.db.service import ensure_user
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app
```

Then:

```python
async def test_the_page_renders_for_an_admin(client):
    """Every page needs a logged-in GET render test: a missing one shipped a
    500 once, from template context drift."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        s.add(Concert(title="ブシロード20周年", event_id="bushi", created_by=ADMIN_ID))
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders")
    assert r.status_code == 200
    assert "bushi" in r.text


async def test_an_editor_is_forbidden(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/quiet-ladders").status_code == 403


async def test_signed_out_is_redirected_not_an_error(client):
    r = client.get("/admin/quiet-ladders")
    assert r.status_code == 303


async def test_checked_stamps_and_redirects(client):
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        s.add(Concert(title="Quiet", event_id="quiet", created_by=ADMIN_ID))
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/quiet-ladders/quiet/checked")
    assert r.status_code == 303

    async with client.db() as s:
        c = (await s.execute(
            select(Concert).where(Concert.event_id == "quiet")
        )).scalar_one()
        assert c.ladder_rechecked_at_utc is not None


async def test_checking_an_unknown_concert_is_404(client):
    login_as(client, ADMIN_ID, "reiji")
    assert client.post("/admin/quiet-ladders/nope/checked").status_code == 404
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run --isolated pytest tests/test_admin_quiet_ladders.py -q`
Expected: FAIL — 404s, because the routes do not exist.

- [ ] **Step 3: Write the route module**

Create `src/app/web/routes/quiet_ladders.py`:

```python
"""Round watch: tracked concerts whose ladder has gone quiet.

  GET  /admin/quiet-ladders                    the worklist, plus one paste block
  POST /admin/quiet-ladders/{event_id}/checked stamp "I re-checked this"

Its own module and its own page rather than a section of /admin/discoveries.
That surface answers "what exists that you are not tracking"; this answers
"what changed about what you already track". discoveries.py's own docstring
argues for splitting on exactly this line, and a router registers whole.

This surface WRITES ONE THING: ladder_rechecked_at_utc. It never edits a
concert -- there is no update path back in (import answers 409 for a concert
that exists, invariant 6), so a re-check ends at the concert's edit page or at
an agent, which is what the copy block is for.

Copy is English-only and NOT wrapped in _(), like /admin/deliveries and
/admin/discoveries: an operational page only admins see should not cost msgids
in three languages. Only the Preferences link to it is translated.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import quiet_ladder_rows, record_ladder_checked
from app.db.session import get_session
from app.domain.quiet_ladder_message import QuietEntry, build_quiet_ladder_block
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web.app at startup


@router.get("/admin/quiet-ladders", response_class=HTMLResponse)
async def quiet_ladders(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = await quiet_ladder_rows(session)
    block = build_quiet_ladder_block([
        QuietEntry(
            title=row.title,
            event_id=row.event_id,
            leg_dates=row.leg_dates,
            round_labels=tuple(r.label for r in row.rounds),
            official_url=row.official_url,
            eventernote_url=row.eventernote_url,
        )
        for row in rows
    ])
    return templates.TemplateResponse(
        request,
        "admin_quiet_ladders.html",
        {"rows": rows, "copy_text": block},
    )


@router.post("/admin/quiet-ladders/{event_id}/checked")
async def mark_checked(
    event_id: str,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if not await record_ladder_checked(session, event_id):
        raise HTTPException(status_code=404)
    await session.commit()
    return RedirectResponse("/admin/quiet-ladders", status_code=303)
```

- [ ] **Step 4: Write the template**

Create `src/app/web/templates/admin_quiet_ladders.html`:

```html
{% extends "base.html" %}
{#- Admin-only operational page: English-only and deliberately NOT wrapped in
    _(), following admin_discoveries.html. -#}
{% block title %}Quiet ladders — dekimasen.app{% endblock %}
{% block content %}
<h1>Quiet ladders</h1>
<p class="dim">
  Concerts you track whose ladder holds no future deadline, and whose show has
  not happened yet. Either the next round has not been announced, or it was
  announced somewhere the catalogue never read. Nothing here is fetched — it is
  a question about what you already hold.
</p>

{% if not rows %}
<div class="edgecard ok">
  Every tracked concert with a future date still has a deadline ahead of it.
</div>
{% else %}
<table class="tagtable">
  <thead>
    <tr><th>Concert</th><th>Dates</th><th>Rounds held</th><th>Quiet</th><th></th></tr>
  </thead>
  <tbody>
  {% for row in rows %}
    <tr{% if row.rechecked_at_utc %} class="dim"{% endif %}>
      <td>
        <a href="/concerts/{{ row.event_id }}">{{ row.title }}</a>
        {% if row.official_url %}
          <a href="{{ row.official_url }}" rel="noreferrer noopener">official</a>
        {% endif %}
        {% if row.eventernote_url %}
          <a href="{{ row.eventernote_url }}" rel="noreferrer noopener">eventernote</a>
        {% endif %}
        <a href="/concerts/{{ row.event_id }}/edit">edit</a>
      </td>
      {#- NOT the `day_month` global: that takes an aware UTC datetime and runs
          utc_to_jst on it, while leg_dates are already JST `date` objects
          (db/core.py's _jst_date). Passing a date would raise. -#}
      <td>
        {% if row.leg_dates %}
          {% for d in row.leg_dates %}{{ d.day }} {{ d.strftime('%b') }}{% if not loop.last %}, {% endif %}{% endfor %}
        {% else %}<span class="dim">no dates announced</span>{% endif %}
      </td>
      <td>
        {% if row.rounds %}
          {% for r in row.rounds %}{{ r.label }}{% if not loop.last %}, {% endif %}{% endfor %}
        {% else %}<span class="dim">none</span>{% endif %}
      </td>
      <td>
        {% if row.quiet_since_utc %}since {{ row.quiet_since_utc.strftime('%Y-%m-%d') }}
        {% else %}<span class="dim">not yet stamped</span>{% endif %}
        {% if row.rechecked_at_utc %}
          <br><span class="dim">checked {{ row.rechecked_at_utc.strftime('%Y-%m-%d') }}</span>
        {% endif %}
      </td>
      <td>
        <form method="post" action="/admin/quiet-ladders/{{ row.event_id }}/checked">
          <button type="submit" class="act">Checked</button>
        </form>
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>

{#- data-copy read via dataset, never interpolated into the onclick: the
    browser HTML-decodes an attribute before parsing it as JS, so Jinja's
    escaping would not protect a title containing an apostrophe (invariant 7).
    Mirrors admin_discoveries.html. -#}
<button type="button" class="act" data-copy="{{ copy_text }}"
        onclick="navigator.clipboard.writeText(this.dataset.copy)">Copy block</button>
<pre style="white-space:pre-wrap;margin:0">{{ copy_text }}</pre>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Register the router**

In `src/app/web/app.py`, beside the other route imports (~line 40):

```python
from app.web.routes import quiet_ladders as quiet_ladder_routes
```

and beside the other registrations (~line 366):

```python
    quiet_ladder_routes.templates = templates
    app.include_router(quiet_ladder_routes.router)
```

There is no ordering hazard here — the paths share no prefix with a
`{event_id}` template route (the `routes/imports.py`-before-`concerts.py` trap
does not apply).

- [ ] **Step 6: Run tests, suite, lint**

Run: `uv run --isolated pytest tests/test_admin_quiet_ladders.py -q`, then
`uv run --isolated pytest -q` and `uv run --isolated ruff check .`

- [ ] **Step 7: Commit**

```bash
git add src/app/web/routes/quiet_ladders.py \
        src/app/web/templates/admin_quiet_ladders.html \
        src/app/web/app.py tests/test_admin_quiet_ladders.py
git commit -m "feat(web): the quiet-ladders admin page

Its own page and module rather than a section of /admin/discoveries: that
surface answers 'what exists that you are not tracking', this answers 'what
changed about what you track'.

Derived live on every load, so it never depends on the scheduler pass. Writes
one thing (ladder_rechecked_at_utc), which sorts and dims but never hides -- a
concert checked in March grows a 一般発売 in July. Copy block plus per-row
links, because at a hundred quiet concerts clicking each one is the whole cost
of the feature.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH"
```

---

### Task 8: Link it, and update the docs

**Files:**
- Modify: `src/app/web/templates/preferences.html` (beside the Discoveries link)
- Modify: `docs/architecture.md`
- Modify: `WISHLIST.md`
- Test: `tests/test_admin_quiet_ladders.py`

- [ ] **Step 1: Add the link beside Discoveries**

In `src/app/web/templates/preferences.html`, immediately after the Discoveries
`<div class="subrow two">` block (currently lines 485-488), insert:

```html
    <div class="subrow two">
      <span class="nm3"><a href="/admin/quiet-ladders">{{ _("Quiet ladders") }}</a></span>
      <span class="sw"><span class="clock">{{ _("Concerts you track whose ladder has no deadline left, and whose show has not happened yet.") }}</span></span>
    </div>
```

The LINK TEXT is translated even though the page it points at is not — that is
the treatment every neighbour in this block uses.

- [ ] **Step 1b: Add both translations, or CI fails**

Two new msgids means two new entries in EACH catalogue. Per CLAUDE.md:

```
uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run pybabel update -i messages.pot -d src/app/translations -l ja
uv run pybabel update -i messages.pot -d src/app/translations -l zh
```

Then fill in the four `msgstr` values by hand in
`src/app/translations/ja/LC_MESSAGES/messages.po` and the `zh` equivalent, and
DELETE `messages.pot` (gitignored, regenerable). Suggested translations —
adjust if you read Japanese or Chinese better than this plan does:

- ja: `Quiet ladders` → `動きのない受付`; the blurb →
  `追跡中の公演のうち、締切がもう残っていないのに開催日がまだ先のもの。`
- zh: `Quiet ladders` → `沉寂的抽选阶梯`; the blurb →
  `你关注的公演中，已无剩余截止日期但演出尚未举行的那些。`

Verify with `uv run --isolated pytest tests/test_i18n_catalogues.py -q` — it
fails on anything left untranslated or fuzzy.

- [ ] **Step 2: Add a render assertion**

Append to `tests/test_admin_quiet_ladders.py`:

```python
async def test_preferences_links_an_admin_to_the_page(client):
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/preferences")
    assert "/admin/quiet-ladders" in r.text
```

- [ ] **Step 3: Run it**

Run: `uv run --isolated pytest tests/test_admin_quiet_ladders.py -q`

- [ ] **Step 4: Document the module**

Add a `db/quiet_ladders.py` entry to `docs/architecture.md`, in the same style
as its neighbours, recording: the predicate; why `next_anchor_at` is reused
rather than restated; why dated legs decide the mixed case; why the pass has no
cadence clock and what would break if one were added; and why both stamps clear
together.

Add one line to CLAUDE.md's Layout list naming `db/quiet_ladders.py`, matching
the existing one-line-per-module style.

- [ ] **Step 5: Move the WISHLIST entry**

Per CLAUDE.md's "Feature wishlist" section: move entry #2 to the Shipped
section with today's date and the reasoning that produced it, noting that only
the CHEAPEST of its three shapes shipped and that the other two (the
discovery-matcher round-gap flag, the scheduled re-fetch) remain. Then do the
full revision pass the file requires: re-rank the remaining entries and
reconsider which are still useful now that this surface exists to receive their
output.

- [ ] **Step 6: Full suite, lint, commit**

```bash
git add src/app/web/templates/preferences.html src/app/translations \
        docs/architecture.md CLAUDE.md WISHLIST.md tests/test_admin_quiet_ladders.py
git commit -m "docs: round watch in architecture, CLAUDE.md and WISHLIST

WISHLIST #2 ships its cheapest shape only; the round-gap flag and the scheduled
re-fetch stay on the list, and the full re-rank pass the file requires is done
in the same commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH"
```

---

## Verification before calling it done

- [ ] `uv run --isolated pytest -q` — full suite green, and the count has grown
      by roughly 25 tests.
- [ ] `uv run --isolated ruff check .` — clean.
- [ ] `uv run --isolated alembic upgrade head` on a COPY of a realistic DB, then
      confirm by query that every concert row carries a `quiet_since_utc`.
- [ ] Run the app (`uv run python -m app.main`), sign in as an admin, open
      `/admin/quiet-ladders`, confirm the list is not empty against the real
      dev catalogue, press Checked on one row and confirm it dims and sorts to
      the bottom.
- [ ] Confirm the first scheduler tick after that migration queues NO
      `quiet_ladder` notification (the blanket backfill's whole purpose).
