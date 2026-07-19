# Home / Discover Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the index into a personal Home ("where do I stand") and a public Discover ("what's on"), driven entirely by data that already exists.

**Architecture:** Pure column-precedence logic in `domain/board.py`; per-user queries in `db/service.py`; two thin routes. `RoundOutcome` already carries applied/won/lost/paid, so **no schema change and no migration**.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx, SQLAlchemy async, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-19-home-discover-split-design.md`

## Global Constraints

- `uv run pytest -q` must pass and `uv run ruff check .` must be clean before every commit. Both are CI gates.
- Baseline: **510 passed, 1 failed**. The failure is `tests/test_crud.py::test_test_dm_when_bot_disabled` — the repo-root `.env` sets a real `DISCORD_TOKEN` while the test assumes empty. Pre-existing, local-only, CI green, **OUT OF SCOPE**. Do not fix it. Verify against reality rather than this plan's arithmetic.
- TDD: failing test first, run it, confirm it fails for the right reason, then implement.
- `src/app/domain/` imports NO discord, fastapi, or sqlalchemy and does no I/O.
- Business logic lives in `db/service.py`. Routes are a thin shell.
- **All `RoundOutcome` writes go through the existing `record_round_outcome`** (`service.py:235`), which enforces the sequence and re-syncs the user's rules. Do not add a second write path.
- `routes/imports.py` MUST stay registered before `routes/concerts.py` in `web/app.py`.
- Times render dual, JST first, via `fmt_dual`.
- Invariant 7: editor URLs through `form_url`; picker script data via `| tojson`, never `| safe`; no user-controlled text in inline `on*` handlers — use `data-` attributes and `dataset`.
- DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener.
- Sentence case everywhere.
- **No schema change.** If a task seems to need one, stop and report — it belongs in a later branch.

## How to read this plan

Task 1 carries complete code because its logic is subtle and has no visual reference.

**Tasks 2-6 are specified, not transcribed** — they name exact function signatures, test cases
and behaviour, but do not reproduce every line of markup and CSS. That is deliberate: the
interactive concept at
`https://claude.ai/code/artifact/ea939428-b99e-43e7-8664-fa276431baba` **is the reference
implementation for all markup, CSS and interaction**, and porting from a working page beats
re-typing it into a document. Use these views:

| Task | View in the concept |
|---|---|
| 4 | **Discover** |
| 5 | **Home** — Closes next, board, Coming up, teaser |
| 6 | the header on any view |

The palette, the pill colour rule, the five-column deadline grid and the responsive breakpoints
are all already solved there. Port them; do not redesign them.

An implementer who finds a test case named but not written should write it to the described
behaviour, and say so in their report if the description was ambiguous.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/app/domain/board.py` (new) | Pure column precedence and rung state. No I/O. |
| `src/app/db/service.py` (modify) | `tracked_concert_ids`, `board_cards`, `my_upcoming_deadlines`. |
| `src/app/web/routes/outcomes.py` (new) | `POST /rounds/{id}/outcome` — the web counterpart to the DM buttons. |
| `src/app/web/app.py` (modify) | `/` becomes Home; register `/discover`; keep router order. |
| `src/app/web/routes/discover.py` (new) | The catalogue page. |
| `src/app/web/templates/home.html` (new) | Closes next, board, Coming up, teaser. |
| `src/app/web/templates/discover.html` (new) | Today's `index.html` content, moved. |
| `src/app/web/templates/_board.html`, `_deadline_rows.html` (new) | Partials; `_deadline_rows` re-renders after a capture action. |
| `src/app/web/templates/base.html` (modify) | Header nav. |

---

### Task 1: Pure board logic

**Files:**
- Create: `src/app/domain/board.py`
- Test: `tests/test_domain_board.py`

**Interfaces:**
- Consumes: `LotteryOutcome` from `app.domain.types`.
- Produces: `Column` (StrEnum: `OPEN`, `APPLIED`, `WON`, `SECURED`), `column_for(outcomes, has_open_round) -> Column | None`, `OPEN_COLUMN_LIMIT = 12`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_domain_board.py
"""Pure board placement. A concert lands in exactly one column, chosen by its
most advanced outcome across all rounds -- money owed outranks a round you
could still enter."""
from app.domain.board import Column, column_for
from app.domain.types import LotteryOutcome as LO


def test_no_outcomes_and_an_open_round_is_open():
    assert column_for([], has_open_round=True) is Column.OPEN


def test_no_outcomes_and_nothing_open_is_absent():
    assert column_for([], has_open_round=False) is None


def test_applied_beats_open():
    assert column_for([LO.APPLIED], has_open_round=True) is Column.APPLIED


def test_won_beats_applied():
    assert column_for([LO.APPLIED, LO.WON], has_open_round=False) is Column.WON


def test_paid_beats_won():
    assert column_for([LO.WON, LO.PAID], has_open_round=False) is Column.SECURED


def test_won_beats_a_later_open_round():
    """You won round 2 and never applied to round 3. The payment you owe is
    the salient fact, not the round you could still enter."""
    assert column_for([LO.WON], has_open_round=True) is Column.WON


def test_lost_alone_with_an_open_round_is_open():
    """Losing a round is not an end state -- the next round is what matters."""
    assert column_for([LO.LOST], has_open_round=True) is Column.OPEN


def test_lost_alone_with_nothing_open_is_absent():
    assert column_for([LO.LOST], has_open_round=False) is None


def test_not_applied_everywhere_with_nothing_open_is_absent():
    assert column_for([LO.NOT_APPLIED, LO.NOT_APPLIED], has_open_round=False) is None


def test_not_applied_does_not_suppress_a_different_open_round():
    assert column_for([LO.NOT_APPLIED], has_open_round=True) is Column.OPEN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_domain_board.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.board'`

- [ ] **Step 3: Implement**

```python
# src/app/domain/board.py
"""Which board column a concert belongs in.

Pure: takes the outcomes already recorded for one concert's rounds plus whether
any of its rounds is currently open, and returns the single column it shows in.
No I/O, no sqlalchemy -- service.py gathers the inputs.

Precedence is deliberate. A concert where you won round 2 and never applied to
round 3 belongs in "Won -- pay", not "Open now": the money you owe outranks the
round you could still enter, and a missed payment loses a ticket you already
have.
"""

import enum

from app.domain.types import LotteryOutcome

# "Open now" is capped so a user following a large franchise does not turn the
# board back into the catalogue this split exists to separate out.
OPEN_COLUMN_LIMIT = 12


class Column(enum.StrEnum):
    OPEN = "open"
    APPLIED = "applied"
    WON = "won"
    SECURED = "secured"


# Only outcomes that place a concert. LOST and NOT_APPLIED deliberately do not:
# neither is an end state, and neither says anything about what happens next.
_RANK: dict[LotteryOutcome, tuple[int, Column]] = {
    LotteryOutcome.APPLIED: (1, Column.APPLIED),
    LotteryOutcome.WON: (2, Column.WON),
    LotteryOutcome.PAID: (3, Column.SECURED),
}


def column_for(
    outcomes: list[LotteryOutcome], has_open_round: bool
) -> Column | None:
    """The one column this concert shows in, or None to leave it off the board."""
    ranked = [_RANK[o] for o in outcomes if o in _RANK]
    if ranked:
        return max(ranked, key=lambda pair: pair[0])[1]
    return Column.OPEN if has_open_round else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_domain_board.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Verify suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 520 passed / 1 pre-existing failure, ruff clean

- [ ] **Step 6: Commit**

```bash
git add src/app/domain/board.py tests/test_domain_board.py
git commit -m "Add pure board column precedence"
```

---

### Task 2: Per-user queries

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_board_queries.py`

**Interfaces:**
- Consumes: `Column`, `column_for`, `OPEN_COLUMN_LIMIT` from Task 1.
- Produces:
  - `async def tracked_concert_ids(session, user_id) -> set[int]`
  - `async def board_cards(session, user_id, now=None) -> dict[Column, list[BoardCard]]`
  - `async def my_upcoming_deadlines(session, user_id, now=None, limit=10) -> list[UpcomingDeadline]`
  - dataclasses `BoardCard(concert, column, rungs, next_deadline, outcome_by_round)` and `Rung(round_id, label, state, detail)` where `state` is one of `"lost" | "won" | "paid" | "applied" | "live" | "todo"`.

- [ ] **Step 1: Write the failing tests**

Cover, with a DB fixture that registers `PRAGMA foreign_keys=ON`:

```python
async def test_tracked_ids_follow_tag_subscriptions(session): ...
async def test_untracked_concert_is_absent_from_the_board(session): ...
async def test_board_places_by_precedence(session): ...      # PAID > WON > APPLIED > open
async def test_open_column_is_capped(session): ...           # 15 open -> 12 returned
async def test_open_column_cap_orders_by_soonest_deadline(session): ...
async def test_rungs_mark_lost_rounds_and_the_live_one(session): ...
async def test_my_deadlines_exclude_untracked_concerts(session): ...
async def test_my_deadlines_exclude_cancelled_legs(session): ...
```

Write the bodies out in full — real fixtures, real assertions.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_board_queries.py -q`
Expected: FAIL with `ImportError: cannot import name 'board_cards'`

- [ ] **Step 3: Implement**

`tracked_concert_ids`: concerts carrying any tag the user has a `TagSubscription` for.
Until `ConcertSubscription` exists (a later branch) this is the definition of "tracked".
Put a comment saying so, naming the branch that replaces it.

`board_cards`: load tracked concerts with `selectinload(Concert.days, Concert.rounds)`, load
this user's `RoundOutcome` rows for those rounds in one query, call `column_for` per concert,
build the ladder, and cap `Column.OPEN` at `OPEN_COLUMN_LIMIT` sorted by soonest deadline.
**Return the pre-cap open count too**, so the template can say "+N more".

`my_upcoming_deadlines`: reuse `upcoming_deadlines` (`service.py:736`) and filter to
`tracked_concert_ids`, or take the same shape with an id filter — do not duplicate its
cancelled-leg handling, which already reuses `is_round_cancelled`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_board_queries.py -q`

- [ ] **Step 5: Verify suite and lint**

- [ ] **Step 6: Commit**

```bash
git add src/app/db/service.py tests/test_board_queries.py
git commit -m "Add per-user board and deadline queries"
```

---

### Task 3: Record an outcome from the web

**Files:**
- Create: `src/app/web/routes/outcomes.py`
- Modify: `src/app/web/app.py` (register the router)
- Test: `tests/test_outcome_routes.py`

**Interfaces:**
- Consumes: `record_round_outcome` (`service.py:235`).
- Produces: `POST /rounds/{round_id}/outcome` taking `outcome` as a form field, returning the re-rendered `_deadline_rows.html` fragment for htmx.

`record_round_outcome` is currently reachable **only** from `bot/views.py:204`. Read that call
site first — this route is its web counterpart and must not diverge in behaviour.

- [ ] **Step 1: Write the failing tests**

```python
def test_i_have_applied_records_applied(client): ...
def test_not_applying_records_not_applied(client): ...
def test_paid_is_reachable_from_won(client): ...
def test_paid_from_no_outcome_does_not_silently_succeed(client): ...
def test_requires_login(client): ...            # 401/redirect, not a write
def test_unknown_round_404s(client): ...
def test_outcome_is_scoped_to_the_calling_user(client): ...
```

That last one matters: two users recording on the same round must not see each other's state.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

Thin route: `require_user`, validate the `LotteryOutcome` value (FastAPI enum coercion gives a
422 for free), call `record_round_outcome`, commit, re-render the fragment. No business logic
in the route.

- [ ] **Step 4-6: Verify and commit**

```bash
git commit -m "Add a web route for recording round outcomes"
```

---

### Task 4: Discover page

**Files:**
- Create: `src/app/web/routes/discover.py`, `src/app/web/templates/discover.html`
- Modify: `src/app/web/app.py`
- Test: `tests/test_discover.py`

Move today's index content — tag sidebar, search, sorts, tile grid — to `GET /discover`.
Use `current_user`, **not** `require_user`: the page is public.

Add: a round-status facet (`Open now` / `Opening soon` / `Not tracking`), a **Next deadline**
sort, and one status pill per card merging the event's round state with the user's standing
(see the spec's table for the exact wording and colour rule). Signed out, render the event
state only.

The tag picker's inline script data uses `| tojson` with raw Python objects — the producers
already do this correctly; do not reintroduce `json.dumps`.

**Tests:** logged-out render (the important one), logged-in render with pills, the facet
filters, and that a signed-out response contains no personal standing.

```bash
git commit -m "Add the Discover page"
```

---

### Task 5: Home page

**Files:**
- Create: `src/app/web/templates/home.html`, `_board.html`, `_deadline_rows.html`
- Modify: `src/app/web/app.py` (the `/` route)
- Test: `tests/test_home.py`

Four blocks in spec order: Closes next, the board, Coming up, the discovery teaser.

Coming up is the five-column table — `Your status` · `Closes` · `Concert` · `What happens` ·
actions — with actions varying by state per the spec. `Not applying` posts, then the follow-up
dialog offers the concert-wide action as a **second** deliberate press.

Signed out: the hero only, plus a link to `/discover`.

**Tests:** signed-out renders the hero and 200s; signed-in renders all four blocks; a concert
appears in the right column; the cap renders 12 and reports the remainder; a row with `APPLIED`
shows "Nothing to do" and no buttons; `Paid` only appears for `WON`.

```bash
git commit -m "Replace the index with a personal Home"
```

---

### Task 6: Navigation and copy

**Files:**
- Modify: `src/app/web/templates/base.html`
- Modify: `CLAUDE.md`
- Test: `tests/test_nav.py`

Header nav: **Home · Discover · Tags**, with the current page marked (`aria-current="page"`).

Update CLAUDE.md's UI conventions: the index-page description currently describes the combined
page with its tag filter and search. Replace it with the Home/Discover split, and record that
capture actions live on deadline rows rather than board cards, with the reason (a row is one
round; a card is a whole campaign).

**Tests:** nav renders on both pages; the active item is marked; signed out shows Discover.

```bash
git commit -m "Add Home and Discover to the nav, update CLAUDE.md"
```

---

## Verification

**Gates:** `uv run pytest -q` (510 baseline + new tests, same single pre-existing failure) and
`uv run ruff check .` clean.

**Drive it** — `uv run python -m app.main`, blank `DISCORD_TOKEN` for web-only mode:

1. Sign in. Home shows Closes next, the board, Coming up.
2. From a Coming up row, press `I have applied`. The row re-renders as "Nothing to do" **and**
   the concert moves from Open now to Applied on the board. Both halves matter — the write is
   only half the feature.
3. Press `Not applying` on a different row. Confirm the follow-up appears and that declining it
   leaves the concert's other rounds intact.
4. Sign out. Home is the hero. `/discover` still browses, with no status pills.
5. Check a phone width: the five-column table stacks, the board goes single-column.

**Reference:** the interactive concept for all of this is at
`https://claude.ai/code/artifact/ea939428-b99e-43e7-8664-fa276431baba` (Home and Discover views).

## Known interim

Until branch 4 adds `ConcertSubscription`, "tracked" means *matches one of your tag
subscriptions* and there is **no way to prune a concert off your Home**. The `OPEN_COLUMN_LIMIT`
cap is what keeps that tolerable. Do not add a pruning mechanism here — it needs the real table,
and a half-version would have to be migrated later.
