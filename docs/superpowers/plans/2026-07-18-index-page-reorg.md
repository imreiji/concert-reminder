# Index Page Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the index page into an "Open & upcoming" bucket (concerts with a currently-open round shown first) plus the existing "Upcoming" concerts below it, and add a global, flattened, chronological "things happening soon" deadline list beneath both — bundled with three small, closely-related cancelled-round-awareness fixes.

**Architecture:** A private helper (`_is_round_cancelled`) in `db/service.py` becomes public (`is_round_cancelled`) since three new call sites outside its original two now need it. A new `UpcomingDeadline` dataclass + `upcoming_deadlines()` service function computes the global chronological list (one row per set timestamp field, not per round). The index route gains a `selectinload(Concert.rounds)` eager-load and computes an `open_concert_ids` set in Python; the template splits its single tile loop into two, reusing a new macro for the (unchanged) tile markup so nothing is duplicated.

**Tech Stack:** FastAPI + Jinja2, SQLAlchemy 2.0 async, discord.py.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` must both be clean before every commit.
- "Open right now": a round has `closes_at_utc` set and in the future, AND `opens_at_utc` is either unset or already passed. Results-only/payment-only rounds are never "open" on their own. The round must also not be implicitly cancelled (`is_round_cancelled`).
- The new global deadline-list label vocabulary is deliberately separate from `bot/messages.py`'s `ANCHOR_VERB` (that dict only covers OPENS/CLOSES/EVENT_START and is built for reminder-message sentence structure) — do not attempt to unify them.
- Sentence case in all new user-facing copy ("Open & upcoming", not "Open & Upcoming").
- Every new page-rendering code path needs at least one logged-in GET render test.
- Spec reference: `docs/superpowers/specs/2026-07-18-index-page-reorg-design.md`.

---

## Task 1: Public `is_round_cancelled` + `UpcomingDeadline`/`upcoming_deadlines()`

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: `def is_round_cancelled(round_: Round, cancelled_day_ids: set[int]) -> bool` (renamed from `_is_round_cancelled`, same signature/behavior).
- Produces: `@dataclass(frozen=True) class UpcomingDeadline: concert_title: str; event_id: str; label: str; anchor: Anchor; at_utc: datetime; url: str | None = None`
- Produces: `async def upcoming_deadlines(session: AsyncSession, now: datetime | None = None, limit: int = 10) -> list[UpcomingDeadline]`
- Produces: `LABEL_BY_ANCHOR: dict[Anchor, str]` module-level dict.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_service.py`, after the `# ── Cancelled-leg filtering` section's tests (before `# ── Notification on cancel + reinstate`):

```python
# ── Global upcoming-deadlines list (index page) ──────────────────────────


async def seed_deadline_scenarios(s) -> tuple[Concert, Round, ConcertDay]:
    """One concert with a round carrying two set timestamps (to confirm it
    produces two independent rows) plus a live future day; one concert
    that's entirely cancelled; one concert with only a past-dated round."""
    await ensure_user(s, 42, "reiji")

    concert = Concert(title="Two-Timestamp Show", event_id="two-ts", created_by=42)
    s.add(concert)
    await s.flush()
    round_both = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Bundled",
        opens_at_utc=dt(6, 5), closes_at_utc=dt(6, 10),
    )
    day = ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=dt(8, 1, 9))
    s.add_all([round_both, day])

    cancelled_concert = Concert(title="Cancelled Show", event_id="cancelled-show", created_by=42)
    s.add(cancelled_concert)
    await s.flush()
    cancelled_day = ConcertDay(
        concert_id=cancelled_concert.id, label="Day 1", starts_at_utc=dt(8, 5, 9), cancelled=True,
    )
    s.add(cancelled_day)
    await s.flush()
    cancelled_round = Round(
        concert_id=cancelled_concert.id, kind=RoundKind.LOTTERY_ROUND, label="Cancelled Round",
        closes_at_utc=dt(6, 15), applies_to=[cancelled_day.id],
    )
    s.add(cancelled_round)

    past_concert = Concert(title="Past Show", event_id="past-show", created_by=42)
    s.add(past_concert)
    await s.flush()
    past_round = Round(
        concert_id=past_concert.id, kind=RoundKind.LOTTERY_ROUND, label="Past Round",
        closes_at_utc=dt(1, 1),
    )
    s.add(past_round)
    await s.flush()
    return concert, round_both, day


async def test_upcoming_deadlines_one_row_per_set_timestamp(session):
    concert, round_both, day = await seed_deadline_scenarios(session)
    result = await upcoming_deadlines(session, NOW, limit=10)
    pairs = [(e.label, e.anchor) for e in result if e.concert_title == "Two-Timestamp Show"]
    assert (round_both.label, Anchor.OPENS) in pairs
    assert (round_both.label, Anchor.CLOSES) in pairs
    assert (day.label, Anchor.EVENT_START) in pairs


async def test_upcoming_deadlines_excludes_cancelled_and_past(session):
    await seed_deadline_scenarios(session)
    result = await upcoming_deadlines(session, NOW, limit=10)
    titles = {e.concert_title for e in result}
    assert "Cancelled Show" not in titles
    assert "Past Show" not in titles


async def test_upcoming_deadlines_sorted_chronologically_and_truncated(session):
    await seed_deadline_scenarios(session)
    result = await upcoming_deadlines(session, NOW, limit=2)
    assert len(result) == 2
    assert result[0].at_utc <= result[1].at_utc
```

Update the existing import blocks at the top of `tests/test_service.py`:
- `from app.db.service import (...)` gains `is_round_cancelled` is NOT needed here (this test file doesn't call it directly) — instead gains `upcoming_deadlines`.
- No other import changes needed (`Anchor`, `Concert`, `ConcertDay`, `Round`, `RoundKind`, `ensure_user`, `NOW`, `dt` are all already imported/defined in this file).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_service.py -k "upcoming_deadlines" -v`
Expected: FAIL with `ImportError` (the function doesn't exist yet).

- [ ] **Step 3: Rename `_is_round_cancelled` to `is_round_cancelled`**

In `src/app/db/service.py`, the current definition (around line 128):

```python
def _is_round_cancelled(round_: Round, cancelled_day_ids: set[int]) -> bool:
    """A round is implicitly cancelled when every leg it applies to is
    cancelled. A "General" round (empty/None applies_to) is never
    auto-cancelled this way -- it isn't tied to any specific leg."""
    if not round_.applies_to:
        return False
    return all(day_id in cancelled_day_ids for day_id in round_.applies_to)
```

Rename to:

```python
def is_round_cancelled(round_: Round, cancelled_day_ids: set[int]) -> bool:
    """A round is implicitly cancelled when every leg it applies to is
    cancelled. A "General" round (empty/None applies_to) is never
    auto-cancelled this way -- it isn't tied to any specific leg.

    Public (no longer a leading-underscore module-private helper): used
    outside this module too now, by upcoming_rounds/upcoming_deadlines
    (below), the index route (web/app.py), and ShowDeadlinesButton
    (bot/views.py)."""
    if not round_.applies_to:
        return False
    return all(day_id in cancelled_day_ids for day_id in round_.applies_to)
```

Update all 3 existing call sites in this same file (search for `_is_round_cancelled(` — there are 3 call expressions plus 1 comment reference):
- In `sync_rule`'s round_id-specific branch: `rounds = [] if _is_round_cancelled(round_, cancelled_day_ids) else [_round_info(round_)]` → `rounds = [] if is_round_cancelled(round_, cancelled_day_ids) else [_round_info(round_)]`
- In `sync_rule`'s concert-wide branch: `_round_info(r) for r in all_rounds if not _is_round_cancelled(r, cancelled_day_ids)` → `_round_info(r) for r in all_rounds if not is_round_cancelled(r, cancelled_day_ids)`
- In `notify_newly_cancelled_legs`: `and _is_round_cancelled(r, all_cancelled_day_ids)` → `and is_round_cancelled(r, all_cancelled_day_ids)`
- The comment in `sync_rule`'s docstring/comment area referencing `(see _is_round_cancelled)` → `(see is_round_cancelled)`.

- [ ] **Step 4: Add `LABEL_BY_ANCHOR`, `UpcomingDeadline`, and `upcoming_deadlines`**

Add this block to `src/app/db/service.py`, right after `upcoming_rounds` (search for `async def upcoming_rounds`, this goes immediately after its closing `return [(c, r) for c, r in res.all()]` line, before the `# ── Personal calendar feed` comment):

```python
LABEL_BY_ANCHOR: dict[Anchor, str] = {
    Anchor.OPENS: "opens",
    Anchor.CLOSES: "closes",
    Anchor.RESULTS: "results announced",
    Anchor.PAYMENT: "payment due",
    Anchor.EVENT_START: "event",
}


@dataclass(frozen=True)
class UpcomingDeadline:
    """One row on the index page's global chronological "things happening
    soon" list -- every non-cancelled round/day across every concert, one
    entry per SET timestamp field (not one per round: a round with both a
    close and a payment deadline set produces two independent rows).
    Future-only, meant to be sorted soonest-first and truncated to a fixed
    count by the caller."""

    concert_title: str
    event_id: str
    label: str
    anchor: Anchor
    at_utc: datetime
    url: str | None = None


async def upcoming_deadlines(
    session: AsyncSession, now: datetime | None = None, limit: int = 10
) -> list[UpcomingDeadline]:
    """Global (not reminder-rule-scoped, not per-user) chronological
    deadline list for the index page. Reuses is_round_cancelled the same
    way sync_rule/notify_newly_cancelled_legs already do."""
    now = now or _now()
    days = list((await session.execute(select(ConcertDay))).scalars())
    rounds = list((await session.execute(select(Round))).scalars())
    cancelled_day_ids = {d.id for d in days if d.cancelled}
    concert_ids = {d.concert_id for d in days} | {r.concert_id for r in rounds}
    concerts = {
        c.id: c for c in
        (await session.execute(select(Concert).where(Concert.id.in_(concert_ids)))).scalars()
    } if concert_ids else {}

    out: list[UpcomingDeadline] = []
    for d in days:
        if d.cancelled or d.starts_at_utc <= now:
            continue
        concert = concerts.get(d.concert_id)
        if concert is None:
            continue
        out.append(UpcomingDeadline(
            concert_title=concert.title, event_id=concert.event_id, label=d.label,
            anchor=Anchor.EVENT_START, at_utc=d.starts_at_utc,
        ))

    for r in rounds:
        if is_round_cancelled(r, cancelled_day_ids):
            continue
        concert = concerts.get(r.concert_id)
        if concert is None:
            continue
        for anchor, ts in (
            (Anchor.OPENS, r.opens_at_utc),
            (Anchor.CLOSES, r.closes_at_utc),
            (Anchor.RESULTS, r.results_at_utc),
            (Anchor.PAYMENT, r.payment_deadline_at_utc),
        ):
            if ts is None or ts <= now:
                continue
            out.append(UpcomingDeadline(
                concert_title=concert.title, event_id=concert.event_id, label=r.label,
                anchor=anchor, at_utc=ts, url=r.url,
            ))

    out.sort(key=lambda e: e.at_utc)
    return out[:limit]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_service.py -k "upcoming_deadlines" -v`
Expected: `3 passed`

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (should be 281, one more than the 280 on main after PR #22).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_service.py
git commit -m "Add upcoming_deadlines and make is_round_cancelled public"
```

---

## Task 2: `upcoming_rounds` excludes cancelled rounds

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `is_round_cancelled` (Task 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_service.py`, right after the Task 1 tests:

```python
async def test_upcoming_rounds_excludes_implicitly_cancelled_round(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    leg_a.cancelled = True
    await session.flush()
    result = await upcoming_rounds(session, NOW, horizon_days=60)
    round_ids = {r.id for _, r in result}
    assert round_a_only.id not in round_ids  # fully cancelled (its only leg is now cancelled)
    assert round_both.id in round_ids  # leg B still live
    assert round_general.id in round_ids  # never tied to a leg, unaffected
```

Add `upcoming_rounds` to the existing `from app.db.service import (...)` import block in `tests/test_service.py`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_service.py -k "upcoming_rounds_excludes" -v`
Expected: FAIL — `round_a_only` (closes_at_utc within the 60-day horizon, per `seed_two_legs`'s `dt(6, 25)`) is still returned today despite its only leg being cancelled.

- [ ] **Step 3: Fix `upcoming_rounds`**

In `src/app/db/service.py`, replace the current `upcoming_rounds`:

```python
async def upcoming_rounds(
    session: AsyncSession, now: datetime | None = None, horizon_days: int = 14
) -> list[tuple[Concert, Round]]:
    """Rounds opening or closing within the horizon — powers /upcoming."""
    from datetime import timedelta

    now = now or _now()
    end = now + timedelta(days=horizon_days)
    res = await session.execute(
        select(Concert, Round)
        .join(Round, Round.concert_id == Concert.id)
        .where(
            (Round.opens_at_utc.between(now, end))
            | (Round.closes_at_utc.between(now, end))
        )
        .order_by(Round.closes_at_utc.is_(None), Round.closes_at_utc, Round.opens_at_utc)
    )
    return [(c, r) for c, r in res.all()]
```

with:

```python
async def upcoming_rounds(
    session: AsyncSession, now: datetime | None = None, horizon_days: int = 14
) -> list[tuple[Concert, Round]]:
    """Rounds opening or closing within the horizon — powers /upcoming.
    Implicitly-cancelled rounds (every leg they apply to is cancelled) are
    excluded, same rule sync_rule/upcoming_deadlines already use."""
    from datetime import timedelta

    now = now or _now()
    end = now + timedelta(days=horizon_days)
    res = await session.execute(
        select(Concert, Round)
        .join(Round, Round.concert_id == Concert.id)
        .where(
            (Round.opens_at_utc.between(now, end))
            | (Round.closes_at_utc.between(now, end))
        )
        .order_by(Round.closes_at_utc.is_(None), Round.closes_at_utc, Round.opens_at_utc)
    )
    pairs = [(c, r) for c, r in res.all()]
    if not pairs:
        return pairs
    cancelled_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.cancelled.is_(True))
    )).scalars())
    return [(c, r) for c, r in pairs if not is_round_cancelled(r, cancelled_day_ids)]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_service.py -k "upcoming_rounds_excludes" -v`
Expected: `1 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (282).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_service.py
git commit -m "Exclude implicitly-cancelled rounds from upcoming_rounds"
```

---

## Task 3: `ShowDeadlinesButton` marks cancelled entries

**Files:**
- Modify: `src/app/bot/views.py`

**Interfaces:**
- Consumes: `is_round_cancelled` (Task 1).

- [ ] **Step 1: Update the import**

In `src/app/bot/views.py`, add `is_round_cancelled` to the existing `from app.db.service import (...)` block:

```python
from app.db.service import (
    apply_default_preset,
    is_round_cancelled,
    reinstate_user_rules,
    remove_user_rules,
    snooze_reminder,
)
```

- [ ] **Step 2: Update `ShowDeadlinesButton.callback`**

Replace the current callback body:

```python
    async def callback(self, interaction: discord.Interaction) -> None:
        from app.db.models import Concert, User

        async with SessionMaker() as session:
            concert = await session.get(Concert, self.concert_id)
            if concert is None:
                await interaction.response.send_message("That event no longer exists.")
                return
            await session.refresh(concert, ["rounds", "days"])
            user = await session.get(User, interaction.user.id)
            tz = user.timezone if user else "America/Moncton"

            lines = []
            for r in concert.rounds:
                bits = []
                if r.opens_at_utc:
                    bits.append(f"opens {fmt_dual(r.opens_at_utc, tz)}")
                if r.closes_at_utc:
                    bits.append(f"closes {fmt_dual(r.closes_at_utc, tz)}")
                if r.results_at_utc:
                    bits.append(f"results {fmt_dual(r.results_at_utc, tz)}")
                if r.payment_deadline_at_utc:
                    bits.append(f"payment due {fmt_dual(r.payment_deadline_at_utc, tz)}")
                lines.append(f"**{r.label}** — {' / '.join(bits)}")
            for d in concert.days:
                lines.append(f"🎤 **{d.label}** — {fmt_dual(d.starts_at_utc, tz)}")
        await interaction.response.send_message(
            "\n".join(lines) or "No deadlines entered yet."
        )
```

with:

```python
    async def callback(self, interaction: discord.Interaction) -> None:
        from app.db.models import Concert, User

        async with SessionMaker() as session:
            concert = await session.get(Concert, self.concert_id)
            if concert is None:
                await interaction.response.send_message("That event no longer exists.")
                return
            await session.refresh(concert, ["rounds", "days"])
            user = await session.get(User, interaction.user.id)
            tz = user.timezone if user else "America/Moncton"

            cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
            lines = []
            for r in concert.rounds:
                bits = []
                if r.opens_at_utc:
                    bits.append(f"opens {fmt_dual(r.opens_at_utc, tz)}")
                if r.closes_at_utc:
                    bits.append(f"closes {fmt_dual(r.closes_at_utc, tz)}")
                if r.results_at_utc:
                    bits.append(f"results {fmt_dual(r.results_at_utc, tz)}")
                if r.payment_deadline_at_utc:
                    bits.append(f"payment due {fmt_dual(r.payment_deadline_at_utc, tz)}")
                suffix = " (cancelled)" if is_round_cancelled(r, cancelled_day_ids) else ""
                lines.append(f"**{r.label}**{suffix} — {' / '.join(bits)}")
            for d in concert.days:
                suffix = " (cancelled)" if d.cancelled else ""
                lines.append(f"🎤 **{d.label}**{suffix} — {fmt_dual(d.starts_at_utc, tz)}")
        await interaction.response.send_message(
            "\n".join(lines) or "No deadlines entered yet."
        )
```

- [ ] **Step 3: Run the full suite and lint**

Run: `uv run pytest -q` — expect all passing (still 282, no new test — this button's callback isn't independently unit-tested in this codebase, matching the existing precedent for `RemoveRemindersButton`/`ApplyDefaultButton`/`SnoozeButton`'s callbacks; verify by inspection that the diff matches the code shown above exactly).
Run: `uv run ruff check .` — expect `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add src/app/bot/views.py
git commit -m "Mark cancelled rounds/days in ShowDeadlinesButton's output"
```

---

## Task 4: Index page "Open & upcoming" bucketing + sort-key fix

**Files:**
- Modify: `src/app/web/app.py`
- Modify: `src/app/web/templates/index.html`
- Test: `tests/test_tags.py`

**Interfaces:**
- Consumes: `is_round_cancelled` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags.py`, after `test_index_keeps_concert_with_zero_days_visible`:

```python
async def test_index_open_upcoming_bucket_shown_first(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Open Round Show", "event_id": "open-show",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post("/concerts", data={"title": "No Open Round", "event_id": "no-open-round"})

    r = client.get("/").text
    assert "Open &amp; upcoming" in r
    open_heading_pos = r.index("Open &amp; upcoming")
    upcoming_heading_pos = r.index(">Upcoming<")
    open_show_pos = r.index("Open Round Show")
    no_open_pos = r.index("No Open Round")
    assert open_heading_pos < open_show_pos < upcoming_heading_pos < no_open_pos


async def test_index_round_with_only_results_date_is_not_open(client):
    """A round with only results_at set (no opens/closes) never counts as
    "open" -- you can't apply to it, it's a pending action on an
    already-closed round."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Results Only Show", "event_id": "results-only",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": [""],
            "round_results_at": ["2099-06-25T23:59"], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    r = client.get("/").text
    assert "Open &amp; upcoming" not in r


async def test_index_sort_key_ignores_cancelled_leg_date(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Mixed Legs Show", "event_id": "mixed-legs",
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-06-01T18:00", "2099-09-01T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
        },
    )
    client.post("/concerts", data={
        "title": "Between Show", "event_id": "between-show",
        "day_label": ["Day 1"], "day_starts_at": ["2099-07-01T18:00"],
        "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
    })
    async with client.db() as s:
        from app.db.models import Concert as ConcertModel

        mixed = (await s.execute(
            select(ConcertModel).where(ConcertModel.event_id == "mixed-legs")
        )).scalar_one()
        days = sorted(
            (await s.execute(
                select(ConcertDay).where(ConcertDay.concert_id == mixed.id)
            )).scalars(),
            key=lambda d: d.starts_at_utc,
        )
        day1_id, day2_id = days[0].id, days[1].id  # June (will be cancelled), September

    client.post(
        "/concerts/mixed-legs/edit",
        data={
            "title": "Mixed Legs Show", "event_id": "mixed-legs",
            "day_id": [str(day1_id), str(day2_id)],
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-06-01T18:00", "2099-09-01T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
            "day_cancelled": ["true", "false"],
        },
    )
    r = client.get("/?sort=event").text
    # Mixed Legs Show's only LIVE date is September, after Between Show's
    # July date -- if the sort key still used the cancelled June date,
    # Mixed Legs would incorrectly sort before Between Show.
    assert r.index("Between Show") < r.index("Mixed Legs Show")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tags.py -k "index_open_upcoming or index_round_with_only_results or index_sort_key_ignores" -v`
Expected: all 3 FAIL — no "Open & upcoming" heading exists yet in the template, and the sort key doesn't exclude cancelled legs yet.

- [ ] **Step 3: Add `is_round_cancelled` import and `datetime` import to `web/app.py`**

At the top of `src/app/web/app.py`, add a new import line right after the module docstring:

```python
from datetime import UTC, datetime
```

Add `is_round_cancelled` to a new import from `app.db.service` — this file currently does a LOCAL (function-scoped) import of `tag_picker_context` inside the `index` route (`from app.db.service import tag_picker_context`); add `is_round_cancelled` to that same local import line so it reads:

```python
        from app.db.service import is_round_cancelled, tag_picker_context
```

- [ ] **Step 4: Add the `has_open_round` helper and wire bucketing into the route**

In `src/app/web/app.py`, add this function right after `region_sidebar_links` (before `def create_app`):

```python
def has_open_round(concert: Concert, now: datetime) -> bool:
    """A concert is "open" if any of its non-cancelled rounds currently has
    an active application window: closes_at_utc set and in the future, AND
    opens_at_utc either unset or already passed. A round with only
    results/payment timestamps set is never "open" on its own."""
    from app.db.service import is_round_cancelled

    cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
    for r in concert.rounds:
        if is_round_cancelled(r, cancelled_day_ids):
            continue
        if r.closes_at_utc and r.closes_at_utc > now and (
            r.opens_at_utc is None or r.opens_at_utc <= now
        ):
            return True
    return False
```

(This function does its own local import of `is_round_cancelled` rather than relying on Step 3's route-local import, since it's a module-level function called from the route, not inline in the route body — keeps it independently usable/testable without depending on where the route happens to import from.)

Now find the current index route's day-eager-load line:

```python
            stmt = select(Concert).options(selectinload(Concert.days))
```

Change it to also eager-load rounds:

```python
            stmt = select(Concert).options(selectinload(Concert.days), selectinload(Concert.rounds))
```

Find the current sort-key computation:

```python
            else:  # "event": earliest concert day first; undated concerts last
                first_day = sa_func.min(ConcertDay.starts_at_utc)
                stmt = (
                    stmt.outerjoin(ConcertDay)
                    .group_by(Concert.id)
                    .order_by(first_day.is_(None), first_day)
                )
```

Replace with (only the join condition changes, to exclude cancelled days from the aggregate):

```python
            else:  # "event": earliest LIVE concert day first; undated concerts last
                first_day = sa_func.min(ConcertDay.starts_at_utc)
                stmt = (
                    stmt.outerjoin(
                        ConcertDay,
                        (ConcertDay.concert_id == Concert.id) & (ConcertDay.cancelled.is_(False)),
                    )
                    .group_by(Concert.id)
                    .order_by(first_day.is_(None), first_day)
                )
```

Find where `concerts = list((await session.execute(stmt)).scalars())` is assigned (right after the `stmt.options(selectinload(Concert.tags))` line), and add the bucketing computation immediately after it:

```python
            concerts = list((await session.execute(stmt)).scalars())
            now = datetime.now(UTC)
            open_concert_ids = {c.id for c in concerts if has_open_round(c, now)}
```

Finally, add `"open_concert_ids": open_concert_ids,` to the dict passed to `templates.TemplateResponse` (the big context dict at the end of the route) — but this variable is only defined inside the `if user:` block, so it needs a default for the anonymous-user path too. At the very top of the route, alongside the existing `concerts, tz, tz_auto, tags = [], settings.default_timezone, True, []` line, add `open_concert_ids: set[int] = set()` right after it (its own line, since it's a different type than the tuple-unpacked line above).

- [ ] **Step 5: Update `index.html`**

Find the current tiles block:

```html
    {% if concerts %}
    <div class="tiles">
      {% for c in concerts %}
      {% set ctags = c.tags %}
      {% set cf = ctags | selectattr("kind.value", "equalto", "franchise") | list %}
      {% set cg = ctags | selectattr("kind.value", "equalto", "group") | list %}
      {% set ca = ctags | selectattr("kind.value", "equalto", "artist") | list %}
      {% set cv = ctags | selectattr("kind.value", "equalto", "venue") | list %}
      <a class="tile" href="/concerts/{{ c.event_id }}"
         data-tags="{{ ctags | map(attribute='id') | join(',') }}"
         data-search="{{ (c.title ~ ' ' ~ (c.title_en or '')) | lower }}"
         {% if c.id not in visible_concert_ids %}style="display:none"{% endif %}>
        <strong>{{ c.title }}</strong>{% if c.kind %} <span class="dim kind-tag">{{ c.kind.value.replace("_", " ") }}</span>{% endif %}
        <span class="who">
          {#- display rules: F+G+A -> franchise+group; G+A -> group; A only -> artists -#}
          {% if cf and cg %}{{ cf | map(attribute="name") | join(", ") }} · {{ cg | map(attribute="name") | join(", ") }}
          {% elif cg %}{{ cg | map(attribute="name") | join(", ") }}
          {% elif ca %}<span class="artists">{% for a in ca %}<span class="chip kind-artist">{{ a.name }}</span>{% endfor %}</span>
          {% endif %}
        </span>
        {% if cv | length > 1 %}<span class="dim">📍 Multiple</span>
        {% elif cv or c.venue %}<span class="dim">📍 {{ (cv | map(attribute="name") | join(", ")) or c.venue }}</span>{% endif %}
        {% set live_days = c.days | rejectattr("cancelled") | list %}
        {% if live_days %}<span class="when dim">{{ jst(live_days[0].starts_at_utc).strftime("%Y-%m-%d") }} JST</span>{% endif %}
      </a>
      {% endfor %}
    </div>
    <p class="dim" id="no-match"
       {% if visible_concert_ids or (not selected_tags and not query) %}style="display:none"{% endif %}>No concerts match.</p>
    {% else %}
    <p class="dim">No concerts match.{% if user.is_editor and not selected_tags and not query %} <a href="/concerts/new">Create the first one →</a>{% endif %}</p>
    {% endif %}
```

Replace with (the tile body is extracted into a macro so it renders identically in both buckets without duplication; nothing about the tile's own markup changes):

```html
    {% macro tile(c) %}
    {% set ctags = c.tags %}
    {% set cf = ctags | selectattr("kind.value", "equalto", "franchise") | list %}
    {% set cg = ctags | selectattr("kind.value", "equalto", "group") | list %}
    {% set ca = ctags | selectattr("kind.value", "equalto", "artist") | list %}
    {% set cv = ctags | selectattr("kind.value", "equalto", "venue") | list %}
    <a class="tile" href="/concerts/{{ c.event_id }}"
       data-tags="{{ ctags | map(attribute='id') | join(',') }}"
       data-search="{{ (c.title ~ ' ' ~ (c.title_en or '')) | lower }}"
       {% if c.id not in visible_concert_ids %}style="display:none"{% endif %}>
      <strong>{{ c.title }}</strong>{% if c.kind %} <span class="dim kind-tag">{{ c.kind.value.replace("_", " ") }}</span>{% endif %}
      <span class="who">
        {#- display rules: F+G+A -> franchise+group; G+A -> group; A only -> artists -#}
        {% if cf and cg %}{{ cf | map(attribute="name") | join(", ") }} · {{ cg | map(attribute="name") | join(", ") }}
        {% elif cg %}{{ cg | map(attribute="name") | join(", ") }}
        {% elif ca %}<span class="artists">{% for a in ca %}<span class="chip kind-artist">{{ a.name }}</span>{% endfor %}</span>
        {% endif %}
      </span>
      {% if cv | length > 1 %}<span class="dim">📍 Multiple</span>
      {% elif cv or c.venue %}<span class="dim">📍 {{ (cv | map(attribute="name") | join(", ")) or c.venue }}</span>{% endif %}
      {% set live_days = c.days | rejectattr("cancelled") | list %}
      {% if live_days %}<span class="when dim">{{ jst(live_days[0].starts_at_utc).strftime("%Y-%m-%d") }} JST</span>{% endif %}
    </a>
    {% endmacro %}

    {% if concerts %}
    {% set open_concerts = concerts | selectattr("id", "in", open_concert_ids) | list %}
    {% set upcoming_concerts = concerts | rejectattr("id", "in", open_concert_ids) | list %}
    {% if open_concerts %}
    <h2>Open &amp; upcoming</h2>
    <div class="tiles">
      {% for c in open_concerts %}{{ tile(c) }}{% endfor %}
    </div>
    {% endif %}
    {% if upcoming_concerts %}
    <h2>Upcoming</h2>
    <div class="tiles">
      {% for c in upcoming_concerts %}{{ tile(c) }}{% endfor %}
    </div>
    {% endif %}
    <p class="dim" id="no-match"
       {% if visible_concert_ids or (not selected_tags and not query) %}style="display:none"{% endif %}>No concerts match.</p>
    {% else %}
    <p class="dim">No concerts match.{% if user.is_editor and not selected_tags and not query %} <a href="/concerts/new">Create the first one →</a>{% endif %}</p>
    {% endif %}
```

No JS changes needed: `document.querySelectorAll(".tile")` in the existing `<script>` block already matches tiles regardless of which `.tiles` container they're in, so tag/search filtering and the `#no-match` empty-state logic work across both new buckets unchanged.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tags.py -k "index_open_upcoming or index_round_with_only_results or index_sort_key_ignores" -v`
Expected: `3 passed`

- [ ] **Step 7: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (285). Pay attention to whether any EXISTING index-page test elsewhere in `tests/test_tags.py` (e.g. `test_index_filters_by_tag`, `test_index_sorts_by_earliest_event_day`, the search tests, the cancelled-concert-hiding tests) broke due to the tile-markup-into-macro refactor or the new bucketing headings appearing — if any test's string-position assertions get confused by the new `<h2>` headings now present, adjust that test's assertions to account for them (the underlying behavior those tests check is unchanged, only new headings were added around the same tiles).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/app.py src/app/web/templates/index.html tests/test_tags.py
git commit -m "Add Open & upcoming bucketing and fix the event-date sort key to ignore cancelled legs"
```

---

## Task 5: Chronological deadline list on the index page

**Files:**
- Modify: `src/app/web/app.py`
- Modify: `src/app/web/templates/index.html`
- Test: `tests/test_tags.py`

**Interfaces:**
- Consumes: `upcoming_deadlines`, `UpcomingDeadline`, `LABEL_BY_ANCHOR` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags.py`, after the Task 4 tests:

```python
async def test_index_shows_chronological_deadline_list(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Deadline Show", "event_id": "deadline-show",
            "round_label": ["最速先行"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    r = client.get("/").text
    assert "Deadline Show" in r
    assert "最速先行" in r
    assert "closes" in r


async def test_index_deadline_list_excludes_cancelled_round(client):
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
    r = client.get("/").text
    assert "R1" not in r


async def test_index_deadline_list_carries_tag_and_search_attributes(client):
    """Tag filter and free-text search apply to the chronological deadline
    list the same way they apply to tiles -- via data-tags/data-search
    attributes the existing client-side JS reads. This test checks the
    attributes are rendered correctly (server-side), matching how this
    file's existing tile tests verify data-tags/data-search rather than
    simulating JS execution."""
    login_as(client, EDITOR_ID, "reiji")
    async with client.db() as s:
        tag = Tag(name="Test Artist", kind=TagKind.ARTIST)
        s.add(tag)
        await s.flush()
        tag_id = tag.id

    client.post(
        "/concerts",
        data={
            "title": "Tagged Deadline Show", "event_id": "tagged-deadline",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    async with client.db() as s:
        from app.db.models import Concert as ConcertModel
        from app.db.service import attach_tag

        concert = (await s.execute(
            select(ConcertModel).where(ConcertModel.event_id == "tagged-deadline")
        )).scalar_one()
        await attach_tag(s, concert.id, tag)
        await s.commit()

    r = client.get("/").text
    li_start = r.index("<li", r.index("deadline-list"))
    li_end = r.index("</li>", li_start)
    li_html = r[li_start:li_end]
    assert f'data-tags="{tag_id}"' in li_html
    assert 'data-search="tagged deadline show"' in li_html
```

Add `Round` to the existing `from app.db.models import Base, Concert, ConcertTag, Tag, TagMember, User` import in `tests/test_tags.py` if it's not already there (check first — it's used by `create_active_concert_with_group` in this file already, so it should already be imported; if the import line doesn't include `Round`, add it).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tags.py -k "index_shows_chronological or index_deadline_list_excludes" -v`
Expected: FAIL — no deadline list rendered yet.

- [ ] **Step 3: Wire `upcoming_deadlines` into the index route**

In `src/app/web/app.py`, add `upcoming_deadlines` to the same local `from app.db.service import (...)` line Task 4 added `is_round_cancelled` to, making it:

```python
        from app.db.service import is_round_cancelled, tag_picker_context, upcoming_deadlines
```

Right after the `open_concert_ids = {c.id for c in concerts if has_open_round(c, now)}` line added in Task 4, add:

```python
            deadlines = await upcoming_deadlines(session, now, limit=10) if user else []
            concert_tags_by_event_id = {c.event_id: {t.id for t in c.tags} for c in concerts}
```

At the top of the route, alongside the `open_concert_ids: set[int] = set()` default added in Task 4, add another default line: `deadlines, concert_tags_by_event_id = [], {}` (for the anonymous-user path, where the `if user:` block never runs).

Add `LABEL_BY_ANCHOR` to the module-level Jinja globals registration near the top of `create_app`'s enclosing module (find `templates.env.globals["dual"] = fmt_dual` and `templates.env.globals["jst"] = utc_to_jst`, add a third line right after them):

```python
from app.db.service import LABEL_BY_ANCHOR
...
templates.env.globals["deadline_label"] = lambda anchor: LABEL_BY_ANCHOR[anchor]
```

(Put the `from app.db.service import LABEL_BY_ANCHOR` import at the top of the file alongside the other module-level imports, not function-local this time — it's a constant dict, not something that needs the function-scoped-import treatment the other two got.)

Finally, add `"deadlines": deadlines,` and `"concert_tags_by_event_id": concert_tags_by_event_id,` to the context dict passed to `templates.TemplateResponse`.

- [ ] **Step 4: Add the deadline list to `index.html`**

Add this section right after the closing `{% endif %}` of the `{% if concerts %}...{% else %}...{% endif %}` block from Task 4 (i.e., after the tiles/no-match section, still inside `<section class="content">`, before its closing `</section>`):

```html
    {% if deadlines %}
    <h2>Coming up soon</h2>
    <ul class="rows">
      {% for d in deadlines %}
      {% set dtags = concert_tags_by_event_id.get(d.event_id, []) %}
      <li data-tags="{{ dtags | join(',') }}" data-search="{{ d.concert_title | lower }}">
        <span><a href="/concerts/{{ d.event_id }}">{{ d.concert_title }}</a> — {{ d.label }}
          <span class="dim tiny">{{ deadline_label(d.anchor) }}</span>
        </span>
        <span class="dim tiny">{{ dual(d.at_utc, tz) }}</span>
      </li>
      {% endfor %}
    </ul>
    {% endif %}
```

This reuses the existing `.rows`/`.rows > li` CSS (already used elsewhere, e.g. `tags.html`) — no new CSS needed. Each `<li>` carries the same `data-tags`/`data-search` attributes the tiles use, so the existing client-side `filterChips`-adjacent JS (`document.querySelectorAll(".tile")` in the current script only matches tiles, not these `<li>` rows — see the note below) needs one small addition.

**Note on filtering:** the existing `applyVisibility` function in `index.html`'s `<script>` block only queries `.tile` elements. To make tag/search filtering apply to the new deadline-list rows too (per the spec), find this line in the existing script:

```js
      document.querySelectorAll(".tile").forEach((tile) => {
```

and change it to:

```js
      document.querySelectorAll(".tile, #deadline-list li").forEach((tile) => {
```

Also add `id="deadline-list"` to the `<ul class="rows">` element added above, so this selector can target it specifically (it currently has no id in the snippet above — add `id="deadline-list"` to that `<ul>` tag).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tags.py -k "index_shows_chronological or index_deadline_list_excludes or index_deadline_list_carries" -v`
Expected: `3 passed`

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (288).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/app.py src/app/web/templates/index.html tests/test_tags.py
git commit -m "Add a global chronological deadline list to the index page"
```

---

## Final step: update CLAUDE.md

Bump the test count and add "an index-page reorg (open-and-upcoming
bucketing plus a global chronological deadline list)" to the
shipped-features sentence in the intro. Also update the UI-conventions
bullet about the index page's tag filter and search combining as AND — it
should now note that both apply to the new deadline list section too, not
just the tile grids. No new invariant needed. Fold into Task 5's commit or
add one small final commit for it alone.
