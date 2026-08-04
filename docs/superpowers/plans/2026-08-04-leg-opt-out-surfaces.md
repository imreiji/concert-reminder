# Leg Opt-Out Suppression on Every Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A leg the reader opted out of stops showing up everywhere — the reminder queue's day rows (and through them the calendar feed, the show-start DM and `/mydeadlines`), Home's Up next / Coming up, the campaign board, the concert page's "Next for you" pick, and `/setup`'s application questions — while a PARTIAL opt-out (a two-leg round with one leg opted out) survives everywhere BY DESIGN.

**Architecture:** One rule, stated once and consumed everywhere (the same shape as `is_round_cancelled` / `all_legs_cancelled`): a round suppresses for a user when its `applies_to` is non-empty and EVERY leg in it is opted out (`_round_fully_opted_out`); a day-derived row suppresses when its own day is opted out. A single batched loader (`user_opted_out_day_ids`) fetches a user's `LegOptOut` rows over any day set in one query. The write side (`set_leg_opt_out`) already owns its invariant-8 resync — no write-path change anywhere in this plan.

**Tech Stack:** Python 3.14, SQLAlchemy async + SQLite, FastAPI, pytest-asyncio (auto mode), Alembic.

**Branch:** `leg-opt-out-surfaces` (off origin/main, already created).

**WISHLIST entry:** #1 "A leg you opted out of keeps showing up everywhere" (2026-08-04, owner report, root cause verified at filing). The entry is the spec.

## Global Constraints

- Run everything with `uv run --isolated` (an external serve.py locks .venv; never plain `uv run`, never resync).
- `uv run --isolated pytest -q` MUST pass and `uv run --isolated ruff check .` MUST be clean before every commit.
- The partial case — a round covering two legs with only ONE opted out — survives on EVERY surface. That mirrors the cancellation rule and is explicitly not this plan's target; if it reads wrong in practice the remedy is labeling, never suppression.
- A round with empty/None `applies_to` (the all-legs / General convention) is tied to no specific leg, so NO set of leg opt-outs ever suppresses it. `_round_fully_opted_out` must read raw `applies_to`, never the all-day-ids fallback.
- An opt-out suppresses informational surfaces only. It NEVER deletes or hides a `RoundOutcome` record itself, and no task here touches `record_round_outcome` or any write path.
- The DB stores aware UTC only (invariant 1); tests build datetimes with `tzinfo=UTC`.
- DB test fixtures register the `PRAGMA foreign_keys=ON` connect listener (the existing fixture in `tests/test_leg_opt_out_suppression.py` already does).
- Every new query added to a read surface is BATCHED (one per call over the whole set), never per-row/per-concert. If a statement-count pin trips (`tests/test_service.py::test_my_deadline_blocks_query_count_is_pinned`, currently `<= 22`), raise it by exactly the queries added, with a comment naming this build.
- Commit messages: end with the standard Co-Authored-By / Claude-Session trailer used on this repo.

## File Structure

- `src/app/db/service.py` — all logic changes: the two shared helpers, `sync_rule`, `_apply_outcome_suppression` (refactor onto the helpers), `UpcomingDeadline`/`upcoming_deadlines`, `my_deadline_rows`, `board_cards`, `RoundRow`/`concert_round_rows`/`_needs_you`, `setup_application_rows`/`setup_tallies`.
- `src/app/web/routes/concerts.py` — one-line skip in `pending_capture_row`.
- `alembic/versions/<new>_clear_unsent_day_reminders_on_opted_out_legs.py` — data migration.
- `tests/test_leg_opt_out_suppression.py` — the home for every new behavior test (its fixtures already build exactly the two-leg concert these tests need).
- `tests/test_migration_opt_out_day_rows.py` — the migration test.
- `WISHLIST.md`, `CLAUDE.md` — the docs task.

---

### Task 1: `sync_rule` plans no day rows for opted-out legs (queue → feed, DM, /mydeadlines)

The `days =` line in `sync_rule` filters candidates by `not d.cancelled` alone, so an `event_start` rule plans show-start rows for legs the user opted out of; those rows are what `user_calendar_events` reads back out for the `.ics` feed, the show-start DM and `/mydeadlines`. The bitter half of the bug: `set_leg_opt_out`'s own resync re-runs this same blind `sync_rule`, so the write that should clear the rows faithfully re-plans them. This task adds the day filter and introduces the shared batched loader.

**Files:**
- Modify: `src/app/db/service.py` (new helper near `_concert_opted_out` ~line 648; `sync_rule`'s concert-scope branch ~line 1134; `_apply_outcome_suppression`'s inline LegOptOut query ~lines 586–597)
- Test: `tests/test_leg_opt_out_suppression.py`

**Interfaces:**
- Produces: `async def user_opted_out_day_ids(session: AsyncSession, user_id: int, day_ids: Iterable[int]) -> set[int]` — this user's `LegOptOut` day ids among `day_ids`, one query, empty-in/empty-out. Tasks 2–5 all consume it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leg_opt_out_suppression.py`. It needs one new fixture-style helper (a concert-scoped EVENT_START rule) and an import of `user_calendar_events`:

```python
# Add to the imports at the top:
from app.db.service import user_calendar_events


async def make_event_rule(s, user: int, concert: Concert) -> ReminderRule:
    """A concert-wide 'remind me at show start' rule -- what plans DAY rows."""
    rule = ReminderRule(
        user_id=user, concert_id=concert.id, anchor=Anchor.EVENT_START, offset_days=0
    )
    s.add(rule)
    await s.flush()
    return rule


async def test_day_rows_not_planned_for_opted_out_leg(session):
    """An event_start rule plans a show-start row per live leg -- but not for
    a leg this user opted out of. The other leg's row survives (partial
    opt-out never widens)."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    await sync_rule(session, rule, NOW)

    day_ids = {row.day_id for row in await queue_rows(session, rule)}
    assert day_ids == {b.id}


async def test_opting_out_clears_already_queued_day_rows(session):
    """Invariant 8's write-owns-the-resync, now covering DAY rows: the queue is
    a materialized outbox, so the set_leg_opt_out write itself must clear the
    show-start row -- before this, its own resync faithfully re-planned it."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)
    await sync_rule(session, rule, NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {a.id, b.id}

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {b.id}

    await set_leg_opt_out(session, USER, a.id, False, now=NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {a.id, b.id}


async def test_day_row_suppression_is_per_user(session):
    """Another user who did not opt out keeps their show-start row."""
    await ensure_user(session, USER, "reiji")
    await ensure_user(session, OTHER, "other")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    mine = await make_event_rule(session, USER, concert)
    theirs = await make_event_rule(session, OTHER, concert)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    await sync_rule(session, mine, NOW)
    await sync_rule(session, theirs, NOW)

    assert await queue_rows(session, mine) == []
    assert {row.day_id for row in await queue_rows(session, theirs)} == {a.id}


async def test_calendar_feed_omits_opted_out_leg(session):
    """The owner's original report was 'shows up on feed': the .ics feed reads
    reminder_queue back out (user_calendar_events), so with the day row gone
    the feed carries only the leg the reader is still going to."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)
    await sync_rule(session, rule, NOW)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    labels = {e.label for e in await user_calendar_events(session, USER, now=NOW)}
    assert "Leg A" not in labels
    assert "Leg B" in labels
```

Note `queue_rows` returns `ReminderQueue` rows, which carry `day_id` — no helper change needed. `make_day` gives both legs the same `starts_at_utc`; that is fine, the assertions key on `day_id`/label, never on order.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py -q`
Expected: the four new tests FAIL (day rows present for the opted-out leg / "Leg A" in the feed). The seven pre-existing tests still pass.

- [ ] **Step 3: Implement the loader and the filter**

In `src/app/db/service.py`, add the loader directly below `_concert_opted_out` (~line 658):

```python
async def user_opted_out_day_ids(
    session: AsyncSession, user_id: int, day_ids: Iterable[int]
) -> set[int]:
    """This user's LegOptOut rows among `day_ids`, as a set -- ONE query,
    whatever the surface. Every read surface that asks "is this leg opted
    out?" loads through here, so none of them can invent a second shape for
    the question (the failure mode invariant 8's entry describes: the rule
    existed in exactly one pass and every other surface never asked)."""
    ids = sorted(day_ids)
    if not ids:
        return set()
    return set((await session.execute(
        select(LegOptOut.concert_day_id).where(
            LegOptOut.user_id == user_id,
            LegOptOut.concert_day_id.in_(ids),
        )
    )).scalars())
```

(`Iterable` is already imported in service.py.)

In `sync_rule`'s concert-scope branch, replace:

```python
        days = [_day_info(d) for d in all_days if not d.cancelled]
```

with:

```python
        # Per-user leg opt-out, applied to DAY candidates exactly as the
        # cancelled filter beside it: fewer candidates in, and the existing
        # "no longer planned -> delete" pass clears any queued show-start
        # rows. Without this, an event_start rule planned rows for legs the
        # user said they are skipping -- and set_leg_opt_out's own resync
        # re-planned them (the write that should clear the rows was the one
        # that restored them). Round suppression is the separate
        # _apply_outcome_suppression pass below; this is the day half.
        opted_out_day_ids = await user_opted_out_day_ids(
            session, rule.user_id, [d.id for d in all_days]
        )
        days = [
            _day_info(d) for d in all_days
            if not d.cancelled and d.id not in opted_out_day_ids
        ]
```

In `_apply_outcome_suppression`, replace the inline LegOptOut query (keep its comment block):

```python
    opted_out_day_ids = set((await session.execute(
        select(LegOptOut.concert_day_id).where(
            LegOptOut.user_id == user_id,
            LegOptOut.concert_day_id.in_(all_day_ids),
        )
    )).scalars()) if all_day_ids else set()
```

with:

```python
    opted_out_day_ids = await user_opted_out_day_ids(session, user_id, all_day_ids)
```

- [ ] **Step 4: Run the file, then the full suite**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py -q` → all pass.
Run: `uv run --isolated pytest -q` → all pass (the refactored query is behavior-identical).
Run: `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/db/service.py tests/test_leg_opt_out_suppression.py
git commit -m "fix: sync_rule plans no day rows for opted-out legs"
```

---

### Task 2: One round rule (`_round_fully_opted_out`) + Home stops showing opted-out legs

`my_upcoming_deadlines` / `my_deadline_rows` drop covered rounds and ineligible upgrades but have NO LegOptOut pass, so a single-leg round on an opted-out leg reaches Up next and Coming up with its capture buttons live — an irreversible APPLIED press on a show the reader said they are skipping. EVENT_START rows are also blind, and `UpcomingDeadline` cannot even say which day it came from. This task extracts the round rule into one named predicate (refactoring `_apply_outcome_suppression`'s inline copy onto it), adds `day_id` to `UpcomingDeadline`, and filters both row shapes in `my_deadline_rows`.

**Files:**
- Modify: `src/app/db/service.py` (`UpcomingDeadline` ~line 1619, `upcoming_deadlines` day loop ~line 1697, new predicate near `_apply_outcome_suppression`, its inline check ~line 621, `my_deadline_rows` ~lines 2416–2586)
- Test: `tests/test_leg_opt_out_suppression.py`

**Interfaces:**
- Consumes: `user_opted_out_day_ids` (Task 1).
- Produces: `def _round_fully_opted_out(round_: Round, opted_out_day_ids: set[int]) -> bool` — Tasks 3–5 consume it. `UpcomingDeadline.day_id: int | None` (set for EVENT_START rows, None for round rows).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leg_opt_out_suppression.py`:

```python
# Add to the service imports:
from app.db.service import my_deadline_blocks, my_deadline_rows


async def test_home_drops_the_show_row_for_an_opted_out_leg(session):
    """Coming up's EVENT_START rows (the show itself) skip a leg this reader
    opted out of; the other leg's row survives."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    rows = await my_deadline_rows(session, USER, now=NOW, concert_ids={concert.id})
    labels = {r.deadline.label for r in rows}
    assert "Leg A" not in labels
    assert "Leg B" in labels


async def test_home_drops_a_round_whose_every_leg_is_opted_out(session):
    """A single-leg round on an opted-out leg must not reach Up next / Coming
    up with live capture buttons -- recording APPLIED there is irreversible."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    rows = await my_deadline_rows(session, USER, now=NOW, concert_ids={concert.id})
    assert all(r.deadline.round_id is None for r in rows)


async def test_home_keeps_a_round_with_one_of_two_legs_opted_out(session):
    """The partial case survives BY DESIGN, mirroring the cancellation rule."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    rows = await my_deadline_rows(session, USER, now=NOW, concert_ids={concert.id})
    assert round_.id in {r.deadline.round_id for r in rows}


async def test_home_blocks_vanish_when_everything_is_opted_out(session):
    """Fully opted out of the only leg: no round row, no show row, so the
    concert contributes no block at all -- Up next reads from these same
    rows, so this is also what keeps it off Up next."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    blocks = await my_deadline_blocks(session, USER, now=NOW, concert_ids={concert.id})
    assert blocks == []
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py -q`
Expected: the four new tests FAIL ("Leg A" present / round row present / block present).

- [ ] **Step 3: Implement**

**(a) The predicate**, directly above `_apply_outcome_suppression` in `src/app/db/service.py`:

```python
def _round_fully_opted_out(round_: Round, opted_out_day_ids: set[int]) -> bool:
    """Invariant 8's round rule, as ONE predicate every surface consumes: a
    round suppresses for a user only when it names specific legs (non-empty
    applies_to) AND every one of them is opted out -- the per-user analogue of
    is_round_cancelled's every-leg rule. Empty/None applies_to (the all-legs /
    General convention) is tied to no specific leg, so no set of leg opt-outs
    can cover it; raw applies_to on purpose, never the all-day-ids fallback,
    precisely so that case falls through untouched. Partial opt-out survives
    BY DESIGN, mirroring partial cancellation."""
    return bool(round_.applies_to) and all(
        d in opted_out_day_ids for d in round_.applies_to
    )
```

**(b)** In `_apply_outcome_suppression`'s survivor loop, replace the inline check (keep a one-line pointer comment):

```python
        if r.applies_to and all(d in opted_out_day_ids for d in r.applies_to):
            continue
```

with:

```python
        # Leg opt-out: the one rule, see _round_fully_opted_out.
        if _round_fully_opted_out(r, opted_out_day_ids):
            continue
```

**(c)** `UpcomingDeadline` gains a defaulted field (below `round_id`):

```python
    # Which ConcertDay an EVENT_START row came from, so a per-user caller
    # (my_deadline_rows) can apply the reader's leg opt-outs. None for round
    # rows -- those carry round_id instead.
    day_id: int | None = None
```

and `upcoming_deadlines`' day loop passes it:

```python
        out.append(UpcomingDeadline(
            concert_title=loc_field(concert, "title", locale),
            event_id=concert.event_id, label=loc_field(d, "label", locale),
            anchor=Anchor.EVENT_START, at_utc=d.starts_at_utc, day_id=d.id,
        ))
```

**(d)** In `my_deadline_rows`, after the `concerts` dict is loaded (~line 2465), add one batched load:

```python
    # This reader's leg opt-outs across every concert on show -- ONE query.
    # Two row shapes consult it below: an EVENT_START row suppresses when its
    # own day is opted out, and a round row suppresses when the round's every
    # named leg is (_round_fully_opted_out). Partial opt-outs survive, same
    # as everywhere else.
    opted_out_ids = await user_opted_out_day_ids(
        session, user_id,
        [day.id for c in concerts.values() for day in c.days],
    )
```

(`Concert.days` is already selectinloaded two lines up.) Then in the row loop, extend the top of the loop body:

```python
    for d in deadlines:
        if d.round_id is not None and d.round_id in covered_ids:
            continue
        if d.day_id is not None and d.day_id in opted_out_ids:
            continue  # the show itself, on a leg this reader said they are skipping
```

and after `round_ = rounds.get(d.round_id) ...` / `outcome = ...` (~line 2550):

```python
        if round_ is not None and _round_fully_opted_out(round_, opted_out_ids):
            continue
```

- [ ] **Step 4: Run the file, the pinned query-count test, then the full suite**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py -q` → all pass.
Run: `uv run --isolated pytest "tests/test_service.py::test_my_deadline_blocks_query_count_is_pinned" -q` → passes (one added batched query; the pin is `<= 22` against a 19 measurement). If it trips, raise the bound by exactly 1 with a comment naming the leg-opt-out load.
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/db/service.py tests/test_leg_opt_out_suppression.py
git commit -m "fix: Home's Up next and Coming up skip opted-out legs"
```

---

### Task 3: The board stops counting fully-opted-out rounds

`board_cards` never consults `LegOptOut`, so an open round on a fully opted-out leg keeps a card in *Open now* — an invitation to act on a show the reader is skipping. The entry left the board's exact behavior to this fix's tests ("skimmed, not pinned"); the design settled here is the exact per-user analogue of the per-round cancellation filter that already exists one line up: a fully-opted-out round leaves `card_rounds` on the live path, so it neither opens the card, nor drives its countdown, nor contributes standing — and if nothing else places the card, the card leaves the board, exactly as a round cancelled by its legs already behaves. The dead-concert path keeps every round (unchanged): a dead card exists only to show standing, never offers actions, and shows no countdown, so there is nothing there for an opt-out to suppress.

**Files:**
- Modify: `src/app/db/service.py` (`board_cards`, ~lines 2037–2067)
- Test: `tests/test_leg_opt_out_suppression.py`

**Interfaces:**
- Consumes: `user_opted_out_day_ids` (Task 1), `_round_fully_opted_out` (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leg_opt_out_suppression.py`:

```python
# Add to the service imports:
from app.db.service import board_cards
from app.domain.board import Column


async def test_board_drops_an_open_card_whose_only_leg_is_opted_out(session):
    """An open round on a fully opted-out leg must not keep a card in Open
    now. With no standing left either, the card leaves the board -- the same
    behavior a round cancelled by its legs already has."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])  # closes 6/25: open at NOW

    columns, open_total = await board_cards(
        session, USER, now=NOW, concert_ids={concert.id}
    )
    assert len(columns[Column.OPEN]) == 1  # sanity: it was on the board

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    columns, open_total = await board_cards(
        session, USER, now=NOW, concert_ids={concert.id}
    )
    assert columns[Column.OPEN] == []
    assert open_total == 0


async def test_board_keeps_a_card_with_one_of_two_legs_opted_out(session):
    """Partial opt-out: the round survives, so the card stays in Open now."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    await make_round(session, concert, [a.id, b.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    columns, _ = await board_cards(session, USER, now=NOW, concert_ids={concert.id})
    assert len(columns[Column.OPEN]) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py -q`
Expected: the first new test FAILS on the post-opt-out assertion; the partial test may already pass (it pins the boundary).

- [ ] **Step 3: Implement**

In `board_cards`, after the `outcomes` batch query (~line 2049), load the opt-outs once for the whole board:

```python
    # This reader's leg opt-outs across the whole board, ONE query (the days
    # are already eager-loaded). Consulted per concert below.
    opted_out_day_ids = await user_opted_out_day_ids(
        session, user_id, [d.id for c in concerts for d in c.days]
    )
```

Then extend the live-path `card_rounds` filter (leave the `dead` branch alone):

```python
        card_rounds = list(concert.rounds) if dead else [
            r for r in concert.rounds
            if not is_round_cancelled(r, cancelled_day_ids)
            # The per-user analogue of the line above (invariant 8): a round
            # whose every named leg this reader opted out of neither opens the
            # card, nor drives its countdown, nor contributes standing -- and
            # with nothing else placing the card, the card leaves the board,
            # exactly as a leg-cancelled round already behaves. The dead path
            # deliberately keeps every round: a dead card is standing-only,
            # offers no actions and counts down to nothing, so there is
            # nothing for an opt-out to suppress there.
            and not _round_fully_opted_out(r, opted_out_day_ids)
        ]
```

- [ ] **Step 4: Run the file, then the full suite**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py tests/test_board_queries.py -q` → all pass.
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/db/service.py tests/test_leg_opt_out_suppression.py
git commit -m "fix: the board stops counting fully-opted-out rounds"
```

---

### Task 4: The concert page's "Next for you" (and catch-up dialog) skip a skipped show

The concert page already dims opted-out legs (`leg_opted_out` in `concert_rounds_context`) — but `_needs_you`, the veto layer over the shared `_wants_you` rule, never asks, so "Next for you" can lead with a round on a leg the reader said they are skipping, and `pending_capture_row` can open the catch-up dialog for one. The rows themselves keep rendering with their capture gates open — the concert page is the one surface that shows the whole campaign in context (it is where you opt back in), and per invariant 8 an opt-out never hides the record.

**Files:**
- Modify: `src/app/db/service.py` (`RoundRow` ~line 3056: new field; `concert_round_rows` ~lines 3198–3348: resolve it; `_needs_you` ~line 3513: veto)
- Modify: `src/app/web/routes/concerts.py` (`pending_capture_row` ~line 1044: skip)
- Test: `tests/test_leg_opt_out_suppression.py`

**Interfaces:**
- Consumes: `user_opted_out_day_ids`, `_round_fully_opted_out`.
- Produces: `RoundRow.opted_out: bool = False` (a round-level fact, identical on every per-leg copy of the row).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leg_opt_out_suppression.py`:

```python
# Add to the service imports:
from app.db.service import concert_next_moment, concert_round_rows


async def test_next_for_you_skips_a_fully_opted_out_round(session):
    """The concert page's 'Next for you' pick must not lead with a round on a
    leg the reader said they are skipping. The row itself still renders (the
    page shows the whole campaign, and it is where you opt back in)."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])

    groups, dateless = await concert_round_rows(session, USER, concert, now=NOW)
    rows = [row for g in groups for row in g.rounds] + dateless
    assert concert_next_moment(rows, now=NOW) is not None  # sanity: open round leads

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    groups, dateless = await concert_round_rows(session, USER, concert, now=NOW)
    rows = [row for g in groups for row in g.rounds] + dateless
    assert rows != []  # the row still renders under its leg
    assert all(r.opted_out for r in rows)
    assert concert_next_moment(rows, now=NOW) is None


async def test_next_for_you_survives_a_partial_opt_out(session):
    """One of two legs opted out: the round still wants you (you are still
    going to the other night), so the pick stands."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    await make_round(session, concert, [a.id, b.id])

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    groups, dateless = await concert_round_rows(session, USER, concert, now=NOW)
    rows = [row for g in groups for row in g.rounds] + dateless
    assert concert_next_moment(rows, now=NOW) is not None


async def test_catch_up_dialog_skips_an_opted_out_round(session):
    """pending_capture_row must not open the catch-up dialog for a round whose
    every leg the reader opted out of -- 'how did this round go?' about a show
    they are skipping is noise with an irreversible answer behind it."""
    from app.web.routes.concerts import pending_capture_row
    from app.db.service import RoundRow
    from app.domain.types import LotteryOutcome

    round_ = Round(
        concert_id=1, kind=RoundKind.LOTTERY_ROUND, label="R1",
        closes_at_utc=dt(5, 25), results_at_utc=dt(5, 26), applies_to=[1],
    )
    row = RoundRow(
        round_=round_, outcome=LotteryOutcome.APPLIED,
        can_capture=True, can_report_result=True, opted_out=True,
    )
    assert pending_capture_row({"leg_groups": [], "all_legs_rows": [row]}) is None
```

(If `LotteryOutcome` import collides with an existing import in the file, use the module-level one instead of the local import.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py -q`
Expected: FAIL — `RoundRow` has no `opted_out` field yet (TypeError on the dialog test), and `concert_next_moment` still picks the opted-out round.

- [ ] **Step 3: Implement**

**(a)** `RoundRow` gains a field (beside `covered`, mirroring its comment style):

```python
    # Every leg this round names is opted out by this viewer (invariant 8's
    # round rule, _round_fully_opted_out) -- a round-level fact, identical on
    # each per-leg copy. It vetoes "Next for you" (_needs_you) and the
    # catch-up dialog, and NOTHING else: the rows keep rendering with their
    # gates open, because the concert page shows the whole campaign in
    # context and is where you opt back in, and an opt-out never hides the
    # record (a RoundOutcome survives it).
    opted_out: bool = False
```

**(b)** In `concert_round_rows`, after `days` are loaded (~line 3204), resolve the viewer's opt-outs (signed out → empty):

```python
    opted_out_day_ids = (
        await user_opted_out_day_ids(session, user_id, [d.id for d in days])
        if user_id is not None else set()
    )
```

and in the row construction, add to the `RoundRow(...)` call:

```python
            opted_out=_round_fully_opted_out(r, opted_out_day_ids),
```

**(c)** `_needs_you` adds the veto (and extends its docstring's veto list with one sentence: an opted-out round wants nothing — the reader said they are skipping every leg it names):

```python
    return (
        not row.covered
        and not row.opted_out
        and not row.concert_cancelled
        and _wants_you(row.outcome, row.can_capture, row.round_.closes_at_utc, now)
    )
```

**(d)** `pending_capture_row` in `src/app/web/routes/concerts.py` extends its skip:

```python
        if row.round_.id in seen or row.covered or row.opted_out or row.upgrade_locked:
            continue
```

- [ ] **Step 4: Run the file, then the full suite**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py tests/test_concert_rows.py tests/test_concert_page.py -q` → all pass.
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/db/service.py src/app/web/routes/concerts.py tests/test_leg_opt_out_suppression.py
git commit -m "fix: Next for you and the catch-up dialog skip opted-out rounds"
```

---

### Task 5: `/setup` stops asking about rounds on skipped legs

`setup_application_rows` filters cancelled and covered rounds but never asks about `LegOptOut`, so screen 2 offers "did you apply?" — an irreversible APPLIED behind it — on a round whose every leg the reader opted out of. This is the same defect the dead-concerts build fixed on this surface for cancellation. `setup_tallies` counts the same round set, so it takes the same filter (a skipped show is not something you are "in").

**Files:**
- Modify: `src/app/db/service.py` (`setup_application_rows` ~lines 2897–2946, `setup_tallies` ~lines 2949–2995)
- Test: `tests/test_leg_opt_out_suppression.py`

**Interfaces:**
- Consumes: `user_opted_out_day_ids`, `_round_fully_opted_out`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leg_opt_out_suppression.py`. `/setup` reads TRACKED upcoming concerts, so these tests need a followed tag:

```python
# Add to the model imports at the top:
from app.db.models import ConcertTag, Tag, TagSubscription
# Add to the type imports:
from app.domain.types import TagKind
# Add to the service imports:
from app.db.service import setup_application_rows, setup_tallies


async def follow_concert(s, user: int, concert: Concert) -> None:
    """Make `concert` tracked for `user` the way production does: a followed
    tag attached to it (setup reads _tracked_upcoming_concerts)."""
    tag = Tag(name="g", kind=TagKind.GROUP)
    s.add(tag)
    await s.flush()
    s.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
    s.add(TagSubscription(user_id=user, tag_id=tag.id))
    await s.flush()


async def test_setup_stops_asking_about_a_fully_opted_out_round(session):
    """Screen 2 must not offer 'did you apply?' -- an irreversible APPLIED
    behind it -- on a round whose every leg the reader opted out of."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, [a.id])
    await follow_concert(session, USER, concert)

    rows = await setup_application_rows(session, USER, NOW)
    assert round_.id in {r.round_.id for r in rows}  # sanity: asked before

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    rows = await setup_application_rows(session, USER, NOW)
    assert round_.id not in {r.round_.id for r in rows}


async def test_setup_still_asks_on_a_partial_opt_out(session):
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])
    await follow_concert(session, USER, concert)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    rows = await setup_application_rows(session, USER, NOW)
    assert round_.id in {r.round_.id for r in rows}


async def test_setup_tallies_exclude_a_fully_opted_out_round(session):
    """The reveal screen's numbers count the same round set screen 2 asks
    about: a skipped show is not a deadline you are waiting on."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_round(session, concert, [a.id])
    await follow_concert(session, USER, concert)

    tallies = await setup_tallies(session, USER, NOW)
    assert tallies.next_deadline_utc is not None  # sanity: counted before

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    tallies = await setup_tallies(session, USER, NOW)
    assert tallies.next_deadline_utc is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py -q`
Expected: the first and third new tests FAIL after the opt-out; the partial test pins the boundary. (If the sanity assertions fail instead, read `_round_asks_application` / `_tracked_upcoming_concerts` and adjust the FIXTURE — e.g. the tag setup — not the assertions.)

- [ ] **Step 3: Implement**

**(a)** `setup_application_rows`: after `surviving` is computed, load once:

```python
    # This reader's leg opt-outs across the surviving set, ONE query. A round
    # whose every named leg is opted out is filtered below exactly as a
    # cancelled or covered one: screen 2's answer (APPLIED) is irreversible,
    # and this reader already said they are skipping that show.
    opted_out_day_ids = await user_opted_out_day_ids(
        session, user_id, [d.id for c in surviving for d in c.days]
    )
```

and in the round loop, after the `is_round_cancelled` skip:

```python
            if _round_fully_opted_out(r, opted_out_day_ids):
                continue
```

**(b)** `setup_tallies`: same load after its `surviving` line (same comment, one line: "Same filter as setup_application_rows -- the reveal counts what screen 2 asks about."), and extend its `live_rounds` comprehension:

```python
        live_rounds = [
            r for r in c.rounds
            if not is_round_cancelled(r, cancelled_day_ids)
            and not _round_fully_opted_out(r, opted_out_day_ids)
        ]
```

- [ ] **Step 4: Run the file, then the full suite**

Run: `uv run --isolated pytest tests/test_leg_opt_out_suppression.py tests/test_setup_service.py tests/test_setup.py -q` → all pass.
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/app/db/service.py tests/test_leg_opt_out_suppression.py
git commit -m "fix: /setup stops asking about rounds on opted-out legs"
```

---

### Task 6: Data migration — clear stale unsent day rows for already-opted-out legs

Task 1 fixes what `sync_rule` PLANS, but `reminder_queue` is a materialized outbox: rows planned BEFORE this fix, for users who opted out before it deploys, sit in the queue until some unrelated write happens to resync that rule — and the scheduler will duly deliver them. That is the owner's own repro, so it must not survive the deploy. Round-anchored rows are NOT stale (the round pass has existed in `_apply_outcome_suppression` since per-leg opt-outs shipped, and `set_leg_opt_out` resyncs on write) — only day-anchored rows are. Deleting UNSENT rows is always safe (invariant 2: re-planning is safe; opting back in re-plans them).

**Files:**
- Create: `alembic/versions/<hash>_clear_unsent_day_reminders_on_opted_out_legs.py` (via `alembic revision`, down_revision `aba3e97e4467`)
- Test: `tests/test_migration_opt_out_day_rows.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_migration_opt_out_day_rows.py`, modeled on `tests/test_migration_welcomed_at.py` (same `_alembic_config` shape). FKs are unenforced on a plain sqlite3 connection, so the fixture needs only the three tables the DELETE reads — no users/concerts rows:

```python
"""Migration test: clearing stale unsent day-anchored reminders on opted-out legs.

sync_rule now plans no day rows for a leg its user opted out of, but the queue
is a materialized outbox: rows planned BEFORE that fix stay queued until some
unrelated write resyncs the rule, and the scheduler delivers them meanwhile.
This migration deletes exactly those rows: UNSENT, day-anchored, where the
rule's own user holds a LegOptOut on that day. Sent rows are history and stay;
other users' rows and other days' rows stay; round-anchored rows were never
stale (the round pass ran at write time since per-leg opt-outs shipped).
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_MIGRATION_REVISION = "aba3e97e4467"  # head immediately before this migration


def _alembic_config(monkeypatch, db_path: Path) -> Config:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def test_stale_unsent_day_rows_on_opted_out_legs_are_deleted(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, PRE_MIGRATION_REVISION)

    con = sqlite3.connect(db_path)  # plain connection: FKs off, no parent rows needed
    con.execute(
        "INSERT INTO reminder_rules (id, user_id, concert_id, round_id, anchor, "
        "offset_days, offset_hours, channel, created_at) "
        "VALUES (1, 42, NULL, NULL, 'event_start', 0, 0, 'dm', '2026-06-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO reminder_rules (id, user_id, concert_id, round_id, anchor, "
        "offset_days, offset_hours, channel, created_at) "
        "VALUES (2, 99, NULL, NULL, 'event_start', 0, 0, 'dm', '2026-06-01 00:00:00')"
    )
    # User 42 opted out of day 501; user 99 did not.
    con.execute(
        "INSERT INTO leg_opt_outs (id, user_id, concert_day_id, created_at) "
        "VALUES (1, 42, 501, '2026-06-01 00:00:00')"
    )
    rows = [
        # (id, rule_id, round_id, day_id, sent_at) -- expected fate in comment
        (1, 1, None, 501, None),                    # DELETED: unsent, opted-out day
        (2, 1, None, 502, None),                    # kept: other day
        (3, 1, None, 501, "2026-05-01 00:00:00"),   # kept: already sent (history)
        (4, 2, None, 501, None),                    # kept: other user's rule
        (5, 1, 900, None, None),                    # kept: round-anchored, day_id NULL
    ]
    for id_, rule_id, round_id, day_id, sent in rows:
        con.execute(
            "INSERT INTO reminder_queue (id, rule_id, round_id, day_id, anchor, "
            "fire_at_utc, sent_at_utc) VALUES (?, ?, ?, ?, 'event_start', "
            "'2026-08-01 09:00:00', ?)",
            (id_, rule_id, round_id, day_id, sent),
        )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    remaining = {r[0] for r in con.execute("SELECT id FROM reminder_queue").fetchall()}
    assert remaining == {2, 3, 4, 5}
    con.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_migration_opt_out_day_rows.py -q`
Expected: FAIL — `remaining` still contains id 1 (head is `aba3e97e4467`, no such migration yet).

- [ ] **Step 3: Write the migration**

Run: `uv run --isolated alembic revision -m "clear unsent day reminders on opted out legs"` (NO `--autogenerate` — pure data migration). Edit the generated file so upgrade/downgrade read:

```python
def upgrade() -> None:
    # Deletes exactly the rows sync_rule would no longer plan: UNSENT,
    # day-anchored, where the rule's own user holds a LegOptOut on that day.
    # Rows planned before the fix otherwise sit queued until some unrelated
    # write resyncs the rule, and the scheduler delivers them meanwhile.
    # Unsent-only: sent rows are history (the delivery already happened);
    # deleting unsent rows is always safe (invariant 2 -- re-planning is
    # safe, and opting back in re-plans them). Round-anchored rows are not
    # stale: the round-suppression pass ran at write time since per-leg
    # opt-outs shipped.
    op.execute(
        """
        DELETE FROM reminder_queue
        WHERE sent_at_utc IS NULL
          AND day_id IN (
            SELECT lo.concert_day_id
            FROM leg_opt_outs lo
            JOIN reminder_rules rr ON rr.user_id = lo.user_id
            WHERE rr.id = reminder_queue.rule_id
          )
        """
    )


def downgrade() -> None:
    # Nothing to restore: the deleted rows are exactly what any resync
    # re-plans (invariant 2), and after downgrading the code re-plans them.
    pass
```

Verify the file is ASCII-only (the owner's GBK-locale rule for configs applies to migration files' comments too — no em-dashes).

- [ ] **Step 4: Run the migration test, then the full suite**

Run: `uv run --isolated pytest tests/test_migration_opt_out_day_rows.py -q` → passes.
Run: `uv run --isolated alembic upgrade head` against the dev DB (sanity: it applies cleanly).
Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions tests/test_migration_opt_out_day_rows.py
git commit -m "fix: clear stale unsent day reminders on already-opted-out legs"
```

---

### Task 7: Docs — WISHLIST shipped entry + revision pass, CLAUDE.md, the discover_statuses verdict

The entry's sweep clause named two more read surfaces to CHECK, not necessarily change. The verdicts, recorded so they are decisions: **`discover_statuses` is deliberately unchanged** — its event-state pill ("Open now") is a fact about the catalogue, not about the viewer (a concert-level prune does not hide catalogue state either), and its standing pill renders from `RoundOutcome` records, which an opt-out never touches by invariant 8's own rule. **`_wants_you` itself is unchanged** — the veto belongs in `_needs_you` (done, Task 4) and Home's rows are filtered upstream (Task 2), so the shared primitive stays ignorant of opt-outs exactly as it is of coverage and cancellation.

**Files:**
- Modify: `WISHLIST.md` (move entry #1 to Shipped; write the pass paragraph; renumber; run the revision pass)
- Modify: `CLAUDE.md` (invariant 8's opt-out paragraph)

- [ ] **Step 1: WISHLIST.md**

Following the file's own discipline (see its header): add a dated pass paragraph above `## Proposed` recording that the 2026-08-04 build ships #1 the day it was filed; move the entry to `## Shipped` as "Per-leg opt-out suppression reaches every surface (2026-08-04)" recording: the one-rule shape (`_round_fully_opted_out` + `user_opted_out_day_ids`), the surfaces fixed (queue day rows → feed/DM/mydeadlines, Home, board, Next-for-you + catch-up dialog, /setup), the board design settled by this build's tests (cancellation-mirror: a fully-opted-out round leaves the live card entirely), the two checked-and-unchanged verdicts above with their reasons, the data migration and WHY it exists (materialized outbox; the write-time resync was the thing re-planning the rows), and the partial-case survival being pinned by tests on every surface. Renumber the remaining Proposed entries 1–14 (pure removal, nothing on merit); the calendar-story entry rises to #1 and its "One constraint flows DOWN from here" paragraph in the OLD #1 is now satisfied — note inside the calendar entry that the opted-out-leg constraint on the future feed is already enforced at the queue (this build), so that design inherits it for free. Minute-level offsets returns to #2; continue its displacement record with one line. Re-read each remaining entry against what shipped and record anything cheaper/changed (expected: nothing — this build lived in read-surface filters and one migration).

- [ ] **Step 2: CLAUDE.md**

In invariant 8, update the per-leg opt-out passage: the suppression is no longer "folded into `_apply_outcome_suppression`" alone — the round rule is `_round_fully_opted_out` and the loader `user_opted_out_day_ids` (both `db/service.py`), consumed by the planner, Home's rows, the board's live card set, the concert page's `_needs_you` veto and `/setup`; day candidates are filtered in `sync_rule` itself. Name the deliberate non-consumers (Discover's pills; the concert page's row rendering and capture gates) so nobody "fixes" them later. Keep it to a few lines — the invariant already carries the rule's statement.

- [ ] **Step 3: Full verification**

Run: `uv run --isolated pytest -q` → all pass.
Run: `uv run --isolated ruff check .` → clean.

- [ ] **Step 4: Commit**

```bash
git add WISHLIST.md CLAUDE.md
git commit -m "docs: wishlist and CLAUDE.md for the opt-out surface sweep"
```

---

## Self-Review (done at plan time)

- **Spec coverage:** entry's three named surfaces (queue day rows → Task 1+6; Home read path → Task 2; board → Task 3) plus the sweep clause (`_wants_you` family → Task 4; `discover_statuses` → Task 7 verdict) plus the per-surface failing-first tests the entry demands → each task's Step 1. The entry's "one rule applied uniformly" → the two shared helpers. The partial-case survival → pinned in Tasks 2, 3, 4, 5.
- **Types:** `user_opted_out_day_ids(session, user_id, day_ids) -> set[int]` and `_round_fully_opted_out(round_, opted_out_day_ids) -> bool` used identically in Tasks 1–5; `UpcomingDeadline.day_id: int | None` produced in Task 2 and consumed only there; `RoundRow.opted_out: bool` produced and consumed in Task 4.
- **Placeholders:** none — every step carries runnable code or an exact command.
