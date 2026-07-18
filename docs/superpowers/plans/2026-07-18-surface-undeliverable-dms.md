# Surface Undeliverable DMs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give users a visible, actionable signal when their Discord DMs are blocked, instead of the app silently going dark.

**Architecture:** A new `User.dm_blocked_since` column is the single source of truth. The scheduler (`scheduler/loop.py`) sets/clears it via a new `db/service.py` function whenever it observes a send outcome; `auth.SessionUser` surfaces it (at zero extra query cost) for a sitewide banner in `base.html`; a new `POST /me/test-dm` route lets a user trigger an immediate, synchronous check, as an explicit, documented exception to CLAUDE.md's outbox-only DM invariant.

**Tech Stack:** FastAPI + Jinja2 + htmx, SQLAlchemy 2.0 async, discord.py, Alembic.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` must both be clean before every commit.
- One shared `dm_blocked_since` signal across reminder-DM and notification-DM sends — not tracked per-source.
- Any successful send (reminder, notification, or the manual test DM) clears the flag; a `discord.Forbidden` sets it; a transient `discord.HTTPException` touches neither.
- `POST /me/test-dm` is an explicit, narrowly-scoped exception to CLAUDE.md's "never send DMs directly from web routes" invariant — document it in CLAUDE.md, don't generalize it.
- This codebase has no flash-message system; `/me/test-dm` follows the existing htmx fragment-swap idiom (`_rules.html`'s `hx-post`/`hx-target`/`hx-swap="outerHTML"`), not a new mechanism.
- Sentence case in all new UI copy.
- Every new page-rendering code path needs at least one logged-in GET render test.
- Spec reference: `docs/superpowers/specs/2026-07-18-surface-undeliverable-dms-design.md`.

---

## Task 1: Data model + `record_dm_outcome`

**Files:**
- Modify: `src/app/db/models.py`
- Create: `alembic/versions/<generated>_dm_blocked_since.py`
- Modify: `src/app/db/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: `User.dm_blocked_since: Mapped[datetime | None]` (nullable `UTCDateTime` column).
- Produces: `async def record_dm_outcome(session: AsyncSession, discord_id: int, blocked: bool) -> None`.

- [ ] **Step 1: Add the column to the model**

In `src/app/db/models.py`, find the `User` class:

```python
class User(Base):
    __tablename__ = "users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(100))
    avatar_hash: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Moncton")
    # tz_auto: timezone is browser-detected until the user overrides it manually
    tz_auto: Mapped[bool] = mapped_column(default=True, server_default="1")
    # DB-persisted editor grant, toggled by admins (web/Discord). Final editor
    # status is this OR settings.editor_whitelist OR settings.admin_whitelist.
    is_editor: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Personal calendar-feed subscription token, hashed at rest (same pattern
    # as WebSession.token_hash) -- the raw token lives only in the feed URL,
    # shown once at generation time, never stored or re-displayable.
    calendar_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    rules: Mapped[list["ReminderRule"]] = relationship(back_populates="user")
```

Add `dm_blocked_since` right after `calendar_token_hash`:

```python
class User(Base):
    __tablename__ = "users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(100))
    avatar_hash: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Moncton")
    # tz_auto: timezone is browser-detected until the user overrides it manually
    tz_auto: Mapped[bool] = mapped_column(default=True, server_default="1")
    # DB-persisted editor grant, toggled by admins (web/Discord). Final editor
    # status is this OR settings.editor_whitelist OR settings.admin_whitelist.
    is_editor: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Personal calendar-feed subscription token, hashed at rest (same pattern
    # as WebSession.token_hash) -- the raw token lives only in the feed URL,
    # shown once at generation time, never stored or re-displayable.
    calendar_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    # None = DMs working (or never tested); a timestamp = the most recent
    # attempted send hit discord.Forbidden. One shared signal across both
    # reminder-DM and notification-DM sends (see scheduler/loop.py's
    # DeliveryOutcome and db/service.py's record_dm_outcome). Surfaced as a
    # sitewide banner via auth.SessionUser.dm_blocked.
    dm_blocked_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    rules: Mapped[list["ReminderRule"]] = relationship(back_populates="user")
```

- [ ] **Step 2: Generate and review the migration**

Run: `uv run alembic revision --autogenerate -m "dm blocked since"`

This creates `alembic/versions/<hash>_dm_blocked_since.py` with a head revision hash Alembic generates at run time. Edit the generated file to match this project's migration convention exactly (see `alembic/versions/1430ba5bbc7e_concert_day_cancelled.py` for the identical single-nullable-column-add shape this mirrors):

- Replace `sa.Column('dm_blocked_since', app.db.models.UTCDateTime(), nullable=True)` with `sa.Column('dm_blocked_since', sa.DateTime(), nullable=True)`.
- Remove the `import app.db.models` line if present.
- Confirm `alembic.ini` and the new migration file stay ASCII-only (no em-dashes).

The final `upgrade()`/`downgrade()` should read:

```python
def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dm_blocked_since', sa.DateTime(), nullable=True))

    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('dm_blocked_since')

    # ### end Alembic commands ###
```

- [ ] **Step 3: Apply the migration to a scratch DB and verify**

Run: `uv run alembic upgrade head`
Expected: no errors; the `users` table now has a `dm_blocked_since` column (verify with `sqlite3 app.db ".schema users"` or equivalent if a local `app.db` exists — otherwise this step is confirmed by the test suite's migration tests passing in Step 6).

- [ ] **Step 4: Write the failing test for `record_dm_outcome`**

Add to `tests/test_service.py`, at the very end of the file, right after `test_list_editors_marks_env_lock_on_db_editor` (the last test in the file, which covers `list_editors` -- the neighboring `User`-mutating function to `record_dm_outcome`):

```python
async def test_record_dm_outcome_sets_and_clears_flag(session):
    await ensure_user(session, 42, "reiji")

    await record_dm_outcome(session, 42, blocked=True)
    user = await session.get(User, 42)
    assert user.dm_blocked_since is not None

    await record_dm_outcome(session, 42, blocked=False)
    assert user.dm_blocked_since is None
```

Add `record_dm_outcome` to the existing `from app.db.service import (...)` import block in `tests/test_service.py`. `ensure_user` and `User` are already imported in this file.

- [ ] **Step 5: Run the test to verify it fails**

Run: `uv run pytest tests/test_service.py -k "record_dm_outcome" -v`
Expected: FAIL with `ImportError` (the function doesn't exist yet).

- [ ] **Step 6: Implement `record_dm_outcome`**

In `src/app/db/service.py`, add this function in the "Users" section, right after `list_editors` (before the `# ── Adapters: ORM -> domain dataclasses ──` section header):

```python
async def record_dm_outcome(session: AsyncSession, discord_id: int, blocked: bool) -> None:
    """Persist whether the most recent attempted DM to this user succeeded
    or hit discord.Forbidden -- the sitewide "DMs blocked" banner reads
    dm_blocked_since directly off the User row (see auth.current_user)."""
    user = await session.get(User, discord_id)
    if user is not None:
        user.dm_blocked_since = _now() if blocked else None
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/test_service.py -k "record_dm_outcome" -v`
Expected: `1 passed`

- [ ] **Step 8: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (291).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/models.py src/app/db/service.py tests/test_service.py alembic/versions/
git commit -m "Add User.dm_blocked_since and record_dm_outcome"
```

---

## Task 2: Scheduler outcome tracking

**Files:**
- Modify: `src/app/scheduler/loop.py`
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: `record_dm_outcome` (Task 1).
- Produces: `class DeliveryOutcome(Enum)` with `SUCCESS`, `FORBIDDEN`, `TRANSIENT_FAILURE` — `deliver()` and `_send_notification()` now return this instead of `bool`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_presets.py`, right after `test_tick_forbidden_send_does_not_affect_others`:

```python
async def test_tick_forbidden_send_sets_dm_blocked(client):
    from datetime import UTC, datetime, timedelta

    import discord

    import app.scheduler.loop as loop_mod
    from app.db.models import User
    from app.scheduler.loop import tick

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)

    past = datetime.now(UTC) - timedelta(seconds=5)
    uids = await _seed_due_reminders(client, 1, past=past)

    class FakeResponse:
        status = 403
        reason = "Forbidden"

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            raise discord.Forbidden(FakeResponse(), "missing access")

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    await tick(FakeBot())

    async with client.db() as s:
        user = await s.get(User, uids[0])
        assert user.dm_blocked_since is not None


async def test_tick_successful_send_clears_dm_blocked(client):
    from datetime import UTC, datetime, timedelta

    import app.scheduler.loop as loop_mod
    from app.db.models import User
    from app.db.service import record_dm_outcome
    from app.scheduler.loop import tick

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)

    past = datetime.now(UTC) - timedelta(seconds=5)
    uids = await _seed_due_reminders(client, 1, past=past)
    async with client.db() as s:
        await record_dm_outcome(s, uids[0], blocked=True)
        await s.commit()

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            pass

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    await tick(FakeBot())

    async with client.db() as s:
        user = await s.get(User, uids[0])
        assert user.dm_blocked_since is None


async def test_tick_transient_failure_leaves_dm_blocked_unchanged(client):
    from datetime import UTC, datetime, timedelta

    import discord

    import app.scheduler.loop as loop_mod
    from app.db.models import ReminderQueue, User
    from app.scheduler.loop import tick

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)

    past = datetime.now(UTC) - timedelta(seconds=5)
    uids = await _seed_due_reminders(client, 1, past=past)

    class FakeResponse:
        status = 500
        reason = "Internal Server Error"

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            raise discord.HTTPException(FakeResponse(), "temporary blip")

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    delivered = await tick(FakeBot())

    assert delivered == 0
    async with client.db() as s:
        user = await s.get(User, uids[0])
        assert user.dm_blocked_since is None  # never set, nothing to clear
        rows = (await s.execute(select(ReminderQueue))).scalars().all()
        assert all(r.sent_at_utc is None for r in rows)  # left unsent, retries next tick
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_presets.py -k "dm_blocked" -v`
Expected: all 3 FAIL — `deliver`/`tick` don't touch `dm_blocked_since` yet.

- [ ] **Step 3: Update `scheduler/loop.py`**

Replace the full current file:

```python
"""The reminder scheduler: every 60s, drain due reminders into Discord DMs.

Failure philosophy (each case is deliberate):
  * Bot not ready / web-only mode  -> skip the tick, rows stay queued.
  * DM delivered                   -> mark sent. Only success marks sent.
    Also clears User.dm_blocked_since.
  * Forbidden (user blocks DMs)    -> mark sent anyway, log a warning, and
    set User.dm_blocked_since (surfaced as a sitewide banner -- see
    auth.SessionUser.dm_blocked). Retrying forever would spam the log and
    never succeed; the row is dead.
  * Any other error (network...)   -> leave unsent; next tick retries.
    Doesn't touch dm_blocked_since -- an unrelated hiccup says nothing
    about whether DMs are actually blocked.
  * Whole-tick exception           -> logged, loop survives. The loop dying
    silently is the one unacceptable outcome for a reminder app.

Concurrency: sends run under a bounded semaphore rather than a fixed
per-message delay. discord.py's own HTTPClient already paces/retries per
Discord's returned rate-limit bucket headers, so a manual gap on top of
that is strictly more conservative than necessary and caps throughput at
1 msg/sec regardless of how many reminders are actually due. Every DB
touch (fetching due rows, building notification embeds, marking sent)
stays strictly sequential on the one shared AsyncSession -- it is not
safe for concurrent use -- only the actual Discord network calls run
concurrently.
"""

import asyncio
import logging
from datetime import UTC, datetime
from enum import Enum

import discord

from app.bot.messages import (
    build_leg_cancelled_message,
    build_new_event_message,
    build_reminder_message,
)
from app.db.service import (
    DueReminder,
    due_notifications,
    due_reminders,
    leg_cancelled_context,
    mark_notification_sent,
    mark_sent,
    notice_context,
    record_dm_outcome,
)
from app.db.session import SessionMaker
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

TICK_SECONDS = 60
SEND_CONCURRENCY = 5  # bounded in-flight Discord calls; discord.py's own
                      # rate limiter is the real backstop beyond this.


class DeliveryOutcome(Enum):
    """A DM send's result. Distinct from "should this row be marked sent"
    (SUCCESS and FORBIDDEN both do; TRANSIENT_FAILURE doesn't) and from
    "should the per-user dm_blocked_since flag change" (SUCCESS clears it,
    FORBIDDEN sets it, TRANSIENT_FAILURE touches neither)."""

    SUCCESS = "success"
    FORBIDDEN = "forbidden"
    TRANSIENT_FAILURE = "transient_failure"


async def deliver(bot, item: DueReminder) -> DeliveryOutcome:
    """Send one reminder DM (embed + buttons). Pure Discord I/O -- no
    session access, safe to run concurrently."""
    try:
        user = bot.get_user(item.discord_id) or await bot.fetch_user(item.discord_id)
        embed, view = build_reminder_message(item)
        await user.send(embed=embed, view=view)
        return DeliveryOutcome.SUCCESS
    except discord.Forbidden:
        log.warning(
            "user %s has DMs closed; dropping reminder %s", item.discord_id, item.queue_id
        )
        return DeliveryOutcome.FORBIDDEN  # permanent failure: retrying can never succeed
    except discord.HTTPException as e:
        log.error("transient send failure for queue row %s: %s", item.queue_id, e)
        return DeliveryOutcome.TRANSIENT_FAILURE  # leave unsent; next tick retries


async def _notification_context(session, note):
    """DB-bound prep for one notification's message payload -- reads the
    session, so callers must run this sequentially, never concurrently.
    Dispatches on note.kind since different notice kinds need different
    context shapes (a leg-cancellation notice doesn't need the new-event
    context's subscriber-state fields, and vice versa)."""
    if note.kind == "leg_cancelled":
        return await leg_cancelled_context(session, note.concert_id) if note.concert_id else None
    return await notice_context(session, note.concert_id, note.user_id) if note.concert_id else None


async def _send_notification(bot, note, ctx) -> DeliveryOutcome:
    """Send a notice DM. Structured (ctx set) -> rich embed with the
    state-aware buttons; otherwise the plain-text fallback body. Pure
    Discord I/O -- no session access, safe to run concurrently."""
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
        return DeliveryOutcome.SUCCESS
    except discord.Forbidden:
        log.warning("user %s has DMs closed; dropping notification", note.user_id)
        return DeliveryOutcome.FORBIDDEN
    except discord.HTTPException as e:
        log.error("transient notification failure for user %s: %s", note.user_id, e)
        return DeliveryOutcome.TRANSIENT_FAILURE


async def tick(bot) -> int:
    """One scheduler pass. Returns how many messages were delivered."""
    now = datetime.now(UTC)
    delivered = 0
    sem = asyncio.Semaphore(SEND_CONCURRENCY)

    async def bounded_deliver(item: DueReminder):
        async with sem:
            return item, await deliver(bot, item)

    async def bounded_send_notification(note, ctx):
        async with sem:
            return note, await _send_notification(bot, note, ctx)

    async with SessionMaker() as session:
        items = await due_reminders(session, now)
        for item, outcome in await asyncio.gather(*(bounded_deliver(i) for i in items)):
            if outcome in (DeliveryOutcome.SUCCESS, DeliveryOutcome.FORBIDDEN):
                await mark_sent(session, item.queue_id, now)
                delivered += 1
            if outcome is not DeliveryOutcome.TRANSIENT_FAILURE:
                await record_dm_outcome(
                    session, item.discord_id, blocked=outcome is DeliveryOutcome.FORBIDDEN
                )

        notes = await due_notifications(session)
        # DB-bound prep stays sequential on the one shared session...
        prepared = [(note, await _notification_context(session, note)) for note in notes]
        # ...then the actual Discord sends run concurrently.
        for note, outcome in await asyncio.gather(
            *(bounded_send_notification(note, ctx) for note, ctx in prepared)
        ):
            if outcome in (DeliveryOutcome.SUCCESS, DeliveryOutcome.FORBIDDEN):
                await mark_notification_sent(session, note.id)
                delivered += 1
            if outcome is not DeliveryOutcome.TRANSIENT_FAILURE:
                await record_dm_outcome(
                    session, note.user_id, blocked=outcome is DeliveryOutcome.FORBIDDEN
                )

        await session.commit()
    return delivered


async def reminder_loop(bot) -> None:
    if bot is None:
        log.info("web-only mode: scheduler idle (reminders queue up, nothing sends)")
        while True:
            heartbeat.beat()
            await asyncio.sleep(TICK_SECONDS)

    await bot.wait_until_ready()
    log.info("scheduler running: tick every %ss", TICK_SECONDS)
    while True:
        heartbeat.beat()
        try:
            n = await tick(bot)
            if n:
                log.info("delivered %d reminder(s)", n)
        except Exception:
            log.exception("scheduler tick failed; will retry next tick")
        await asyncio.sleep(TICK_SECONDS)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_presets.py -k "dm_blocked" -v`
Expected: `3 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (294). Pay attention to the pre-existing scheduler tests (`test_scheduler_delivers_notifications`, `test_tick_delivers_multiple_due_reminders_concurrently`, `test_tick_forbidden_send_does_not_affect_others`) — they assert on `delivered` counts and `sent_at_utc`, not on `dm_blocked_since`, so they should be unaffected by this change, but confirm they still pass.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/scheduler/loop.py tests/test_presets.py
git commit -m "Track dm_blocked_since through the scheduler's DeliveryOutcome"
```

---

## Task 3: `SessionUser.dm_blocked` + sitewide banner

**Files:**
- Modify: `src/app/web/auth.py`
- Modify: `src/app/web/templates/base.html`
- Modify: `src/app/web/static/style.css`
- Modify: `tests/test_auth.py`

**Interfaces:**
- Consumes: `User.dm_blocked_since` (Task 1).
- Produces: `SessionUser.dm_blocked: bool` field.

- [ ] **Step 1: Fix `tests/test_auth.py`'s DB fixture (found during this plan's research, not previously caught)**

`tests/test_auth.py`'s `db()` fixture is missing the `PRAGMA foreign_keys=ON` connect listener CLAUDE.md's testing conventions require for every DB fixture — every other fixture in the suite already has it (confirmed via a project-wide grep during this plan's writing). This task touches `test_auth.py` anyway, so fix it here rather than leaving it.

Find the current fixture:

```python
@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
```

Replace with:

```python
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
```

Add `event` to the existing `from sqlalchemy import select` import line in `tests/test_auth.py`, making it `from sqlalchemy import event, select`.

- [ ] **Step 2: Run the existing test_auth.py suite to confirm the fixture fix doesn't break anything**

Run: `uv run pytest tests/test_auth.py -v`
Expected: all existing tests still pass (FK enforcement being newly-on shouldn't affect any of them — none currently rely on a dangling/uncommitted cross-table reference).

- [ ] **Step 3: Write the failing tests for the banner**

Add to `tests/test_auth.py`, after the existing admin/editor-panel tests near the end of the file:

```python
def test_banner_hidden_when_dm_not_blocked(client):
    do_login(client)
    r = client.get("/")
    assert "couldn't be delivered" not in r.text


async def test_banner_shown_when_dm_blocked(client):
    from datetime import UTC, datetime

    do_login(client)
    async with client.db() as s:
        user = await s.get(User, 42)
        user.dm_blocked_since = datetime.now(UTC)
        await s.commit()

    r = client.get("/")
    assert "couldn't be delivered" in r.text
```

`User` is already imported at the top of `tests/test_auth.py`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -k "banner" -v`
Expected: both FAIL — `user.dm_blocked` doesn't exist yet, and the banner isn't in `base.html` yet.

- [ ] **Step 5: Add `dm_blocked` to `SessionUser` and populate it in `current_user()`**

In `src/app/web/auth.py`, find:

```python
@dataclass(frozen=True)
class SessionUser:
    id: int
    username: str
    avatar: str | None
    # Resolved once in current_user() (env whitelist OR admin OR DB flag) —
    # unlike is_admin this needs a DB read, so it can't be a cheap property.
    is_editor: bool = False
```

Replace with:

```python
@dataclass(frozen=True)
class SessionUser:
    id: int
    username: str
    avatar: str | None
    # Resolved once in current_user() (env whitelist OR admin OR DB flag) —
    # unlike is_admin this needs a DB read, so it can't be a cheap property.
    is_editor: bool = False
    # Also resolved in current_user() from the same already-loaded User row
    # (dm_blocked_since is not None) -- zero extra query cost. Drives the
    # sitewide "DMs blocked" banner in base.html.
    dm_blocked: bool = False
```

Find:

```python
    return SessionUser(
        id=user_id, username=data["username"], avatar=data.get("avatar"), is_editor=is_editor
    )
```

Replace with:

```python
    return SessionUser(
        id=user_id, username=data["username"], avatar=data.get("avatar"), is_editor=is_editor,
        dm_blocked=bool(db_user and db_user.dm_blocked_since is not None),
    )
```

- [ ] **Step 6: Add the banner to `base.html`**

Find:

```html
  </header>
  <main>{% block content %}{% endblock %}</main>
```

Replace with:

```html
  </header>
  {% if user and user.dm_blocked %}
  <div class="banner-warn">
    Your last Discord DM couldn't be delivered — check your Discord privacy
    settings to allow DMs from server members, or
    <a href="/preferences">test it from Preferences</a>.
  </div>
  {% endif %}
  <main>{% block content %}{% endblock %}</main>
```

- [ ] **Step 7: Add the banner's CSS**

In `src/app/web/static/style.css`, add this rule near the `.panel`/`.dim` rules (around line 48-49, right after the `.panel` block):

```css
.banner-warn {
  border: 1px solid var(--off);
  border-radius: 8px;
  padding: .6rem 1rem;
  margin: 0 auto 1rem;
  max-width: 60rem;
  font-size: .9rem;
}
.banner-warn a { color: var(--off); font-weight: 600; }
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -k "banner" -v`
Expected: `2 passed`

- [ ] **Step 9: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (296).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/auth.py src/app/web/templates/base.html src/app/web/static/style.css tests/test_auth.py
git commit -m "Add SessionUser.dm_blocked and a sitewide undeliverable-DM banner"
```

---

## Task 4: `/me/test-dm` route + button

**Files:**
- Modify: `src/app/web/routes/preferences.py`
- Modify: `src/app/web/templates/preferences.html`
- Test: `tests/test_crud.py`

**Interfaces:**
- Consumes: `record_dm_outcome` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_crud.py`, after `test_timezone_setting_validates`:

```python
async def test_test_dm_succeeds_and_clears_dm_blocked(client, monkeypatch):
    from app.config import settings as app_settings
    from app.db.models import User

    class FakeUser:
        async def send(self, body):
            pass

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    # bot_enabled defaults to False in tests (discord_token defaults to "");
    # this route short-circuits on that flag, so it must be turned on here.
    monkeypatch.setattr(app_settings, "discord_token", "fake-token")
    login_as(client, EDITOR_ID, "reiji")
    async with client.db() as s:
        user = await s.get(User, EDITOR_ID)
        user.dm_blocked_since = datetime.now(UTC)
        await s.commit()

    import app.bot.client as bot_client_mod

    client.monkeypatch.setattr(bot_client_mod, "bot", FakeBot())
    r = client.post("/me/test-dm")
    assert r.status_code == 200
    assert "Test DM sent" in r.text

    async with client.db() as s:
        user = await s.get(User, EDITOR_ID)
        assert user.dm_blocked_since is None


async def test_test_dm_forbidden_sets_dm_blocked(client, monkeypatch):
    import discord

    from app.config import settings as app_settings
    from app.db.models import User

    class FakeResponse:
        status = 403
        reason = "Forbidden"

    class FakeUser:
        async def send(self, body):
            raise discord.Forbidden(FakeResponse(), "missing access")

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    monkeypatch.setattr(app_settings, "discord_token", "fake-token")
    login_as(client, EDITOR_ID, "reiji")

    import app.bot.client as bot_client_mod

    client.monkeypatch.setattr(bot_client_mod, "bot", FakeBot())
    r = client.post("/me/test-dm")
    assert r.status_code == 200
    assert "Still blocked" in r.text

    async with client.db() as s:
        user = await s.get(User, EDITOR_ID)
        assert user.dm_blocked_since is not None


def test_test_dm_when_bot_disabled(client):
    """discord_token defaults to "" (bot_enabled False) in every test
    environment, so no monkeypatch is needed for this one -- it's the
    default state."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/test-dm")
    assert r.status_code == 200
    assert "isn't running" in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_crud.py -k "test_dm" -v`
Expected: all 3 FAIL with 404 (the route doesn't exist yet).

- [ ] **Step 3: Add the route**

In `src/app/web/routes/preferences.py`, update the module docstring's route list:

```python
"""User preferences: reminder presets, tag subscriptions, and timezone.

  GET  /preferences                          the page
  POST /presets                              create preset
  POST /presets/{id}/delete
  POST /presets/{id}/items                   add an item to a preset
  POST /presets/{id}/items/{item_id}/delete
  POST /subscriptions                        subscribe to a tag (+preset, notify)
  POST /subscriptions/{id}/delete
  POST /concerts/{event_id}/presets/{pid}/apply   one-click apply (rules fragment swap)
  POST /me/timezone                          manual timezone choice
  POST /me/timezone/auto                     browser-detected timezone
  POST /me/timezone/reset                    back to browser auto-detect
  POST /me/test-dm                           send a synchronous diagnostic test DM

Everything here is per-user: routes verify ownership and 404 on other
people's presets/subscriptions rather than admitting they exist.
"""
```

Add `import discord` right after `from zoneinfo import ZoneInfo`:

```python
from zoneinfo import ZoneInfo

import discord
```

Add `record_dm_outcome` to the existing `from app.db.service import (...)` block:

```python
from app.db.service import (
    apply_preset,
    ensure_user,
    group_members,
    list_editors,
    record_dm_outcome,
    set_default_preset,
    set_editor,
)
```

Add `"bot_enabled": settings.bot_enabled,` to the context dict returned by the `preferences()` GET route. Find:

```python
    return templates.TemplateResponse(
        request,
        "preferences.html",
        {"user": user, "presets": presets, "subs": subs, "sub_by_tag": sub_by_tag,
         "franchises": franchises, "groups": groups, "members": members,
         "solo_artists": solo_artists, "venues": venues,
         "tz": tz, "tz_auto": tz_auto,
         "common_timezones": COMMON_TIMEZONES, "all_timezones": all_timezones(),
         "anchors": list(Anchor), "editors": editors,
         "has_calendar_feed": has_calendar_feed,
         "feed_url": f"{settings.base_url}/calendar/{feed_token}.ics" if feed_token else None},
    )
```

Replace with:

```python
    return templates.TemplateResponse(
        request,
        "preferences.html",
        {"user": user, "presets": presets, "subs": subs, "sub_by_tag": sub_by_tag,
         "franchises": franchises, "groups": groups, "members": members,
         "solo_artists": solo_artists, "venues": venues,
         "tz": tz, "tz_auto": tz_auto,
         "common_timezones": COMMON_TIMEZONES, "all_timezones": all_timezones(),
         "anchors": list(Anchor), "editors": editors,
         "has_calendar_feed": has_calendar_feed,
         "feed_url": f"{settings.base_url}/calendar/{feed_token}.ics" if feed_token else None,
         "bot_enabled": settings.bot_enabled},
    )
```

At the end of the file, after `reset_timezone_auto`, add a new section:

```python
# ── DM diagnostics ───────────────────────────────────────────────────────


@router.post("/me/test-dm", response_class=HTMLResponse)
async def send_test_dm(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Synchronous, explicit exception to CLAUDE.md's "never send DMs
    directly from web routes" invariant (see the invariant's own addendum
    for why) -- a manual, user-initiated diagnostic action, unlike the
    notifications-table-driven system notices. Returns a one-line htmx
    fragment (this codebase has no flash-message system), following the
    hx-post/hx-target/hx-swap idiom _rules.html already establishes."""
    if not settings.bot_enabled:
        return HTMLResponse("Discord bot isn't running in this environment.")

    from app.bot.client import bot  # lazy: avoid discord.py setup cost in web-only dev mode

    try:
        discord_user = bot.get_user(user.id) or await bot.fetch_user(user.id)
        await discord_user.send(
            "This is a test DM from dekimasen.app — your reminders are working!"
        )
        await record_dm_outcome(session, user.id, blocked=False)
        await session.commit()
        return HTMLResponse("Test DM sent!")
    except discord.Forbidden:
        await record_dm_outcome(session, user.id, blocked=True)
        await session.commit()
        return HTMLResponse("Still blocked — check your Discord privacy settings.")
    except discord.HTTPException:
        return HTMLResponse("Couldn't reach Discord, try again.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_crud.py -k "test_dm" -v`
Expected: `3 passed`

- [ ] **Step 5: Add the button to `preferences.html`**

Find:

```html
<h2>Calendar feed</h2>
```

Insert a new section right before it:

```html
<h2>Discord DMs</h2>
{% if user.dm_blocked %}
<p class="dim">Your last reminder couldn't be delivered — check your Discord privacy
  settings to allow DMs from server members, then try again.</p>
{% endif %}
{% if bot_enabled %}
<p class="inline">
  <button hx-post="/me/test-dm" hx-target="#dm-test-result" hx-swap="innerHTML">Send test DM</button>
  <span id="dm-test-result" class="dim"></span>
</p>
{% else %}
<p class="dim">The Discord bot isn't running in this environment (dev mode), so DMs can't be tested here.</p>
{% endif %}

<h2>Calendar feed</h2>
```

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (299).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/routes/preferences.py src/app/web/templates/preferences.html tests/test_crud.py
git commit -m "Add a synchronous /me/test-dm diagnostic route and button"
```

---

## Final step: update CLAUDE.md and WISHLIST.md

**CLAUDE.md:**

- Bump the test count in the intro sentence to 299, and add "surfaced undeliverable-DM feedback (a sitewide banner plus a synchronous test-DM diagnostic)" to the shipped-features list.
- Update invariant #4 to document the new carve-out. Find:

  ```
  4. **Notifications**: new-event notices go through the `notifications`
     table (DB outbox drained by the scheduler) — never send DMs directly
     from web routes.
  ```

  Replace with:

  ```
  4. **Notifications**: new-event notices go through the `notifications`
     table (DB outbox drained by the scheduler) — never send DMs directly
     from web routes. One narrow, explicit exception: `POST /me/test-dm`
     (`web/routes/preferences.py`) sends synchronously and reports the
     result inline — a manual, user-initiated, low-volume diagnostic
     action is a different animal from a system-initiated notice, which
     must still go through the outbox for its retry/ordering/audit
     properties. Don't extend this carve-out to anything else without
     discussing it first.
  ```

**WISHLIST.md:** per CLAUDE.md's "Feature wishlist" maintenance convention (move the shipped entry, then do a full revision pass over what's left):

- Move item 1 ("Surface undeliverable DMs to the user") from `## Proposed` to `## Shipped`, with today's date and a one-line note on what shipped (the sitewide banner + the `/me/test-dm` diagnostic route/button).
- Re-rank and reconsider the remaining 4 entries. Worth noting explicitly during the revision pass: item 5 (first-run guided setup) can now reference the test-DM button as a natural "confirm DMs work" step in its onboarding sequence — a small addition to that entry's description, not a reordering by itself. Items 2, 3, and 4 are otherwise unaffected by this ship; re-confirm their relative order still makes sense rather than assuming it's unchanged.
