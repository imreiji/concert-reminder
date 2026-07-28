# Delivery Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admins a durable record of every DM the scheduler delivered, plus a per-tick digest DM, so a production incident is visible within a minute instead of never.

**Architecture:** One new table (`delivery_log`) written by the scheduler after its delivery-bookkeeping commit, in its own try/except with its own commit — the same isolation `evaluate_and_alert` already uses, because DMs are already on the wire and a logging failure must never roll back `sent_at_utc` and cause double-sends. A pure formatter in `domain/digest.py` turns log facts into digest text; `db/service.py` gathers rows and queues one `Notification(kind="delivery_digest")` per admin, which the *next* tick delivers through the existing plain-text path. `/admin/deliveries` reads the table.

**Tech Stack:** SQLAlchemy 2.0 async + Alembic (SQLite/WAL), FastAPI + Jinja2, discord.py, pytest-asyncio auto mode.

**Spec:** `docs/superpowers/specs/2026-07-28-delivery-feed-design.md`

## Global Constraints

- **Deviation from the spec, applied throughout:** the spec calls the table `reminder_deliveries`, but it also specifies logging **both** drains — reminders *and* notifications — so that name describes half its contents. This plan uses **`delivery_log`** (class `DeliveryLog`). Task 1 records this in the spec's Deviations section.
- All datetime columns use `UTCDateTime`; the DB stores aware UTC only. Never store or compare naive datetimes (invariant 1).
- Every enum column uses `Enum(E, values_callable=lambda e: [m.value for m in e])` so `.value` strings land in SQLite.
- After `alembic revision --autogenerate`, ALWAYS hand-edit the revision: replace `app.db.models.UTCDateTime()` with `sa.DateTime()`. **Corrected during Task 2:** revisions live in **`alembic/versions/`** (`alembic.ini` sets `script_location = alembic`), not `migrations/versions/`; and this repo's `script.py.mako` emits no imports block, so there is no `import app.db.models` line to delete — autogenerate emits a bare `app.db.models.UTCDateTime()` that is a `NameError` if applied unedited. The substitution is a correctness fix, not a tidiness one.
- **New imports in test files go at the TOP of the file, in one block — never appended mid-file.** Several tasks below say "append to `tests/<file>.py`" and show an import statement in the snippet. Ruff's config selects `E`, which includes **E402 (module-import-not-at-top-of-file)**, so a literal append fails the lint gate. Merge the imports into the existing top-of-file block and append only the test functions. (Found the hard way in Task 2; it applies to Tasks 3, 5, 6 and 7.)
- All enums live in `src/app/domain/types.py` as `enum.StrEnum`. `db/` must never import from `scheduler/` — `scheduler/` calls `db/service.py`, not the reverse.
- DB test fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener, or cascades silently do not fire and erasure tests pass while leaking.
- Digest and `/admin/deliveries` copy is **English-only, NOT wrapped in `_()`** — following the `/me/test-dm` precedent (`HTMLResponse("Test DM sent!")`). Adding msgids would force ja+zh translations via `tests/test_i18n_catalogues.py` for copy only admins read.
- Gates before every commit: `uv run --isolated ruff check .` clean AND `uv run --isolated pytest -q` passing. Use `--isolated` (an external `serve.py` can lock `.venv`). Run tests in the FOREGROUND.
- Never send DMs directly from web routes (invariant 4). The digest goes through the `notifications` outbox.
- `RETENTION_DAYS = 30`, matching `deploy/backup.sh`'s S3 lifecycle so the system has one retention number, not two.

---

### Task 1: Move `DeliveryOutcome` into the domain layer

`DeliveryOutcome` currently lives in `scheduler/loop.py`. Task 2 needs it as a DB column type, and `db/models.py` importing from `scheduler/` would invert the layering. Pure value-preserving move — the enum has never been persisted, so there is no data to migrate.

**Files:**
- Modify: `src/app/domain/types.py` (append the enum)
- Modify: `src/app/scheduler/loop.py:31,65-73` (drop the local definition, import instead)
- Modify: `docs/superpowers/specs/2026-07-28-delivery-feed-design.md` (Deviations section)
- Test: `tests/test_delivery_log.py` (new file)

**Interfaces:**
- Consumes: nothing.
- Produces: `app.domain.types.DeliveryOutcome`, a `StrEnum` with members `SUCCESS = "success"`, `FORBIDDEN = "forbidden"`, `TRANSIENT_FAILURE = "transient_failure"`. `app.scheduler.loop.DeliveryOutcome` remains a working alias by virtue of the import, so no existing caller changes.

- [x] **Step 1: Write the failing test**

Create `tests/test_delivery_log.py`:

```python
"""The delivery log, its digest, and the retention prune."""

from app.domain.types import DeliveryOutcome


def test_delivery_outcome_lives_in_domain_types():
    assert DeliveryOutcome.SUCCESS.value == "success"
    assert DeliveryOutcome.FORBIDDEN.value == "forbidden"
    assert DeliveryOutcome.TRANSIENT_FAILURE.value == "transient_failure"


def test_delivery_outcome_is_a_str_enum():
    """Every other enum in this app is a StrEnum, and the DB stores .value
    strings. A plain Enum here would serialise differently."""
    assert isinstance(DeliveryOutcome.SUCCESS, str)


def test_scheduler_reexports_the_same_object():
    """scheduler/loop.py keeps working through the import, so no existing
    caller had to change. If these ever diverge, an `is` comparison in tick()
    silently stops matching."""
    from app.scheduler.loop import DeliveryOutcome as FromScheduler

    assert FromScheduler is DeliveryOutcome
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run --isolated pytest tests/test_delivery_log.py -v`
Expected: FAIL — `ImportError: cannot import name 'DeliveryOutcome' from 'app.domain.types'`

- [x] **Step 3: Add the enum to `domain/types.py`**

Append to `src/app/domain/types.py`:

```python
class DeliveryOutcome(enum.StrEnum):
    """A DM send's result. Distinct from "should this row be marked sent"
    (SUCCESS and FORBIDDEN both do; TRANSIENT_FAILURE doesn't) and from
    "should the per-user dm_blocked_since flag change" (SUCCESS clears it,
    FORBIDDEN sets it, TRANSIENT_FAILURE touches neither).

    Lives here rather than in scheduler/loop.py, where it was defined
    originally, because db/models.py types a column on it and db/ must never
    import from scheduler/.
    """

    SUCCESS = "success"
    FORBIDDEN = "forbidden"
    TRANSIENT_FAILURE = "transient_failure"


class DeliverySource(enum.StrEnum):
    """Which outbox a delivery came from. Both are logged: the likeliest way
    this app messages the wrong people is handle_newly_tagged fanning a
    new_event NOTIFICATION across a tag's followers, not a reminder."""

    REMINDER = "reminder"
    NOTIFICATION = "notification"
```

- [x] **Step 4: Replace the definition in `scheduler/loop.py` with an import**

In `src/app/scheduler/loop.py`, delete the `class DeliveryOutcome(Enum):` block (lines 65-73) and the now-unused `from enum import Enum` on line 31. Add `DeliveryOutcome` to the existing domain import; if there is no `app.domain.types` import yet, add one after the `from app.db.session import SessionMaker` line:

```python
from app.domain.types import DeliveryOutcome
```

- [x] **Step 5: Run the new test and the scheduler suites**

Run: `uv run --isolated pytest tests/test_delivery_log.py tests/test_scheduler.py tests/test_ops_alerts.py -v`
Expected: PASS. If `tests/test_scheduler.py` does not exist under that name, run `uv run --isolated pytest -q -k "scheduler or tick or deliver"` instead.

- [x] **Step 6: Record the table-name deviation in the spec**

Append to the `## Deviations from this spec` section of `docs/superpowers/specs/2026-07-28-delivery-feed-design.md`:

```markdown
1. **The table is `delivery_log`, not `reminder_deliveries`.** The spec named
   it for reminders and then specified logging both drains, so the original
   name described half its contents. Renamed during planning; the class is
   `DeliveryLog`.
```

- [x] **Step 7: Full gates and commit**

Run: `uv run --isolated ruff check .` then `uv run --isolated pytest -q`
Expected: ruff clean, full suite passing.

```bash
git add src/app/domain/types.py src/app/scheduler/loop.py tests/test_delivery_log.py docs/superpowers/specs/2026-07-28-delivery-feed-design.md
git commit -m "refactor: move DeliveryOutcome to domain/types.py, add DeliverySource

A DB column typed on DeliveryOutcome where it lived (scheduler/loop.py)
would make db/ import from scheduler/, inverting the layering that
db/service.py already imports app.ops function-locally to preserve. Pure
value-preserving move -- the enum has never been persisted."
```

---

### Task 2: The `delivery_log` table

Schema only. No behaviour change, nothing writes to it yet.

**Files:**
- Modify: `src/app/db/models.py` (new model after `Notification`, ~line 622)
- Create: `alembic/versions/<rev>_add_delivery_log.py` (autogenerated, then hand-edited)
- Test: `tests/test_delivery_log.py`

**Interfaces:**
- Consumes: `DeliveryOutcome`, `DeliverySource` from Task 1.
- Produces: `app.db.models.DeliveryLog` with columns `id`, `batch_at_utc`, `user_id`, `source`, `outcome`, `anchor`, `note_kind`, `concert_title`, `leg_label`, `round_label`, `concert_id`, `round_id`, `day_id`, `sent_at_utc`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_delivery_log.py`:

```python
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, DeliveryLog, User
from app.domain.types import Anchor, DeliverySource


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(session):
    session.add(User(discord_id=1, username="reiji"))
    concert = Concert(event_id="c", title="スノーミク2027", title_en="Snow Miku 2027")
    session.add(concert)
    await session.flush()
    return concert


@pytest.mark.asyncio
async def test_delivery_row_round_trips(db):
    async with db() as s:
        concert = await _seed(s)
        s.add(
            DeliveryLog(
                batch_at_utc=datetime(2026, 7, 28, 14, 23, tzinfo=UTC),
                user_id=1,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.SUCCESS,
                anchor=Anchor.CLOSES,
                concert_title="Snow Miku 2027",
                leg_label="Day 1",
                round_label="一次先行",
                concert_id=concert.id,
                sent_at_utc=datetime(2026, 7, 28, 14, 23, tzinfo=UTC),
            )
        )
        await s.commit()
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.outcome is DeliveryOutcome.SUCCESS
        assert row.anchor is Anchor.CLOSES
        assert row.batch_at_utc.tzinfo is not None


@pytest.mark.asyncio
async def test_deleting_the_user_removes_their_rows(db):
    """This table holds personal data -- which events a named person was
    DMed about -- so POST /me/delete's cascade must reach it."""
    async with db() as s:
        await _seed(s)
        s.add(
            DeliveryLog(
                batch_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
                user_id=1,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.SUCCESS,
                sent_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
            )
        )
        await s.commit()
        await s.delete(await s.get(User, 1))
        await s.commit()
        assert (await s.execute(select(DeliveryLog))).all() == []


@pytest.mark.asyncio
async def test_deleting_the_concert_keeps_the_row_and_the_title(db):
    """The whole point of denormalizing the labels: deleting a concert must
    not erase the record that people were DMed about it. That record IS the
    investigation when a bad edit is the suspect."""
    async with db() as s:
        concert = await _seed(s)
        s.add(
            DeliveryLog(
                batch_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
                user_id=1,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.SUCCESS,
                concert_title="Snow Miku 2027",
                concert_id=concert.id,
                sent_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
            )
        )
        await s.commit()
        await s.delete(await s.get(Concert, concert.id))
        await s.commit()
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.concert_id is None
        assert row.concert_title == "Snow Miku 2027"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run --isolated pytest tests/test_delivery_log.py -v`
Expected: FAIL — `ImportError: cannot import name 'DeliveryLog' from 'app.db.models'`

- [x] **Step 3: Add the model**

Insert into `src/app/db/models.py` immediately after the `Notification` class (before `class ReminderQueue`):

```python
class DeliveryLog(Base):
    """One row per attempted DM delivery, reminders and notifications alike.

    Durable on purpose. reminder_queue can already answer "who was DMed about
    this" by joining rule_id -> reminder_rules -> users, but those rows are
    not evidence: sync_rule deletes rows it no longer plans and a deleted
    round cascades them away, so the trail vanishes exactly when a bad
    concert edit is the thing being investigated.

    Hence the two shapes of column below. The labels are DENORMALIZED text so
    a row survives its catalogue being edited or deleted; the *_id columns are
    convenience pointers for linking through while the entity still exists,
    and are SET NULL rather than CASCADE for the same reason
    Concert.created_by is (keep the record, drop the pointer).

    user_id is CASCADE, and that is not optional: this table records which
    events a named person was reminded about, and delete_user is a single
    session.delete relying on cascades (invariant 5).
    """

    __tablename__ = "delivery_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The tick's `now`. Doubles as the batch identity -- there is deliberately
    # no delivery_batches table, so aggregates compute on read and no stored
    # count can drift from the rows it summarizes.
    batch_at_utc: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="CASCADE")
    )
    source: Mapped[DeliverySource] = mapped_column(
        Enum(DeliverySource, values_callable=lambda e: [m.value for m in e])
    )
    outcome: Mapped[DeliveryOutcome] = mapped_column(
        Enum(DeliveryOutcome, values_callable=lambda e: [m.value for m in e])
    )
    # Reminder rows only.
    anchor: Mapped[Anchor | None] = mapped_column(
        Enum(Anchor, values_callable=lambda e: [m.value for m in e])
    )
    # Notification rows only: the Notification.kind that was delivered.
    note_kind: Mapped[str | None] = mapped_column(String(30))
    concert_title: Mapped[str | None] = mapped_column(String(300))
    leg_label: Mapped[str | None] = mapped_column(String(200))
    round_label: Mapped[str | None] = mapped_column(String(200))
    concert_id: Mapped[int | None] = mapped_column(
        ForeignKey("concerts.id", ondelete="SET NULL")
    )
    round_id: Mapped[int | None] = mapped_column(ForeignKey("rounds.id", ondelete="SET NULL"))
    day_id: Mapped[int | None] = mapped_column(
        ForeignKey("concert_days.id", ondelete="SET NULL")
    )
    sent_at_utc: Mapped[datetime] = mapped_column(UTCDateTime)
```

Add `DeliveryOutcome` and `DeliverySource` to the existing `from app.domain.types import ...` line at the top of `models.py`.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run --isolated pytest tests/test_delivery_log.py -v`
Expected: PASS (all five tests).

- [x] **Step 5: Generate the migration**

Run: `uv run --isolated alembic revision --autogenerate -m "add delivery_log"`

- [x] **Step 6: Hand-edit the migration**

Open the generated file in `alembic/versions/` and make exactly these edits:
1. Replace every `app.db.models.UTCDateTime()` with `sa.DateTime()`.
2. Delete the `import app.db.models` line.
3. Confirm the `op.create_index` for `batch_at_utc` is present and named (`ix_delivery_log_batch_at_utc`).

No `batch_alter_table` and no `drop_constraint` here — this is a brand-new table, so the legacy-anonymous-constraint hazard (`tests/test_migration_legacy_anonymous_constraints.py`) does not apply.

- [x] **Step 7: Apply and verify the migration**

Run: `uv run --isolated alembic upgrade head` then `uv run --isolated alembic downgrade -1` then `uv run --isolated alembic upgrade head`
Expected: all three succeed. The down-then-up proves the downgrade is real rather than a stub.

- [x] **Step 8: Full gates and commit**

Run: `uv run --isolated ruff check .` then `uv run --isolated pytest -q`

```bash
git add src/app/db/models.py alembic/versions/ tests/test_delivery_log.py
git commit -m "feat: add the delivery_log table

Durable record of every attempted DM. reminder_queue cannot serve this
role -- sync_rule deletes unplanned rows and a deleted round cascades
them, so the trail disappears exactly when a bad edit is the suspect.
Labels are denormalized text and the *_id columns are SET NULL so a row
outlives its catalogue; user_id is CASCADE so erasure reaches it."
```

---

### Task 3: Record deliveries from both drains

The write path, as a service function, plus the two `DueReminder` fields it needs. Still not wired into `tick()` — that is Task 4, so this task's diff can be reviewed as pure data-in/data-out.

**Files:**
- Modify: `src/app/db/service.py` (`DueReminder` ~line 1318; `due_reminders` ~line 1348; new section after `mark_notification_sent` ~line 4689)
- Test: `tests/test_delivery_log.py`

**Interfaces:**
- Consumes: `DeliveryLog` (Task 2), `DeliveryOutcome`, `DeliverySource` (Task 1).
- Produces:
  - `DueReminder` gains `concert_id: int | None = None` and `day_id: int | None = None`.
  - `service.UNREPORTED_NOTE_KINDS: frozenset[str]`
  - `async def record_deliveries(session, batch_at_utc, reminder_results, notification_results) -> int` where `reminder_results: list[tuple[DueReminder, DeliveryOutcome]]` and `notification_results: list[tuple[Notification, DeliveryOutcome]]`. Returns rows written. Flushes; does not commit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_delivery_log.py`:

```python
from app.db.models import Notification
from app.db.service import UNREPORTED_NOTE_KINDS, DueReminder, record_deliveries

BATCH = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)


def _reminder(concert_id, **kw):
    base = dict(
        queue_id=7,
        discord_id=1,
        user_timezone="America/Moncton",
        concert_title="Snow Miku 2027",
        anchor=Anchor.CLOSES,
        fire_at_utc=BATCH,
        concert_id=concert_id,
        round_label="一次先行",
        day_label="Day 1",
    )
    base.update(kw)
    return DueReminder(**base)


@pytest.mark.asyncio
async def test_logs_a_reminder_delivery(db):
    async with db() as s:
        concert = await _seed(s)
        n = await record_deliveries(
            s, BATCH, [(_reminder(concert.id), DeliveryOutcome.SUCCESS)], []
        )
        await s.commit()
        assert n == 1
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.source is DeliverySource.REMINDER
        assert row.round_label == "一次先行"
        assert row.leg_label == "Day 1"
        assert row.anchor is Anchor.CLOSES
        assert row.note_kind is None


@pytest.mark.asyncio
async def test_logs_a_notification_delivery(db):
    async with db() as s:
        concert = await _seed(s)
        note = Notification(user_id=1, body="x", concert_id=concert.id, kind="new_event")
        s.add(note)
        await s.flush()
        await record_deliveries(s, BATCH, [], [(note, DeliveryOutcome.SUCCESS)])
        await s.commit()
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.source is DeliverySource.NOTIFICATION
        assert row.note_kind == "new_event"
        assert row.anchor is None
        # Title resolved from the concert so the row survives its deletion.
        assert row.concert_title == "Snow Miku 2027"


@pytest.mark.asyncio
async def test_logs_transient_and_forbidden_too(db):
    """A digest of successes only would hide the incident it exists to show."""
    async with db() as s:
        concert = await _seed(s)
        await record_deliveries(
            s,
            BATCH,
            [
                (_reminder(concert.id), DeliveryOutcome.FORBIDDEN),
                (_reminder(concert.id, queue_id=8), DeliveryOutcome.TRANSIENT_FAILURE),
            ],
            [],
        )
        await s.commit()
        outcomes = {r.outcome for r in (await s.execute(select(DeliveryLog))).scalars()}
        assert outcomes == {DeliveryOutcome.FORBIDDEN, DeliveryOutcome.TRANSIENT_FAILURE}


@pytest.mark.asyncio
async def test_the_digest_notification_is_never_logged(db):
    """THE feedback-loop guard. Log the digest's own delivery and the next
    tick reports it, forever, once per minute. Asserted directly rather than
    inferred from the exclusion set's contents."""
    async with db() as s:
        await _seed(s)
        note = Notification(user_id=1, body="digest", kind="delivery_digest")
        s.add(note)
        await s.flush()
        n = await record_deliveries(s, BATCH, [], [(note, DeliveryOutcome.SUCCESS)])
        await s.commit()
        assert n == 0
        assert (await s.execute(select(DeliveryLog))).all() == []


def test_the_exclusion_set_covers_the_future_broadcast():
    """Sub-project C queues admin_broadcast notifications. Excluded up front,
    because discovering this after C ships means a DM loop in production."""
    assert UNREPORTED_NOTE_KINDS == frozenset({"delivery_digest", "admin_broadcast"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --isolated pytest tests/test_delivery_log.py -v`
Expected: FAIL — `ImportError: cannot import name 'UNREPORTED_NOTE_KINDS' from 'app.db.service'`

- [ ] **Step 3: Add the two `DueReminder` fields**

In `src/app/db/service.py`, inside the `DueReminder` dataclass, add to the defaulted block (after `user_language`):

```python
    # Denormalization sources for delivery_log. Carried on the dataclass rather
    # than re-queried at log time: the scheduler already has this row in hand,
    # and a second SELECT per delivered reminder would undo due_reminders'
    # fixed-round-trip batching.
    concert_id: int | None = None
    day_id: int | None = None
```

- [ ] **Step 4: Populate them in `due_reminders`**

In `due_reminders`, find where each `DueReminder(...)` is constructed and add `concert_id=<the concert's id>` and `day_id=<the queue row's day_id>`. The queue row already carries `day_id`; the concert is already fetched for `concert_title`. Do not add new queries.

- [ ] **Step 5: Add the writer**

Insert into `src/app/db/service.py` after `mark_notification_sent`:

```python
# ── Delivery log ─────────────────────────────────────────────────────────

# Notification kinds that are delivered but never logged. Without this, the
# digest would log its own delivery, the next tick would report that, and the
# bot would DM every admin once a minute forever. Excluded by KIND rather than
# by recipient so it holds however many admins exist. "admin_broadcast" is
# listed before sub-project C ships it, deliberately: finding this out
# afterwards means discovering a DM loop in production.
UNREPORTED_NOTE_KINDS = frozenset({"delivery_digest", "admin_broadcast"})


async def record_deliveries(
    session: AsyncSession,
    batch_at_utc: datetime,
    reminder_results: list[tuple[DueReminder, DeliveryOutcome]],
    notification_results: list[tuple[Notification, DeliveryOutcome]],
) -> int:
    """Write one delivery_log row per attempted delivery. Returns rows written.

    Flushes, never commits: the caller owns transaction boundaries, and in
    tick() this runs in its own commit AFTER the delivery bookkeeping is
    already durable.
    """
    rows: list[DeliveryLog] = []

    for item, outcome in reminder_results:
        rows.append(
            DeliveryLog(
                batch_at_utc=batch_at_utc,
                user_id=item.discord_id,
                source=DeliverySource.REMINDER,
                outcome=outcome,
                anchor=item.anchor,
                concert_title=item.concert_title,
                leg_label=item.day_label,
                round_label=item.round_label,
                concert_id=item.concert_id,
                round_id=item.round_id,
                day_id=item.day_id,
                sent_at_utc=batch_at_utc,
            )
        )

    # One batched lookup for the titles, not one per row: a new_event fan-out
    # is exactly the case with many notifications sharing few concerts.
    note_concert_ids = {
        n.concert_id
        for n, _ in notification_results
        if n.concert_id is not None and n.kind not in UNREPORTED_NOTE_KINDS
    }
    titles: dict[int, str] = {}
    if note_concert_ids:
        res = await session.execute(
            select(Concert.id, Concert.title).where(Concert.id.in_(note_concert_ids))
        )
        titles = {cid: title for cid, title in res.all()}

    for note, outcome in notification_results:
        if note.kind in UNREPORTED_NOTE_KINDS:
            continue
        rows.append(
            DeliveryLog(
                batch_at_utc=batch_at_utc,
                user_id=note.user_id,
                source=DeliverySource.NOTIFICATION,
                outcome=outcome,
                note_kind=note.kind,
                concert_title=titles.get(note.concert_id) if note.concert_id else None,
                concert_id=note.concert_id,
                sent_at_utc=batch_at_utc,
            )
        )

    if rows:
        session.add_all(rows)
        await session.flush()
    return len(rows)
```

Add `DeliveryLog` to the `from app.db.models import ...` block and `DeliveryOutcome`, `DeliverySource` to the `from app.domain.types import ...` block at the top of `service.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --isolated pytest tests/test_delivery_log.py -v`
Expected: PASS.

- [ ] **Step 7: Full gates and commit**

Run: `uv run --isolated ruff check .` then `uv run --isolated pytest -q`

```bash
git add src/app/db/service.py tests/test_delivery_log.py
git commit -m "feat: record_deliveries writes delivery_log rows for both drains

Both drains, not reminders alone: the incident class this feature exists
for is 'messages sent to the wrong users', and the likeliest cause is
handle_newly_tagged fanning a new_event notice across a tag's followers.
A reminders-only log would be blind to exactly that. The feedback loop
that opens is closed by UNREPORTED_NOTE_KINDS, which lists C's future
admin_broadcast up front."
```

---

### Task 4: Wire the log into `tick()`

**Files:**
- Modify: `src/app/scheduler/loop.py:148-218` (`tick`)
- Test: `tests/test_delivery_log_tick.py` (new file)

**Interfaces:**
- Consumes: `record_deliveries` (Task 3).
- Produces: `tick()` writes `delivery_log` rows for the batch. `tick`'s return value (delivered count) is unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_delivery_log_tick.py`. Mirror the fixture shape of the existing scheduler tests (in-memory engine, `PRAGMA foreign_keys=ON`, module `SessionMaker` monkeypatched, a fake bot):

```python
"""tick() writes the delivery log without endangering delivery bookkeeping."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as session_mod
import app.scheduler.loop as loop_mod
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    DeliveryLog,
    ReminderQueue,
    ReminderRule,
    Round,
    User,
)
from app.domain.types import Anchor, DeliveryOutcome


class FakeUser:
    def __init__(self):
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


class FakeBot:
    def __init__(self):
        self.user_obj = FakeUser()

    def get_user(self, _uid):
        return self.user_obj


@pytest_asyncio.fixture()
async def maker(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_mod, "SessionMaker", m)
    monkeypatch.setattr(loop_mod, "SessionMaker", m)
    yield m
    await engine.dispose()


async def _due_reminder(session):
    """One reminder already due: a concert with a leg, a round with a close
    time, a rule, and a queue row whose fire time has passed."""
    past = datetime.now(UTC) - timedelta(minutes=5)
    session.add(User(discord_id=1, username="reiji", timezone="America/Moncton"))
    concert = Concert(event_id="c", title="スノーミク2027", title_en="Snow Miku 2027")
    session.add(concert)
    await session.flush()
    day = ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=past + timedelta(days=30))
    round_ = Round(concert_id=concert.id, label="一次先行", closes_at_utc=past + timedelta(days=7))
    session.add_all([day, round_])
    await session.flush()
    rule = ReminderRule(user_id=1, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    session.add(
        ReminderQueue(
            rule_id=rule.id, round_id=round_.id, anchor=Anchor.CLOSES, fire_at_utc=past
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_tick_logs_the_delivery(maker):
    async with maker() as s:
        await _due_reminder(s)

    delivered = await loop_mod.tick(FakeBot())
    assert delivered == 1

    async with maker() as s:
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.outcome is DeliveryOutcome.SUCCESS
        assert row.concert_title == "Snow Miku 2027"
        assert row.anchor is Anchor.CLOSES


@pytest.mark.asyncio
async def test_an_empty_tick_writes_nothing(maker):
    assert await loop_mod.tick(FakeBot()) == 0
    async with maker() as s:
        assert (await s.execute(select(DeliveryLog))).all() == []


@pytest.mark.asyncio
async def test_a_logging_failure_leaves_the_reminder_marked_sent(maker, monkeypatch):
    """The reason this runs in its own commit AFTER the delivery commit. The
    DM is already on the wire; if a logging bug could roll back sent_at_utc,
    the next tick would send it again. Duplicate reminders must never be
    reachable from an observability feature."""
    async with maker() as s:
        await _due_reminder(s)

    async def boom(*a, **kw):
        raise RuntimeError("log write failed")

    monkeypatch.setattr(loop_mod, "record_deliveries", boom)

    assert await loop_mod.tick(FakeBot()) == 1  # tick survives

    async with maker() as s:
        queued = (await s.execute(select(ReminderQueue))).scalar_one()
        assert queued.sent_at_utc is not None  # bookkeeping survived
        assert (await s.execute(select(DeliveryLog))).all() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --isolated pytest tests/test_delivery_log_tick.py -v`
Expected: FAIL — no `DeliveryLog` rows written; `AttributeError` on the monkeypatched `record_deliveries`.

- [ ] **Step 3: Collect the outcomes in `tick()`**

In `src/app/scheduler/loop.py`, inside `tick`, change the two drain loops to keep their results. Replace the reminder loop:

```python
        items = await due_reminders(session, now)
        reminder_results = await asyncio.gather(*(bounded_deliver(i) for i in items))
        for item, outcome in reminder_results:
            if outcome in (DeliveryOutcome.SUCCESS, DeliveryOutcome.FORBIDDEN):
                await mark_sent(session, item.queue_id, now)
                delivered += 1
            if outcome is not DeliveryOutcome.TRANSIENT_FAILURE:
                await record_dm_outcome(
                    session, item.discord_id, blocked=outcome is DeliveryOutcome.FORBIDDEN
                )
```

and the notification loop:

```python
        notes = await due_notifications(session)
        prepared = [(note, await _notification_context(session, note)) for note in notes]
        notification_results = await asyncio.gather(
            *(bounded_send_notification(note, ctx) for note, ctx in prepared)
        )
        for note, outcome in notification_results:
            if outcome in (DeliveryOutcome.SUCCESS, DeliveryOutcome.FORBIDDEN):
                await mark_notification_sent(session, note.id)
                delivered += 1
            if outcome is not DeliveryOutcome.TRANSIENT_FAILURE:
                await record_dm_outcome(
                    session, note.user_id, blocked=outcome is DeliveryOutcome.FORBIDDEN
                )
```

- [ ] **Step 4: Add the log write after the delivery commit**

Immediately after `await session.commit()` (the delivery-bookkeeping commit, ~line 198) and BEFORE the health block, insert:

```python
        # Delivery log: its own try/except and its own commit, for the same
        # reason the health block below has them. By this point the DMs are on
        # the wire and recorded as sent; an exception that rolled that back
        # would make the next tick re-send every one of them. An observability
        # feature must never be able to cause a duplicate reminder.
        if reminder_results or notification_results:
            try:
                await record_deliveries(
                    session, now, list(reminder_results), list(notification_results)
                )
                await session.commit()
            except Exception:
                log.exception("delivery logging failed; delivery itself was unaffected")
                await session.rollback()
```

Add `record_deliveries` to the `from app.db.service import (...)` block at the top of the file.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --isolated pytest tests/test_delivery_log_tick.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Full gates and commit**

Run: `uv run --isolated ruff check .` then `uv run --isolated pytest -q`

```bash
git add src/app/scheduler/loop.py tests/test_delivery_log_tick.py
git commit -m "feat: tick() writes the delivery log after its delivery commit

Own try/except, own commit, after the bookkeeping commit -- the same
isolation the health block uses and for the same reason: the DMs are
already on the wire, so a logging failure that rolled back sent_at_utc
would re-send every one of them next tick."
```

---

### Task 5: The digest

Pure formatter in `domain/`, gathering and queueing in `service.py`, called from `tick()`.

**Files:**
- Create: `src/app/domain/digest.py`
- Modify: `src/app/db/service.py` (after `record_deliveries`)
- Modify: `src/app/scheduler/loop.py` (inside the log-write try block)
- Test: `tests/test_delivery_digest.py` (new file)

**Interfaces:**
- Consumes: `record_deliveries` (Task 3), `DeliveryLog` (Task 2).
- Produces:
  - `domain/digest.py`: frozen dataclass `DeliveryFact(source, outcome, user_id, concert_title, leg_label, round_label, anchor, note_kind)`; constants `MAX_FAILURE_LINES = 10`, `MAX_SENT_GROUPS = 10`; `build_digest(facts: list[DeliveryFact], batch_at_utc: datetime) -> str`.
  - `service.queue_delivery_digest(session, batch_at_utc, rows: list[DeliveryLog]) -> int` — returns admins queued.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_delivery_digest.py`:

```python
"""The digest body: impersonal, failure-first, and bounded."""

from datetime import UTC, datetime

from app.domain.digest import MAX_SENT_GROUPS, DeliveryFact, build_digest
from app.domain.types import Anchor, DeliveryOutcome, DeliverySource

BATCH = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)


def _fact(user_id, outcome=DeliveryOutcome.SUCCESS, anchor=Anchor.CLOSES, **kw):
    base = dict(
        source=DeliverySource.REMINDER,
        outcome=outcome,
        user_id=user_id,
        concert_title="Snow Miku 2027",
        leg_label="Day 1",
        round_label="一次先行",
        anchor=anchor,
        note_kind=None,
    )
    base.update(kw)
    return DeliveryFact(**base)


def test_header_counts_sends_and_distinct_users():
    body = build_digest([_fact(1), _fact(2), _fact(2)], BATCH)
    assert "3 sent" in body
    assert "2 users" in body
    assert "2026-07-28 14:23 UTC" in body


def test_no_warning_marker_when_nothing_failed():
    assert "⚠" not in build_digest([_fact(1)], BATCH)


def test_failures_lead_and_are_marked():
    body = build_digest(
        [_fact(1), _fact(2, outcome=DeliveryOutcome.FORBIDDEN)], BATCH
    )
    assert "⚠" in body
    assert "1 failed" in body
    assert body.index("FAILED") < body.index("SENT")
    assert "forbidden" in body


def test_sent_rows_group_with_a_recipient_count():
    """The count IS the anomaly detector -- x40 on a three-user app is the
    tell. A per-recipient list would bury it and blow Discord's 2000 chars."""
    body = build_digest([_fact(i) for i in range(1, 6)], BATCH)
    assert "×5" in body
    assert body.count("一次先行") == 1  # one group line, not five


def test_different_anchors_are_different_groups():
    body = build_digest(
        [_fact(1, anchor=Anchor.CLOSES), _fact(2, anchor=Anchor.RESULTS)], BATCH
    )
    assert "closes" in body.lower()
    assert "results" in body.lower()


def test_notification_rows_group_by_kind_not_anchor():
    body = build_digest(
        [
            _fact(
                1,
                source=DeliverySource.NOTIFICATION,
                anchor=None,
                note_kind="new_event",
                leg_label=None,
                round_label=None,
            )
        ],
        BATCH,
    )
    assert "new_event" in body


def test_sent_groups_are_capped_with_a_remainder_line():
    facts = [
        _fact(i, round_label=f"round {i}") for i in range(MAX_SENT_GROUPS + 3)
    ]
    body = build_digest(facts, BATCH)
    assert "+3 more groups" in body


def test_never_contains_a_user_id():
    """Counts in the DM, names in the app: identity belongs on
    /admin/deliveries, inside POST /me/delete's reach, not in Discord history
    that no deletion path can touch."""
    body = build_digest([_fact(123456789012345678)], BATCH)
    assert "123456789012345678" not in body


def test_empty_facts_produce_no_digest():
    assert build_digest([], BATCH) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --isolated pytest tests/test_delivery_digest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.digest'`

- [ ] **Step 3: Write the pure formatter**

Create `src/app/domain/digest.py`:

```python
"""The admin delivery digest's body, as a pure function.

Deliberately impersonal. The digest groups by what was sent and counts the
recipients rather than naming them, for three reasons: identity in a DM builds
a permanent record of who follows which artists in a place POST /me/delete
cannot reach; a 100-reminder tick would blow Discord's 2000-character ceiling;
and the recipient COUNT is the actual anomaly detector -- a group reading x40
on a three-user app is the tell that something fanned out wrongly, which a
per-recipient list would bury.

English-only and not wrapped in _(): the body is composed at queue time,
before any recipient is known, so translating it would mean gettext_in per
admin for operational copy only admins read.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from app.domain.types import Anchor, DeliveryOutcome, DeliverySource

# Separate caps rather than one shared line budget: failures are the reason
# this message exists, so they get their own allowance and can never be
# squeezed out by a large batch of successful sends.
MAX_FAILURE_LINES = 10
MAX_SENT_GROUPS = 10


@dataclass(frozen=True)
class DeliveryFact:
    """One attempted delivery, flattened out of a DeliveryLog row. A plain
    dataclass so this module stays pure -- no ORM, no session."""

    source: DeliverySource
    outcome: DeliveryOutcome
    user_id: int
    concert_title: str | None
    leg_label: str | None
    round_label: str | None
    anchor: Anchor | None
    note_kind: str | None


def _describe(fact: DeliveryFact) -> str:
    """The grouping label. A reminder is identified by its anchor and the
    round/leg it names; a notification has neither, only its kind."""
    if fact.source is DeliverySource.NOTIFICATION:
        head = fact.note_kind or "notice"
        return f"{head} · {fact.concert_title or '(no concert)'}"
    parts = [p for p in (fact.concert_title, fact.leg_label, fact.round_label) if p]
    anchor = fact.anchor.value if fact.anchor else "?"
    return f"{anchor} · {' / '.join(parts) if parts else '(no concert)'}"


def build_digest(facts: list[DeliveryFact], batch_at_utc: datetime) -> str:
    """Render the digest, or "" when there is nothing to report.

    Returning "" rather than a "nothing happened" line is what keeps a quiet
    app quiet: the caller queues no notification at all for an empty result.
    """
    if not facts:
        return ""

    failures = [f for f in facts if f.outcome is not DeliveryOutcome.SUCCESS]
    sent = [f for f in facts if f.outcome is DeliveryOutcome.SUCCESS]
    users = len({f.user_id for f in facts})
    stamp = batch_at_utc.strftime("%Y-%m-%d %H:%M UTC")

    head = f"{len(sent)} sent · {users} users · batch {stamp}"
    if failures:
        head = f"⚠ {len(failures)} failed / {head}"
    lines = [head]

    if failures:
        lines += ["", "FAILED"]
        for fact in failures[:MAX_FAILURE_LINES]:
            lines.append(f"  {fact.outcome.value} · {_describe(fact)}")
        if len(failures) > MAX_FAILURE_LINES:
            lines.append(f"  +{len(failures) - MAX_FAILURE_LINES} more failures")

    if sent:
        groups = Counter(_describe(f) for f in sent)
        lines += ["", "SENT"]
        for label, count in groups.most_common(MAX_SENT_GROUPS):
            lines.append(f"  ×{count}  {label}")
        if len(groups) > MAX_SENT_GROUPS:
            lines.append(f"  +{len(groups) - MAX_SENT_GROUPS} more groups")

    return "\n".join(lines)
```

- [ ] **Step 4: Run the formatter tests**

Run: `uv run --isolated pytest tests/test_delivery_digest.py -v`
Expected: PASS (all nine).

- [ ] **Step 5: Write the failing queueing test**

Append to `tests/test_delivery_digest.py`:

```python
import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import Notification
from app.db.service import queue_delivery_digest


@pytest.mark.asyncio
async def test_queues_one_notification_per_admin(db, monkeypatch):
    """Same shape as evaluate_and_alert: one Notification per admin id, with
    concert_id=None so it falls through _notification_context to the
    plain-text path and the send code needs no changes."""
    monkeypatch.setattr(settings, "discord_token", "x")  # bot_enabled
    monkeypatch.setattr(settings, "admin_whitelist", "1,2")
    async with db() as s:
        await _seed_users(s)
        rows = await _one_success_row(s)
        n = await queue_delivery_digest(s, BATCH, rows)
        await s.commit()
        assert n == 2
        notes = (await s.execute(select(Notification))).scalars().all()
        assert {x.user_id for x in notes} == {1, 2}
        assert all(x.kind == "delivery_digest" for x in notes)
        assert all(x.concert_id is None for x in notes)
        assert all("1 sent" in x.body for x in notes)


@pytest.mark.asyncio
async def test_queues_nothing_when_the_bot_is_disabled(db, monkeypatch):
    """evaluate_and_alert's reason: without this, every local dev run
    accumulates junk notifications nobody will ever receive."""
    monkeypatch.setattr(settings, "discord_token", "")
    monkeypatch.setattr(settings, "admin_whitelist", "1")
    async with db() as s:
        await _seed_users(s)
        rows = await _one_success_row(s)
        assert await queue_delivery_digest(s, BATCH, rows) == 0
        await s.commit()
        assert (await s.execute(select(Notification))).all() == []


@pytest.mark.asyncio
async def test_queues_nothing_for_an_empty_batch(db, monkeypatch):
    monkeypatch.setattr(settings, "discord_token", "x")
    monkeypatch.setattr(settings, "admin_whitelist", "1")
    async with db() as s:
        await _seed_users(s)
        assert await queue_delivery_digest(s, BATCH, []) == 0
```

Add these helpers to the same file, plus the `db` fixture copied from `tests/test_delivery_log.py` (in-memory engine with the FK pragma):

```python
from app.db.models import DeliveryLog, User


async def _seed_users(session):
    session.add_all([User(discord_id=1, username="a"), User(discord_id=2, username="b")])
    await session.flush()


async def _one_success_row(session):
    row = DeliveryLog(
        batch_at_utc=BATCH,
        user_id=1,
        source=DeliverySource.REMINDER,
        outcome=DeliveryOutcome.SUCCESS,
        anchor=Anchor.CLOSES,
        concert_title="Snow Miku 2027",
        leg_label="Day 1",
        round_label="一次先行",
        sent_at_utc=BATCH,
    )
    session.add(row)
    await session.flush()
    return [row]
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_delivery_digest.py -v`
Expected: FAIL — `ImportError: cannot import name 'queue_delivery_digest'`

- [ ] **Step 7: Add the queueing function**

Append to the delivery-log section of `src/app/db/service.py`:

```python
async def queue_delivery_digest(
    session: AsyncSession, batch_at_utc: datetime, rows: list[DeliveryLog]
) -> int:
    """Queue the admin digest for this batch. Returns admins queued.

    Goes through the notifications outbox rather than a direct DM -- that is
    invariant 4, and it buys retry, ordering and Forbidden handling for free.
    kind="delivery_digest" with concert_id=None falls through
    scheduler.loop._notification_context to the plain-text path, so the send
    code needs no changes. That kind is also in UNREPORTED_NOTE_KINDS, which
    is what stops this digest reporting its own delivery next tick.
    """
    if not rows or not settings.bot_enabled:
        return 0

    body = build_digest(
        [
            DeliveryFact(
                source=r.source,
                outcome=r.outcome,
                user_id=r.user_id,
                concert_title=r.concert_title,
                leg_label=r.leg_label,
                round_label=r.round_label,
                anchor=r.anchor,
                note_kind=r.note_kind,
            )
            for r in rows
        ],
        batch_at_utc,
    )
    if not body:
        return 0

    queued = 0
    for admin_id in settings.admin_ids:
        # An admin who has never logged in has no users row, and
        # Notification.user_id is a FK to it -- the same guard
        # evaluate_and_alert needs, and for the same reason.
        if await session.get(User, admin_id) is None:
            await ensure_user(session, admin_id, str(admin_id))
        session.add(
            Notification(user_id=admin_id, body=body, kind="delivery_digest")
        )
        queued += 1
    await session.flush()
    return queued
```

Add `from app.domain.digest import DeliveryFact, build_digest` to `service.py`'s imports.

- [ ] **Step 8: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_delivery_digest.py -v`
Expected: PASS.

- [ ] **Step 9: Call it from `tick()`**

In `src/app/scheduler/loop.py`, change the log-write block added in Task 4 so the digest is queued in the same try/commit. Replace `await record_deliveries(...)` with:

```python
                rows = await record_deliveries(
                    session, now, list(reminder_results), list(notification_results)
                )
                if rows:
                    await queue_delivery_digest(
                        session, now, await batch_rows(session, now)
                    )
                await session.commit()
```

Have `record_deliveries` return the rows it wrote instead of a count, so no second query is needed. Change its signature to `-> list[DeliveryLog]`, return `rows`, and update Task 3's tests: `assert n == 1` becomes `assert len(n) == 1`, `assert n == 0` becomes `assert n == []`. Then the call site is:

```python
                rows = await record_deliveries(
                    session, now, list(reminder_results), list(notification_results)
                )
                if rows:
                    await queue_delivery_digest(session, now, rows)
                await session.commit()
```

Use this second form — no `batch_rows` helper is needed. Add `queue_delivery_digest` to the `from app.db.service import (...)` block.

- [ ] **Step 10: Add the end-to-end tick test**

Append to `tests/test_delivery_log_tick.py`:

```python
from app.config import settings
from app.db.models import Notification


@pytest.mark.asyncio
async def test_tick_queues_a_digest_for_the_admin(maker, monkeypatch):
    monkeypatch.setattr(settings, "discord_token", "x")
    monkeypatch.setattr(settings, "admin_whitelist", "1")
    async with maker() as s:
        await _due_reminder(s)

    await loop_mod.tick(FakeBot())

    async with maker() as s:
        note = (
            await s.execute(select(Notification).where(Notification.kind == "delivery_digest"))
        ).scalar_one()
        assert "1 sent" in note.body
        assert note.concert_id is None


@pytest.mark.asyncio
async def test_the_digests_own_delivery_is_not_logged(maker, monkeypatch):
    """End-to-end feedback-loop guard: tick 1 delivers the reminder and
    queues the digest; tick 2 delivers the digest. After tick 2 there must
    still be exactly one log row, or the bot DMs admins once a minute
    forever."""
    monkeypatch.setattr(settings, "discord_token", "x")
    monkeypatch.setattr(settings, "admin_whitelist", "1")
    async with maker() as s:
        await _due_reminder(s)

    await loop_mod.tick(FakeBot())  # delivers reminder, queues digest
    await loop_mod.tick(FakeBot())  # delivers digest

    async with maker() as s:
        assert len((await s.execute(select(DeliveryLog))).scalars().all()) == 1
```

- [ ] **Step 11: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_delivery_log_tick.py tests/test_delivery_digest.py tests/test_delivery_log.py -v`
Expected: PASS.

- [ ] **Step 12: Full gates and commit**

Run: `uv run --isolated ruff check .` then `uv run --isolated pytest -q`

```bash
git add src/app/domain/digest.py src/app/db/service.py src/app/scheduler/loop.py tests/test_delivery_digest.py tests/test_delivery_log_tick.py tests/test_delivery_log.py
git commit -m "feat: queue a per-tick delivery digest to admins

Failure-first, grouped by what was sent with a recipient COUNT rather
than names -- the count is the anomaly detector (x40 on a three-user app
is the tell), and identity in a DM would build a permanent record of who
follows which artists somewhere POST /me/delete cannot reach. Through the
outbox per invariant 4; concert_id=None routes it down the existing
plain-text path so no send code changed."
```

---

### Task 6: Retention prune

**Files:**
- Modify: `src/app/db/service.py` (delivery-log section)
- Modify: `src/app/scheduler/loop.py` (health block, ~line 211-217)
- Test: `tests/test_delivery_log.py`

**Interfaces:**
- Consumes: `DeliveryLog` (Task 2).
- Produces: `service.DELIVERY_LOG_RETENTION_DAYS = 30`; `async def prune_delivery_log(session, now=None) -> int` returning rows deleted.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_delivery_log.py`:

```python
from app.db.service import DELIVERY_LOG_RETENTION_DAYS, prune_delivery_log


@pytest.mark.asyncio
async def test_prune_deletes_past_the_window_and_spares_inside_it(db):
    now = datetime(2026, 7, 28, tzinfo=UTC)
    async with db() as s:
        await _seed(s)
        for days in (1, 29, 31, 400):
            s.add(
                DeliveryLog(
                    batch_at_utc=now - timedelta(days=days),
                    user_id=1,
                    source=DeliverySource.REMINDER,
                    outcome=DeliveryOutcome.SUCCESS,
                    sent_at_utc=now - timedelta(days=days),
                )
            )
        await s.commit()
        assert await prune_delivery_log(s, now) == 2
        await s.commit()
        remaining = sorted(
            (now - r.batch_at_utc).days
            for r in (await s.execute(select(DeliveryLog))).scalars()
        )
        assert remaining == [1, 29]


def test_retention_matches_the_backup_lifecycle():
    """One retention number in the system, not two -- deploy/backup.sh's S3
    lifecycle is 30 days."""
    assert DELIVERY_LOG_RETENTION_DAYS == 30
```

Add `timedelta` to that file's datetime import.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_delivery_log.py -v`
Expected: FAIL — `ImportError: cannot import name 'prune_delivery_log'`

- [ ] **Step 3: Add the prune**

Append to the delivery-log section of `src/app/db/service.py`:

```python
# Matches deploy/backup.sh's S3 lifecycle so the system has ONE retention
# number rather than two that can drift apart.
DELIVERY_LOG_RETENTION_DAYS = 30


async def prune_delivery_log(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete delivery_log rows older than the retention window. Returns rows
    deleted. Flushes, never commits -- the caller owns the transaction."""
    now = now or _now()
    cutoff = now - timedelta(days=DELIVERY_LOG_RETENTION_DAYS)
    res = await session.execute(
        delete(DeliveryLog).where(DeliveryLog.batch_at_utc < cutoff)
    )
    await session.flush()
    return res.rowcount or 0
```

Ensure `delete` is in `service.py`'s `from sqlalchemy import ...` and `timedelta` in its datetime import.

- [ ] **Step 4: Call it from the health block**

In `src/app/scheduler/loop.py`, inside the `if _tick_count % HEALTH_EVERY_N_TICKS == 0:` try block, add the prune before the commit:

```python
            try:
                await evaluate_and_alert(session, await run_checks(session), now)
                # Same 5-minute cadence, same try/except: a table that grows
                # for another five minutes is not an incident, and a failed
                # prune must not be able to take health alerting down with it.
                await prune_delivery_log(session, now)
                await session.commit()
            except Exception:
                log.exception("health evaluation failed; reminder delivery was unaffected")
                await session.rollback()
```

Add `prune_delivery_log` to the `from app.db.service import (...)` block.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_delivery_log.py -v`
Expected: PASS.

- [ ] **Step 6: Full gates and commit**

Run: `uv run --isolated ruff check .` then `uv run --isolated pytest -q`

```bash
git add src/app/db/service.py src/app/scheduler/loop.py tests/test_delivery_log.py
git commit -m "feat: prune delivery_log past 30 days on the health cadence

30 to match deploy/backup.sh's S3 lifecycle -- one retention number in
the system, not two. Reuses the existing every-5th-tick block rather than
adding a cron: a table that grows five more minutes is not an incident."
```

---

### Task 7: `/admin/deliveries`, plus the docs the feature owes

The reader, and the three documentation obligations the spec names. Folded into one task because the page is what makes the `/privacy` line true — shipping the page without the disclosure is the state to avoid.

**Files:**
- Create: `src/app/web/routes/admin.py`
- Create: `src/app/web/templates/admin_deliveries.html`
- Modify: `src/app/web/app.py:28-40` (import), `:242` (register)
- Modify: `src/app/db/service.py` (delivery-log section)
- Modify: `src/app/web/templates/privacy.html`
- Modify: `CLAUDE.md`, `WISHLIST.md`
- Test: `tests/test_admin_deliveries.py` (new file)

**Interfaces:**
- Consumes: `DeliveryLog` (Task 2), `DELIVERY_LOG_RETENTION_DAYS` (Task 6).
- Produces: `service.delivery_batches(session, limit=50) -> list[BatchSummary]` where `BatchSummary` is a frozen dataclass `(batch_at_utc, sent, users, failed)`; `service.delivery_failures(session, limit=100) -> list[DeliveryLog]`; `service.delivery_batch_rows(session, batch_at_utc) -> list[DeliveryLog]`; route `GET /admin/deliveries` and `GET /admin/deliveries/{batch_iso}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_deliveries.py`. Copy the `db`/`client` fixture pair from `tests/test_crud.py:37-70` (in-memory engine with the FK pragma, `create_app()`, `dependency_overrides[get_session]`, the `login_as` helper) and add:

```python
"""/admin/deliveries: admin-only, and the one place identity is revealed."""

ADMIN_ID, PLAIN_ID = 42, 777


@pytest.mark.asyncio
async def test_page_renders_for_an_admin(client, monkeypatch):
    """Every page needs at least one logged-in GET render test -- a missing
    one shipped a 500 here once (template context drift)."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/deliveries")
    assert r.status_code == 200
    assert "Recent failures" in r.text


def test_a_signed_in_non_admin_gets_403(client):
    """Signed-in-but-unauthorized IS an error and stays 403 -- not folded in
    with the signed-out redirect (invariant 5)."""
    login_as(client, PLAIN_ID, "someone")
    assert client.get("/admin/deliveries").status_code == 403


def test_signed_out_is_redirected_not_403(client):
    r = client.get("/admin/deliveries")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?next=")


@pytest.mark.asyncio
async def test_failures_show_before_any_batch_is_opened(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    async with client.db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        s.add(
            DeliveryLog(
                batch_at_utc=datetime.now(UTC),
                user_id=ADMIN_ID,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.FORBIDDEN,
                anchor=Anchor.CLOSES,
                concert_title="Snow Miku 2027",
                sent_at_utc=datetime.now(UTC),
            )
        )
        await s.commit()
    r = client.get("/admin/deliveries")
    assert "forbidden" in r.text
    assert "Snow Miku 2027" in r.text


@pytest.mark.asyncio
async def test_batch_detail_names_recipients(client, monkeypatch):
    """The deliberate counts-in-the-DM / names-in-the-app split: this is the
    only surface that reveals identity, and it is admin-gated and inside the
    30-day window."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    batch = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)
    async with client.db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        s.add(
            DeliveryLog(
                batch_at_utc=batch,
                user_id=ADMIN_ID,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.SUCCESS,
                anchor=Anchor.CLOSES,
                concert_title="Snow Miku 2027",
                sent_at_utc=batch,
            )
        )
        await s.commit()
    r = client.get(f"/admin/deliveries/{batch.isoformat()}")
    assert r.status_code == 200
    assert str(ADMIN_ID) in r.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_admin_deliveries.py -v`
Expected: FAIL — 404 on `/admin/deliveries` (router not registered).

- [ ] **Step 3: Add the three read helpers**

Append to the delivery-log section of `src/app/db/service.py`:

```python
@dataclass(frozen=True)
class BatchSummary:
    """One tick's deliveries, aggregated on read. There is no stored count
    anywhere, so these can never disagree with the rows they describe."""

    batch_at_utc: datetime
    sent: int
    users: int
    failed: int


async def delivery_batches(session: AsyncSession, limit: int = 50) -> list[BatchSummary]:
    """Newest first. Capped rather than paginated: the retention window is 30
    days and a batch only exists if it delivered something."""
    res = await session.execute(
        select(
            DeliveryLog.batch_at_utc,
            func.count(DeliveryLog.id),
            func.count(func.distinct(DeliveryLog.user_id)),
            func.sum(
                case((DeliveryLog.outcome != DeliveryOutcome.SUCCESS.value, 1), else_=0)
            ),
        )
        .group_by(DeliveryLog.batch_at_utc)
        .order_by(DeliveryLog.batch_at_utc.desc())
        .limit(limit)
    )
    return [
        BatchSummary(
            batch_at_utc=at, sent=total - (failed or 0), users=users, failed=failed or 0
        )
        for at, total, users, failed in res.all()
    ]


async def delivery_failures(session: AsyncSession, limit: int = 100) -> list[DeliveryLog]:
    """Every non-SUCCESS row in the window, newest first, independent of
    batch. The digest says something broke in the last minute; this says
    whether it has been breaking all week."""
    res = await session.execute(
        select(DeliveryLog)
        .where(DeliveryLog.outcome != DeliveryOutcome.SUCCESS.value)
        .order_by(DeliveryLog.batch_at_utc.desc())
        .limit(limit)
    )
    return list(res.scalars())


async def delivery_batch_rows(
    session: AsyncSession, batch_at_utc: datetime
) -> list[DeliveryLog]:
    res = await session.execute(
        select(DeliveryLog)
        .where(DeliveryLog.batch_at_utc == batch_at_utc)
        .order_by(DeliveryLog.id)
    )
    return list(res.scalars())
```

Ensure `func`, `case` are imported from `sqlalchemy` and `dataclass` from `dataclasses` in `service.py`.

- [ ] **Step 4: Add the route module**

Create `src/app/web/routes/admin.py`:

```python
"""Admin-only operational reader for the delivery log.

Three screens, one template: recent failures (the incident view), the batch
list, and one batch's rows expanded to their actual recipients.

This is the ONLY surface that names delivery recipients. The digest DM
deliberately reports counts, because a name in Discord history is a permanent
record of who follows which artists that POST /me/delete cannot reach; here it
sits behind require_admin, inside the app's own deletion story, on the 30-day
retention window.

Copy is English-only and NOT wrapped in _(), following /me/test-dm: an
operational page only admins see should not cost msgids in three languages
(tests/test_i18n_catalogues.py would enforce them).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    DELIVERY_LOG_RETENTION_DAYS,
    delivery_batch_rows,
    delivery_batches,
    delivery_failures,
)
from app.db.session import get_session
from app.web.auth import SessionUser, require_admin
from app.web.templating import templates

router = APIRouter()


@router.get("/admin/deliveries", response_class=HTMLResponse)
async def deliveries(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "admin_deliveries.html",
        {
            "user": user,
            "failures": await delivery_failures(session),
            "batches": await delivery_batches(session),
            "rows": None,
            "batch": None,
            "retention_days": DELIVERY_LOG_RETENTION_DAYS,
        },
    )


@router.get("/admin/deliveries/{batch_iso}", response_class=HTMLResponse)
async def delivery_batch(
    request: Request,
    batch_iso: str,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        batch = datetime.fromisoformat(batch_iso)
    except ValueError:
        # A malformed timestamp is a bad link, not a server fault.
        raise HTTPException(status_code=404, detail="no such batch") from None
    return templates.TemplateResponse(
        request,
        "admin_deliveries.html",
        {
            "user": user,
            "failures": await delivery_failures(session),
            "batches": await delivery_batches(session),
            "rows": await delivery_batch_rows(session, batch),
            "batch": batch,
            "retention_days": DELIVERY_LOG_RETENTION_DAYS,
        },
    )
```

If `app.web.templating` does not exist, import `templates` from wherever the other route modules get it (check the top of `src/app/web/routes/preferences.py`) and match that exactly.

- [ ] **Step 5: Add the template**

Create `src/app/web/templates/admin_deliveries.html`:

```html
{% extends "base.html" %}
{% block title %}Deliveries{% endblock %}
{% block content %}
<h1>Deliveries</h1>
<p class="muted">
  Every DM the scheduler attempted, kept {{ retention_days }} days.
  Recipients are named here and only here — the digest DM reports counts.
</p>

<h2>Recent failures</h2>
{% if failures %}
<div class="tablewrap">
  <table>
    <thead><tr><th>Batch</th><th>Outcome</th><th>What</th><th>User</th></tr></thead>
    <tbody>
      {% for f in failures %}
      <tr>
        <td><a href="/admin/deliveries/{{ f.batch_at_utc.isoformat() }}">{{ f.batch_at_utc.strftime('%Y-%m-%d %H:%M') }}</a></td>
        <td>{{ f.outcome.value }}</td>
        <td>{{ f.concert_title or '—' }}{% if f.round_label %} / {{ f.round_label }}{% endif %}{% if f.anchor %} · {{ f.anchor.value }}{% elif f.note_kind %} · {{ f.note_kind }}{% endif %}</td>
        <td>{{ f.user_id }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="muted">No failed deliveries in the window.</p>
{% endif %}

<h2>Batches</h2>
{% if batches %}
<div class="tablewrap">
  <table>
    <thead><tr><th>Batch</th><th>Sent</th><th>Users</th><th>Failed</th></tr></thead>
    <tbody>
      {% for b in batches %}
      <tr>
        <td><a href="/admin/deliveries/{{ b.batch_at_utc.isoformat() }}">{{ b.batch_at_utc.strftime('%Y-%m-%d %H:%M') }}</a></td>
        <td>{{ b.sent }}</td><td>{{ b.users }}</td><td>{{ b.failed }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="muted">Nothing delivered yet.</p>
{% endif %}

{% if batch %}
<h2>Batch {{ batch.strftime('%Y-%m-%d %H:%M') }} UTC</h2>
<div class="tablewrap">
  <table>
    <thead><tr><th>User</th><th>Source</th><th>Outcome</th><th>What</th></tr></thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{ r.user_id }}</td>
        <td>{{ r.source.value }}</td>
        <td>{{ r.outcome.value }}</td>
        <td>{{ r.concert_title or '—' }}{% if r.leg_label %} / {{ r.leg_label }}{% endif %}{% if r.round_label %} / {{ r.round_label }}{% endif %}{% if r.anchor %} · {{ r.anchor.value }}{% elif r.note_kind %} · {{ r.note_kind }}{% endif %}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
{% endblock %}
```

Wrap wide tables in `overflow-x: auto` if `.tablewrap` does not already exist in `style.css`; if it does not, use a plain `<div>` and add nothing — do not invent new design tokens for an admin page.

- [ ] **Step 6: Register the router**

In `src/app/web/app.py`, add to the route imports (alphabetically, before `calendar`):

```python
from app.web.routes import admin as admin_routes
```

and register it alongside the others (after `terms_routes`, ~line 242):

```python
    app.include_router(admin_routes.router)
```

Order does not matter here — `/admin/deliveries` shares no prefix with a path-template route, unlike the `imports`-before-`concerts` constraint.

- [ ] **Step 7: Run to verify they pass**

Run: `uv run --isolated pytest tests/test_admin_deliveries.py -v`
Expected: PASS. (Six tests in the file by now: three from Task 1, three new.)

- [ ] **Step 8: Add the `/privacy` disclosure**

In `src/app/web/templates/privacy.html`, add to the list of what is stored. Match the surrounding markup and wrap the copy in `_()` exactly as the neighbouring entries do — this page is user-facing, unlike the admin page:

```
{{ _("A 30-day record of the reminder DMs we sent you — which event, leg and round each one was about — so we can diagnose delivery problems. Deleting your account deletes these immediately.") }}
```

Then update both catalogues: run `uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .`, then `pybabel update -i messages.pot -d src/app/translations -l ja` and again `-l zh`, fill in both msgstrs by hand, and delete `messages.pot`.

- [ ] **Step 9: Verify the catalogues**

Run: `uv run --isolated pytest tests/test_i18n_catalogues.py -v`
Expected: PASS. A fuzzy entry counts as untranslated and will fail — remove the `#, fuzzy` markers after filling each msgstr.

- [ ] **Step 10: Document the feedback-loop rule in CLAUDE.md**

In `CLAUDE.md`, append to invariant 4 (**Notifications**):

```
   Any new notification kind that REPORTS ON deliveries must be added to
   `UNREPORTED_NOTE_KINDS` (`db/service.py`), or it will log its own delivery,
   report that next tick, and DM every admin once a minute forever. The
   delivery log (`delivery_log`) covers both drains deliberately -- the
   likeliest way this app messages the wrong people is `handle_newly_tagged`
   fanning a `new_event` notice across a tag's followers, which is a
   notification, not a reminder.
```

- [ ] **Step 11: Update WISHLIST.md**

Move nothing (no Proposed entry covered this feature — it came from a direct owner request). Add a Shipped entry dated 2026-07-28 describing the delivery feed, then append two Proposed entries: **C, the targeted admin broadcast** (designed next, depends on this log) and **A, the local rehearsal harness** (spec written, `2026-07-28-rehearsal-harness-design.md`). Note on the existing **#1 admin catalogue export** that A's spec found a second use for it — a catalogue-only copy is the clean way to seed a local dev DB without putting personal data on a laptop — which raises its value beyond backup/rebuild. Then do the full revision pass the CLAUDE.md wishlist rule requires: re-rank the remaining entries and record what moved and why.

- [ ] **Step 12: Full gates and commit**

Run: `uv run --isolated ruff check .` then `uv run --isolated pytest -q`

```bash
git add src/app/web/routes/admin.py src/app/web/templates/admin_deliveries.html src/app/web/app.py src/app/db/service.py src/app/web/templates/privacy.html src/app/translations/ tests/test_admin_deliveries.py CLAUDE.md WISHLIST.md
git commit -m "feat: /admin/deliveries, plus the privacy line the log owes

The reader for the delivery log: recent failures (the incident view), the
batch list, and one batch expanded to its actual recipients. That last
screen is the deliberate other half of counts-in-the-DM -- identity lives
behind require_admin, inside POST /me/delete's reach, on the 30-day
window, rather than permanently in Discord history.

Ships with the /privacy disclosure in the same commit, because the page
is what makes that sentence necessary, and with the CLAUDE.md rule that
any future delivery-reporting notification kind must join
UNREPORTED_NOTE_KINDS or loop forever."
```

---

## Self-Review

**Spec coverage.** Every section of `2026-07-28-delivery-feed-design.md` maps to a task: §A the table → Task 2 (with the `DeliveryOutcome` move it names → Task 1); §B both-drains logging and `UNREPORTED_NOTE_KINDS` → Task 3; tick placement → Task 4; queueing, suppression, body, caps → Task 5; retention → Task 6; §C three screens → Task 7; the three "obligations this creates" → Task 7 steps 8-11. The spec's error-handling section is covered by Task 4 step 4 and Task 6 step 4 (own try/except, own commit) and asserted by Task 4's third test. Every spec testing bullet appears as a named test.

**Placeholder scan.** No TBDs. Two steps say "match what the neighbouring module does" (Task 7 step 4's `templates` import, step 5's `.tablewrap`) rather than guessing an import path I have not read — each names the exact file to check.

**Type consistency.** `record_deliveries` returns `list[DeliveryLog]` throughout — Task 3 defines it as `-> int`, and Task 5 step 9 explicitly changes it and names the two test assertions to update. That is the one signature that changes mid-plan, called out rather than left to drift. `DeliveryFact`'s eight fields are identical in `domain/digest.py`, the `queue_delivery_digest` construction and the test helper. `BatchSummary(batch_at_utc, sent, users, failed)` matches its template use. `DeliveryOutcome`/`DeliverySource`/`Anchor` all come from `app.domain.types` everywhere.

**One thing left for the implementer to confirm rather than assume:** Task 3 step 4 says to populate `concert_id`/`day_id` in `due_reminders` without adding queries. Verify the concert row and the queue row are both genuinely in scope at each `DueReminder(...)` construction site; if a construction site lacks one, carry it from the batched dict already built there rather than adding a SELECT.
