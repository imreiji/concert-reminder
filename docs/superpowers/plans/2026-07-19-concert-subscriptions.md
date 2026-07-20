# Concert Subscriptions and Per-Leg Opt-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make per-concert and per-leg following real, without materialising a row per user per concert.

**Architecture:** The subscription row is an *override* on the tag-derived default: no row = follow the default (today's behaviour), a `subscribed` row = explicit opt-in, an `opted_out` row = explicit prune. `tracked_concert_ids` becomes the single place the override is applied; per-leg opt-out folds into the existing `_apply_outcome_suppression` pass. Two new tables, **no backfill migration**.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx, SQLAlchemy async, Alembic, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-19-concert-subscriptions-design.md`

## This branch gates two others — its public API is a contract

Branches 5 (upgrade rounds) and 6 (onboarding) depend on what this branch exposes. Branch 6's plan
names four requirements it needs from here; this plan must satisfy all four:

1. A **clear-override writer** (un-prune → delete the row → back to tag default).
2. A **concert-level opt-out writer** that performs the invariant-2 reminder-queue resync itself.
3. A **read surface** for existing opt-out rows (so setup can show pruned concerts, which the tracked
   set excludes by definition).
4. `tracked_concert_ids` keeps its name and role; only its body changes.

Keep these signatures stable once written — do not rename them in a later task.

## Global Constraints

- `uv run pytest -q` must pass and `uv run ruff check .` must be clean before every commit.
- Baseline: **638 passed, 1 failed**. The failure is `tests/test_crud.py::test_test_dm_when_bot_disabled` (repo-root `.env` sets a real `DISCORD_TOKEN` while the test assumes empty) — pre-existing, local-only, CI green, **OUT OF SCOPE**. Verify against reality, not this plan's arithmetic.
- TDD: failing test first, run it, confirm it fails for the right reason, then implement.
- **Two new tables, NO backfill migration.** If the plan finds itself needing to populate rows for existing users, the design is wrong — stop and reconsider.
- No second `RoundOutcome` write path (invariant 2); suppression folds into the existing pass.
- `tracked_concert_ids` stays the **single** definition of "tracked" — do not add a second.
- Invariant 3 unchanged: overrides are the concert-level analogue of member pruning.
- Invariant 7: no user text in inline `on*` handlers; `data-name` collides with `base.html`'s `filterChips()`.
- Migrations follow CLAUDE.md: NAMING_CONVENTION, batch mode, `sa.DateTime()` not `UTCDateTime()`, ASCII config, and the legacy-anonymous-constraint lesson (pass `naming_convention` into `batch_alter_table` if touching an existing table; these are new tables so a plain `CREATE TABLE` suffices — say so in the revision).
- DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener — the cascade behaviour here is load-bearing.
- Times dual, JST first, via `fmt_dual`. Sentence case. Every page a logged-in GET render test.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/db/models.py` (modify) | `ConcertSubscription`, `LegOptOut`. |
| `alembic/versions/<rev>_concert_subscriptions.py` (new) | Two `CREATE TABLE`s. |
| `src/app/db/service.py` (modify) | Override logic in `tracked_concert_ids`; the writers; leg suppression. |
| `src/app/web/routes/subscriptions.py` (new) | The write routes. |
| `src/app/web/routes/concerts.py` (modify) | Following toggle + per-leg opt-out on the concert page. |
| `src/app/web/templates/concert_detail.html` (modify) | Wire the toggles. |
| `src/app/web/templates/preferences.html` (rewrite) | Left-rail redesign; Following section with counts + restore. |
| `src/app/web/routes/preferences.py` (modify) | Following-section context. |

---

### Task 1: The two tables

**Files:**
- Modify: `src/app/db/models.py`
- Create: the migration
- Test: `tests/test_migration_concert_subscriptions.py`

**Interfaces produced:**
- `ConcertSubscription(user_id, concert_id, state, created_at)` — `state` a `SubscriptionState` StrEnum (`subscribed`, `opted_out`), unique on `(user_id, concert_id)`, both FKs cascade on delete.
- `LegOptOut(user_id, concert_day_id, created_at)` — presence = opted out, unique on `(user_id, concert_day_id)`, both FKs cascade.

- [ ] Step 1: add the `SubscriptionState` enum to `domain/types.py` (pure) and the two models, mirroring the FK/cascade shape of the existing user-owned tables (`WebSession`, `TagSubscription`).
- [ ] Step 2: `uv run alembic revision --autogenerate -m "concert subscriptions"`, then edit: `sa.DateTime()` not `UTCDateTime()`, no `import app.db.models`, and a docstring line noting these are plain `CREATE TABLE`s needing no legacy-schema fixture.
- [ ] Step 3: migration test — both tables land with the right columns; upgrade AND downgrade both run against a real SQLite file; a `PRAGMA foreign_keys=ON` delete of a user cascades both.
- [ ] Steps 4-6: run migration, verify suite + lint, commit.

```bash
git commit -m "Add concert_subscriptions and leg_opt_outs tables"
```

---

### Task 2: The override predicate and the writers

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_subscription_service.py`

**Interfaces produced (the contract branches 5/6 consume):**
- `async def set_concert_subscription(session, user_id, concert_id, state: SubscriptionState) -> None`
- `async def clear_concert_subscription(session, user_id, concert_id) -> None` — deletes the row, back to default. **After either write, re-sync this user's rules for the concert** (invariant 2), the same way `record_round_outcome` re-syncs — a newly-pruned concert must stop reminding.
- `async def set_leg_opt_out(session, user_id, day_id, opted_out: bool) -> None`
- `async def concert_subscription_states(session, user_id) -> dict[int, SubscriptionState]` — the read surface: every explicit override this user holds. Setup and Preferences both need it to show pruned concerts, which `tracked_concert_ids` excludes.

**The predicate change** — rewrite `tracked_concert_ids` (`service.py:812`) to:

```
tracked = (tag-matched concert ids  -  opted_out ids)  ∪  subscribed ids
```

Replace the interim docstring that names this branch. This stays the single definition of tracked.

- [ ] Step 1: failing tests for each rule:
  - no row → tracked iff a followed tag matches (unchanged; pin it)
  - `opted_out` row → not tracked even with a matching tag
  - `subscribed` row → tracked even with no matching tag
  - clearing an `opted_out` row → tracked again if a tag matches
  - a leg opt-out → that leg's rounds suppressed, the other leg's rounds untouched
  - a concert opt-out re-syncs the queue (assert a reminder row for it is gone after)
- [ ] Steps 2-6: confirm failures, implement, verify, commit.

```bash
git commit -m "Add subscription overrides to tracked_concert_ids and the writers"
```

---

### Task 3: Per-leg suppression folds into the existing pass

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_leg_opt_out_suppression.py`

`_apply_outcome_suppression` (`service.py:182`) already drops rounds a user's outcomes make
irrelevant, per user, before the planner sees them. Add a pass that drops rounds **all** of whose
`applies_to` legs this user has opted out of — the per-user analogue of the fully-cancelled-leg rule
already in the codebase.

**Do not touch the write path.** This is a read-side filter, exactly like the outcome and
cancellation passes beside it. A round covering two legs where the user opted out of only one is
**not** suppressed — same rule as cancellation.

Tests: opt out of one leg of a two-leg round → round still planned; opt out of the only leg a round
covers → round suppressed for this user, planned for another user who did not.

```bash
git commit -m "Suppress opted-out legs' rounds per user in the planner"
```

---

### Task 4: Concert-page toggles

**Files:**
- Create: `src/app/web/routes/subscriptions.py`
- Modify: `src/app/web/routes/concerts.py`, `src/app/web/templates/concert_detail.html`, `src/app/web/app.py` (register the router — **keep `imports` before `concerts`**)
- Test: `tests/test_subscription_routes.py`

The Following toggle writes a subscription override; per-leg "Not going to this day" writes a leg
opt-out. Both replace the demo's placeholders. Home's follow-up dialog "Skip this concert entirely"
posts a real concert opt-out here instead of linking away.

**The heavy confirmation** — opting out of a concert where the user holds a `WON`/`PAID` outcome
requires a confirmation naming the specific loss ("You won this ticket. Payment is due Tue 22 Jul.
Opting out stops that reminder and forfeits the ticket."), not a generic prompt. The route still
performs the write; the confirmation is a client gate, but the server must not *require* the outcome
to be gone — opting out never deletes the `RoundOutcome` (spec decision 3).

Routes: `require_user`; user from the session, never the form; a bad concert/day 404s. Return the
re-rendered fragment for htmx, full-page fallback otherwise — reuse branch 1's surface pattern.

Tests: toggle following off → `opted_out` row + concert leaves the board; toggle on → row cleared;
leg opt-out → `LegOptOut` row; opting out of a `WON` concert succeeds and leaves the `RoundOutcome`
intact; every write is scoped to the calling user; a logged-in GET render test for the page.

```bash
git commit -m "Wire the concert-page following and per-leg opt-out toggles"
```

---

### Task 5: Preferences redesign

**Files:**
- Rewrite: `src/app/web/templates/preferences.html`
- Modify: `src/app/web/routes/preferences.py`
- Test: `tests/test_preferences_following.py`

Rebuild Preferences on the demo's left-rail structure (**Preferences** view in the concept): Following,
Reminders, Time, Delivery, Account, Editors — each a section. The rail is built once here so later
branches do not redesign it.

**Following section** carries the real model: per-tag notify / auto-apply toggles, the tracked and
pruned counts ("18 concerts · 2 you pruned"), and a review-and-restore list — each pruned concert
with a control that calls `clear_concert_subscription`. This is what makes a deliberately-invisible
state visible (spec decision 1).

**Account** keeps the setup-rerun button (targets `GET /setup`, added by branch 6) and gains
**Delete my account**, worded to match the erasure the privacy branch shipped: personal data
removed, contributed catalogue kept with the author anonymised.

Reuse existing routes for timezone / preset / feed / editors — do not rewrite their logic, only
their presentation.

Tests: the Following section shows the pruned count and a restore control; restoring calls
`clear_concert_subscription`; a logged-in GET render test; the page renders for a non-admin without
the Editors section.

```bash
git commit -m "Rebuild Preferences with a real Following section"
```

---

### Task 6: CLAUDE.md and WISHLIST.md

**Files:** modify `CLAUDE.md`, `WISHLIST.md`

Document the override model in the invariants (no row = default; a row is an explicit opt-in or
opt-out; prunes stick across re-follow) — this is the kind of non-obvious rule the invariants section
exists for. Move the shipped wishlist entry with the date and do the full re-rank pass. ASCII-only.

```bash
git commit -m "Document the subscription override model"
```

---

## Verification

**Gates:** `uv run pytest -q` (638 baseline + new) and `uv run ruff check .` clean.

**Drive it** — `uv run python -m app.main`, blank `DISCORD_TOKEN`:

1. Follow a tag; its concerts populate Home.
2. Prune one from the concert page; it leaves Home. Preferences says "1 pruned".
3. Unfollow then re-follow the tag; the pruned concert stays gone.
4. Opt out of one leg of a two-day concert; only that leg's rounds stop reminding.
5. Try to opt out of a concert you have won; the heavy confirmation fires, and after confirming the
   outcome is still recorded but the payment reminder stops.
6. Restore the pruned concert from Preferences; it returns to Home.

## Migration note

Two `CREATE TABLE`s, **no backfill**. On deploy every existing board keeps working because "no row"
means "follow the tag default" — the state before this branch. Nothing to populate.
