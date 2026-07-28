# Admin Broadcast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin send a plain-text DM to a chosen set of users — the recipients of a delivery batch, everyone, or an explicit id list — behind four safety rails, with a two-minute window to cancel it.

**Architecture:** Everything goes through the existing `notifications` outbox (invariant 4). A new `broadcasts` table is the permanent audit record. Two nullable columns on `Notification` add a hold (`send_after_utc`) and a cancel handle (`broadcast_id`); NULL on both means exactly today's behaviour, so every existing notice path is untouched by construction. The per-recipient localized frame is applied **at queue time**, so the scheduler's send code does not change at all.

**Tech Stack:** SQLAlchemy 2.0 async + Alembic (SQLite/WAL), FastAPI + Jinja2, discord.py, pytest-asyncio auto mode.

**Spec:** `docs/superpowers/specs/2026-07-28-admin-broadcast-design.md`

## Global Constraints

Five of these were learned the hard way while implementing sub-project B. Do not rediscover them.

- **Migrations live in `alembic/versions/`** (`alembic.ini` sets `script_location = alembic`), NOT `migrations/versions/`. After `alembic revision --autogenerate`, hand-edit: replace `app.db.models.UTCDateTime()` with `sa.DateTime()`. There is **no `import app.db.models` line to delete** — this repo's `script.py.mako` emits no imports block, so autogenerate produces a bare `app.db.models.UTCDateTime()` that is a `NameError` if applied unedited. The substitution is a correctness fix, not tidiness.
- **New imports go in the TOP-OF-FILE block, never appended mid-file.** Ruff selects `E`, which includes E402. Several steps below say "append to `tests/<file>.py`" and show imports in the snippet — hoist them.
- **`Round.kind` is NOT NULL with no default.** Any seeded `Round(...)` needs `kind=RoundKind.LOTTERY_ROUND` or the insert dies before your assertions run.
- **`templates` is NOT imported.** There is no `app.web.templating`. Every route module declares `templates = None  # set by web.app at startup` and `create_app()` assigns `<module>.templates = templates` before `include_router`. Match that idiom exactly.
- **CSS classes that exist:** `.tagtable` (with a `.r` right-align modifier) for tables, `.dim` for muted text. `.tablewrap` and `.muted` do NOT exist. Do not invent tokens or classes for an admin page.
- All datetime columns use `UTCDateTime`; the DB stores aware UTC only (invariant 1).
- Enum columns use `Enum(E, values_callable=lambda e: [m.value for m in e])`. All enums live in `src/app/domain/types.py` as `enum.StrEnum`.
- DB test fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener.
- Admin-facing copy is **English-only, NOT wrapped in `_()`** (the `/me/test-dm` and `/admin/deliveries` precedent). The single exception is the two recipient-facing frame msgids in Task 6, which ARE translated.
- Gates before every commit: `uv run --isolated ruff check .` clean AND `uv run --isolated pytest -q` passing. Run tests in the **FOREGROUND** with a 600000 ms timeout. **Baseline at branch point: 1519 passed, 0 failed.**
- Commit messages take the `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_015QGCFscyzpVhVVMFJtBbEA` trailers. Use a bash heredoc or repeated `-m` flags — **never PowerShell here-string syntax in the Bash tool**, it corrupts the message.
- Never send a DM directly from a web route (invariant 4). This whole feature queues; the scheduler sends.
- **Owner ruling:** `HOLD_SECONDS = 120`, a constant, not configurable. One fewer thing to get wrong at 3am.

---

### Task 1: Stop excluding broadcasts from the delivery log

A correction to sub-project B. `"admin_broadcast"` was added to `UNREPORTED_NOTE_KINDS` defensively before this feature existed, and it is wrong: the feedback loop is specific to the digest reporting on *itself*, and a broadcast terminates after one hop. Logging broadcasts is also the point — whether the remedy reached its recipients, `FORBIDDEN` ones included, is the question you send it asking.

**Files:**
- Modify: `src/app/db/service.py` (`UNREPORTED_NOTE_KINDS`, ~line 4713)
- Modify: `tests/test_delivery_log.py` (the exclusion-set assertion)
- Test: `tests/test_delivery_log.py`

**Interfaces:**
- Produces: `UNREPORTED_NOTE_KINDS == frozenset({"delivery_digest"})`.

- [ ] **Step 1: Update the existing assertion and add the new one**

In `tests/test_delivery_log.py`, replace `test_the_exclusion_set_covers_the_future_broadcast` entirely with:

```python
def test_only_the_digest_is_excluded_from_logging():
    """A broadcast is NOT excluded, deliberately -- see the next test. Only
    the digest can report on itself, and only self-reporting loops."""
    assert UNREPORTED_NOTE_KINDS == frozenset({"delivery_digest"})


@pytest.mark.asyncio
async def test_a_broadcast_delivery_is_logged(db):
    """The reason the exclusion was removed: 'did my remedy actually reach
    them?' is the question a broadcast is sent asking, and it is only
    answerable if the deliveries are recorded."""
    async with db() as s:
        await _seed(s)
        note = Notification(user_id=1, body="sorry about that", kind="admin_broadcast")
        s.add(note)
        await s.flush()
        rows = await record_deliveries(s, BATCH, [], [(note, DeliveryOutcome.SUCCESS)])
        await s.commit()
        assert len(rows) == 1
        assert rows[0].note_kind == "admin_broadcast"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_delivery_log.py -v`
Expected: FAIL — the frozenset still contains two entries, and the broadcast row is not written.

- [ ] **Step 3: Make the change**

In `src/app/db/service.py`, replace the constant and its comment:

```python
# The digest reports on deliveries, so logging its own delivery would make the
# next tick report that, forever, once a minute. Only self-reporting kinds
# belong here. A broadcast does NOT: it terminates after one hop (broadcast ->
# logged -> one digest line -> digest delivered -> not logged -> stop), and
# recording it is the point -- whether a remedy reached its recipients,
# FORBIDDEN ones included, is the question the broadcast was sent asking.
UNREPORTED_NOTE_KINDS = frozenset({"delivery_digest"})
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_delivery_log.py tests/test_delivery_digest.py tests/test_delivery_log_tick.py -v`
Expected: PASS. The digest's own two-tick guard must still pass — if it does not, the wrong entry was removed.

- [ ] **Step 5: Full gates and commit**

```bash
git add src/app/db/service.py tests/test_delivery_log.py
git commit -m "fix: log broadcast deliveries, exclude only the digest

admin_broadcast went into UNREPORTED_NOTE_KINDS defensively while
planning the delivery feed, before the broadcast feature existed. It is
wrong: the loop guarded against is specific to the digest reporting on
itself, and a broadcast terminates after one hop. It also suppressed
exactly what a broadcast is sent to find out -- whether the remedy
reached its recipients, FORBIDDEN ones included."
```

---

### Task 2: `broadcasts` table and the two `Notification` columns

Schema only. Nothing writes or reads these yet.

**Files:**
- Modify: `src/app/domain/types.py` (append `BroadcastMode`)
- Modify: `src/app/db/models.py` (new model; two columns on `Notification`)
- Create: `alembic/versions/<rev>_add_broadcasts.py`
- Test: `tests/test_broadcast.py` (new file)

**Interfaces:**
- Produces: `app.domain.types.BroadcastMode` (`BATCH = "batch"`, `ALL = "all"`, `EXPLICIT = "explicit"`); `app.db.models.Broadcast`; `Notification.send_after_utc`, `Notification.broadcast_id`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_broadcast.py`:

```python
"""The admin broadcast: audit record, hold, and cancel."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Broadcast, Notification, User
from app.domain.types import BroadcastMode

NOW = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)


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


async def _admin(session, discord_id=1):
    session.add(User(discord_id=discord_id, username="reiji"))
    await session.flush()


@pytest.mark.asyncio
async def test_broadcast_row_round_trips(db):
    async with db() as s:
        await _admin(s)
        s.add(
            Broadcast(
                created_by=1,
                created_at_utc=NOW,
                mode=BroadcastMode.BATCH,
                mode_param="2026-07-28T14:00:00+00:00",
                body="sorry about that",
                recipient_count=40,
                send_after_utc=NOW + timedelta(seconds=120),
            )
        )
        await s.commit()
        row = (await s.execute(select(Broadcast))).scalar_one()
        assert row.mode is BroadcastMode.BATCH
        assert row.cancelled_at_utc is None
        assert row.send_after_utc.tzinfo is not None


@pytest.mark.asyncio
async def test_the_audit_row_survives_deleting_the_admin(db):
    """It records an admin action against other people's DMs. Deleting the
    account that did it must not erase the fact that it happened."""
    async with db() as s:
        await _admin(s)
        s.add(
            Broadcast(
                created_by=1,
                created_at_utc=NOW,
                mode=BroadcastMode.ALL,
                body="hello",
                recipient_count=3,
                send_after_utc=NOW,
            )
        )
        await s.commit()
        await s.delete(await s.get(User, 1))
        await s.commit()
        row = (await s.execute(select(Broadcast))).scalar_one()
        assert row.created_by is None
        assert row.body == "hello"


@pytest.mark.asyncio
async def test_notification_hold_columns_default_to_null(db):
    """NULL on both means exactly today's behaviour. Every existing notice
    path depends on that being true."""
    async with db() as s:
        await _admin(s)
        s.add(Notification(user_id=1, body="x", kind="new_event"))
        await s.commit()
        note = (await s.execute(select(Notification))).scalar_one()
        assert note.send_after_utc is None
        assert note.broadcast_id is None


@pytest.mark.asyncio
async def test_deleting_a_broadcast_orphans_its_notifications(db):
    """SET NULL, not CASCADE: a queued notice is a thing that happened to a
    user, and it should not vanish because the audit row was removed."""
    async with db() as s:
        await _admin(s)
        b = Broadcast(
            created_by=1,
            created_at_utc=NOW,
            mode=BroadcastMode.ALL,
            body="hello",
            recipient_count=1,
            send_after_utc=NOW,
        )
        s.add(b)
        await s.flush()
        s.add(Notification(user_id=1, body="hello", kind="admin_broadcast", broadcast_id=b.id))
        await s.commit()
        await s.delete(await s.get(Broadcast, b.id))
        await s.commit()
        note = (await s.execute(select(Notification))).scalar_one()
        assert note.broadcast_id is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_broadcast.py -v`
Expected: FAIL — `ImportError: cannot import name 'Broadcast' from 'app.db.models'`

- [ ] **Step 3: Add the enum**

Append to `src/app/domain/types.py`:

```python
class BroadcastMode(enum.StrEnum):
    """How an admin broadcast chose its recipients. All three are RESOLVED
    sets -- a known list of user ids at send time. Derived modes (everyone
    tracking a concert, followers of a tag) were considered and rejected: the
    set can change between the preview an admin approved and the send that
    executes, so the count they confirmed would be a lie."""

    BATCH = "batch"        # the recipients of one delivery_log batch
    ALL = "all"            # every user
    EXPLICIT = "explicit"  # a hand-entered list of discord ids
```

- [ ] **Step 4: Add the model and the two columns**

In `src/app/db/models.py`, add `BroadcastMode` to the `app.domain.types` import, then insert before `class Notification`:

```python
class Broadcast(Base):
    """The permanent audit record of one admin broadcast.

    NEVER pruned, unlike delivery_log's 30-day window: this records an admin
    action against other people's DMs, not a delivery. "Did we already tell
    them?" must stay answerable indefinitely.
    """

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # SET NULL, matching Concert.created_by: erasing the admin's account keeps
    # the record of what they did and anonymizes who did it.
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="SET NULL")
    )
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    mode: Mapped[BroadcastMode] = mapped_column(
        Enum(BroadcastMode, values_callable=lambda e: [m.value for m in e])
    )
    # The batch timestamp, the raw id list as typed, or NULL for ALL.
    mode_param: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    # What was actually queued, not what the preview showed.
    recipient_count: Mapped[int] = mapped_column()
    send_after_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    cancelled_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

Then add to `Notification`, after `kind`:

```python
    # Both nullable, and NULL means exactly the pre-broadcast behaviour --
    # which is what keeps new_event, leg_cancelled, ops_alert and
    # delivery_digest untouched by a change to the drain query they all use.
    #
    # send_after_utc holds a row back until its moment: it is what turns an
    # unrecallable mass DM into one that can be cancelled for two minutes.
    send_after_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # SET NULL, not CASCADE: a queued notice is something that happened to a
    # user, and deleting the audit row should not erase it.
    broadcast_id: Mapped[int | None] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="SET NULL")
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_broadcast.py -v`
Expected: PASS (all four).

- [ ] **Step 6: Generate and hand-edit the migration**

Run: `uv run --isolated alembic revision --autogenerate -m "add broadcasts"`

Open the file in `alembic/versions/` and replace every `app.db.models.UTCDateTime()` with `sa.DateTime()`. Read the whole file: it must contain ONLY the new table plus the two `add_column` calls on `notifications`. If autogenerate emits anything else — an alter or drop on an unrelated table, meaning the local DB has drifted — STOP and report rather than applying it.

Note: `notifications` is an older table and may carry anonymous constraints on the live server. This migration only ADDs columns and does not call `drop_constraint`, so the legacy-constraint hazard in `tests/test_migration_legacy_anonymous_constraints.py` does not apply. Do not add `batch_alter_table` for an add-column.

- [ ] **Step 7: Apply, prove the downgrade, re-apply**

Run: `uv run --isolated alembic upgrade head`, then `uv run --isolated alembic downgrade -1`, then `uv run --isolated alembic upgrade head`
Expected: all three succeed. The second upgrade succeeding proves the downgrade really dropped/removed rather than stubbing.

- [ ] **Step 8: Full gates and commit**

```bash
git add src/app/domain/types.py src/app/db/models.py alembic/versions/ tests/test_broadcast.py
git commit -m "feat: broadcasts table, and the hold columns on notifications

Both Notification columns are nullable and NULL means exactly today's
behaviour, which is the property that keeps every existing notice path
untouched by a change to the drain query they all share. The audit row is
never pruned and survives deleting the admin who sent it."
```

---

### Task 3: `due_notifications` honours the hold

The single riskiest change in this feature: it modifies the query every notification in the app passes through.

**Files:**
- Modify: `src/app/db/service.py` (`due_notifications`, ~line 4672)
- Test: `tests/test_broadcast.py`

**Interfaces:**
- Consumes: `Notification.send_after_utc` (Task 2).
- Produces: `due_notifications(session, limit=100, now=None)` — gains an optional `now`, defaulting to `_now()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_broadcast.py` (imports hoisted to the top block):

```python
from app.db.service import due_notifications


@pytest.mark.asyncio
async def test_a_notification_with_no_hold_still_drains_immediately(db):
    """THE regression test for this task. Every existing notice -- new_event,
    leg_cancelled, ops_alert, delivery_digest -- has send_after_utc NULL, and
    a NULL must never be read as 'not yet due'. In SQL, `NULL <= now` is NULL,
    not true, so a naive comparison would silently stop the entire outbox."""
    async with db() as s:
        await _admin(s)
        s.add(Notification(user_id=1, body="x", kind="new_event"))
        await s.commit()
        assert len(await due_notifications(s, now=NOW)) == 1


@pytest.mark.asyncio
async def test_a_held_notification_is_not_drained_before_its_moment(db):
    async with db() as s:
        await _admin(s)
        s.add(
            Notification(
                user_id=1,
                body="x",
                kind="admin_broadcast",
                send_after_utc=NOW + timedelta(seconds=120),
            )
        )
        await s.commit()
        assert await due_notifications(s, now=NOW) == []


@pytest.mark.asyncio
async def test_a_held_notification_drains_once_its_moment_passes(db):
    async with db() as s:
        await _admin(s)
        s.add(
            Notification(
                user_id=1,
                body="x",
                kind="admin_broadcast",
                send_after_utc=NOW,
            )
        )
        await s.commit()
        assert len(await due_notifications(s, now=NOW + timedelta(seconds=1))) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_broadcast.py -v`
Expected: the two hold tests FAIL (held rows drain immediately); the NULL test passes already.

- [ ] **Step 3: Make the change**

Replace `due_notifications` in `src/app/db/service.py`:

```python
async def due_notifications(
    session: AsyncSession, limit: int = 100, now: datetime | None = None
) -> list[Notification]:
    """Unsent notices whose hold, if any, has expired.

    `send_after_utc IS NULL` is the common case and means "send now" -- every
    notice in the app except a broadcast leaves it NULL. The IS NULL branch is
    load-bearing rather than defensive: in SQL `NULL <= now` evaluates to NULL,
    not true, so comparing without it would silently stop the entire outbox.
    """
    now = now or _now()
    res = await session.execute(
        select(Notification)
        .where(
            Notification.sent_at_utc.is_(None),
            or_(
                Notification.send_after_utc.is_(None),
                Notification.send_after_utc <= now,
            ),
        )
        .order_by(Notification.created_at)
        .limit(limit)
    )
    return list(res.scalars())
```

Ensure `or_` is imported from `sqlalchemy` in `service.py`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --isolated pytest tests/test_broadcast.py -v`
Expected: PASS.

- [ ] **Step 5: Prove the NULL branch is load-bearing**

Temporarily change the `where` to use only `Notification.send_after_utc <= now` (dropping the `or_` and the `is_(None)` branch). Run `uv run --isolated pytest tests/test_broadcast.py -v` and confirm `test_a_notification_with_no_hold_still_drains_immediately` FAILS. Restore, re-run, confirm green. Report what you saw — this is the mutation that proves the whole app's outbox is protected.

- [ ] **Step 6: Full gates and commit**

The full suite matters here more than anywhere: every notification test in the suite exercises this query.

```bash
git add src/app/db/service.py tests/test_broadcast.py
git commit -m "feat: due_notifications honours a per-row hold

The IS NULL branch is load-bearing, not defensive: SQL evaluates
NULL <= now as NULL rather than true, so comparing without it would
silently stop every notification in the app -- new_event, leg_cancelled,
ops_alert and the delivery digest all leave send_after_utc NULL."
```

---

### Task 4: Recipient resolution, queue, and cancel

The service layer. No routes yet.

**Files:**
- Modify: `src/app/db/service.py` (new section after the delivery-log section)
- Test: `tests/test_broadcast.py`

**Interfaces:**
- Consumes: `Broadcast`, `BroadcastMode` (Task 2).
- Produces:
  - `HOLD_SECONDS = 120`, `BROADCAST_BODY_MAX = 1900`, `TYPED_CONFIRM_THRESHOLD = 10`
  - `@dataclass(frozen=True) Recipients(ids: tuple[tuple[int, str], ...], unmatched: tuple[str, ...])` — `(discord_id, language)` pairs plus id strings that matched no user.
  - `async def resolve_recipients(session, mode: BroadcastMode, param: str | None) -> Recipients`
  - `async def queue_broadcast(session, created_by, mode, param, body, now=None) -> Broadcast`
  - `async def cancel_broadcast(session, broadcast_id, now=None) -> tuple[int, int]` — `(cancelled, already_delivered)`
  - `async def recent_broadcasts(session, limit=50) -> list[Broadcast]`
  - `async def duplicate_body_recently(session, body, now=None) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_broadcast.py` (imports hoisted):

```python
from app.db.models import DeliveryLog
from app.db.service import (
    BROADCAST_BODY_MAX,
    HOLD_SECONDS,
    cancel_broadcast,
    duplicate_body_recently,
    queue_broadcast,
    resolve_recipients,
)
from app.domain.types import DeliveryOutcome, DeliverySource


async def _users(session, *ids_and_langs):
    for discord_id, lang in ids_and_langs:
        session.add(User(discord_id=discord_id, username=str(discord_id), language=lang))
    await session.flush()


@pytest.mark.asyncio
async def test_resolve_all_returns_every_user_with_language(db):
    async with db() as s:
        await _users(s, (1, "en"), (2, "ja"))
        r = await resolve_recipients(s, BroadcastMode.ALL, None)
        assert dict(r.ids) == {1: "en", 2: "ja"}
        assert r.unmatched == ()


@pytest.mark.asyncio
async def test_resolve_batch_returns_that_batch_s_recipients_only(db):
    async with db() as s:
        await _users(s, (1, "en"), (2, "ja"), (3, "zh"))
        for uid, at in ((1, NOW), (2, NOW), (3, NOW + timedelta(minutes=5))):
            s.add(
                DeliveryLog(
                    batch_at_utc=at,
                    user_id=uid,
                    source=DeliverySource.REMINDER,
                    outcome=DeliveryOutcome.SUCCESS,
                    sent_at_utc=at,
                )
            )
        await s.commit()
        r = await resolve_recipients(s, BroadcastMode.BATCH, NOW.isoformat())
        assert {uid for uid, _ in r.ids} == {1, 2}


@pytest.mark.asyncio
async def test_resolve_explicit_reports_unknown_ids_rather_than_dropping_them(db):
    """Silently discarding a mistyped id is how you conclude you messaged
    someone you did not."""
    async with db() as s:
        await _users(s, (1, "en"))
        r = await resolve_recipients(s, BroadcastMode.EXPLICIT, "1, 999, notanumber")
        assert {uid for uid, _ in r.ids} == {1}
        assert set(r.unmatched) == {"999", "notanumber"}


@pytest.mark.asyncio
async def test_queue_writes_one_held_notification_per_recipient(db):
    async with db() as s:
        await _users(s, (1, "en"), (2, "ja"))
        b = await queue_broadcast(s, 1, BroadcastMode.ALL, None, "hello", now=NOW)
        await s.commit()
        assert b.recipient_count == 2
        assert b.send_after_utc == NOW + timedelta(seconds=HOLD_SECONDS)
        notes = (await s.execute(select(Notification))).scalars().all()
        assert len(notes) == 2
        assert all(n.kind == "admin_broadcast" for n in notes)
        assert all(n.send_after_utc == b.send_after_utc for n in notes)
        assert all(n.broadcast_id == b.id for n in notes)


@pytest.mark.asyncio
async def test_queue_rejects_an_over_long_body(db):
    """Discord's hard limit is 2000 characters and the frame costs some of
    them. A body that silently truncates on send is a broadcast that says
    something other than what was approved."""
    async with db() as s:
        await _users(s, (1, "en"))
        with pytest.raises(ValueError):
            await queue_broadcast(s, 1, BroadcastMode.ALL, None, "x" * (BROADCAST_BODY_MAX + 1))


@pytest.mark.asyncio
async def test_cancel_deletes_unsent_rows_and_counts_the_delivered(db):
    async with db() as s:
        await _users(s, (1, "en"), (2, "ja"))
        b = await queue_broadcast(s, 1, BroadcastMode.ALL, None, "hello", now=NOW)
        await s.commit()
        # Simulate the race: one row drained before the cancel landed.
        notes = (await s.execute(select(Notification))).scalars().all()
        notes[0].sent_at_utc = NOW
        await s.commit()
        cancelled, delivered = await cancel_broadcast(s, b.id, now=NOW)
        await s.commit()
        assert (cancelled, delivered) == (1, 1)
        assert (await s.get(Broadcast, b.id)).cancelled_at_utc is not None
        remaining = (await s.execute(select(Notification))).scalars().all()
        assert len(remaining) == 1 and remaining[0].sent_at_utc is not None


@pytest.mark.asyncio
async def test_cancel_after_full_delivery_reports_nothing_cancelled(db):
    """Honest rather than erroring: the page says what it could not undo."""
    async with db() as s:
        await _users(s, (1, "en"))
        b = await queue_broadcast(s, 1, BroadcastMode.ALL, None, "hello", now=NOW)
        await s.commit()
        for n in (await s.execute(select(Notification))).scalars():
            n.sent_at_utc = NOW
        await s.commit()
        assert await cancel_broadcast(s, b.id, now=NOW) == (0, 1)


@pytest.mark.asyncio
async def test_duplicate_body_within_the_hour_is_reported(db):
    async with db() as s:
        await _users(s, (1, "en"))
        await queue_broadcast(s, 1, BroadcastMode.ALL, None, "hello", now=NOW)
        await s.commit()
        assert await duplicate_body_recently(s, "hello", now=NOW + timedelta(minutes=30))
        assert not await duplicate_body_recently(s, "hello", now=NOW + timedelta(hours=2))
        assert not await duplicate_body_recently(s, "different", now=NOW)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_broadcast.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_recipients'`

- [ ] **Step 3: Implement**

Append a new section to `src/app/db/service.py`, after the delivery-log section:

```python
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
    preview an admin approved and the send that executes.
    """
    if mode is BroadcastMode.ALL:
        res = await session.execute(select(User.discord_id, User.language))
        return Recipients(ids=tuple((i, lang) for i, lang in res.all()), unmatched=())

    if mode is BroadcastMode.BATCH:
        batch_at = datetime.fromisoformat(param) if param else None
        if batch_at is None:
            return Recipients(ids=(), unmatched=())
        res = await session.execute(
            select(User.discord_id, User.language)
            .join(DeliveryLog, DeliveryLog.user_id == User.discord_id)
            .where(DeliveryLog.batch_at_utc == batch_at)
            .distinct()
        )
        return Recipients(ids=tuple((i, lang) for i, lang in res.all()), unmatched=())

    # EXPLICIT: report what did not match rather than dropping it silently.
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
    """
    return f"**{gettext_in(language, 'From dekimasen.app')}**\n\n{body}"


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
```

Add `Broadcast` and `BroadcastMode` to `service.py`'s model/type imports, and `gettext_in` from `app.i18n`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --isolated pytest tests/test_broadcast.py -v`
Expected: PASS.

- [ ] **Step 5: Full gates and commit**

```bash
git add src/app/db/service.py tests/test_broadcast.py
git commit -m "feat: broadcast recipient resolution, queue and cancel

Every mode resolves to a concrete id set, so the count an admin confirms
cannot drift before the send. Unknown explicit ids are reported rather
than dropped -- silently discarding a mistyped id is how you conclude you
messaged someone you did not. Cancel returns what it could not undo."
```

---

### Task 5: Compose and preview

The read-only half. These two routes write nothing at all.

**Files:**
- Modify: `src/app/web/routes/admin.py`
- Create: `src/app/web/templates/admin_broadcast.html`
- Test: `tests/test_admin_broadcast.py` (new file)

**Interfaces:**
- Consumes: `resolve_recipients`, `recent_broadcasts`, `duplicate_body_recently`, `TYPED_CONFIRM_THRESHOLD`, `BROADCAST_BODY_MAX` (Task 4).
- Produces: `GET /admin/broadcast`, `POST /admin/broadcast/preview`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_broadcast.py`. Copy the `db`/`client` fixture pair and `login_as` from `tests/test_admin_deliveries.py` (which already has the right shape, including the FK pragma). Note that `login_as` creates the user row — do NOT also seed the admin, or you get an `IntegrityError`.

```python
ADMIN_ID, PLAIN_ID = 42, 777


def test_compose_renders_for_an_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/broadcast")
    assert r.status_code == 200
    assert "Broadcast" in r.text


def test_a_signed_in_non_admin_gets_403(client):
    login_as(client, PLAIN_ID, "someone")
    assert client.get("/admin/broadcast").status_code == 403


def test_signed_out_is_redirected(client):
    r = client.get("/admin/broadcast")
    assert r.status_code == 303


def test_preview_writes_nothing(client, monkeypatch):
    """The whole point of a preview: nothing reaches the outbox until the
    admin confirms from the preview screen."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "all", "mode_param": "", "body": "hello everyone"},
    )
    assert r.status_code == 200
    assert "hello everyone" in r.text

    async def counts():
        async with client.db() as s:
            n = len((await s.execute(select(Notification))).scalars().all())
            b = len((await s.execute(select(Broadcast))).scalars().all())
            return n, b

    assert asyncio.get_event_loop().run_until_complete(counts()) == (0, 0)


def test_preview_shows_the_recipient_count(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "all", "mode_param": "", "body": "hi"},
    )
    assert "1 recipient" in r.text  # just the logged-in admin exists


def test_preview_lists_unmatched_explicit_ids(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "explicit", "mode_param": "999 oops", "body": "hi"},
    )
    assert "999" in r.text
    assert "oops" in r.text


def test_preview_rejects_an_over_long_body(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "all", "mode_param": "", "body": "x" * 5000},
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_admin_broadcast.py -v`
Expected: FAIL — 404, the routes do not exist.

- [ ] **Step 3: Add the two routes**

Append to `src/app/web/routes/admin.py`:

```python
@router.get("/admin/broadcast", response_class=HTMLResponse)
async def broadcast_compose(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {
            "user": user,
            "past": await recent_broadcasts(session),
            "preview": None,
            "body_max": BROADCAST_BODY_MAX,
            "bot_enabled": settings.bot_enabled,
        },
    )


@router.post("/admin/broadcast/preview", response_class=HTMLResponse)
async def broadcast_preview(
    request: Request,
    mode: str = Form(...),
    mode_param: str = Form(""),
    body: str = Form(...),
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Resolves and renders. Writes NOTHING -- the outbox is untouched until
    the admin confirms from this screen."""
    try:
        chosen = BroadcastMode(mode)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown broadcast mode") from None
    text = body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="broadcast body is empty")
    if len(text) > BROADCAST_BODY_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"body exceeds {BROADCAST_BODY_MAX} characters",
        )

    recipients = await resolve_recipients(session, chosen, mode_param or None)
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {
            "user": user,
            "past": await recent_broadcasts(session),
            "body_max": BROADCAST_BODY_MAX,
            "bot_enabled": settings.bot_enabled,
            "preview": {
                "mode": chosen.value,
                "mode_param": mode_param,
                "body": text,
                "count": len(recipients.ids),
                "unmatched": recipients.unmatched,
                "needs_typed_confirm": len(recipients.ids) > TYPED_CONFIRM_THRESHOLD,
                "duplicate": await duplicate_body_recently(session, text),
            },
        },
    )
```

Add the needed imports to the top block: `Form` from `fastapi`; `BROADCAST_BODY_MAX`, `TYPED_CONFIRM_THRESHOLD`, `duplicate_body_recently`, `recent_broadcasts`, `resolve_recipients` from `app.db.service`; `BroadcastMode` from `app.domain.types`; `settings` from `app.config`.

- [ ] **Step 4: Add the template**

Create `src/app/web/templates/admin_broadcast.html`. English-only, no new CSS classes — reuse `.tagtable` and `.dim`:

```html
{% extends "base.html" %}
{% block title %}Broadcast{% endblock %}
{% block content %}
<h1>Broadcast</h1>
<p class="dim">
  Sends a plain-text DM to the recipients you choose. Held for two minutes
  after you send, so it can be cancelled.
  {% if not bot_enabled %}<strong>The bot is not running here — messages would queue and never send.</strong>{% endif %}
</p>

{% if preview %}
<h2>Preview</h2>
{% if preview.duplicate %}
<p><strong>This exact message was already sent within the last hour.</strong></p>
{% endif %}
<p><strong>{{ preview.count }} recipient{{ '' if preview.count == 1 else 's' }}</strong> — mode: {{ preview.mode }}</p>
{% if preview.unmatched %}
<p>Not matched to any user, and will NOT be messaged:
  {% for u in preview.unmatched %}<code>{{ u }}</code>{% if not loop.last %}, {% endif %}{% endfor %}
</p>
{% endif %}
<pre>{{ preview.body }}</pre>
<form method="post" action="/admin/broadcast/send">
  <input type="hidden" name="mode" value="{{ preview.mode }}">
  <input type="hidden" name="mode_param" value="{{ preview.mode_param }}">
  <input type="hidden" name="body" value="{{ preview.body }}">
  {% if preview.needs_typed_confirm %}
  <p>Type <strong>{{ preview.count }}</strong> to confirm sending to {{ preview.count }} people:</p>
  <input type="text" name="confirm_count" required autocomplete="off">
  {% endif %}
  <button type="submit">Send</button>
</form>
{% endif %}

<h2>New broadcast</h2>
<form method="post" action="/admin/broadcast/preview">
  <label>Mode
    <select name="mode">
      <option value="all">All users</option>
      <option value="batch">Recipients of a delivery batch</option>
      <option value="explicit">Explicit Discord ids</option>
    </select>
  </label>
  <label>Batch timestamp or id list
    <input type="text" name="mode_param" placeholder="leave blank for All users">
  </label>
  <label>Message
    <textarea name="body" rows="6" maxlength="{{ body_max }}" required></textarea>
  </label>
  <button type="submit">Preview</button>
</form>

<h2>Past broadcasts</h2>
{% if past %}
<table class="tagtable">
  <thead><tr><th>When</th><th>Mode</th><th class="r">Recipients</th><th>State</th></tr></thead>
  <tbody>
    {% for b in past %}
    <tr>
      <td><a href="/admin/broadcast/{{ b.id }}">{{ b.created_at_utc.strftime('%Y-%m-%d %H:%M') }}</a></td>
      <td>{{ b.mode.value }}</td>
      <td class="r">{{ b.recipient_count }}</td>
      <td>{% if b.cancelled_at_utc %}cancelled{% else %}sent{% endif %}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p class="dim">No broadcasts yet.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run --isolated pytest tests/test_admin_broadcast.py -v`
Expected: PASS.

- [ ] **Step 6: Full gates and commit**

```bash
git add src/app/web/routes/admin.py src/app/web/templates/admin_broadcast.html tests/test_admin_broadcast.py
git commit -m "feat: broadcast compose and preview

Preview resolves and renders but writes nothing -- the outbox is
untouched until the admin confirms from that screen. Unmatched explicit
ids are shown, and a body over the Discord ceiling is rejected at the
boundary rather than truncated on send."
```

---

### Task 6: Send, status, cancel — and the localized frame

The write path.

**Files:**
- Modify: `src/app/web/routes/admin.py`
- Modify: `src/app/web/templates/admin_broadcast.html` (status block)
- Modify: `src/app/web/templates/admin_deliveries.html` (the handoff)
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`
- Test: `tests/test_admin_broadcast.py`

**Interfaces:**
- Consumes: `queue_broadcast`, `cancel_broadcast` (Task 4).
- Produces: `POST /admin/broadcast/send`, `GET /admin/broadcast/{broadcast_id}`, `POST /admin/broadcast/{broadcast_id}/cancel`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin_broadcast.py`:

```python
def test_send_queues_held_notifications_and_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "sorry about that"},
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/broadcast/")

    async def rows():
        async with client.db() as s:
            notes = (await s.execute(select(Notification))).scalars().all()
            b = (await s.execute(select(Broadcast))).scalar_one()
            return notes, b

    notes, b = asyncio.get_event_loop().run_until_complete(rows())
    assert len(notes) == 1
    assert notes[0].kind == "admin_broadcast"
    assert notes[0].send_after_utc is not None
    assert notes[0].broadcast_id == b.id
    assert b.recipient_count == 1


def test_send_above_the_threshold_requires_the_typed_count(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(service, "TYPED_CONFIRM_THRESHOLD", 0)
    login_as(client, ADMIN_ID, "reiji")
    bad = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "hi", "confirm_count": "99"},
    )
    assert bad.status_code == 422
    ok = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "hi", "confirm_count": "1"},
    )
    assert ok.status_code == 303


def test_status_page_and_cancel(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    sent = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "oops"},
    )
    bid = sent.headers["location"].rsplit("/", 1)[1]

    page = client.get(f"/admin/broadcast/{bid}")
    assert page.status_code == 200
    assert "Cancel" in page.text

    cancelled = client.post(f"/admin/broadcast/{bid}/cancel")
    assert cancelled.status_code == 303

    async def remaining():
        async with client.db() as s:
            return len((await s.execute(select(Notification))).scalars().all())

    assert asyncio.get_event_loop().run_until_complete(remaining()) == 0


def test_the_body_is_framed_per_recipient_language(client, monkeypatch):
    """The frame is applied at QUEUE time, in each recipient's language, which
    is what keeps the scheduler's plain-text send path unchanged."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")

    async def make_ja():
        async with client.db() as s:
            s.add(User(discord_id=1234, username="jp", language="ja"))
            await s.commit()

    asyncio.get_event_loop().run_until_complete(make_ja())
    client.post(
        "/admin/broadcast/send",
        data={"mode": "explicit", "mode_param": "1234", "body": "test"},
    )

    async def body():
        async with client.db() as s:
            return (await s.execute(select(Notification))).scalar_one().body

    text = asyncio.get_event_loop().run_until_complete(body())
    assert "test" in text
    assert "dekimasen.app" in text
    assert "From dekimasen.app" not in text  # translated, not the English msgid
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_admin_broadcast.py -v`
Expected: FAIL — 404 on `/admin/broadcast/send`.

- [ ] **Step 3: Add the three routes**

Append to `src/app/web/routes/admin.py`:

```python
@router.post("/admin/broadcast/send")
async def broadcast_send(
    mode: str = Form(...),
    mode_param: str = Form(""),
    body: str = Form(...),
    confirm_count: str = Form(""),
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Re-resolves rather than trusting a snapshot from the preview form:
    tampering is not the threat (EXPLICIT already accepts arbitrary ids), drift
    is, and recipient_count must record what was actually queued."""
    try:
        chosen = BroadcastMode(mode)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown broadcast mode") from None

    recipients = await resolve_recipients(session, chosen, mode_param or None)
    count = len(recipients.ids)
    if count > TYPED_CONFIRM_THRESHOLD and confirm_count.strip() != str(count):
        raise HTTPException(
            status_code=422,
            detail=f"type {count} to confirm sending to {count} people",
        )

    try:
        broadcast = await queue_broadcast(
            session, user.id, chosen, mode_param or None, body
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    await session.commit()
    return RedirectResponse(f"/admin/broadcast/{broadcast.id}", status_code=303)


@router.get("/admin/broadcast/{broadcast_id}", response_class=HTMLResponse)
async def broadcast_status(
    request: Request,
    broadcast_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise HTTPException(status_code=404, detail="no such broadcast")
    pending = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.broadcast_id == broadcast_id,
                Notification.sent_at_utc.is_(None),
            )
        )
    ).scalar_one()
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {
            "user": user,
            "past": await recent_broadcasts(session),
            "preview": None,
            "body_max": BROADCAST_BODY_MAX,
            "bot_enabled": settings.bot_enabled,
            "status": {"broadcast": broadcast, "pending": pending},
        },
    )


@router.post("/admin/broadcast/{broadcast_id}/cancel")
async def broadcast_cancel(
    broadcast_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if await session.get(Broadcast, broadcast_id) is None:
        raise HTTPException(status_code=404, detail="no such broadcast")
    await cancel_broadcast(session, broadcast_id)
    await session.commit()
    return RedirectResponse(f"/admin/broadcast/{broadcast_id}", status_code=303)
```

Add to the imports: `RedirectResponse` from `fastapi.responses`, `func` from `sqlalchemy`, `Broadcast` from `app.db.models`, `cancel_broadcast`/`queue_broadcast` from `app.db.service`.

Every other template context in this module passes `"status": None` — add that key to the two Task 5 routes so the template can rely on it.

- [ ] **Step 4: Add the status block to the template**

Insert into `admin_broadcast.html`, before the `{% if preview %}` block:

```html
{% if status %}
<h2>Broadcast {{ status.broadcast.created_at_utc.strftime('%Y-%m-%d %H:%M') }} UTC</h2>
<p>
  {{ status.broadcast.recipient_count }} recipient{{ '' if status.broadcast.recipient_count == 1 else 's' }} —
  {% if status.broadcast.cancelled_at_utc %}
    <strong>cancelled.</strong>
    {{ status.broadcast.recipient_count - status.pending }} had already been delivered and cannot be recalled.
  {% elif status.pending %}
    <strong>{{ status.pending }} still held.</strong> Cancel now and they will not be sent.
  {% else %}
    all delivered.
  {% endif %}
</p>
<pre>{{ status.broadcast.body }}</pre>
{% if status.pending and not status.broadcast.cancelled_at_utc %}
<form method="post" action="/admin/broadcast/{{ status.broadcast.id }}/cancel">
  <button type="submit">Cancel this broadcast</button>
</form>
{% endif %}
{% endif %}
```

- [ ] **Step 5: Add the handoff from `/admin/deliveries`**

In `admin_deliveries.html`'s batch-detail section, add above the table:

```html
<p><a href="/admin/broadcast?mode=batch&amp;mode_param={{ batch.isoformat() }}">Message these recipients</a></p>
```

Then in `broadcast_compose`, read the optional `mode` and `mode_param` query parameters and pass them into the context as `prefill`, and have the compose form use `prefill` for its `<select>` and `mode_param` values when present. Keep the parameters optional — a bare `GET /admin/broadcast` must still render.

- [ ] **Step 6: Add the two frame msgids**

The frame string is `From dekimasen.app`, wrapped by `gettext_in` in `service._framed_body` (Task 4). Run the catalogue ritual:

```
uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l ja
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill in by hand — the brand is never translated, exactly as EN/中文/日本語 are not:
- ja: `dekimasen.app より`
- zh: `来自 dekimasen.app`

Remove any `#, fuzzy` markers (fuzzy counts as untranslated). Delete `messages.pot` afterwards.

- [ ] **Step 7: Verify the catalogues and run the tests**

Run: `uv run --isolated pytest tests/test_i18n_catalogues.py tests/test_admin_broadcast.py -v`
Expected: PASS.

- [ ] **Step 8: Full gates and commit**

```bash
git add src/app/web/routes/admin.py src/app/web/templates/ src/app/translations/ tests/test_admin_broadcast.py
git commit -m "feat: broadcast send, status and cancel

Send re-resolves rather than trusting the preview form, so
recipient_count records what was queued. The status page reports what
cancel could NOT undo -- a rail that lies about what it stopped is worse
than no rail. The recipient-facing frame is applied at queue time in each
recipient's language, so the scheduler's plain-text path is unchanged."
```

---

### Task 7: The docs this feature owes

**Files:**
- Modify: `CLAUDE.md`, `WISHLIST.md`
- Verify: `src/app/web/templates/privacy.html`

- [ ] **Step 1: Confirm the privacy page needs no change**

The broadcast stores admin-authored text and a recipient count — no new category of *user* data. Read `privacy.html` and confirm this rather than assuming. If the existing wording implies DMs are only ever automated reminders, it needs a sentence; if it is already general about DMs, it does not. Report which.

- [ ] **Step 2: Document the invariant in CLAUDE.md**

Append to invariant 4 (**Notifications**):

```
   An admin broadcast (`/admin/broadcast`) is the one path that puts
   admin-authored text into other users' DMs, and it still goes through the
   outbox -- it is queued HELD via `Notification.send_after_utc` (120s) so it
   can be cancelled, and cancelling deletes only the UNSENT rows. Both new
   `Notification` columns are nullable and NULL means the pre-broadcast
   behaviour, which is what keeps every other notice unaffected by the drain
   query's hold clause. `due_notifications`' `send_after_utc IS NULL` branch is
   load-bearing: SQL evaluates `NULL <= now` as NULL, so dropping it stops the
   entire outbox.
```

- [ ] **Step 3: Update WISHLIST.md**

Move the admin-broadcast entry from Proposed to Shipped, dated 2026-07-28, describing what shipped and the two decisions worth recording (all modes resolved rather than derived; the undo window as the answer to an unrecallable action). Then do the full revision pass the CLAUDE.md wishlist rule requires — re-rank the remaining entries, and write the narrative paragraph in the house voice explaining what moved and why. Note that this closes the arc the delivery feed opened, and that the rehearsal harness (A) is now the only unbuilt piece of the three.

- [ ] **Step 4: Full gates and commit**

```bash
git add CLAUDE.md WISHLIST.md
git commit -m "docs: record the broadcast invariant and update the wishlist"
```

---

## Self-Review

**Spec coverage.** §0 (the `UNREPORTED_NOTE_KINDS` correction) → Task 1. §1 (data model) → Task 2. §2 (recipient modes) → Task 4. §3 rails: preview → Task 5, typed confirm → Task 6, undo window → Tasks 2+3+6, audit record → Tasks 2+4, duplicate warning → Tasks 4+5. §4 (flow, five routes, deliveries handoff) → Tasks 5+6. §5 (plain text, localized frame) → Tasks 4+6. §6 hazards: cancel race → Task 4's `cancel_broadcast` tests and Task 6's status template; `bot_enabled` false → Task 5's template. §7 testing — every bullet appears as a named test. §8 obligations → Task 7.

**Placeholder scan.** No TBDs. Task 5 Step 1 says to copy a fixture from `tests/test_admin_deliveries.py` rather than restating it; that file exists and its shape is known good. Task 6 Step 5's prefill wiring is described rather than coded — it is three lines of template plumbing whose exact form depends on the compose form written in Task 5.

**Type consistency.** `Recipients(ids, unmatched)` has the same two fields in Task 4's definition, its tests, and both routes. `resolve_recipients(session, mode, param)` and `cancel_broadcast(...) -> (cancelled, delivered)` match everywhere. `BroadcastMode` values (`batch`/`all`/`explicit`) match the template's `<option value>`s and the tests' form data. `queue_broadcast` returns `Broadcast` and every caller uses `.id`.

**One thing left to the implementer's judgement:** Task 6's typed-confirm test monkeypatches `service.TYPED_CONFIRM_THRESHOLD` to 0 because a test DB has one user. Confirm the route reads the module attribute at call time (`service.TYPED_CONFIRM_THRESHOLD`) rather than importing the value into its own namespace at import time — otherwise the monkeypatch will not take, and the test will pass vacuously against the real threshold of 10.
