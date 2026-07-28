# Dead Concerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A concert whose every leg is cancelled stops planning reminders, stops appearing in Coming up, leaves the board unless the reader has standing, and stops offering capture — per `docs/superpowers/specs/2026-07-27-dead-concerts-design.md`.

**Architecture:** One Python predicate (`all_legs_cancelled`), the twin of the SQL `discoverable_concert_criterion` that already encodes this rule for Discover, pinned to it by an agreement test and consumed by the planner and the three personal surfaces. No new rule, no widening of `is_round_cancelled`.

**Tech Stack:** Python 3.12/3.13, SQLAlchemy 2.0 async + SQLite, FastAPI + Jinja2, babel gettext (ja/zh).

## Global Constraints

- `uv run pytest -q` green and `uv run ruff check .` clean before EVERY commit. Suites run in the FOREGROUND. Accepted baseline: exactly 2 pre-existing env failures (`test_test_dm_when_bot_disabled`, `test_healthz`).
- Branch is `cancelled-concerts` (off `main`). Commit there; never switch branches.
- `is_round_cancelled` and its every-leg rule are UNTOUCHED — a general round on a multi-leg concert with one cancelled leg must stay live. This is a concert-level question that predicate cannot answer.
- Invariant 2 holds: cancelled legs stay rows and are never deleted; `applies_to` consumers keep resolving; `notify_newly_cancelled_legs` and its DM are untouched; recorded outcomes are never erased.
- Re-planning must stay safe: the planner change works by contributing no live rounds, letting the existing "no longer planned → delete" pass clear the queue. Do not add a second deletion path.
- New user-visible strings `_()`/`{% trans %}`, hand-filled ja+zh, no fuzzy; run the pybabel cycle and delete `messages.pot`.
- CSS in the main body or inside the existing `@media (max-width: 700px)` / `701-1040px` sections — no new top-level media query (guard pins 6). Radius 3px, existing tokens, both themes. Callout grammar: `.banner` (wash ground, full border) is the "needs attention" shape; `.dgr` is its danger tone. Do not invent a third callout shape.
- Invariant 7: `| tojson` never `| safe`; no user text in inline `on*`; never `data-name`.
- Commit messages as given, plus `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: The predicate, the planner, and Coming up

**Files:**
- Modify: `src/app/db/service.py` (`all_legs_cancelled` beside `discoverable_concert_criterion`; `sync_rule`; `upcoming_deadlines`)
- Test: `tests/test_discover.py` (agreement), `tests/test_service.py` or `tests/test_lottery_outcomes.py` (planner), `tests/test_home.py` (Coming up) — read them and place each test where its fixtures already live; say which you chose.

**Interfaces:**
- Produces:

```python
def all_legs_cancelled(days: Sequence[ConcertDay]) -> bool
    """True when the concert HAS legs and every one is cancelled."""
```

Takes days the caller already holds; issues no query. Docstring must name it the Python twin of `discoverable_concert_criterion` and point at the agreement test.

- [ ] **Step 1: Write the failing tests.**

Agreement (the anti-drift pin — this is the important one):

```python
async def test_the_predicate_agrees_with_the_discover_criterion(session):
    """One rule, two forms: the SQL criterion /discover filters on and the
    Python predicate the personal surfaces use must classify every concert
    the same way, or the two halves of the app disagree about which events
    are dead."""
    live = ...              # one live leg
    partly = ...            # one live leg + one cancelled
    dead = ...              # every leg cancelled
    draft = ...             # no days at all
    visible_ids = set((await session.execute(
        select(Concert.id).where(discoverable_concert_criterion())
    )).scalars())
    for concert in (live, partly, dead, draft):
        await session.refresh(concert, ["days"])
        assert all_legs_cancelled(concert.days) == (concert.id not in visible_ids)
```

Planner:

```python
async def test_cancelling_the_last_live_leg_clears_a_general_rounds_reminders(session):
    # general round (applies_to empty) + a rule + sync_concert -> queue row
    # exists; cancel the only leg, sync_concert again -> the unsent row is gone
    ...

async def test_a_concert_with_one_live_leg_left_keeps_its_reminders(session):
    # two legs, cancel one -> the general round's reminder survives
    ...
```

Coming up:

```python
async def test_a_dead_concert_has_no_coming_up_rows(session): ...
async def test_discovers_public_deadline_list_drops_dead_concerts(session):
    # upcoming_deadlines is shared, so this falls out -- pin it so the two
    # halves of /discover cannot disagree
    ...
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement.**
  - `all_legs_cancelled` as specified.
  - `sync_rule`: where it already computes `cancelled_day_ids` and filters `is_round_cancelled`, additionally empty `live_rounds` when the concert's legs are all cancelled. Both scopes (round-scoped and concert-scoped rules) must be covered; the round-scoped branch has the concert reachable through `round_.concert_id`. Add a comment naming why this cannot be `is_round_cancelled`'s job.
  - `upcoming_deadlines`: skip a concert whose legs are all cancelled, beside the existing `is_round_cancelled` filtering.

- [ ] **Step 4: Run** the chosen files, then the FULL suite + ruff. Existing cancellation suites must stay green untouched — that is the evidence the every-leg rule did not widen.

- [ ] **Step 5: Commit** — `feat: a fully cancelled concert plans nothing and leaves Coming up (task 1)`

---

### Task 2: Close the silent-loss hole, and the fourth surface

Added 2026-07-27 after Task 1's review. Task 1 CREATED the first of these: a
queue row that used to survive (wrongly, but visibly) is now deleted, and
the notice that exists to warn about exactly that loss does not count it.

**Files:**
- Modify: `src/app/db/service.py` (`notify_newly_cancelled_legs`, `upcoming_rounds`)
- Test: `tests/test_service.py` (or wherever the existing `notify_newly_cancelled_legs` tests live — find them and join them)

**Item 1 — the notice must count a dead concert's General rounds.**
`notify_newly_cancelled_legs` builds `affected_round_ids` from
`set(r.applies_to or []) & newly_cancelled_day_ids`. A General round's
`applies_to` is empty, so it is never "affected", so its queue row is never
in `doomed_ids`, so the "did this user lose everything?" probe finds that row
and skips the notice. Failure scenario the reviewer verified: a reader holds
a rule on a leg-specific round AND one on the General round; an editor
cancels both legs; the notice sees the surviving General row and stays
silent; `sync_concert` then deletes it too. The reader loses 100% of their
reminders on that concert with no DM.

Fix: when the newly-cancelled legs leave the concert with NO live leg
(`all_legs_cancelled`), every round on the concert is affected, not just
those naming the cancelled legs. Move the function's docstring with it — it
currently promises "the now-unplanned queue rows *for these legs*", which
becomes under-stated.

This widens what an existing outbox notice covers; it does NOT add a new DM
path or a second writer, so invariant 4 is intact — the notice still goes
through the `notifications` table and the scheduler drains it as before.

**Item 2 — `upcoming_rounds` is a fourth surface, and its docstring now lies.**
It filters on `is_round_cancelled` alone, so a General round on a dead
concert still shows in the bot's `/upcoming` and in `ShowDeadlinesButton` —
and its docstring claims it uses "the same rule `sync_rule`/
`upcoming_deadlines` already use", which stopped being true in Task 1. In a
codebase that leans this hard on comments as the design record, a
cross-reference that says "same rule as X" after X moved is exactly the
drift the agreement test exists to prevent. Thread the predicate and correct
the docstring.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_a_reader_losing_only_a_general_rounds_reminder_is_told(session):
    """The hole Task 1 opened: before it, that row survived; now it is
    deleted, so the notice has to count it."""
    # rule on the General round only; cancel every leg; assert one
    # Notification row for that user
    ...

async def test_a_reader_losing_a_leg_round_and_a_general_round_is_told(session):
    # the reviewer's exact scenario -- the probe used to find the General row
    # and stay silent
    ...

async def test_a_concert_with_a_live_leg_left_still_notifies_only_the_bereft(session):
    # the every-leg rule must not over-fire: a reader with a surviving
    # reminder on the live leg gets NO notice
    ...

async def test_upcoming_rounds_drops_a_dead_concerts_general_round(session): ...
async def test_upcoming_rounds_keeps_a_partly_cancelled_concerts_rounds(session): ...
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** both items.
- [ ] **Step 4:** Run the touched files plus the existing `notify_newly_cancelled_legs` and cancellation suites UNMODIFIED, then the FULL suite + ruff.
- [ ] **Step 5: Commit** — `fix: tell a reader when a dead concert takes their last reminder (task 2)`

---

### Task 3: The board

**Files:**
- Modify: `src/app/db/service.py` (`BoardCard`, `board_cards`), `src/app/web/templates/_board.html`, `src/app/web/static/style.css`
- Modify: both `messages.po`
- Test: `tests/test_board_queries.py`, `tests/test_home.py`

**Interfaces:**
- Consumes: Task 1's `all_legs_cancelled`.
- Produces: `BoardCard` gains `cancelled: bool = False`.

**Method (spec §B3):** pass `has_open_round=False` into `column_for` for a fully-cancelled concert. That single change delivers both halves of the owner decision — with no standing `column_for` returns None and the EXISTING `if column is None: continue` drops the card; with standing the outcome ranks place it in APPLIED / WON / SECURED and never OPEN. Do not add a separate skip branch.

- [ ] **Step 1: Failing tests:**

```python
async def test_a_dead_concert_with_no_standing_leaves_the_board(session): ...
async def test_a_dead_concert_you_won_stays_badged(session):
    assert card.cancelled is True
    assert card.column is Column.WON        # never Column.OPEN
async def test_a_dead_concert_you_applied_to_stays_in_applied(session): ...
async def test_a_concert_with_one_live_leg_is_untouched(session): ...
async def test_the_board_marks_a_cancelled_card(client, db):
    # render assertion: the badge text appears on that card and nowhere else
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** the service half.

- [ ] **Step 4: Template + CSS.** In `_board.html`, render the badge in the card head when `card.cancelled` — reuse the existing badge/pill vocabulary rather than inventing a component; read what the card already renders and match it. CSS (if any new rule is needed) in the main body, danger tone from existing tokens, radius 3px.

- [ ] **Step 5: Catalogue** for the badge string; delete `messages.pot`.

- [ ] **Step 6: Run** the two files, then the FULL suite + ruff. **Commit** — `feat: the board badges a cancelled concert and drops it without standing (task 3)`

---

### Task 4: The concert page

**Files:**
- Modify: `src/app/db/service.py` (`concert_next_moment` and the capture-gate threading), `src/app/web/routes/concerts.py` (context), `src/app/web/templates/concert_detail.html`, `src/app/web/static/style.css`
- Modify: both `messages.po`
- Test: `tests/test_concert_rows.py`, `tests/test_concert_page.py`

**Interfaces:** consumes Task 1's `all_legs_cancelled`.

- [ ] **Step 1: Failing tests:**

```python
async def test_a_dead_concert_leads_with_nothing(session):
    assert concert_next_moment(rows, NOW) is None
async def test_no_round_offers_capture_on_a_dead_concert(session):
    assert all(not r.can_capture and not r.can_report_result for r in rows)
async def test_a_partly_cancelled_concert_still_leads_and_captures(session): ...
async def test_the_concert_page_says_the_event_is_cancelled(client, db): ...
async def test_a_live_concert_page_has_no_cancelled_banner(client, db): ...
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement.** Thread the fact the way `covered` is already threaded into `capture_gates` — one input shutting both gates, NOT a second rule and not a new predicate at each call site. `concert_next_moment` returns None for a dead concert (it already returns None as a real answer when nothing is worth leading with, so this is the same contract, not a new one).

- [ ] **Step 4: Banner.** In `concert_detail.html`, a `.banner.dgr` stating the event is cancelled, placed where the reader meets it before the round list. Per the callout grammar `.banner` is the "needs attention" shape — do not invent a third shape, and do not use `.edgecard` (that is for ongoing state).

- [ ] **Step 5: Catalogue**; delete `messages.pot`.

- [ ] **Step 6: Measure.** Seed a temp dev DB (never the repo's `app.db`), run web-only (empty `DISCORD_TOKEN`), and check the banner and a badged board card at 375/730/1200 in both themes. Record what you observed.

- [ ] **Step 7: Run** the two files + `tests/test_i18n_catalogues.py` + `tests/test_theme_and_tokens.py`, then the FULL suite + ruff. **Commit** — `feat: a dead concert page stops asking for action (task 4)`

---

### Task 5: Closing sweep

- [ ] **Step 1:** `uv run pytest -q` (foreground, full) + `uv run ruff check .`; record tallies.
- [ ] **Step 2:** Smoke against a seeded temp DB: cancel every leg of a concert you have standing on and one you do not; confirm the first keeps a badged card and the second leaves the board; confirm neither appears in Coming up; confirm the concert page shows the banner and no capture; confirm a partly-cancelled concert is untouched throughout.
- [ ] **Step 3:** Spec Status → implemented (2026-07-27) plus an "Implementation deviations (recorded)" section if any arose.
- [ ] **Step 4:** WISHLIST: move #2 to Shipped dated, house style, naming the owner ruling and the fact that the planner was brought into scope beyond the entry's three surfaces; renumber; revision-pass paragraph; fix `#N` cross-references.
- [ ] **Step 5:** CLAUDE.md: invariant 2's paragraph gains a sentence — a concert whose every leg is cancelled contributes no live rounds anywhere, and that concert-level question is `all_legs_cancelled`, the Python twin of `discoverable_concert_criterion`, NOT a widening of `is_round_cancelled`.
- [ ] **Step 6:** Reconcile `docs/superpowers/demo/dekimasen-demo.html` if the badge or banner belongs in it (judge; say either way).
- [ ] **Step 7: Commit** — `chore: dead concerts closing sweep (task 5)`
