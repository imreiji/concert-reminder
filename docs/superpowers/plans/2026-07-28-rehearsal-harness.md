# Rehearsal Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local-only admin page that seeds one canonical concert, pulls its reminders forward, and sends every DM shape on demand — so the whole user flow, all 5 anchors and all 11 buttons, can be walked in minutes against a dev Discord bot.

**Architecture:** A `rehearsal_enabled` config flag gates registration of a dedicated `routes/rehearsal.py` router, so on production the routes do not exist at all. The harness seeds real rows through the real service layer and lets the real planner compute fire times, then rewrites unsent queue rows' `fire_at_utc` into the past so the real 60s tick delivers immediately. Nothing about the planner, the queue or the send path is faked.

**Tech Stack:** SQLAlchemy 2.0 async + Alembic (SQLite/WAL), FastAPI + Jinja2, discord.py, pytest-asyncio auto mode.

**Spec:** `docs/superpowers/specs/2026-07-28-rehearsal-harness-design.md`

**No migration.** This feature adds no columns and no tables — that was the point of retargeting it from production to a local dev bot.

## Global Constraints

The first six were learned the hard way across sub-projects B and C. Do not rediscover them.

- **Migrations live in `alembic/versions/`** — not applicable here (no migration), but noted so nobody adds one out of habit.
- **New imports go in the TOP-OF-FILE block, never appended mid-file.** Ruff selects `E`, which includes E402. Steps saying "append to `tests/<file>.py`" with an import in the snippet mean: hoist the import, append only the test.
- **`Round.kind` is NOT NULL with no default.** Every seeded `Round(...)` needs a `kind=`.
- **`templates` is NOT imported.** There is no `app.web.templating`. Every route module declares `templates = None  # set by web.app at startup` and `create_app()` assigns `<module>.templates = templates` before `include_router`. Match that idiom.
- **CSS classes that exist:** `.tagtable` (with a `.r` right-align modifier), `.dim`. `.tablewrap` and `.muted` do NOT. Do not invent tokens for an admin page.
- **`gettext_in` is NOT a babel extraction keyword.** A bare literal passed to it never reaches the catalogues. If you pass a literal to `gettext_in`, wrap it in `N_()`.
- This page is **English-only, NOT wrapped in `_()`** (the `/me/test-dm`, `/admin/deliveries` and `/admin/broadcast` precedent). It renders only on a developer machine.
- DB test fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener.
- Gates before every commit: `uv run --isolated ruff check .` clean AND `uv run --isolated pytest -q` passing. **Run the full suite with `timeout: 600000` and `run_in_background: false`** — the Bash tool's 120s default silently backgrounds a 4-minute suite and strands the run. **Baseline at branch point: 1549 passed, 0 failed.**
- Commit messages take the `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_015QGCFscyzpVhVVMFJtBbEA` trailers. Use a bash heredoc or repeated `-m` flags — **never PowerShell here-string syntax in the Bash tool**.

## Two decisions the spec left implicit

1. **The rehearsal concert is identified by a fixed `event_id`: `"rehearsal"`.** The spec dropped the `Concert.rehearsal` column when it retargeted to local, without naming a replacement. A constant `event_id` is the cheapest one, and it satisfies the spec's own rule — "the pull-forward action resolves its queue rows through the rehearsal concert, never by queue id from a form field" — by construction, because there is no id to pass. `"rehearsal"` is deliberately NOT added to `RESERVED_EVENT_IDS`: that set exists to stop collisions with the `/concerts/new` and `/concerts/import` routes, and there is no `/concerts/rehearsal` route.
2. **The harness gets its own router module,** `web/routes/rehearsal.py`, not a section of `admin.py`. `admin.py` now serves `/admin/deliveries` and `/admin/broadcast`, which must exist in production; a router can only be registered whole, so the gated routes need their own.

**Open question answered:** the shape catalogue DOES get a locale picker (Task 5). `set_locale` makes it nearly free and it turns the eight-shape walk into the fastest ja/zh copy review the project has.

---

### Task 1: The flag, the router, and the empty page

The safety model, before anything with write power exists.

**Files:**
- Modify: `src/app/config.py`
- Create: `src/app/web/routes/rehearsal.py`
- Create: `src/app/web/templates/rehearsal.html`
- Modify: `src/app/web/app.py`
- Test: `tests/test_rehearsal.py` (new)

**Interfaces:**
- Produces: `settings.rehearsal_enabled: bool = False`; `GET /admin/rehearsal`; `rehearsal.templates` assigned by `create_app`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_rehearsal.py`. Copy the `db`/`client` fixture pair and `login_as` from `tests/test_admin_broadcast.py` — note `login_as` CREATES the user row, so do not also seed the admin.

```python
"""The local rehearsal harness. Gated off in production by config."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.web.app import create_app

ADMIN_ID, PLAIN_ID = 42, 777


def test_the_router_is_not_registered_when_the_flag_is_off(monkeypatch):
    """THE safety model, asserted directly. With the flag off the route must
    not exist at all -- not 403, not 404-from-a-guard, but absent from the
    application's route table. Production never sets the flag, so a
    'pull every reminder forward' button is unreachable by construction
    rather than by a permission check somebody could get wrong."""
    monkeypatch.setattr(settings, "rehearsal_enabled", False)
    paths = {r.path for r in create_app().routes}
    assert "/admin/rehearsal" not in paths


def test_the_router_is_registered_when_the_flag_is_on(monkeypatch):
    monkeypatch.setattr(settings, "rehearsal_enabled", True)
    paths = {r.path for r in create_app().routes}
    assert "/admin/rehearsal" in paths


def test_the_flag_defaults_to_off():
    """A developer opts in; nobody opts out."""
    assert settings.model_fields["rehearsal_enabled"].default is False


def test_page_renders_for_an_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/rehearsal")
    assert r.status_code == 200
    assert "Rehearsal" in r.text


def test_a_signed_in_non_admin_gets_403(client):
    """require_admin stays on the routes as a second layer, in case a deploy
    is ever misconfigured with the flag on."""
    login_as(client, PLAIN_ID, "someone")
    assert client.get("/admin/rehearsal").status_code == 403
```

The `client` fixture must build its app with the flag ON. Add
`monkeypatch.setattr(settings, "rehearsal_enabled", True)` **before**
`create_app()` inside the copied fixture.

- [x] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_rehearsal.py -v`
Expected: FAIL — `rehearsal_enabled` is not a settings field.

- [x] **Step 3: Add the setting**

In `src/app/config.py`, next to `dev_guild_id`:

```python
    # Local-only harness switch. When true, web/app.py registers the rehearsal
    # router; production never sets it, so those routes do not exist there at
    # all -- no auth surface, no accidental "fire every reminder now" button.
    # Same shape as bot_enabled: one config value switching a subsystem off.
    rehearsal_enabled: bool = False
```

- [x] **Step 4: Add the router module**

Create `src/app/web/routes/rehearsal.py`:

```python
"""The local rehearsal harness: seed a canonical concert, pull its reminders
forward, and send every DM shape on demand.

Registered ONLY when `settings.rehearsal_enabled` is true, which production
never sets -- see web/app.py. `require_admin` is a second layer for a
misconfigured deploy, not the primary guard.

Its own module rather than a section of admin.py: a router registers whole,
and admin.py serves /admin/deliveries and /admin/broadcast, which must exist
in production.

English-only and not wrapped in _(), following /me/test-dm and
/admin/deliveries -- this page only ever renders on a developer machine.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.web.auth import SessionUser, require_admin

router = APIRouter()
templates = None  # set by web.app at startup


@router.get("/admin/rehearsal", response_class=HTMLResponse)
async def rehearsal(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "rehearsal.html", {"user": user, "state": None}
    )
```

- [x] **Step 5: Add the template**

Create `src/app/web/templates/rehearsal.html`:

```html
{% extends "base.html" %}
{% block title %}Rehearsal — dekimasen.app{% endblock %}
{% block content %}
<h1>Rehearsal</h1>
<p class="dim">
  Local harness. Seeds one concert, pulls its reminders forward so the real
  60-second tick delivers them now, and sends every DM shape on demand.
</p>
{% endblock %}
```

- [x] **Step 6: Register it conditionally**

In `src/app/web/app.py`, alongside the other route imports:

```python
from app.web.routes import rehearsal as rehearsal_routes
```

and after the admin router is registered:

```python
    # Gated, not guarded: production leaves rehearsal_enabled false, so these
    # routes are absent from the app entirely rather than merely protected.
    if settings.rehearsal_enabled:
        rehearsal_routes.templates = templates
        app.include_router(rehearsal_routes.router)
```

Confirm `settings` is already imported in `app.py`; add it if not.

- [x] **Step 7: Run to verify they pass**

Run: `uv run --isolated pytest tests/test_rehearsal.py -v`
Expected: PASS (all five).

- [x] **Step 8: Full gates and commit**

```bash
git add src/app/config.py src/app/web/routes/rehearsal.py src/app/web/templates/rehearsal.html src/app/web/app.py tests/test_rehearsal.py
git commit -m "feat: gate a local rehearsal harness behind rehearsal_enabled

The route does not exist in production rather than being guarded there.
A 'pull every reminder forward' button protected only by a permission
check is one misconfiguration away from firing real reminders early;
absent from the route table, it is unreachable by construction. Its own
router module because a router registers whole and admin.py serves
routes production needs."
```

---

### Task 2: Seed and teardown

**Files:**
- Modify: `src/app/db/service.py` (new section at the end)
- Test: `tests/test_rehearsal.py`

**Interfaces:**
- Produces: `REHEARSAL_EVENT_ID = "rehearsal"`; `async def seed_rehearsal(session, user_id, now=None) -> Concert`; `async def teardown_rehearsal(session) -> bool`; `async def get_rehearsal_concert(session) -> Concert | None`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_rehearsal.py` (imports hoisted):

```python
@pytest.mark.asyncio
async def test_seed_builds_the_canonical_scenario(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()

        assert concert.event_id == REHEARSAL_EVENT_ID
        days = (await s.execute(select(ConcertDay).where(
            ConcertDay.concert_id == concert.id).order_by(ConcertDay.starts_at_utc))).scalars().all()
        assert len(days) == 2
        rounds = (await s.execute(select(Round).where(
            Round.concert_id == concert.id))).scalars().all()
        assert len(rounds) == 3
        kinds = {r.kind for r in rounds}
        assert kinds == {RoundKind.LOTTERY_ROUND, RoundKind.FCFS_SALE, RoundKind.UPGRADE}


@pytest.mark.asyncio
async def test_the_lottery_round_carries_all_four_anchors_and_both_legs(db):
    """One round yields the whole ladder, and spanning two legs is what
    exercises the per-day RoundOutcomeDay materialization."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        r1 = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.LOTTERY_ROUND))).scalar_one()
        assert r1.opens_at_utc and r1.closes_at_utc
        assert r1.results_at_utc and r1.payment_deadline_at_utc
        assert len(r1.applies_to) == 2


@pytest.mark.asyncio
async def test_the_upgrade_round_qualifies_on_the_lottery_round(db):
    """Before a WON on R1 the viewer is ineligible; after it, eligible. That
    gate is what this round exists to prove end to end."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        upgrade = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.UPGRADE))).scalar_one()
        lottery = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.LOTTERY_ROUND))).scalar_one()
        pairs = (await s.execute(select(RoundQualifier).where(
            RoundQualifier.upgrade_round_id == upgrade.id))).scalars().all()
        assert [p.qualifying_round_id for p in pairs] == [lottery.id]


@pytest.mark.asyncio
async def test_seed_queues_reminders_through_the_real_planner(db):
    """The point of seeding real rules: sync_rule and the pure planner compute
    the fire times, so what the harness later pulls forward is a genuine
    plan, not a fabricated row."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        queued = (await s.execute(select(ReminderQueue))).scalars().all()
        anchors = {q.anchor for q in queued}
        assert Anchor.OPENS in anchors
        assert Anchor.CLOSES in anchors
        assert Anchor.RESULTS in anchors
        assert Anchor.PAYMENT in anchors
        assert Anchor.EVENT_START in anchors


@pytest.mark.asyncio
async def test_seed_is_idempotent(db):
    """Start twice leaves ONE rehearsal concert -- the harness reseeds from a
    clean slate rather than accumulating."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        concerts = (await s.execute(select(Concert).where(
            Concert.event_id == REHEARSAL_EVENT_ID))).scalars().all()
        assert len(concerts) == 1


@pytest.mark.asyncio
async def test_teardown_removes_the_concert_but_not_the_user(db):
    """Cascades take the days, rounds, queue rows and outcomes. Users,
    presets and subscriptions are never touched."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        assert await teardown_rehearsal(s) is True
        await s.commit()
        assert await get_rehearsal_concert(s) is None
        assert (await s.execute(select(ReminderQueue))).scalars().all() == []
        assert await s.get(User, ADMIN_ID) is not None


@pytest.mark.asyncio
async def test_teardown_with_nothing_seeded_is_a_no_op(db):
    async with db() as s:
        assert await teardown_rehearsal(s) is False
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_rehearsal.py -v`
Expected: FAIL — `ImportError: cannot import name 'seed_rehearsal'`

- [x] **Step 3: Implement**

Append a new section to `src/app/db/service.py`:

```python
# ── Rehearsal harness (local only) ───────────────────────────────────────

# The rehearsal concert is identified by a constant event_id rather than a
# column. That is deliberate: it means the pull-forward action can only ever
# reach THIS concert's queue rows, because there is no id for a caller to
# pass. Not added to RESERVED_EVENT_IDS -- that set exists to stop collisions
# with the /concerts/new and /concerts/import routes, and there is no
# /concerts/rehearsal route.
REHEARSAL_EVENT_ID = "rehearsal"


async def get_rehearsal_concert(session: AsyncSession) -> Concert | None:
    res = await session.execute(
        select(Concert).where(Concert.event_id == REHEARSAL_EVENT_ID)
    )
    return res.scalar_one_or_none()


async def teardown_rehearsal(session: AsyncSession) -> bool:
    """Delete the rehearsal concert. Returns whether one existed.

    Deletes the Concert row only and lets the existing cascades take days,
    rounds, queue rows, outcomes and audits. It never touches users, presets
    or subscriptions -- those are the operator's real local state.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return False
    await session.delete(concert)
    await session.flush()
    return True


async def seed_rehearsal(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> Concert:
    """Build the canonical scenario, replacing any previous one.

    Two legs, three rounds, and one reminder rule per anchor. Anchors are set
    at realistic future distances and the rules are real, so `sync_concert`
    and the pure planner compute genuine fire times -- the harness later pulls
    those rows forward rather than fabricating them.
    """
    now = now or _now()
    await teardown_rehearsal(session)

    concert = Concert(
        event_id=REHEARSAL_EVENT_ID,
        title="リハーサル公演",
        title_en="Rehearsal Concert",
        title_zh="彩排演出",
        created_by=user_id,
    )
    session.add(concert)
    await session.flush()

    day1 = ConcertDay(
        concert_id=concert.id,
        label="Day 1",
        label_en="Day 1",
        label_zh="第一天",
        starts_at_utc=now + timedelta(days=30),
    )
    day2 = ConcertDay(
        concert_id=concert.id,
        label="Day 2",
        label_en="Day 2",
        label_zh="第二天",
        starts_at_utc=now + timedelta(days=31),
    )
    session.add_all([day1, day2])
    await session.flush()

    # R1 carries all four anchors and both legs: the whole ladder from one
    # round, and a WON on it exercises the per-day RoundOutcomeDay
    # materialization that a single-leg round never reaches.
    lottery = Round(
        concert_id=concert.id,
        kind=RoundKind.LOTTERY_ROUND,
        label="一次先行",
        label_en="1st lottery",
        label_zh="第一轮抽选",
        applies_to=[day1.id, day2.id],
        opens_at_utc=now + timedelta(days=1),
        closes_at_utc=now + timedelta(days=7),
        results_at_utc=now + timedelta(days=10),
        payment_deadline_at_utc=now + timedelta(days=14),
    )
    # R2 exists to prove SUPPRESSION: once R1 is won on Day 1, the
    # secured-elsewhere pass should silently delete this round's reminders.
    # A round that stops arriving is the hardest thing to notice by hand.
    fcfs = Round(
        concert_id=concert.id,
        kind=RoundKind.FCFS_SALE,
        label="一般発売",
        label_en="General sale",
        label_zh="一般发售",
        applies_to=[day1.id],
        opens_at_utc=now + timedelta(days=3),
        closes_at_utc=now + timedelta(days=8),
    )
    # R3 is invisible until the viewer holds a ticket -- the eligibility gate,
    # proven end to end rather than by unit test.
    upgrade = Round(
        concert_id=concert.id,
        kind=RoundKind.UPGRADE,
        label="アップグレード先行",
        label_en="Upgrade lottery",
        label_zh="升级抽选",
        applies_to=[day1.id, day2.id],
        opens_at_utc=now + timedelta(days=11),
        closes_at_utc=now + timedelta(days=13),
    )
    session.add_all([lottery, fcfs, upgrade])
    await session.flush()

    session.add(
        RoundQualifier(upgrade_round_id=upgrade.id, qualifying_round_id=lottery.id)
    )

    # Track it explicitly rather than via a tag: tracked_concert_ids treats an
    # explicit subscription as authoritative, so the harness does not depend
    # on the operator following anything.
    await set_concert_subscription(session, user_id, concert.id, SubscriptionState.SUBSCRIBED)

    # One rule per anchor, at zero offset. The offset is irrelevant to what
    # this proves -- pull-forward moves the fire time regardless -- and zero
    # keeps the seeded plan legible in the harness's own state table.
    for anchor in (Anchor.OPENS, Anchor.CLOSES, Anchor.RESULTS, Anchor.PAYMENT):
        session.add(
            ReminderRule(
                user_id=user_id, concert_id=concert.id, anchor=anchor,
                offset_days=0, offset_hours=0,
            )
        )
    session.add(
        ReminderRule(
            user_id=user_id, concert_id=concert.id, anchor=Anchor.EVENT_START,
            offset_days=0, offset_hours=0,
        )
    )
    await session.flush()

    await sync_concert(session, concert.id)
    return concert
```

Check what `service.py` already imports before adding: `RoundQualifier`, `SubscriptionState`, `timedelta` may or may not be present.

- [x] **Step 4: Run to verify they pass**

Run: `uv run --isolated pytest tests/test_rehearsal.py -v`
Expected: PASS.

If `test_seed_queues_reminders_through_the_real_planner` fails on a missing anchor, do NOT relax the assertion — it means the seeded rounds or rules do not actually produce that anchor, which is the harness failing at its one job. Read `plan_for_rule` and fix the seed.

- [x] **Step 5: Full gates and commit**

```bash
git add src/app/db/service.py tests/test_rehearsal.py
git commit -m "feat: seed and tear down the canonical rehearsal scenario

Two legs and three rounds chosen so one walk reaches every anchor and
every button: R1 carries all four anchors across both legs (the whole
ladder, plus the per-day outcome materialization), R2 exists to prove
suppression stops it arriving once R1 is won, R3 proves the upgrade
eligibility gate. Rules are real and sync_concert plans them, so what the
harness pulls forward is a genuine plan rather than a fabricated row."
```

---

### Task 3: Pull forward, and cancel a leg

**Files:**
- Modify: `src/app/db/service.py` (rehearsal section)
- Test: `tests/test_rehearsal.py`

**Interfaces:**
- Produces: `async def rehearsal_queue_rows(session) -> list[ReminderQueue]`; `async def pull_rehearsal_forward(session, now=None) -> ReminderQueue | None`; `async def cancel_rehearsal_show(session, now=None) -> int`.

- [x] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_pull_forward_moves_the_soonest_unsent_row_into_the_past(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        before = sorted(
            (await s.execute(select(ReminderQueue))).scalars().all(),
            key=lambda q: q.fire_at_utc,
        )
        pulled = await pull_rehearsal_forward(s)
        await s.commit()
        assert pulled is not None
        assert pulled.id == before[0].id
        assert pulled.fire_at_utc < datetime.now(UTC)


@pytest.mark.asyncio
async def test_pull_forward_never_touches_another_concert_s_rows(db):
    """The spec's hard rule. There is no queue id parameter, so the only rows
    reachable are the rehearsal concert's -- a harness that could fire an
    arbitrary reminder early is the version of this feature worth designing
    out."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        other = Concert(event_id="real", title="Real", title_en="Real")
        s.add(other)
        await s.flush()
        day = ConcertDay(concert_id=other.id, label="D",
                         starts_at_utc=datetime.now(UTC) + timedelta(days=5))
        s.add(day)
        await s.flush()
        rule = ReminderRule(user_id=ADMIN_ID, concert_id=other.id,
                            anchor=Anchor.EVENT_START, offset_days=0, offset_hours=0)
        s.add(rule)
        await s.flush()
        far = datetime.now(UTC) + timedelta(days=5)
        s.add(ReminderQueue(rule_id=rule.id, day_id=day.id,
                            anchor=Anchor.EVENT_START, fire_at_utc=far))
        await s.commit()

        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        for _ in range(10):
            if await pull_rehearsal_forward(s) is None:
                break
            await s.commit()

        untouched = (await s.execute(select(ReminderQueue).where(
            ReminderQueue.day_id == day.id))).scalar_one()
        assert untouched.fire_at_utc == far


@pytest.mark.asyncio
async def test_pull_forward_skips_rows_already_sent(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        rows = sorted((await s.execute(select(ReminderQueue))).scalars().all(),
                      key=lambda q: q.fire_at_utc)
        rows[0].sent_at_utc = datetime.now(UTC)
        await s.commit()
        pulled = await pull_rehearsal_forward(s)
        await s.commit()
        assert pulled.id == rows[1].id


@pytest.mark.asyncio
async def test_pull_forward_returns_none_when_everything_is_sent(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        for row in (await s.execute(select(ReminderQueue))).scalars():
            row.sent_at_utc = datetime.now(UTC)
        await s.commit()
        assert await pull_rehearsal_forward(s) is None


@pytest.mark.asyncio
async def test_cancelling_a_leg_queues_the_cancellation_notice(db):
    """notify_newly_cancelled_legs must run BEFORE sync_concert, which deletes
    the queue rows it inspects. Get that order wrong and the notice is silent."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        n = await cancel_rehearsal_show(s)
        await s.commit()
        assert n >= 1
        notes = (await s.execute(select(Notification).where(
            Notification.kind == "leg_cancelled"))).scalars().all()
        assert len(notes) == 1
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_rehearsal.py -v`
Expected: FAIL — `ImportError: cannot import name 'pull_rehearsal_forward'`

- [x] **Step 3: Implement**

Append to the rehearsal section of `src/app/db/service.py`:

```python
async def rehearsal_queue_rows(session: AsyncSession) -> list[ReminderQueue]:
    """Every queue row belonging to the rehearsal concert, soonest first.

    Scoped by joining through the concert's rounds and days rather than by an
    id the caller supplies -- see REHEARSAL_EVENT_ID's note.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return []
    round_ids = set(
        (await session.execute(
            select(Round.id).where(Round.concert_id == concert.id)
        )).scalars()
    )
    day_ids = set(
        (await session.execute(
            select(ConcertDay.id).where(ConcertDay.concert_id == concert.id)
        )).scalars()
    )
    if not round_ids and not day_ids:
        return []
    res = await session.execute(
        select(ReminderQueue)
        .where(
            or_(
                ReminderQueue.round_id.in_(round_ids) if round_ids else false(),
                ReminderQueue.day_id.in_(day_ids) if day_ids else false(),
            )
        )
        .order_by(ReminderQueue.fire_at_utc)
    )
    return list(res.scalars())


async def pull_rehearsal_forward(
    session: AsyncSession, now: datetime | None = None
) -> ReminderQueue | None:
    """Rewrite the soonest UNSENT rehearsal queue row's fire time into the
    past, so the next real tick delivers it. Returns the row, or None.

    This is the only thing the harness fakes, and it fakes the wait, not the
    work: sync_rule and the pure planner already computed this row and its
    anchor. Everything downstream -- suppression, gating, the send path, the
    buttons -- runs exactly as in production.
    """
    now = now or _now()
    for row in await rehearsal_queue_rows(session):
        if row.sent_at_utc is None:
            row.fire_at_utc = now - timedelta(seconds=1)
            await session.flush()
            return row
    return None


async def cancel_rehearsal_show(
    session: AsyncSession, now: datetime | None = None
) -> int:
    """Cancel the rehearsal concert's LAST leg and queue the notices.

    Order is load-bearing: notify_newly_cancelled_legs must run BEFORE
    sync_concert, which deletes the very queue rows it inspects to decide who
    is losing a reminder.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return 0
    res = await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id, ConcertDay.cancelled.is_(False))
        .order_by(ConcertDay.starts_at_utc.desc())
    )
    leg = res.scalars().first()
    if leg is None:
        return 0
    leg.cancelled = True
    await session.flush()
    queued = await notify_newly_cancelled_legs(session, concert.id, {leg.id}, now)
    await sync_concert(session, concert.id)
    return queued
```

Ensure `or_` and `false` are imported from `sqlalchemy`.

- [x] **Step 4: Run to verify they pass**

Run: `uv run --isolated pytest tests/test_rehearsal.py -v`
Expected: PASS.

- [x] **Step 5: Prove the scoping is real**

`test_pull_forward_never_touches_another_concert_s_rows` is the safety test. Prove it bites: temporarily change `pull_rehearsal_forward` to select the soonest unsent row in the WHOLE table (ignoring `rehearsal_queue_rows`), confirm that test FAILS, then restore. Report what you saw.

- [x] **Step 6: Full gates and commit**

```bash
git add src/app/db/service.py tests/test_rehearsal.py
git commit -m "feat: pull rehearsal reminders forward, and cancel a leg

Pull-forward fakes the WAIT, not the work: the planner already computed
the row and its anchor, and everything downstream -- suppression, gating,
the send path, the buttons -- runs as in production. Scoped by joining
through the rehearsal concert rather than by a caller-supplied id, so
there is no way to reach another concert's rows. Leg cancellation runs
notify_newly_cancelled_legs before sync_concert, which deletes the rows
that function inspects."
```

---

### Task 4: The page — state, actions, and the oracle

**Files:**
- Modify: `src/app/web/routes/rehearsal.py`, `src/app/web/templates/rehearsal.html`
- Create: `src/app/domain/rehearsal.py`
- Test: `tests/test_rehearsal.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: `domain/rehearsal.py:expected_buttons(anchor, outcome) -> tuple[str, ...]`; routes `POST /admin/rehearsal/start|next|cancel-show|end`.

- [x] **Step 1: Write the failing tests for the oracle**

`expected_buttons` is pure, so test it directly:

```python
def test_expected_buttons_match_the_anchor_and_outcome_gating():
    """The page names what SHOULD appear on the row it just pulled. Without
    this the harness is a trigger; with it, an oracle -- it distinguishes
    'no button rendered' from 'wrong button rendered', which is the whole
    difference between watching DMs arrive and testing them."""
    assert expected_buttons(Anchor.CLOSES, None) == ("applied", "notapplied", "remindlater")
    assert expected_buttons(Anchor.RESULTS, None) == ("won", "lost")
    assert expected_buttons(Anchor.RESULTS, LotteryOutcome.APPLIED) == ("won", "lost")
    assert expected_buttons(Anchor.PAYMENT, LotteryOutcome.WON) == ("paid",)
    assert expected_buttons(Anchor.PAYMENT, LotteryOutcome.LOST) == ("snooze",)
    assert expected_buttons(Anchor.OPENS, None) == ("snooze",)
    assert expected_buttons(Anchor.EVENT_START, None) == ("snooze",)
```

Then the route tests:

```python
def test_start_seeds_and_end_tears_down(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    assert client.post("/admin/rehearsal/start").status_code == 303
    page = client.get("/admin/rehearsal")
    assert "Rehearsal Concert" in page.text
    assert client.post("/admin/rehearsal/end").status_code == 303
    assert "Rehearsal Concert" not in client.get("/admin/rehearsal").text


def test_next_reports_what_it_pulled_and_what_to_expect(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/rehearsal/start")
    client.post("/admin/rehearsal/next")
    page = client.get("/admin/rehearsal")
    assert "opens" in page.text.lower()


def test_the_actions_are_admin_only(client):
    login_as(client, PLAIN_ID, "someone")
    for path in ("start", "next", "cancel-show", "end"):
        assert client.post(f"/admin/rehearsal/{path}").status_code == 403
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_rehearsal.py -v`
Expected: FAIL — no `app.domain.rehearsal`, 404 on the action routes.

- [x] **Step 3: Write the pure oracle**

Create `src/app/domain/rehearsal.py`:

```python
"""What the harness EXPECTS to see, as a pure function.

`bot/messages.py:build_reminder_message` decides which buttons a reminder DM
carries from its anchor and the viewer's current outcome. Restating that here
is deliberate duplication: an oracle that derived its expectation from the
code under test would agree with it however wrong that code became.
"""

from app.domain.types import Anchor, LotteryOutcome


def expected_buttons(
    anchor: Anchor, outcome: LotteryOutcome | None
) -> tuple[str, ...]:
    """The custom_id stems a correct DM should carry for this row."""
    if anchor is Anchor.CLOSES and outcome is None:
        return ("applied", "notapplied", "remindlater")
    if anchor is Anchor.RESULTS and outcome in (None, LotteryOutcome.APPLIED):
        return ("won", "lost")
    if anchor is Anchor.PAYMENT and outcome is LotteryOutcome.WON:
        return ("paid",)
    return ("snooze",)
```

- [x] **Step 4: Add the four actions and the state context**

In `rehearsal.py`, extend the GET to build state from `rehearsal_queue_rows` (each row with its anchor, its round or leg label, its fire time, whether it is sent, and — for the soonest unsent — `expected_buttons`), and add:

```python
@router.post("/admin/rehearsal/start")
async def start(user=Depends(require_admin), session=Depends(get_session)):
    await seed_rehearsal(session, user.id)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/next")
async def next_reminder(user=Depends(require_admin), session=Depends(get_session)):
    await pull_rehearsal_forward(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/cancel-show")
async def cancel_show(user=Depends(require_admin), session=Depends(get_session)):
    await cancel_rehearsal_show(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/end")
async def end(user=Depends(require_admin), session=Depends(get_session)):
    await teardown_rehearsal(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)
```

- [x] **Step 5: Extend the template**

Add the prescribed walk as a static ordered list (steps 1-9 from the spec), the four action buttons, and a `.tagtable` of queue rows: anchor, round/leg, fire time, sent, and for the next-to-fire row the expected buttons. Reuse `.dim` for secondary text. No new CSS.

- [x] **Step 6: Run to verify they pass**

Run: `uv run --isolated pytest tests/test_rehearsal.py -v`
Expected: PASS.

- [x] **Step 7: Full gates and commit**

```bash
git add src/app/domain/rehearsal.py src/app/web/routes/rehearsal.py src/app/web/templates/rehearsal.html tests/test_rehearsal.py
git commit -m "feat: the rehearsal page, with an expected-buttons oracle

The page names the buttons the DM it just triggered SHOULD carry.
Without that the harness only triggers messages; with it, it tests them
-- 'no button rendered' and 'wrong button rendered' stop looking alike.
The expectation is restated in domain/rehearsal.py rather than derived
from bot/messages.py on purpose: an oracle that read the code under test
would agree with it however wrong it became."
```

---

### Task 5: The shape catalogue

**Files:**
- Modify: `src/app/web/routes/rehearsal.py`, `src/app/web/templates/rehearsal.html`
- Test: `tests/test_rehearsal.py`

**Interfaces:**
- Produces: `POST /admin/rehearsal/shape` taking `shape` and `locale` form fields.

- [x] **Step 1: Write the failing test**

```python
def test_the_shape_catalogue_sends_the_chosen_shape(client, monkeypatch):
    """Independent of the pipeline half: renders a builder directly under a
    chosen locale, so a copy or translation change can be re-checked in
    seconds without constructing the state a real DM needs."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "x")
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/rehearsal/start")

    sent = []

    class FakeUser:
        async def send(self, *a, **kw):
            sent.append(kw)

    class FakeBot:
        def get_user(self, _uid):
            return FakeUser()

    import app.bot.client as bot_mod
    monkeypatch.setattr(bot_mod, "bot", FakeBot())

    r = client.post("/admin/rehearsal/shape", data={"shape": "reminder_closes", "locale": "ja"})
    assert r.status_code == 303
    assert len(sent) == 1


def test_the_shape_catalogue_needs_the_bot(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "")
    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/rehearsal/shape", data={"shape": "reminder_closes", "locale": "en"})
    assert r.status_code == 303  # reports, does not crash
```

- [x] **Step 2: Run to verify it fails, then implement**

Add a `POST /admin/rehearsal/shape` route that:
- refuses politely when `not settings.bot_enabled` (redirect with a note, like `/me/test-dm` does),
- calls `i18n.set_locale(locale)` for the chosen locale, then resets to `"en"` in a `finally`,
- dispatches on `shape` to build one of: `build_reminder_message` for each of the five anchors (using a `DueReminder` constructed from the seeded concert), `build_new_event_message` via `notice_context`, `build_leg_cancelled_message` via `leg_cancelled_context`, and a plain-text `ops_alert` body,
- sends it directly with `bot.get_user(...).send(...)`.

**This direct send is a second deliberate exception to invariant 4**, alongside `POST /me/test-dm`, and for the same reason: it is a manual, user-initiated, local-only diagnostic, not a system-initiated notice. Say so in the route docstring, and note that the route does not exist in production at all, which `/me/test-dm` cannot claim.

- [x] **Step 3: Add the picker to the template**

A `<select>` of the eight shapes plus a `<select>` of en / 中文 / 日本語, and a Send button.

- [x] **Step 4: Full gates and commit**

```bash
git add src/app/web/routes/rehearsal.py src/app/web/templates/rehearsal.html tests/test_rehearsal.py
git commit -m "feat: the shape catalogue, with a locale picker

Renders any of the eight DM shapes directly through the real builders,
under a chosen locale. This is the half that stays useful after every
i18n change: eight embeds in three languages, checked in a minute,
without constructing the state a real delivery needs."
```

---

### Task 6: The docs, and the setup guide the operator needs

**Files:**
- Create: `docs/local-dev-bot.md`
- Modify: `CLAUDE.md`, `WISHLIST.md`, `.env.example` if one exists
- Test: none (docs)

- [ ] **Step 1: Write the setup guide**

Create `docs/local-dev-bot.md` from the spec's "Environment setup" section: the second Discord application, the private test server, `DEV_GUILD_ID`, **the `http://localhost:8000/auth/callback` redirect URI** (the step that fails Discord-side in a way the app never sees), its own `DISCORD_CLIENT_ID`/`SECRET`, and the second Discord account for the new-user flow. Include the full local `.env` block, `REHEARSAL_ENABLED=true` included, and the prescribed nine-step walk from the spec.

- [ ] **Step 2: CLAUDE.md**

Add a short entry under Layout for `src/app/web/routes/rehearsal.py`: local-only, gated by `rehearsal_enabled`, absent in production, and that its shape catalogue is the second sanctioned exception to invariant 4. Cross-reference `docs/local-dev-bot.md`.

- [ ] **Step 3: WISHLIST**

Move the rehearsal-harness entry to Shipped, dated 2026-07-28, recording that it closes the three-part arc and that it was retargeted from production to local mid-design — which removed a schema column, three query filters and a tag convention. Then the full revision pass and its narrative paragraph, in the house voice.

- [ ] **Step 4: Full gates and commit**

```bash
git add docs/local-dev-bot.md CLAUDE.md WISHLIST.md
git commit -m "docs: the local dev-bot setup guide and the harness entry"
```

---

## Self-Review

**Spec coverage.** Safety model (config flag, router gating, the two hard rules) → Task 1 and Task 3. The local DB guidance → Task 6's guide. Pull-forward → Task 3. Control surface → Tasks 1 and 4. Coverage target (5 anchors, 3 notices, 11 buttons) → Task 2's seed and Task 5's catalogue; `ops_alert` is catalogue-only as the spec requires. The canonical scenario and the prescribed walk → Task 2 and Task 4's template. The shape catalogue and its now-answered locale question → Task 5. The new-user flow → Task 6's guide (it needs no code). The production smoke checklist → Task 6's guide. Every spec testing bullet appears as a named test.

**Placeholder scan.** Tasks 4 step 5 and 5 steps 2-3 describe the template and the dispatch rather than giving full code — deliberate: the template's exact markup depends on the state shape built in the same task, and eight builder call signatures are better read from `bot/messages.py` than transcribed here. Every other step has real code.

**Type consistency.** `expected_buttons(anchor, outcome)` returns `tuple[str, ...]` in its definition, its tests and the template. `seed_rehearsal(session, user_id, now=None) -> Concert`, `pull_rehearsal_forward(session, now=None) -> ReminderQueue | None`, `teardown_rehearsal(session) -> bool` and `cancel_rehearsal_show(session, now=None) -> int` match across definitions, tests and routes. `REHEARSAL_EVENT_ID` is the single identifier everywhere.

**One thing left to the implementer:** Task 2's seed calls `set_concert_subscription`, which per invariant 8 re-syncs that user's rules. Confirm the ordering is right — the subscription is written before the rules exist, so `sync_concert` at the end is what actually plans them. If the seeded queue comes back empty, that ordering is the first place to look.
