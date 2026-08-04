# The Calendar Story Becomes the Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The personal calendar feed becomes a standing-aware landscape (every tracked concert's show dates + the deadlines that still need the user), the per-round `.ics` downloads are replaced by one subscribe affordance with a `webcal://` link, and `/mydeadlines` inherits the same derivation.

**Architecture:** `user_calendar_events` is rewritten to derive from the user's standing over `tracked_concert_ids` through the shared suppression helpers (no queue read, no new rule invented); a required `anchor` rides on `CalendarEvent` so one round's several moments stay distinguishable; the UI work is one shared partial (`_feed_links.html`), a two-state dialog on the concert page, and deletions.

**Tech Stack:** Python 3.14, SQLAlchemy async + SQLite, FastAPI + Jinja2, gettext catalogues (ja/zh), pytest-asyncio.

**Branch:** `calendar-feed-story` (off origin/main, already created; spec committed as `45cbc9d`).

**Spec:** `docs/superpowers/specs/2026-08-04-calendar-feed-story-design.md` — the owner's three rulings and the full derivation table live there; this plan implements it exactly.

## Global Constraints

- Run everything with `uv run --isolated` (an external serve.py locks .venv; never plain `uv run`). Run test suites in the FOREGROUND and wait — never as background jobs.
- `uv run --isolated pytest -q` MUST pass and `uv run --isolated ruff check .` MUST be clean before every commit.
- **No new suppression rule anywhere.** Every per-user exclusion goes through the existing helpers: `tracked_concert_ids`, `user_opted_out_day_ids`, `_round_fully_opted_out`, `covered_round_ids_by_concert`, `is_round_cancelled`, `all_legs_cancelled`, `_qualifiers_by_upgrade_round` + `_eligible_upgrade_ids`, `_result_moment`.
- **Locale contract unchanged:** the `.ics` feed renders CANONICAL (locale `None` — a URL has no viewer); the `/mydeadlines` cog passes the recipient's language. `test_i18n_ugc.py` pins this.
- Every new/changed translatable string updates BOTH `messages.po` files (ja and zh) — `pybabel extract`/`update` per CLAUDE.md's Commands section, then hand-fill msgstrs, then delete `messages.pot`. `tests/test_i18n_catalogues.py` fails on anything untranslated (fuzzy counts as untranslated). Canonical Japanese qualifier text in the feed is NOT gettext — it is plain data.
- Aware-UTC datetimes only (invariant 1). All new queries batched (one per call, never per concert/round).
- No token/regeneration model changes (invariant 5's secret-link shape stays byte-identical).
- Commit messages end with:
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01TFZ93NWpcHbsS9Ha1ezytv

## File Structure

- `src/app/db/service.py` — `CalendarEvent` + `user_calendar_events` rewrite (Task 1).
- `src/app/domain/ics_export.py` — canonical anchor-qualifier map (Task 2); `build_ics` deleted (Task 3).
- `src/app/web/routes/calendar.py` — feed summary composition (Task 2), `next` validation (Task 4).
- `src/app/bot/cogs/reminders.py` — `/mydeadlines` rendering + copy (Task 2).
- `src/app/web/routes/concerts.py` — download route deleted (Task 3), `concert_detail` context (Task 5).
- `src/app/web/templates/_feed_links.html` — NEW shared partial (Task 4).
- `src/app/web/templates/preferences.html`, `welcome.html` — refit + copy (Task 4).
- `src/app/web/templates/concert_detail.html`, `_round_rows.html` — dialog (Task 5), 📅 removal (Task 3).
- `tests/test_calendar_landscape.py` — NEW, the derivation's home (Task 1).
- Fallout updates: `tests/test_calendar_feed.py`, `tests/test_service.py`, `tests/test_i18n_ugc.py`, `tests/test_leg_opt_out_suppression.py`, `tests/test_bot_reminders.py` (Task 1), `tests/test_ics_export.py` (Task 3).
- `WISHLIST.md`, `CLAUDE.md` — Task 6.

---

### Task 1: Rewrite `user_calendar_events` as the standing-aware landscape

The feed stops reading `reminder_queue`. It derives from tracked concerts: show dates for live, non-opted-out legs; deadlines per surviving round selected by the user's outcome (None → opens+closes; APPLIED → `_result_moment`; WON → payment; LOST/NOT_APPLIED/PAID → nothing). `CalendarEvent` gains a required `anchor`. Reminder rules now control DMs only.

**Files:**
- Modify: `src/app/db/service.py` (`CalendarEvent` ~line 4111, `user_calendar_events` ~line 4124)
- Create: `tests/test_calendar_landscape.py`
- Modify (fallout, same commit): `tests/test_calendar_feed.py`, `tests/test_service.py`, `tests/test_i18n_ugc.py`, `tests/test_leg_opt_out_suppression.py`, `tests/test_bot_reminders.py`

**Interfaces:**
- Produces: `CalendarEvent(concert_title, label, at_utc, anchor, url=None, notes=None)` with `anchor: Anchor` required — Task 2's qualifier rendering keys on it.
- `user_calendar_events(session, user_id, now=None, locale=None) -> list[CalendarEvent]` — signature unchanged; behavior per the spec's derivation table.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calendar_landscape.py`:

```python
"""user_calendar_events as the standing-aware landscape (spec 2026-08-04).

Shows + live deadlines over TRACKED concerts, selected by the user's standing
-- reminder rules play no part. Every exclusion is a shared helper the other
read surfaces already use; these tests pin the derivation, not the helpers.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, Round, RoundQualifier
from app.db.service import (
    ensure_user,
    record_round_outcome,
    set_concert_subscription,
    set_leg_opt_out,
    user_calendar_events,
)
from app.domain.types import Anchor, LotteryOutcome, RoundKind, SubscriptionState

USER = 42
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


async def make_tracked_concert(s, event_id: str = "c") -> Concert:
    await ensure_user(s, USER, "reiji")
    concert = Concert(title=event_id, event_id=event_id, created_by=USER)
    s.add(concert)
    await s.flush()
    await set_concert_subscription(s, USER, concert.id, SubscriptionState.SUBSCRIBED)
    return concert


async def make_day(s, concert: Concert, label: str, starts=None, cancelled=False) -> ConcertDay:
    day = ConcertDay(
        concert_id=concert.id, label=label,
        starts_at_utc=starts or dt(8, 1, 9), cancelled=cancelled,
    )
    s.add(day)
    await s.flush()
    return day


async def make_round(s, concert: Concert, label: str = "R1", applies_to=None, *,
                     kind=RoundKind.LOTTERY_ROUND, opens=None, closes=None,
                     results=None, payment=None) -> Round:
    round_ = Round(
        concert_id=concert.id, kind=kind, label=label, applies_to=applies_to,
        opens_at_utc=opens, closes_at_utc=closes,
        results_at_utc=results, payment_deadline_at_utc=payment,
    )
    s.add(round_)
    await s.flush()
    return round_


def moments(events, label):
    return {(e.anchor, e.at_utc) for e in events if e.label == label}


async def test_untracked_concert_contributes_nothing(session):
    await ensure_user(session, USER, "reiji")
    concert = Concert(title="x", event_id="x", created_by=USER)
    session.add(concert)
    await session.flush()
    await make_day(session, concert, "Leg A")
    await make_round(session, concert, closes=dt(6, 25))

    assert await user_calendar_events(session, USER, NOW) == []


async def test_show_dates_for_live_legs_only(session):
    concert = await make_tracked_concert(session)
    await make_day(session, concert, "Leg A")
    await make_day(session, concert, "Cancelled", cancelled=True)
    await make_day(session, concert, "Past", starts=dt(5, 1, 9))
    b = await make_day(session, concert, "Opted out")
    await set_leg_opt_out(session, USER, b.id, True, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert {e.label for e in events} == {"Leg A"}
    assert events[0].anchor is Anchor.EVENT_START
    assert events[0].at_utc == dt(8, 1, 9)


async def test_no_outcome_round_contributes_opens_and_closes(session):
    concert = await make_tracked_concert(session)
    await make_round(session, concert, opens=dt(6, 10), closes=dt(6, 25),
                     results=dt(6, 28), payment=dt(6, 30))

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == {(Anchor.OPENS, dt(6, 10)), (Anchor.CLOSES, dt(6, 25))}


async def test_applied_round_contributes_its_result_moment(session):
    concert = await make_tracked_concert(session)
    r = await make_round(session, concert, opens=dt(5, 10), closes=dt(5, 25),
                         results=dt(6, 28), payment=dt(7, 30))
    await record_round_outcome(session, USER, r.id, LotteryOutcome.APPLIED, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == {(Anchor.RESULTS, dt(6, 28))}


async def test_applied_round_without_results_time_falls_back_to_the_close(session):
    """_result_moment's rule: results become knowable at the close."""
    concert = await make_tracked_concert(session)
    r = await make_round(session, concert, closes=dt(6, 25))
    await record_round_outcome(session, USER, r.id, LotteryOutcome.APPLIED, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == {(Anchor.RESULTS, dt(6, 25))}


async def test_won_round_contributes_payment_only(session):
    concert = await make_tracked_concert(session)
    r = await make_round(session, concert, closes=dt(5, 25), results=dt(5, 28),
                         payment=dt(6, 30))
    await record_round_outcome(session, USER, r.id, LotteryOutcome.WON, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == {(Anchor.PAYMENT, dt(6, 30))}


async def test_settled_rounds_contribute_nothing(session):
    concert = await make_tracked_concert(session)
    for label, outcome in (
        ("Lost", LotteryOutcome.LOST),
        ("Skipped", LotteryOutcome.NOT_APPLIED),
    ):
        r = await make_round(session, concert, label, opens=dt(6, 10),
                            closes=dt(6, 25), payment=dt(6, 30))
        await record_round_outcome(session, USER, r.id, outcome, now=NOW)
    won = await make_round(session, concert, "Paid", closes=dt(5, 25), payment=dt(6, 30))
    await record_round_outcome(session, USER, won.id, LotteryOutcome.WON, now=NOW)
    await record_round_outcome(session, USER, won.id, LotteryOutcome.PAID, now=NOW)

    assert await user_calendar_events(session, USER, NOW) == []


async def test_fully_opted_out_round_contributes_nothing_partial_survives(session):
    concert = await make_tracked_concert(session)
    a = await make_day(session, concert, "Leg A", starts=dt(5, 1))  # past: no show event
    b = await make_day(session, concert, "Leg B", starts=dt(5, 2))
    await make_round(session, concert, "Solo", applies_to=[a.id], closes=dt(6, 25))
    await make_round(session, concert, "Both", applies_to=[a.id, b.id], closes=dt(6, 26))
    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert {e.label for e in events} == {"Both"}


async def test_covered_round_contributes_nothing(session):
    """Leg secured through round A: round B selling the same leg is covered."""
    concert = await make_tracked_concert(session)
    leg = await make_day(session, concert, "Leg A", starts=dt(8, 1))
    a = await make_round(session, concert, "A", applies_to=[leg.id], closes=dt(5, 25))
    await make_round(session, concert, "B", applies_to=[leg.id],
                     opens=dt(6, 10), closes=dt(6, 25))
    await record_round_outcome(session, USER, a.id, LotteryOutcome.WON, now=NOW)
    await record_round_outcome(session, USER, a.id, LotteryOutcome.PAID, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert "B" not in {e.label for e in events}
    assert "Leg A" in {e.label for e in events}  # the show itself stays


async def test_dead_concert_contributes_nothing(session):
    concert = await make_tracked_concert(session)
    await make_day(session, concert, "Leg A", cancelled=True)
    await make_round(session, concert, closes=dt(6, 25))  # General: no leg named

    assert await user_calendar_events(session, USER, NOW) == []


async def test_upgrade_round_only_when_eligible(session):
    concert = await make_tracked_concert(session)
    base = await make_round(session, concert, "Base", closes=dt(5, 25))
    up = await make_round(session, concert, "Upgrade", kind=RoundKind.UPGRADE,
                          opens=dt(6, 10), closes=dt(6, 25))
    session.add(RoundQualifier(upgrade_round_id=up.id, qualifying_round_id=base.id))
    await session.flush()

    events = await user_calendar_events(session, USER, NOW)
    assert "Upgrade" not in {e.label for e in events}

    await record_round_outcome(session, USER, base.id, LotteryOutcome.WON, now=NOW)
    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "Upgrade") == {(Anchor.OPENS, dt(6, 10)), (Anchor.CLOSES, dt(6, 25))}


async def test_events_are_future_only_and_sorted(session):
    concert = await make_tracked_concert(session)
    await make_day(session, concert, "Show", starts=dt(8, 1, 9))
    await make_round(session, concert, "Late", opens=dt(7, 10), closes=dt(7, 25))
    await make_round(session, concert, "Early", opens=dt(5, 10), closes=dt(6, 5))

    events = await user_calendar_events(session, USER, NOW)
    assert [e.at_utc for e in events] == sorted(e.at_utc for e in events)
    assert all(e.at_utc > NOW for e in events)
    assert moments(events, "Early") == {(Anchor.CLOSES, dt(6, 5))}  # past opens dropped
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_calendar_landscape.py -q`
Expected: FAIL — `CalendarEvent` has no `anchor`, and the current derivation returns `[]` for everything (no reminder rules exist in these tests).

- [ ] **Step 3: Implement the rewrite**

In `src/app/db/service.py`, replace `CalendarEvent` with:

```python
@dataclass(frozen=True)
class CalendarEvent:
    """One entry on a user's personal feed: a show date (EVENT_START) or a
    deadline that still needs them, at its real moment -- never a reminder's
    lead time. `anchor` says WHICH of a round's moments this is, so a round
    contributing both its open and its close stays distinguishable once
    rendered."""

    concert_title: str
    label: str
    at_utc: datetime
    anchor: Anchor
    url: str | None = None
    notes: str | None = None
```

Replace `user_calendar_events` whole (keep the `_title`/`_label` inner helpers verbatim — the locale contract is theirs):

```python
async def user_calendar_events(
    session: AsyncSession, user_id: int, now: datetime | None = None,
    locale: str | None = None,
) -> list[CalendarEvent]:
    """The user's standing-aware landscape (spec 2026-08-04): every TRACKED
    concert's live show dates, plus each surviving round's next moments
    selected by this user's outcome on it -- no outcome: opens + closes;
    APPLIED: the result moment (_result_moment, results falling back to the
    close); WON: the payment deadline; LOST/NOT_APPLIED/PAID: nothing, and a
    LOST round's auto-armed successor contributes its own opens/closes as an
    ordinary no-outcome round. Future-only throughout.

    Reminder RULES play no part -- they control when Discord DMs fire, and
    this used to read reminder_queue, which made a sparse preset read as a
    broken calendar. Every exclusion is a shared helper other surfaces
    already use (tracked_concert_ids, opt-outs, cancellation, coverage,
    upgrade eligibility); nothing here invents a rule.

    `locale` localizes titles/labels for a locale-aware caller (the
    /mydeadlines cog passes the recipient's language). Left None by the .ics
    feed, which has no viewer locale -- that path keeps the canonical text.
    """
    now = now or _now()

    def _title(concert: Concert | None) -> str:
        if concert is None:
            return "Concert"
        return loc_field(concert, "title", locale) if locale else concert.title

    def _label(obj: Round | ConcertDay) -> str:
        """Same rule as _title: an explicit caller locale localizes, None
        (the .ics feed) keeps the canonical text. Deliberately NOT
        get_locale() -- the feed must stay byte-identical per viewer."""
        return loc_field(obj, "label", locale) if locale else obj.label

    tracked = await tracked_concert_ids(session, user_id)
    if not tracked:
        return []
    concerts = list((await session.execute(
        select(Concert)
        .where(Concert.id.in_(tracked))
        .options(selectinload(Concert.days), selectinload(Concert.rounds))
    )).scalars())

    # Per-user suppression inputs, each ONE batched query over the whole set.
    opted_out = await user_opted_out_day_ids(
        session, user_id, [d.id for c in concerts for d in c.days]
    )
    all_round_ids = [r.id for c in concerts for r in c.rounds]
    outcomes: dict[int, LotteryOutcome] = {
        o.round_id: o.outcome
        for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id,
                RoundOutcome.round_id.in_(all_round_ids),
            )
        )).scalars()
    } if all_round_ids else {}
    # Coverage: only concerts where the user holds a secured ticket can
    # produce a covered round -- same short-circuit my_deadline_rows uses.
    secured_concert_ids = set((await session.execute(
        select(Round.concert_id)
        .join(RoundOutcome, RoundOutcome.round_id == Round.id)
        .where(
            RoundOutcome.user_id == user_id,
            Round.concert_id.in_(tracked),
            RoundOutcome.outcome.in_([LotteryOutcome.WON, LotteryOutcome.PAID]),
        )
    )).scalars())
    covered: set[int] = set()
    for ids in (await covered_round_ids_by_concert(
        session, user_id, secured_concert_ids
    )).values():
        covered |= ids
    qualifiers_by_round = await _qualifiers_by_upgrade_round(
        session,
        [r.id for c in concerts for r in c.rounds if r.kind is RoundKind.UPGRADE],
    )

    events: list[CalendarEvent] = []
    for c in concerts:
        if all_legs_cancelled(c.days):
            continue  # the show is off: nothing on it is a question
        cancelled_day_ids = {d.id for d in c.days if d.cancelled}
        title = _title(c)

        for d in c.days:
            if d.cancelled or d.id in opted_out or d.starts_at_utc <= now:
                continue
            events.append(CalendarEvent(
                concert_title=title, label=_label(d),
                at_utc=d.starts_at_utc, anchor=Anchor.EVENT_START,
            ))

        # Upgrade eligibility is concert-scoped (a secured ticket elsewhere
        # must not qualify an empty-qualifier upgrade here).
        c_outcomes = {r.id: outcomes[r.id] for r in c.rounds if r.id in outcomes}
        eligible_up = _eligible_upgrade_ids(list(c.rounds), c_outcomes, qualifiers_by_round)
        for r in c.rounds:
            if is_round_cancelled(r, cancelled_day_ids):
                continue
            if _round_fully_opted_out(r, opted_out):
                continue
            if r.id in covered:
                continue
            if r.kind is RoundKind.UPGRADE and r.id not in eligible_up:
                continue
            outcome = outcomes.get(r.id)
            if outcome is None:
                moments = [(Anchor.OPENS, r.opens_at_utc), (Anchor.CLOSES, r.closes_at_utc)]
            elif outcome is LotteryOutcome.APPLIED:
                # The one shared "when does the result become knowable" rule.
                moments = [(Anchor.RESULTS, _result_moment(r))]
            elif outcome is LotteryOutcome.WON:
                moments = [(Anchor.PAYMENT, r.payment_deadline_at_utc)]
            else:  # LOST / NOT_APPLIED / PAID: settled, nothing left to act on
                moments = []
            for anchor, ts in moments:
                if ts is None or ts <= now:
                    continue
                events.append(CalendarEvent(
                    concert_title=title, label=_label(r), at_utc=ts,
                    anchor=anchor, url=r.url, notes=r.notes,
                ))

    events.sort(key=lambda e: e.at_utc)
    return events
```

- [ ] **Step 4: Run the new file; triage the fallout**

Run: `uv run --isolated pytest tests/test_calendar_landscape.py -q` → all pass.
Run: `uv run --isolated pytest -q` → EXPECT failures in exactly five files, all because a test's concert is no longer TRACKED (rules no longer feed the feed) or asserts rule-derived content. Fix each by making the concert tracked and keeping the test's original claim:

- `tests/test_calendar_feed.py`: `create_round_with_rule` — after the concert POST, subscribe the caller: `client.post(f"/concerts/{event_id}/subscription", data=...)` (read `src/app/web/routes/subscriptions.py:70` for the exact form field; it drives `set_concert_subscription`). The rule POST may stay (rules are harmless now) or go. `test_calendar_feed_returns_ics_with_active_reminders` renames to `..._returns_ics_with_tracked_deadlines`; its `SUMMARY:` assertion gains the qualifier ONLY in Task 2 — here assert `"C — R1" in r.text` (substring, qualifier-agnostic).
- `tests/test_service.py::test_user_calendar_events_covers_rounds_and_days` and `..._excludes_past_deadlines` (~lines 267–290): read them; add a `SUBSCRIBED` override for user 42 via `set_concert_subscription`, and update expectations to the landscape (a no-outcome round yields opens/closes moments, not one-row-per-rule).
- `tests/test_i18n_ugc.py` (~lines 599–624, the canonical-vs-localized contract): add the subscription; the CONTRACT assertions (canonical vs `locale="zh"`) stay untouched — that is the point of the test.
- `tests/test_leg_opt_out_suppression.py::test_calendar_feed_omits_opted_out_leg` (~line 306): add `await set_concert_subscription(session, USER, concert.id, SubscriptionState.SUBSCRIBED)` (import both from the module's existing import sites) after creating the concert; the claim (opted-out leg absent, other leg present) survives verbatim — the feed now omits the leg via the derivation directly rather than via the queue. Update the docstring's mechanism sentence to match.
- `tests/test_bot_reminders.py` (/mydeadlines tests, lines ~111–202): read the file; its fixtures create rules to feed the command — make the concerts tracked instead (same `set_concert_subscription` service call, the file uses a service-level db fixture). `test_mydeadlines_lists_only_this_users_reminders` keeps its isolation claim (per-user outcomes/subscriptions differ, so tracking one user's concert and not the other's preserves it); rename to `..._only_this_users_landscape` if the old name lies.

Do NOT weaken any assertion to make a test fit — each keeps its original claim with tracking added. If a test's claim genuinely no longer exists (it asserted rule-derivation itself), rewrite its docstring to the landscape claim it now pins.

- [ ] **Step 5: Full suite green, then commit**

Run: `uv run --isolated pytest -q` → all pass. `uv run --isolated ruff check .` → clean.

```bash
git add src/app/db/service.py tests/test_calendar_landscape.py tests/test_calendar_feed.py tests/test_service.py tests/test_i18n_ugc.py tests/test_leg_opt_out_suppression.py tests/test_bot_reminders.py
git commit -m "feat: the calendar feed becomes a standing-aware landscape"
```

---

### Task 2: Anchor qualifiers — canonical in the feed, localized in `/mydeadlines`

A no-outcome round now emits two same-summary events. The feed qualifies with canonical Japanese ticketing terms (plain data — canonical text is by definition untranslated); the cog qualifies through gettext. The cog's copy also stops claiming rule-derivation.

**Files:**
- Modify: `src/app/domain/ics_export.py` (qualifier map), `src/app/web/routes/calendar.py` (summary composition), `src/app/bot/cogs/reminders.py` (rendering + copy)
- Modify: `src/app/translations/ja/LC_MESSAGES/messages.po`, `src/app/translations/zh/LC_MESSAGES/messages.po`
- Test: `tests/test_calendar_feed.py`, `tests/test_bot_reminders.py`

**Interfaces:**
- Consumes: `CalendarEvent.anchor` (Task 1).
- Produces: `CANONICAL_ANCHOR_QUALIFIERS: dict[Anchor, str]` in `domain/ics_export.py` (EVENT_START absent — a show date needs no qualifier).

- [ ] **Step 1: Write the failing tests**

In `tests/test_calendar_feed.py`, extend the tracked-deadlines test (or add beside it):

```python
def test_calendar_feed_qualifies_round_moments_canonically(client):
    """A no-outcome round emits opens AND closes; the canonical qualifier is
    what keeps the two apart on somebody's phone. Japanese on purpose: the
    feed has no viewer, and Japanese is this catalogue's source of truth."""
    login_as(client, EDITOR_ID, "reiji")
    create_round_with_rule(client, "2099-06-25T23:59")  # helper posts opens too: add
    # (extend the helper's form data with "round_opens_at": ["2099-06-10T10:00"])
    token = generate_feed_token(client)

    r = client.get(f"/calendar/{token}.ics")
    assert "SUMMARY:C — R1 · 受付開始" in r.text
    assert "SUMMARY:C — R1 · 申込締切" in r.text
```

(Adjust the helper so the round carries both an open and a close; keep existing tests' expectations updated to carry the qualifier — the Task 1 substring assertion now becomes exact again.)

In `tests/test_bot_reminders.py`, extend one `/mydeadlines` test to assert the localized qualifier appears in the embed description for a closes-moment (English catalogue: `"apply by"` per the map below).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_calendar_feed.py tests/test_bot_reminders.py -q`
Expected: the new assertions FAIL (no qualifier in summaries).

- [ ] **Step 3: Implement**

**(a)** `src/app/domain/ics_export.py` (top, after imports — add `from app.domain.types import Anchor`):

```python
# Canonical qualifiers for a round's moments on the personal feed. Plain
# data, NOT gettext: the feed renders canonical (a URL has no viewer), and
# canonical text is by definition untranslated. Japanese ticketing terms
# because Japanese is this catalogue's source of truth. EVENT_START is
# deliberately absent -- a show date is its own summary and takes no
# qualifier.
CANONICAL_ANCHOR_QUALIFIERS = {
    Anchor.OPENS: "受付開始",
    Anchor.CLOSES: "申込締切",
    Anchor.RESULTS: "当落発表",
    Anchor.PAYMENT: "支払期限",
}
```

**(b)** `src/app/web/routes/calendar.py`, the feed route's composition:

```python
    events = await user_calendar_events(session, user.discord_id)
    def _summary(e):
        qual = CANONICAL_ANCHOR_QUALIFIERS.get(e.anchor)
        base = f"{e.concert_title} — {e.label}"
        return f"{base} · {qual}" if qual else base
    text = build_calendar([
        (_summary(e), e.at_utc, e.url, e.notes) for e in events
    ])
```

(import `CANONICAL_ANCHOR_QUALIFIERS` beside `build_calendar`).

**(c)** `src/app/bot/cogs/reminders.py`, `/mydeadlines`: replace the line comprehension with a qualified one and truthful copy:

```python
        loc = get_locale()
        quals = {
            Anchor.OPENS: _("opens"),
            Anchor.CLOSES: _("apply by"),
            Anchor.RESULTS: _("results announced"),
            Anchor.PAYMENT: _("payment due"),
        }
        lines = []
        for e in events[:count]:
            qual = quals.get(e.anchor)
            head = f"**{e.concert_title}** — {e.label}"
            if qual:
                head += f" · {qual}"
            lines.append(f"{head}\n{fmt_dual(e.at_utc, tz, loc)}")
```

Copy truth in the same file: the command description becomes `"Your own next deadlines -- everything you track"` (slash-command descriptions are English-only, not gettext, matching the file's existing style); the empty state becomes `_("Nothing on your calendar yet — follow a tag or an event first.")`; the docstring's reminder_queue sentence is rewritten to name the landscape derivation.

**(d)** Catalogues: `uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .`, then `pybabel update` for ja and zh (exact commands in CLAUDE.md), hand-fill the new msgstrs in BOTH files, delete `messages.pot`. Translations to use (owner-reviewable, consistent with the canonical terms):

| msgid | ja | zh |
|---|---|---|
| `opens` | (already translated — reuse; only new if pybabel marks it) | (same) |
| `apply by` | 申込締切 | 报名截止 |
| `results announced` | 当落発表 | 抽选结果公布 |
| `payment due` | 支払期限 | 付款期限 |
| `Nothing on your calendar yet — follow a tag or an event first.` | まだカレンダーに何もありません。まずタグやイベントをフォローしてください。 | 你的日历还没有内容——先关注一个标签或活动吧。 |

The old empty-state msgid (`No upcoming deadlines on your reminders. ...`) becomes unused; `pybabel update` will comment it out — leave that to the tool.

- [ ] **Step 4: Run, then full suite**

Run: `uv run --isolated pytest tests/test_calendar_feed.py tests/test_bot_reminders.py tests/test_i18n_catalogues.py -q` → pass.
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/domain/ics_export.py src/app/web/routes/calendar.py src/app/bot/cogs/reminders.py src/app/translations tests/test_calendar_feed.py tests/test_bot_reminders.py
git commit -m "feat: anchor qualifiers on feed and /mydeadlines entries"
```

---

### Task 3: Delete the per-round `.ics` downloads

The ruling: replaced, not supplemented. The 📅 link, the route, the single-event builder, and their tests go; a 404 test pins the route's absence.

**Files:**
- Modify: `src/app/web/templates/_round_rows.html` (line ~106), `src/app/web/routes/concerts.py` (route at ~1895, `build_ics` import), `src/app/domain/ics_export.py` (delete `build_ics`; keep `_vevent_lines`/`_uid`/`_escape`/`_stamp` and `build_calendar`)
- Test: `tests/test_ics_export.py` (rewrite `build_ics` tests onto `build_calendar`), `tests/test_calendar_feed.py` (404 pin)

- [ ] **Step 1: Write the failing test**

In `tests/test_calendar_feed.py`:

```python
def test_per_round_ics_download_is_gone(client):
    """Ruling 2026-08-04: the download buttons are REPLACED by the feed. A
    file is a snapshot that rots when a deadline moves; the feed re-plans."""
    login_as(client, EDITOR_ID, "reiji")
    create_round_with_rule(client, "2099-06-25T23:59", event_id="gone")
    assert client.get("/rounds/1/ics").status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_calendar_feed.py::test_per_round_ics_download_is_gone -q`
Expected: FAIL — the route answers 200.

- [ ] **Step 3: Delete**

- `_round_rows.html` line ~106: remove the `{% if not past and row.primary_at_utc %}<a class="ics" ...>📅</a>{% endif %}` link (the surrounding markup stays).
- `web/routes/concerts.py`: delete the whole `GET /rounds/{round_id}/ics` route function (~1893–1922) including its local `from app.domain.ics_export import build_ics`.
- `domain/ics_export.py`: delete `build_ics`; update the module docstring (it currently describes the single-VEVENT download) to describe the multi-event feed builder. `CANONICAL_ANCHOR_QUALIFIERS`, `_escape`, `_stamp`, `_uid`, `_vevent_lines`, `build_calendar` all stay.
- `tests/test_ics_export.py`: read it; port any escaping/UID/format assertions that exercise `build_ics` onto `build_calendar` (same `_vevent_lines` path, so the claims survive), delete the rest.
- Check for stray consumers: `grep -r "build_ics\|/rounds/.*ics" src tests` must come back empty (the `style.css` `.ics` class may stay — it is dead CSS; remove the rule if trivial).

- [ ] **Step 4: Run, then full suite**

Run: `uv run --isolated pytest tests/test_calendar_feed.py tests/test_ics_export.py tests/test_concert_rows.py -q` → pass.
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/web/templates/_round_rows.html src/app/web/routes/concerts.py src/app/domain/ics_export.py src/app/web/static/style.css tests/test_ics_export.py tests/test_calendar_feed.py
git commit -m "feat: remove the per-round .ics downloads"
```

---

### Task 4: The mint route's `next`, the shared `_feed_links.html` partial, and truthful copy

Preferences and welcome step 4 each hand-roll the shown-once URL block; the concert dialog (Task 5) needs the same. One partial, three consumers, and the mint route learns to return to a concert page — through `safe_next`, not a widening hardcoded list.

**Files:**
- Create: `src/app/web/templates/_feed_links.html`
- Modify: `src/app/web/routes/calendar.py` (`_ALLOWED_NEXT` → `_allowed_next()`), `src/app/web/templates/preferences.html` (~341–349), `src/app/web/templates/welcome.html` (~437–448)
- Modify: both `messages.po` files
- Test: `tests/test_calendar_feed.py`, plus the existing preferences/welcome render tests

**Interfaces:**
- Produces: `_feed_links.html`, included with `{% include %}` wherever `feed_url` is in context. It renders: webcal link, https URL, copy button.

- [ ] **Step 1: Write the failing tests**

In `tests/test_calendar_feed.py`:

```python
def test_generate_feed_honors_concert_page_next(client):
    login_as(client, EDITOR_ID, "reiji")
    create_round_with_rule(client, "2099-06-25T23:59", event_id="mine")
    r = client.post("/me/calendar-feed", data={"next": "/concerts/mine"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/concerts/mine?feed_token=")


def test_generate_feed_rejects_offsite_and_odd_next(client):
    login_as(client, EDITOR_ID, "reiji")
    for bad in ("https://evil.example/x", "/\\evil.example", "/admin", "//evil"):
        r = client.post("/me/calendar-feed", data={"next": bad})
        assert r.headers["location"].startswith("/preferences?feed_token="), bad


def test_fresh_feed_url_shows_webcal_link_on_preferences(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed", data={"next": "/preferences"})
    page = client.get(r.headers["location"])
    assert "webcal://" in page.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_calendar_feed.py -q`
Expected: concert `next` falls back to `/preferences` (first test fails); no `webcal://` anywhere (third fails).

- [ ] **Step 3: Implement**

**(a)** `web/routes/calendar.py`:

```python
from app.domain.urls import safe_next

_ALLOWED_NEXT = {"/preferences", "/welcome"}


def _allowed_next(raw: str) -> str:
    """Where the mint may bounce back to. safe_next FIRST (the standing
    open-redirect guard: same-origin path or None), then an allowlist of
    shapes rather than of literal paths -- the concert page is the third
    surface that mints, and hardcoding every concert is not a list anyone
    maintains. Anything else falls back to /preferences, as always."""
    path = safe_next(raw)
    if path is None:
        return "/preferences"
    if path in _ALLOWED_NEXT or path.startswith("/concerts/"):
        return path
    return "/preferences"
```

and in the POST handler: `destination = _allowed_next(next_url)`.

**(b)** `src/app/web/templates/_feed_links.html` (new; expects `feed_url` in context):

```jinja
{#- The shown-once feed URL, rendered identically on its three surfaces
    (Preferences, welcome step 4, the concert page's calendar dialog) so the
    ergonomics cannot drift. The raw token appears exactly once -- only its
    hash is stored (invariant 5) -- which is why every consumer gates this
    include on feed_url being present. -#}
<div class="feedlinks">
  <div class="ruleline"><b>{{ _("Your feed link is ready — save it now, it won't be shown again.") }}</b></div>
  <p class="inline" style="margin:.4rem 0 0">
    <a class="btn" href="{{ feed_url | replace('https://', 'webcal://') | replace('http://', 'webcal://') }}">{{ _("Open in calendar app") }}</a>
    <button type="button" class="act" data-copy="{{ feed_url }}"
            onclick="navigator.clipboard.writeText(this.dataset.copy)">{{ _("Copy link") }}</button>
  </p>
  <p class="inline" style="margin:.4rem 0 0"><code class="feed-url">{{ feed_url }}</code></p>
  <span class="clock">{% trans %}Google Calendar: Settings &rarr; Add calendar &rarr; From URL.
    Apple Calendar: File &rarr; New Calendar Subscription.{% endtrans %}</span>
</div>
```

**(c)** `preferences.html` ~341–349: the `{% if feed_url %}` presetcard body becomes `{% include "_feed_links.html" %}` inside the existing `.presetcard` wrapper. The section's status line copy gains the landscape truth: change `{{ _("Subscribe by URL in Google or Apple Calendar.") }}` to `{{ _("Your shows and the deadlines that still need you, always current.") }}`.

**(d)** `welcome.html` step 4 (~437–448): the lede paragraph becomes `{% trans %}One link your calendar app subscribes to: every show you follow, and the deadlines that still need you. A backup to Discord DMs.{% endtrans %}`; the `{% if feed_url %}` edgecard keeps its ✓ headline but its `<code>`/copy-button rows are replaced by `{% include "_feed_links.html" %}` (drop the now-duplicated headline inside the partial's context — keep the partial as the single body).

**(e)** Catalogues for every new/changed msgid, both files:

| msgid | ja | zh |
|---|---|---|
| `Open in calendar app` | カレンダーアプリで開く | 在日历应用中打开 |
| `Your shows and the deadlines that still need you, always current.` | フォロー中の公演と、まだ対応が必要な締切を常に最新で。 | 你关注的演出和仍需处理的截止日期，始终保持最新。 |
| `One link your calendar app subscribes to: every show you follow, and the deadlines that still need you. A backup to Discord DMs.` | カレンダーアプリに1つのリンクを登録するだけ。フォロー中の公演と、まだ対応が必要な締切がすべて入ります。Discord DMのバックアップにも。 | 日历应用只需订阅一个链接：你关注的所有演出，以及仍需处理的截止日期。也是 Discord 私信的后备。 |

(The moved-but-unchanged msgids — "Your feed link is ready…", "Copy link", the Google/Apple instructions — keep their existing translations; byte-identical msgids, per the standing rule.)

- [ ] **Step 4: Run, then full suite**

Run: `uv run --isolated pytest tests/test_calendar_feed.py tests/test_preferences_page.py tests/test_welcome.py tests/test_i18n_catalogues.py -q` → pass (update any render assertion pinned to the old copy).
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/web/routes/calendar.py src/app/web/templates/_feed_links.html src/app/web/templates/preferences.html src/app/web/templates/welcome.html src/app/translations tests/test_calendar_feed.py tests/test_preferences_page.py tests/test_welcome.py
git commit -m "feat: shared feed-links partial, webcal link, concert-page next"
```

---

### Task 5: The concert page's "📅 Calendar" dialog

One action for every signed-in reader (the page is login-gated), on the `.bar2` row beside the following toggle — never in the kebab (destructive-only). A native dialog in two server-rendered states; backdrop-close comes only from base.html's global handler (the sweep test forbids local ones).

**Files:**
- Modify: `src/app/web/routes/concerts.py` (`concert_detail`, ~line 1133: context), `src/app/web/templates/concert_detail.html` (~line 74: button; dialog markup at the template's dialog section)
- Modify: both `messages.po` files
- Test: `tests/test_calendar_feed.py` (or `tests/test_concert_page.py` — wherever the concert page render tests live; read both and put them beside their kin)

- [ ] **Step 1: Write the failing tests**

```python
def test_concert_page_offers_calendar_dialog_no_feed_state(client):
    login_as(client, EDITOR_ID, "reiji")
    create_round_with_rule(client, "2099-06-25T23:59", event_id="dlg")
    page = client.get("/concerts/dlg")
    assert "Turn on my calendar feed" in page.text
    assert 'name="next" value="/concerts/dlg"' in page.text


def test_concert_page_calendar_dialog_shows_fresh_url_once(client):
    login_as(client, EDITOR_ID, "reiji")
    create_round_with_rule(client, "2099-06-25T23:59", event_id="dlg2")
    r = client.post("/me/calendar-feed", data={"next": "/concerts/dlg2"})
    page = client.get(r.headers["location"])
    assert "webcal://" in page.text
    # And the has-feed state on the NEXT visit: no URL, honest copy instead.
    page = client.get("/concerts/dlg2")
    assert "webcal://" not in page.text
    assert "already" in page.text.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_calendar_feed.py -q` → the two new tests FAIL (no dialog exists).

- [ ] **Step 3: Implement**

**(a)** `concert_detail` route: add `feed_token: str = ""` as a query parameter, load `db_user = await session.get(User, user.id)` if not already loaded (read the function — `user_tz` may already fetch it), and pass into the template context:

```python
        "has_calendar_feed": bool(db_user and db_user.calendar_token_hash),
        "feed_url": f"{settings.base_url}/calendar/{feed_token}.ics" if feed_token else None,
```

(mirroring `preferences.py:109/133/171` exactly; import `settings` if the module does not already).

**(b)** `concert_detail.html`: on the reader `.bar2` row (line ~74), after the following-toggle include:

```jinja
      <button class="btn quiet" type="button" onclick="document.getElementById('caldlg').showModal()">📅 {{ _("Calendar") }}</button>
```

and the dialog (beside the page's other `<dialog>` markup if any, else before `{% endblock %}`):

```jinja
<dialog id="caldlg" class="picker">
  <div class="dlghead">
    <h3>{{ _("Calendar feed") }}</h3>
    <button class="x" type="button" onclick="this.closest('dialog').close()">×</button>
  </div>
  {% if feed_url %}
    {% include "_feed_links.html" %}
  {% elif has_calendar_feed %}
    <p>{{ _("Your calendar feed is already on — this event's dates and deadlines are in it while you follow the event.") }}</p>
    <p class="clock">{{ _("Lost the link? Generate a new one in Preferences — the old link stops working.") }}</p>
    <p class="inline"><a class="act" href="/preferences#p-deliver">{{ _("Open Preferences") }}</a></p>
  {% else %}
    <p>{{ _("One subscription link keeps your calendar current: every show you follow, and the deadlines that still need you.") }}</p>
    <form method="post" action="/me/calendar-feed">
      <input type="hidden" name="next" value="/concerts/{{ concert.event_id }}">
      <button class="btn" type="submit">{{ _("Turn on my calendar feed") }}</button>
    </form>
  {% endif %}
</dialog>
{% if feed_url %}<script>document.getElementById('caldlg').showModal()</script>{% endif %}
```

Follow the page's existing dialog classes — read a `.picker`/`.tagdlg` dialog in the codebase first and match its header markup (`.dlghead`/`.x` names above are illustrative; use whatever the app's dialogs actually use). NO local backdrop-click handler (the sweep test in `test_theme_and_tokens.py` fails the build if one appears). The auto-open script tag mirrors how a fresh `feed_token` should present: the user just pressed the mint button on this page.

**(c)** Catalogues, both files:

| msgid | ja | zh |
|---|---|---|
| `Calendar` | カレンダー | 日历 |
| `Calendar feed` | カレンダーフィード | 日历订阅 |
| `Your calendar feed is already on — this event's dates and deadlines are in it while you follow the event.` | カレンダーフィードは有効です。このイベントをフォローしている間、公演日と締切はフィードに含まれています。 | 你的日历订阅已开启——只要你关注这个活动，它的演出日期和截止日期就都在里面。 |
| `Lost the link? Generate a new one in Preferences — the old link stops working.` | リンクを紛失した場合は、設定で新しく生成してください。古いリンクは無効になります。 | 链接丢了？在偏好设置里重新生成一个——旧链接会立即失效。 |
| `Open Preferences` | 設定を開く | 打开偏好设置 |
| `One subscription link keeps your calendar current: every show you follow, and the deadlines that still need you.` | 1つの購読リンクでカレンダーが常に最新に。フォロー中の公演と、まだ対応が必要な締切がすべて入ります。 | 一个订阅链接让日历始终最新：你关注的所有演出，以及仍需处理的截止日期。 |
| `Turn on my calendar feed` | カレンダーフィードを有効にする | 开启我的日历订阅 |

- [ ] **Step 4: Run, then full suite**

Run: `uv run --isolated pytest tests/test_calendar_feed.py tests/test_concert_page.py tests/test_i18n_catalogues.py tests/test_theme_and_tokens.py -q` → pass.
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/web/routes/concerts.py src/app/web/templates/concert_detail.html src/app/translations tests/test_calendar_feed.py
git commit -m "feat: concert-page calendar dialog replaces the download story"
```

---

### Task 6: Docs — WISHLIST shipped entry + revision pass, CLAUDE.md

**Files:**
- Modify: `WISHLIST.md`, `CLAUDE.md`

- [ ] **Step 1: WISHLIST.md**

Per the file's discipline: a dated 2026-08-04 pass paragraph above `## Proposed` (this is the second same-day ship of a #1 this file has seen — say so); the calendar entry moves to `## Shipped` as "The calendar story becomes the feed (2026-08-04)" recording: the three rulings and WHERE each landed, the landscape derivation and its standing table, the `/mydeadlines` inheritance (a deliberate behavior change), the canonical-vs-localized qualifier split, the deletions, the shared partial, and the safe_next-based `next` growth. Renumber Proposed 1–13 (pure removal); minute-level offsets returns to #1 — continue its record (ninth displacement ended by removal, the second time in one day this file has done that dance). Re-read remaining entries: the PWA entry (#6 at last count) gets one annotation — a subscribable calendar is the second "works without the site open" surface, which neither raises nor lowers it, but its push-notification argument should now name the feed as prior art. Everything else expected unchanged; verify rather than assume.

- [ ] **Step 2: CLAUDE.md**

Update the `routes/calendar.py` bullet in the Layout section: the feed is the standing-aware landscape (shows + live deadlines over tracked concerts, standing-selected moments), `user_calendar_events` no longer reads `reminder_queue`, rules control DMs only, `/mydeadlines` shares the derivation, and the per-round `.ics` download route no longer exists. Note `CANONICAL_ANCHOR_QUALIFIERS` in `domain/ics_export.py` and the `_feed_links.html` three-consumer partial. A few tight lines in the file's voice; touch nothing else.

- [ ] **Step 3: Full verification, commit**

Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

```bash
git add WISHLIST.md CLAUDE.md
git commit -m "docs: wishlist and CLAUDE.md for the calendar-feed story"
```

---

## Self-Review (done at plan time)

- **Spec coverage:** ruling 1 → Task 3 + 5; ruling 2 + 3 (landscape, standing-aware) → Task 1; qualifier split → Task 2; discoverability/mint-on-first-use/webcal/copy → Tasks 4–5; `/mydeadlines` consequence → Tasks 1–2; copy truth → Tasks 2, 4, 5; docs → Task 6. Out-of-scope list respected (no token changes, no CalDAV, no durations).
- **Type consistency:** `CalendarEvent.anchor: Anchor` produced in Task 1, consumed in Task 2's two renderers; `_allowed_next(raw: str) -> str` local to Task 4; `CANONICAL_ANCHOR_QUALIFIERS` produced in Task 2, referenced in Task 3's deletion survivor list.
- **Placeholder scan:** the two "read the file first" instructions (subscription route's form field, the app's real dialog header classes) are deliberate look-before-writing steps against files whose exact contents this plan verified exist but did not pin — each names the file and line to read and what to extract. No TBDs.
