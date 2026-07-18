# First-run guided setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sequence a new user's first login through the five onboarding
actions that already exist (follow artists, pick a default preset, confirm
timezone, send a test DM, get a calendar feed link) via a dedicated
`/welcome` wizard page, instead of landing them on an index shaped by none
of them.

**Architecture:** One new `User.onboarding_step` column (0-4 in progress,
`>=5` done) drives a single `GET /welcome` route that renders whichever
step's screen matches the current value. Every step's real action reuses
an **existing** route verbatim (`/subscriptions`, `/presets`,
`/me/timezone`, `/me/test-dm`, `/me/calendar-feed`) via a small,
generic `next`-redirect parameter added to the four of those that
currently hard-code a redirect to `/preferences` — this keeps the wizard
a single continuous page instead of bouncing the user across
Tags/Preferences/Calendar. A shared `POST /welcome/advance` increments
the step; `POST /welcome/skip-all` jumps straight to done. The OAuth
callback (`auth.py`) redirects a genuinely brand-new login to `/welcome`;
a returning user always lands on `/` regardless of their
`onboarding_step` value.

**Tech Stack:** FastAPI + Jinja2 + htmx (existing), SQLAlchemy async +
Alembic (existing), no new dependencies.

## Global Constraints

- Sentence case everywhere in new UI copy ("Follow some artists", not
  "Follow Some Artists").
- Every step's underlying action (subscribe, preset creation, timezone
  set, test DM, calendar-feed generation) is reused **verbatim** from its
  existing route/service function — no new business logic for what any
  step *does*. The only new backend surface beyond the wizard's own 3
  routes is a generic `next`-redirect parameter (destination only, not
  behavior) on the routes that currently hard-code `/preferences`.
- `onboarding_step >= 5` means done (finished naturally or skipped) — no
  separate boolean. This value is never re-derived or reset.
- A returning user (row already existed before this login) is **never**
  redirected to `/welcome`, regardless of their `onboarding_step` value —
  the wizard is offered once, at first login only.
- Skipping any individual step never performs that step's underlying
  action — it only advances `onboarding_step`.
- ruff `line-length = 100` applies to everything under `src/` and
  `tests/` (not `alembic/**`, which is exempted in `pyproject.toml`).
- Migration files: ASCII-only, `sa.DateTime()`/`sa.Integer()` etc. (never
  `app.db.models.UTCDateTime()`), no `import app.db.models` line, `sa`
  imported as `import sqlalchemy as sa`.
- Every page needs at least one logged-in GET render test (CLAUDE.md's
  testing convention — a missing one shipped a 500 once).
- This branch starts fresh off `origin/main` at commit `2e79cd1` (305
  tests, migration head `5ea945b713c4`) — the `lottery-outcome-tracking`
  branch (currently an open draft PR, not yet merged) is a sibling, not a
  parent; do not assume anything from it is present.

---

### Task 1: `User.onboarding_step` column + migration

**Files:**
- Modify: `src/app/db/models.py` (the `User` class, around line 92)
- Create: `alembic/versions/e8a1c9d2f7b5_onboarding_step.py`
- Test: `tests/test_migration_onboarding_step.py`

**Interfaces:**
- Produces: `User.onboarding_step: Mapped[int]`, default `0`, non-nullable,
  `server_default="0"`. Later tasks read/write this field directly via a
  loaded `User` row — no service-layer helper is introduced for it.

- [ ] **Step 1: Add the column to the model**

In `src/app/db/models.py`, find the `User` class's `dm_blocked_since`
field and the `created_at` field right after it:

```python
    dm_blocked_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
```

Replace with:

```python
    dm_blocked_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # First-run guided setup: 0-4 = the wizard step in progress, >=5 = done
    # (finished naturally or skipped). Offered once at first login only --
    # never re-derived from this value on any later login.
    onboarding_step: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
```

- [ ] **Step 2: Create the migration with an explicit revision id**

Run:

```
uv run alembic revision --rev-id=e8a1c9d2f7b5 -m "onboarding step"
```

This writes a stub at `alembic/versions/e8a1c9d2f7b5_onboarding_step.py`.
Replace its entire contents with:

```python
"""onboarding step

Revision ID: e8a1c9d2f7b5
Revises: 5ea945b713c4
Create Date: 2026-07-18 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e8a1c9d2f7b5'
down_revision = '5ea945b713c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('onboarding_step', sa.Integer(), server_default='0', nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('onboarding_step')
```

(`5ea945b713c4` is the current migration head on `origin/main`, the
`dm_blocked_since` migration — confirm with
`uv run alembic heads` before writing this if anything looks off.)

- [ ] **Step 3: Write the migration test**

Create `tests/test_migration_onboarding_step.py`:

```python
"""Migration test: the onboarding_step column on users.

Same pattern as test_migration_round_outcomes.py: runs the real alembic
upgrade path against a scratch SQLite file, confirming the column exists
and that pre-existing rows backfill to 0 via server_default.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "5ea945b713c4"  # head immediately before this column


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def test_onboarding_step_column_exists_and_backfills_to_zero(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)
    columns_before = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    assert "onboarding_step" not in columns_before
    con.execute(
        "INSERT INTO users (discord_id, username, timezone, tz_auto, is_editor, created_at) "
        "VALUES (1, 'pre-existing', 'UTC', 1, 0, '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    columns = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    assert "onboarding_step" in columns
    value = con.execute("SELECT onboarding_step FROM users WHERE discord_id = 1").fetchone()[0]
    assert value == 0
    con.close()
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_migration_onboarding_step.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/db/models.py alembic/versions/e8a1c9d2f7b5_onboarding_step.py tests/test_migration_onboarding_step.py
git commit -m "Add User.onboarding_step column and migration"
```

---

### Task 2: Redirect a brand-new login to `/welcome`

**Files:**
- Modify: `src/app/web/auth.py` (the `callback` route, lines 148-171)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `User` (already imported in `auth.py`), `ensure_user` (already
  imported, signature unchanged: `ensure_user(session, discord_id,
  username) -> User`).
- Produces: nothing new for later tasks — this is a leaf change local to
  the OAuth callback.

**Design note:** the spec describes this as "`ensure_user` needs a small
return-shape change." `ensure_user` is called from ~15 other call sites
across `web/routes/*.py`, `bot/cogs/*.py`, and most of `tests/*.py` — none
of which care whether the row was just created. Changing its return type
to a tuple would force every one of those call sites (and their tests) to
unpack a value they don't need, for a need that is entirely local to this
one call site. Instead, `callback()` checks for the row's existence
itself, directly, before calling `ensure_user` — zero blast radius
elsewhere, and `ensure_user`'s signature stays exactly as it is today.

- [ ] **Step 1: Write the failing tests**

In `tests/test_auth.py`, right after `test_state_is_single_use` (ends
around line 98), add:

```python
def test_callback_redirects_new_user_to_welcome(client):
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/auth/callback?code=good-code&state={state}")
    assert r.headers["location"] == "/welcome"


def test_callback_redirects_returning_user_to_index(client):
    do_login(client)  # first login: creates the row
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/auth/callback?code=good-code&state={state}")
    assert r.headers["location"] == "/"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -k "redirects" -v`
Expected: `test_callback_redirects_new_user_to_welcome` FAILS with
`AssertionError` (`r.headers["location"]` is currently `"/"` for every
login). `test_callback_redirects_returning_user_to_index` PASSES
trivially (both branches currently redirect to `/`).

- [ ] **Step 3: Implement the redirect**

In `src/app/web/auth.py`, find:

```python
    user_id = int(me["id"])
    username = me.get("global_name") or me["username"]
    await ensure_user(db, user_id, username)
    sid = await create_web_session(db, user_id)
    await db.commit()

    request.session["sid"] = sid
    request.session["user"] = {"id": user_id, "username": username, "avatar": me.get("avatar")}
    log.info("login: %s (%s)", username, user_id)
    return RedirectResponse("/")
```

Replace with:

```python
    user_id = int(me["id"])
    username = me.get("global_name") or me["username"]
    is_new_user = await db.get(User, user_id) is None
    await ensure_user(db, user_id, username)
    sid = await create_web_session(db, user_id)
    await db.commit()

    request.session["sid"] = sid
    request.session["user"] = {"id": user_id, "username": username, "avatar": me.get("avatar")}
    log.info("login: %s (%s)", username, user_id)
    return RedirectResponse("/welcome" if is_new_user else "/")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -v`
Expected: all PASS, including the full existing file (this change must
not break any of the other auth tests — `do_login` only ever asserts a
302/307 status code, never a specific `location`, so it's unaffected).

- [ ] **Step 5: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/web/auth.py tests/test_auth.py
git commit -m "Redirect a brand-new login to /welcome"
```

---

### Task 3: `next`-redirect support on the reused routes

**Files:**
- Modify: `src/app/web/routes/preferences.py` (`subscribe`, `unsubscribe`,
  `create_preset`, `set_timezone`)
- Modify: `src/app/web/routes/calendar.py` (`create_calendar_feed`)
- Test: `tests/test_presets.py`, `tests/test_calendar_feed.py`

**Interfaces:**
- Produces: every one of the 5 routes above now accepts an optional form
  field `next` (HTML `name="next"`, defaulting to `/preferences`, aliased
  in Python as `next_url` to avoid shadowing the `next()` builtin) and
  redirects there instead of the hard-coded `/preferences`, restricted to
  the allow-list `{"/preferences", "/welcome"}` (anything else silently
  falls back to `/preferences` — this is form input, not meant to be an
  open redirect). Task 4/5's wizard templates pass `next=/welcome`; every
  existing caller (preferences.html) passes nothing and keeps today's
  exact behavior.

- [ ] **Step 1: Write the failing tests**

In `tests/test_presets.py`, add these tests after
`test_subscriber_without_preset_gets_notification_only` (around line 205):

```python
async def test_subscribe_default_redirect_is_unchanged(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1})
    assert r.headers["location"] == "/preferences"


async def test_subscribe_honors_next_param(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1, "next": "/welcome"})
    assert r.headers["location"] == "/welcome"


async def test_subscribe_rejects_an_unrecognized_next_value(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1, "next": "https://evil.example"})
    assert r.headers["location"] == "/preferences"


async def test_create_preset_honors_next_param(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/presets", data={"name": "standard", "next": "/welcome"})
    assert r.headers["location"] == "/welcome"


async def test_set_timezone_honors_next_param(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/me/timezone", data={"timezone": "Asia/Tokyo", "next": "/welcome"})
    assert r.headers["location"] == "/welcome"
```

In `tests/test_calendar_feed.py`, add this test after
`test_generate_feed_creates_token_and_redirects_with_it`:

```python
def test_generate_feed_honors_next_param(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed", data={"next": "/welcome"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/welcome?feed_token=")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_presets.py -k "next_param or redirect_is_unchanged" tests/test_calendar_feed.py -k next_param -v`
Expected: every new test except `test_subscribe_default_redirect_is_unchanged`
FAILS (that one passes trivially against today's hard-coded behavior;
`next="/welcome"` and the malicious value are both currently ignored, so
those assertions fail against the always-`/preferences` redirect).

- [ ] **Step 3: Implement `next`-redirect support**

In `src/app/web/routes/preferences.py`, find `owned_preset` (ends around
line 66) and add this helper right after it, before the `# ── The page
──` section:

```python
_ALLOWED_NEXT = {"/preferences", "/welcome"}


def _safe_next(next_url: str) -> str:
    return next_url if next_url in _ALLOWED_NEXT else "/preferences"
```

Find `create_preset`:

```python
@router.post("/presets")
async def create_preset(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., min_length=1, max_length=100),
    anchor: Anchor = Form(Anchor.CLOSES),
    days: int = Form(3, ge=0, le=60),
    hours: int = Form(0, ge=0, le=23),
    direction: str = Form("before"),
):
    """Create a preset WITH its first item — no empty-preset limbo."""
    await ensure_user(session, user.id, user.username)
    preset = ReminderPreset(user_id=user.id, name=name.strip())
    session.add(preset)
    await session.flush()
    sign = 1 if direction == "after" else -1
    session.add(PresetItem(
        preset_id=preset.id, anchor=anchor,
        offset_days=sign * days, offset_hours=sign * hours,
    ))
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)
```

Replace with:

```python
@router.post("/presets")
async def create_preset(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., min_length=1, max_length=100),
    anchor: Anchor = Form(Anchor.CLOSES),
    days: int = Form(3, ge=0, le=60),
    hours: int = Form(0, ge=0, le=23),
    direction: str = Form("before"),
    next_url: str = Form("/preferences", alias="next"),
):
    """Create a preset WITH its first item — no empty-preset limbo."""
    await ensure_user(session, user.id, user.username)
    preset = ReminderPreset(user_id=user.id, name=name.strip())
    session.add(preset)
    await session.flush()
    sign = 1 if direction == "after" else -1
    session.add(PresetItem(
        preset_id=preset.id, anchor=anchor,
        offset_days=sign * days, offset_hours=sign * hours,
    ))
    await session.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)
```

Find `subscribe`:

```python
@router.post("/subscriptions")
async def subscribe(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    tag_id: int = Form(...),
    preset_id: int = Form(0),
    notify: bool = Form(False),
):
    if await session.get(Tag, tag_id) is None:
        raise HTTPException(status_code=404, detail="tag not found")
    if preset_id:
        await owned_preset(session, user.id, preset_id)
    existing = await session.execute(
        select(TagSubscription).where(
            TagSubscription.user_id == user.id, TagSubscription.tag_id == tag_id
        )
    )
    sub = existing.scalar_one_or_none()
    await ensure_user(session, user.id, user.username)
    if sub is None:
        session.add(TagSubscription(
            user_id=user.id, tag_id=tag_id,
            preset_id=preset_id or None, notify=notify,
        ))
    else:  # re-submitting updates the existing subscription
        sub.preset_id = preset_id or None
        sub.notify = notify
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)
```

Replace with:

```python
@router.post("/subscriptions")
async def subscribe(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    tag_id: int = Form(...),
    preset_id: int = Form(0),
    notify: bool = Form(False),
    next_url: str = Form("/preferences", alias="next"),
):
    if await session.get(Tag, tag_id) is None:
        raise HTTPException(status_code=404, detail="tag not found")
    if preset_id:
        await owned_preset(session, user.id, preset_id)
    existing = await session.execute(
        select(TagSubscription).where(
            TagSubscription.user_id == user.id, TagSubscription.tag_id == tag_id
        )
    )
    sub = existing.scalar_one_or_none()
    await ensure_user(session, user.id, user.username)
    if sub is None:
        session.add(TagSubscription(
            user_id=user.id, tag_id=tag_id,
            preset_id=preset_id or None, notify=notify,
        ))
    else:  # re-submitting updates the existing subscription
        sub.preset_id = preset_id or None
        sub.notify = notify
    await session.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)
```

Find `unsubscribe`:

```python
@router.post("/subscriptions/{sub_id}/delete")
async def unsubscribe(
    sub_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(TagSubscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status_code=404)
    await session.delete(sub)
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)
```

Replace with:

```python
@router.post("/subscriptions/{sub_id}/delete")
async def unsubscribe(
    sub_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    next_url: str = Form("/preferences", alias="next"),
):
    sub = await session.get(TagSubscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status_code=404)
    await session.delete(sub)
    await session.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)
```

Find `set_timezone`:

```python
@router.post("/me/timezone")
async def set_timezone(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    timezone: str = Form(...),
):
    """Manual choice: sticks, and turns browser auto-detection off."""
    try:
        ZoneInfo(timezone)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {timezone}") from e
    db_user = await ensure_user(session, user.id, user.username)
    db_user.timezone = timezone
    db_user.tz_auto = False
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)
```

Replace with:

```python
@router.post("/me/timezone")
async def set_timezone(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    timezone: str = Form(...),
    next_url: str = Form("/preferences", alias="next"),
):
    """Manual choice: sticks, and turns browser auto-detection off."""
    try:
        ZoneInfo(timezone)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {timezone}") from e
    db_user = await ensure_user(session, user.id, user.username)
    db_user.timezone = timezone
    db_user.tz_auto = False
    await session.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)
```

In `src/app/web/routes/calendar.py`, find:

```python
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    generate_calendar_token,
    get_user_by_calendar_token,
    user_calendar_events,
)
from app.db.session import get_session
from app.domain.ics_export import build_calendar
from app.web.auth import SessionUser, require_user

router = APIRouter()


@router.post("/me/calendar-feed")
async def create_calendar_feed(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Generating a new token invalidates any previously-issued feed URL
    (only the hash is stored, so the old raw token stops matching)."""
    token = await generate_calendar_token(session, user.id)
    await session.commit()
    return RedirectResponse(f"/preferences?feed_token={token}", status_code=303)
```

Replace with:

```python
from fastapi import APIRouter, Depends, Form, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    generate_calendar_token,
    get_user_by_calendar_token,
    user_calendar_events,
)
from app.db.session import get_session
from app.domain.ics_export import build_calendar
from app.web.auth import SessionUser, require_user

router = APIRouter()

_ALLOWED_NEXT = {"/preferences", "/welcome"}


@router.post("/me/calendar-feed")
async def create_calendar_feed(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    next_url: str = Form("/preferences", alias="next"),
):
    """Generating a new token invalidates any previously-issued feed URL
    (only the hash is stored, so the old raw token stops matching)."""
    token = await generate_calendar_token(session, user.id)
    await session.commit()
    destination = next_url if next_url in _ALLOWED_NEXT else "/preferences"
    return RedirectResponse(f"{destination}?feed_token={token}", status_code=303)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_presets.py tests/test_calendar_feed.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/web/routes/preferences.py src/app/web/routes/calendar.py tests/test_presets.py tests/test_calendar_feed.py
git commit -m "Add a next-redirect param to the routes the wizard reuses"
```

---

### Task 4: The wizard shell + steps 0 and 1

**Files:**
- Create: `src/app/web/routes/welcome.py`
- Create: `src/app/web/templates/welcome.html`
- Modify: `src/app/web/app.py` (router registration)
- Test: `tests/test_welcome.py` (new file)

**Interfaces:**
- Consumes: `User.onboarding_step` (Task 1), the `next`-param routes
  (Task 3), `group_members` (existing, `db/service.py`), `my_presets`
  (existing, `web/routes/preferences.py`).
- Produces: `GET /welcome`, `POST /welcome/advance`,
  `POST /welcome/skip-all`; `TOTAL_STEPS = 5` module constant later tasks
  reuse for context only (no cross-task import needed — Task 5 edits this
  same file directly).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_welcome.py`:

```python
"""First-run guided setup: the wizard's own routes (GET /welcome dispatch,
POST /welcome/advance, POST /welcome/skip-all). The new-user-redirect half
that sends a brand-new login here lives in test_auth.py.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, ReminderPreset, TagSubscription, User
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID, FAN_ID = 42, 777


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


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False)
    c.db = db
    c.monkeypatch = monkeypatch
    return c


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def _onboarding_step(client, discord_id: int) -> int:
    async with client.db() as s:
        user = await s.get(User, discord_id)
        return user.onboarding_step


def test_welcome_requires_login(client):
    assert client.get("/welcome").status_code == 401


def test_welcome_shows_step_0_for_a_brand_new_user(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/welcome")
    assert r.status_code == 200
    assert "Follow some artists" in r.text


def test_welcome_redirects_to_index_once_done(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/skip-all")
    r = client.get("/welcome")
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_advance_increments_step_by_one(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/advance")
    assert r.status_code == 303
    assert r.headers["location"] == "/welcome"
    assert await _onboarding_step(client, FAN_ID) == 1


async def test_advance_stops_at_total_steps(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(10):
        client.post("/welcome/advance")
    assert await _onboarding_step(client, FAN_ID) == 5


async def test_skip_all_jumps_straight_to_done(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/skip-all")
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert await _onboarding_step(client, FAN_ID) == 5


async def test_step_0_subscribe_form_returns_to_welcome(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1, "next": "/welcome"})
    assert r.headers["location"] == "/welcome"
    async with client.db() as s:
        subs = (await s.execute(select(TagSubscription))).scalars().all()
    assert len(subs) == 1 and subs[0].user_id == FAN_ID
    # "Gakumas" alone would render either way (it's the tag name in the
    # picker); the "✓" only appears once sub_by_tag actually has this tag.
    assert "Gakumas ✓" in client.get("/welcome").text


async def test_skipping_step_1_does_not_create_a_preset(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    r = client.get("/welcome")
    assert "Skip this" in r.text
    client.post("/welcome/advance")  # step 1 -> 2, no preset created
    async with client.db() as s:
        presets = (await s.execute(select(ReminderPreset))).scalars().all()
    assert presets == []


def test_step_1_shows_continue_once_a_preset_exists(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    client.post("/presets", data={"name": "standard", "next": "/welcome"})
    r = client.get("/welcome")
    assert "Continue" in r.text and "Preset created" in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_welcome.py -v`
Expected: every test FAILS (`GET /welcome`, `POST /welcome/advance`,
`POST /welcome/skip-all` don't exist yet — 404s).

- [ ] **Step 3: Create the route file**

Create `src/app/web/routes/welcome.py`:

```python
"""First-run guided setup: a five-step wizard offered once at first login
(see auth.py's callback -- a brand-new row redirects here instead of /).
Each step reuses an existing action's route verbatim; this file only
sequences them.

  GET  /welcome              current step's screen (redirects to / once done)
  POST /welcome/advance      move to the next step
  POST /welcome/skip-all     jump straight to done
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tag, TagSubscription
from app.db.service import ensure_user, group_members
from app.db.session import get_session
from app.domain.types import Anchor, TagKind
from app.web.auth import SessionUser, require_user
from app.web.routes.preferences import my_presets

router = APIRouter()

templates = None  # set by web.app at startup

TOTAL_STEPS = 5


@router.get("/welcome", response_class=HTMLResponse)
async def welcome(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    if db_user.onboarding_step >= TOTAL_STEPS:
        return RedirectResponse("/", status_code=303)

    step = db_user.onboarding_step
    context = {"user": user, "step": step}

    if step == 0:
        subs = list((await session.execute(
            select(TagSubscription, Tag)
            .join(Tag, TagSubscription.tag_id == Tag.id)
            .where(TagSubscription.user_id == user.id)
        )).all())
        sub_by_tag = {tag.id: sub for sub, tag in subs}
        tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
        franchises = [t for t in tags if t.kind is TagKind.FRANCHISE]
        groups = [t for t in tags if t.kind is TagKind.GROUP]
        venues = [t for t in tags if t.kind is TagKind.VENUE]
        members = {g.id: await group_members(session, g.id) for g in groups}
        grouped_artist_ids = {m.id for ms in members.values() for m in ms}
        solo_artists = [
            t for t in tags if t.kind is TagKind.ARTIST and t.id not in grouped_artist_ids
        ]
        context.update({
            "franchises": franchises, "groups": groups, "members": members,
            "solo_artists": solo_artists, "venues": venues, "sub_by_tag": sub_by_tag,
        })
    elif step == 1:
        presets = await my_presets(session, user.id)
        context.update({"has_preset": bool(presets), "anchors": list(Anchor)})

    return templates.TemplateResponse(request, "welcome.html", context)


@router.post("/welcome/advance")
async def advance(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    db_user.onboarding_step = min(db_user.onboarding_step + 1, TOTAL_STEPS)
    await session.commit()
    return RedirectResponse("/welcome", status_code=303)


@router.post("/welcome/skip-all")
async def skip_all(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    db_user.onboarding_step = TOTAL_STEPS
    await session.commit()
    return RedirectResponse("/", status_code=303)
```

- [ ] **Step 4: Create the template with steps 0 and 1**

Create `src/app/web/templates/welcome.html`:

```html
{% extends "base.html" %}
{% block title %}welcome — dekimasen.app{% endblock %}
{% block content %}
<h1>Welcome to dekimasen.app</h1>
<p class="dim">Step {{ step + 1 }} of 5 —
  <form method="post" action="/welcome/skip-all" class="inline">
    <button type="submit" class="quiet tiny">skip setup entirely</button>
  </form>
</p>

{% if step == 0 %}
<h2>Follow some artists</h2>
<p class="dim">Get notified the moment a new event drops for anyone you follow.</p>
<input class="tag-search" type="search" placeholder="Search tags…" oninput="filterChips(this, '.sub-box')">

{% macro subchip(t) -%}
  {%- set sub = sub_by_tag.get(t.id) -%}
  {%- if sub -%}
  <form class="chipform" method="post" action="/subscriptions/{{ sub.id }}/delete">
    <input type="hidden" name="next" value="/welcome">
    <button class="chip kind-{{ t.kind.value }} on" data-name="{{ t.name | lower }}"
      title="Following — click to unfollow">{{ t.name }} ✓</button>
  </form>
  {%- else -%}
  <form class="chipform" method="post" action="/subscriptions">
    <input type="hidden" name="tag_id" value="{{ t.id }}">
    <input type="hidden" name="preset_id" value="0">
    <input type="hidden" name="notify" value="true">
    <input type="hidden" name="next" value="/welcome">
    <button class="chip kind-{{ t.kind.value }}" data-name="{{ t.name | lower }}">{{ t.name }}</button>
  </form>
  {%- endif -%}
{%- endmacro %}

<div class="sub-box">
  {% for f in franchises %}
    <details open class="tag-section">
      <summary>{{ f.name }} {{ subchip(f) }}</summary>
      {% for g in groups if g.parent_id == f.id %}
        <div class="group-row">
          {{ subchip(g) }}
          <span class="member-chips">{% for m in members.get(g.id, []) %}{{ subchip(m) }}{% endfor %}</span>
        </div>
      {% endfor %}
    </details>
  {% endfor %}
  {% set orphan_groups = groups | selectattr("parent_id", "none") | list %}
  {% if orphan_groups %}
  <details open class="tag-section"><summary>Other groups</summary>
    {% for g in orphan_groups %}
    <div class="group-row">{{ subchip(g) }}
      <span class="member-chips">{% for m in members.get(g.id, []) %}{{ subchip(m) }}{% endfor %}</span></div>
    {% endfor %}
  </details>
  {% endif %}
  {% if solo_artists %}
  <details open class="tag-section"><summary>Solo artists</summary>
    <div class="taglist">{% for a in solo_artists %}{{ subchip(a) }}{% endfor %}</div>
  </details>
  {% endif %}
  {% if venues %}
  <details class="tag-section"><summary>Venues</summary>
    <div class="taglist">{% for v in venues %}{{ subchip(v) }}{% endfor %}</div>
  </details>
  {% endif %}
</div>

<form method="post" action="/welcome/advance"><button>Continue</button></form>
{% endif %}

{% if step == 1 %}
<h2>Pick a default reminder preset</h2>
<p class="dim">This is what fires when you tap "Set my reminders" on a concert or click the
  Discord DM button — a standard loadout you won't have to rebuild every time.</p>
{% if has_preset %}
<p class="dim">Preset created — nice.</p>
{% else %}
<form class="stack" method="post" action="/presets">
  <input type="hidden" name="next" value="/welcome">
  <label>Name <input name="name" required maxlength="100" placeholder="standard lottery coverage"></label>
  <div class="sentence">
    <span>Remind me</span>
    <select name="days">{% for n in range(0, 61) %}<option value="{{ n }}" {% if n == 3 %}selected{% endif %}>{{ n }}</option>{% endfor %}</select>
    <span>day(s)</span>
    <select name="hours">{% for n in range(0, 24) %}<option value="{{ n }}">{{ n }}</option>{% endfor %}</select>
    <span>hour(s)</span>
    <select name="direction">
      <option value="before" selected>before</option>
      <option value="after">after</option>
    </select>
    <span>each</span>
    <select name="anchor">{% for a in anchors %}<option value="{{ a.value }}" {% if a.value == "closes" %}selected{% endif %}>{{ a.value.replace("_"," ") | capitalize }}</option>{% endfor %}</select>
  </div>
  <button>Create preset</button>
</form>
{% endif %}
<form method="post" action="/welcome/advance"><button>{{ "Continue" if has_preset else "Skip this" }}</button></form>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Register the router**

In `src/app/web/app.py`, add the import alongside the other route
imports:

```python
from app.web.routes import preferences as pref_routes
from app.web.routes import reminders as reminder_routes
from app.web.routes import tags as tag_routes
```

becomes:

```python
from app.web.routes import preferences as pref_routes
from app.web.routes import reminders as reminder_routes
from app.web.routes import tags as tag_routes
from app.web.routes import welcome as welcome_routes
```

Then find:

```python
    pref_routes.templates = templates
    app.include_router(pref_routes.router)
    # no templates: pure .ics responses
    app.include_router(calendar_routes.router)
```

Replace with:

```python
    pref_routes.templates = templates
    app.include_router(pref_routes.router)
    welcome_routes.templates = templates
    app.include_router(welcome_routes.router)
    # no templates: pure .ics responses
    app.include_router(calendar_routes.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_welcome.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/web/routes/welcome.py src/app/web/templates/welcome.html src/app/web/app.py tests/test_welcome.py
git commit -m "Add the /welcome wizard shell with steps 0 and 1"
```

---

### Task 5: Steps 2, 3, and 4

**Files:**
- Modify: `src/app/web/routes/welcome.py` (the `welcome()` route)
- Modify: `src/app/web/templates/welcome.html` (append steps 2-4)
- Test: `tests/test_welcome.py`

**Interfaces:**
- Consumes: `COMMON_TIMEZONES`, `all_timezones` (existing, both defined
  in `web/routes/preferences.py`), `settings.bot_enabled`,
  `settings.base_url` (existing, `app.config.settings`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_welcome.py`, append:

```python
async def test_welcome_shows_step_2_timezone(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # 0 -> 1
    client.post("/welcome/advance")  # 1 -> 2
    r = client.get("/welcome")
    assert "Confirm your timezone" in r.text


async def test_step_2_set_timezone_returns_to_welcome(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")
    client.post("/welcome/advance")
    r = client.post("/me/timezone", data={"timezone": "Asia/Tokyo", "next": "/welcome"})
    assert r.headers["location"] == "/welcome"
    # "Asia/Tokyo" alone would render regardless (it's already one of the
    # ~400 option values in the picker); only the `selected` marker proves
    # the wizard is actually showing the NEW value, not just any option.
    assert 'value="Asia/Tokyo" selected' in client.get("/welcome").text


async def test_welcome_shows_step_3_test_dm(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(3):
        client.post("/welcome/advance")  # 0 -> 1 -> 2 -> 3
    r = client.get("/welcome")
    assert "Send a test DM" in r.text


async def test_welcome_shows_step_4_calendar_feed(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(4):
        client.post("/welcome/advance")  # 0 -> 1 -> 2 -> 3 -> 4
    r = client.get("/welcome")
    assert "Get your calendar feed" in r.text
    assert "Skip this" in r.text


async def test_step_4_generate_feed_returns_to_welcome_with_link_shown(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(4):
        client.post("/welcome/advance")
    r = client.post("/me/calendar-feed", data={"next": "/welcome"})
    assert r.headers["location"].startswith("/welcome?feed_token=")
    r = client.get(r.headers["location"])
    assert "feed link is ready" in r.text
    assert "Continue" in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_welcome.py -k "step_2 or step_3 or step_4" -v`
Expected: `test_welcome_shows_step_2_timezone`,
`test_welcome_shows_step_3_test_dm`, and `test_welcome_shows_step_4_calendar_feed`
FAIL (the template has no content for steps 2-4 yet, so none of those
strings appear). `test_step_2_set_timezone_returns_to_welcome` FAILS on
the second assertion (page renders but doesn't show the new timezone
value yet). `test_step_4_generate_feed_returns_to_welcome_with_link_shown`
FAILS (no `feed_url` context, so "feed link is ready" never renders).

- [ ] **Step 3: Extend the route with steps 2 and 4 context**

In `src/app/web/routes/welcome.py`, update the imports:

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tag, TagSubscription
from app.db.service import ensure_user, group_members
from app.db.session import get_session
from app.domain.types import Anchor, TagKind
from app.web.auth import SessionUser, require_user
from app.web.routes.preferences import my_presets
```

becomes:

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Tag, TagSubscription
from app.db.service import ensure_user, group_members
from app.db.session import get_session
from app.domain.types import Anchor, TagKind
from app.web.auth import SessionUser, require_user
from app.web.routes.preferences import COMMON_TIMEZONES, all_timezones, my_presets
```

Find the `welcome()` route's signature and its `step == 1` branch:

```python
@router.get("/welcome", response_class=HTMLResponse)
async def welcome(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    if db_user.onboarding_step >= TOTAL_STEPS:
        return RedirectResponse("/", status_code=303)

    step = db_user.onboarding_step
    context = {"user": user, "step": step}
```

Replace with:

```python
@router.get("/welcome", response_class=HTMLResponse)
async def welcome(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    feed_token: str = "",
):
    db_user = await ensure_user(session, user.id, user.username)
    if db_user.onboarding_step >= TOTAL_STEPS:
        return RedirectResponse("/", status_code=303)

    step = db_user.onboarding_step
    context = {"user": user, "step": step, "bot_enabled": settings.bot_enabled}
```

Then find the end of the `step == 1` branch:

```python
    elif step == 1:
        presets = await my_presets(session, user.id)
        context.update({"has_preset": bool(presets), "anchors": list(Anchor)})

    return templates.TemplateResponse(request, "welcome.html", context)
```

Replace with:

```python
    elif step == 1:
        presets = await my_presets(session, user.id)
        context.update({"has_preset": bool(presets), "anchors": list(Anchor)})
    elif step == 2:
        context.update({
            "tz": db_user.timezone, "tz_auto": db_user.tz_auto,
            "common_timezones": COMMON_TIMEZONES, "all_timezones": all_timezones(),
        })
    elif step == 4:
        context.update({
            "has_calendar_feed": bool(db_user.calendar_token_hash),
            "feed_url": f"{settings.base_url}/calendar/{feed_token}.ics" if feed_token else None,
        })

    return templates.TemplateResponse(request, "welcome.html", context)
```

(Step 3, the test-DM step, needs no extra context beyond `bot_enabled`,
already added above.)

- [ ] **Step 4: Extend the template with steps 2, 3, and 4**

In `src/app/web/templates/welcome.html`, find the end of the step 1
block:

```html
<form method="post" action="/welcome/advance"><button>{{ "Continue" if has_preset else "Skip this" }}</button></form>
{% endif %}
{% endblock %}
```

Replace with:

```html
<form method="post" action="/welcome/advance"><button>{{ "Continue" if has_preset else "Skip this" }}</button></form>
{% endif %}

{% if step == 2 %}
<h2>Confirm your timezone</h2>
<p class="dim">Deadlines always show in JST plus this timezone.</p>
<p>Detected: <strong>{{ tz }}</strong>{% if tz_auto %} <span class="dim tiny">(auto-detected from your browser)</span>{% endif %}</p>
<form class="inline" method="post" action="/me/timezone">
  <input type="hidden" name="next" value="/welcome">
  <select name="timezone">
    <optgroup label="Common">
      {% for t in common_timezones %}<option value="{{ t }}" {% if t == tz %}selected{% endif %}>{{ t }}</option>{% endfor %}
    </optgroup>
    <optgroup label="All zones (IANA)">
      {% for t in all_timezones %}<option value="{{ t }}" {% if t == tz and t not in common_timezones %}selected{% endif %}>{{ t }}</option>{% endfor %}
    </optgroup>
  </select>
  <button>Use this timezone</button>
</form>
<form method="post" action="/welcome/advance"><button>Looks right, continue</button></form>
{% endif %}

{% if step == 3 %}
<h2>Send a test DM</h2>
<p class="dim">Confirm reminders can actually reach you on Discord before you rely on them.</p>
{% if bot_enabled %}
<p class="inline">
  <button hx-post="/me/test-dm" hx-target="#dm-test-result" hx-swap="innerHTML">Send test DM</button>
  <span id="dm-test-result" class="dim"></span>
</p>
{% else %}
<p class="dim">The Discord bot isn't running in this environment (dev mode), so DMs can't be tested here.</p>
{% endif %}
<form method="post" action="/welcome/advance"><button>Continue</button></form>
{% endif %}

{% if step == 4 %}
<h2>Get your calendar feed</h2>
<p class="dim">Subscribe once in your phone/calendar app and every deadline you have a
  reminder for stays in sync automatically.</p>
{% if feed_url %}
<div class="panel">
  <p><strong>Your feed link is ready — save it now, it won't be shown again:</strong></p>
  <p class="inline"><code class="feed-url">{{ feed_url }}</code>
    <button type="button" class="quiet" data-copy="{{ feed_url }}"
            onclick="navigator.clipboard.writeText(this.dataset.copy)">Copy</button></p>
</div>
{% elif has_calendar_feed %}
<p class="dim">Your feed is active.</p>
{% else %}
<form method="post" action="/me/calendar-feed">
  <input type="hidden" name="next" value="/welcome">
  <button>Generate feed link</button>
</form>
{% endif %}
<form method="post" action="/welcome/advance"><button>{{ "Continue" if has_calendar_feed else "Skip this" }}</button></form>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_welcome.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full suite and ruff, then commit**

```bash
uv run pytest -q
uv run ruff check .
git add src/app/web/routes/welcome.py src/app/web/templates/welcome.html tests/test_welcome.py
git commit -m "Add wizard steps 2-4 (timezone, test DM, calendar feed)"
```

---

### Task 6: Final step — docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `WISHLIST.md`

- [ ] **Step 1: Update CLAUDE.md**

Run `uv run pytest -q` and read the final passing count from its output
(do not hard-code a guessed number — earlier work on this project has
gone stale between when a task was planned and when it actually ran).
Update the intro sentence in `CLAUDE.md` (currently "305 tests as of this
writing (past the Phase 12 roadmap in README.md — ... have shipped
since)") to that real count, and append to the shipped-features list:
"and a first-run guided setup wizard sequencing tag subscriptions, a
default preset, timezone confirmation, a test DM, and the calendar feed
for brand-new logins".

- [ ] **Step 2: Update WISHLIST.md**

Move the "First-run guided setup" entry from `## Proposed` to `##
Shipped` with today's date and a summary covering what actually shipped:
the `/welcome` wizard, the `onboarding_step` column, the OAuth-callback
new-user redirect, and the `next`-redirect param added to the reused
routes. Note in the same edit that `## Proposed` is now empty — the next
feature idea raised in any future discussion starts a fresh section
rather than finding a ranking to slot into.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md WISHLIST.md
git commit -m "Update CLAUDE.md and WISHLIST.md for first-run-guided-setup"
```
