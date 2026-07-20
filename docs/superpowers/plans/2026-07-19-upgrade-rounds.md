# Upgrade Rounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model the Japanese upgrade round — a nested second campaign only ticket-holders from a set of qualifying rounds may enter — end to end: schema, per-user eligibility, reminder planning, board/Discover display, and editor.

**Architecture:** One new JSON column (`Round.qualifies_round_ids`, mirroring `applies_to`), one new pure eligibility helper (`domain/upgrades.py`), and eligibility threaded through the three existing per-user seams: `_apply_outcome_suppression`, `column_for`, and the status pills. The pure planner in `domain/reminders.py` is not modified — like cancellation and outcomes, eligibility just filters its candidate list.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx, SQLAlchemy async, SQLite + Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-07-19-upgrade-rounds-design.md`

## Global Constraints

- `uv run pytest -q` must pass and `uv run ruff check .` must be clean before every commit.
- Baseline: **638 passed, 1 failed**. The failure is `tests/test_crud.py::test_test_dm_when_bot_disabled` — the repo-root `.env` sets a real `DISCORD_TOKEN` while the test assumes empty. Pre-existing, local-only, CI green, **OUT OF SCOPE**. Verify against reality, not this plan's arithmetic.
- TDD: failing test first, run it, confirm it fails for the right reason, then implement.
- **No second `RoundOutcome` write path** (invariant 2): every outcome write goes through `record_round_outcome` via the existing `POST /rounds/{round_id}/outcome` (`web/routes/outcomes.py:95`). Queue rows are only reconciled by the `sync_*` functions.
- **Migration rules** (CLAUDE.md, these have bitten before): keep `Base.metadata`'s NAMING_CONVENTION — SQLite batch/table-rebuild mode refuses the legacy anonymous constraints; after autogenerate, ALWAYS open the revision and replace any `app.db.models.UTCDateTime()` with `sa.DateTime()` and remove the `import app.db.models` line (this revision should need neither — perform the check anyway and say so in your report); ASCII only in the revision file (GBK-locale Windows); never touch the `coalesce()` dedupe index on `reminder_queue`.
- **Migration inventory for this branch:** exactly one — a plain nullable `batch_op.add_column` on `rounds`. **No data migration, no backfill, no CREATE TABLE, no table rebuild.** Adding the `RoundKind.UPGRADE` enum member needs no migration at all (string-stored via `values_callable`; precedent: FCFS/tour package shipped with none).
- Business logic in `db/service.py`; `src/app/domain/` stays pure (no I/O, no sqlalchemy/discord/fastapi imports).
- Times dual, JST first, via `fmt_dual`. Sentence case. Every page touched keeps a logged-in GET render test.
- `routes/imports.py` stays registered before `routes/concerts.py` in `web/app.py`.
- Invariant 7: round labels are user-controlled — text content + `data-` attributes only, never inline `on*` handlers; inline script data via `| tojson`; avoid `data-name` (collides with `base.html`'s `filterChips()`).
- DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener.

## How to read this plan

The interactive concept at `https://claude.ai/code/artifact/ea939428-b99e-43e7-8664-fa276431baba` is the **reference for markup and exact label text**: the Discover cards show the pill pairs (`Secured` + `Upgrade · Closes in 3d`, `Secured` + `Upgrade · Applied`, and the collapsed `Upgrade won — pay by 24 Jul`), the Home "Coming up" row shows `Entered upgrade` / `Skipping`, and the Editor view shows the `.upgradebox` callout with the "Qualifies" chip row (最速先行 / 先行抽選 R1 / 一般発売). Port from it; do not redesign.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/domain/types.py` (modify) | `RoundKind.UPGRADE = "upgrade"`. |
| `src/app/domain/upgrades.py` (new) | Pure `is_eligible`. |
| `src/app/domain/board.py` (modify) | Upgrade-aware `column_for` precedence. |
| `src/app/db/models.py` (modify) | `Round.qualifies_round_ids` JSON column. |
| `alembic/versions/<rev>_round_qualifies.py` (new) | The one migration: nullable add_column. |
| `src/app/db/service.py` (modify) | Label map; suppression eligibility pass; auto-arm exclusions; `board_cards` / `discover_statuses` / `my_deadline_rows` / `concert_round_rows` / `capture_gates` eligibility. |
| `src/app/bot/messages.py` (modify) | `KIND_EMOJI["upgrade"] = "⬆️"`. |
| `src/app/web/routes/concerts.py` (modify) | `parse_round_qualifiers`, save/read paths, validation. |
| `src/app/web/templates/_round_qualifier_chips.html` (new) | The "Qualifies" chip row, mirroring `_round_leg_chips.html`. |
| `src/app/web/templates/concert_edit.html` (modify) | Upgradebox + qualifier chips per round row, kind-select toggle. |
| `src/app/web/templates/discover.html` + `_board.html` + `_deadline_rows.html` + `_round_rows.html` + `_capture_actions.html` (modify) | Second pill, upgrade capture labels, requirement line. |
| `src/app/web/static/style.css` (modify) | `.s-up` accent pill, `.upgradebox`. |
| `tests/test_upgrade_rounds.py` (new) | Eligibility, suppression, planner, board. |
| `tests/test_upgrade_rounds_web.py` (new) | Discover pills, Home rows, concert page, editor round-trips. |

---

### Task 1: Vocabulary — kind, label, emoji

**Files:**
- Modify: `src/app/domain/types.py`, `src/app/db/service.py` (`LABEL_BY_ROUND_KIND`, line ~710), `src/app/bot/messages.py` (`KIND_EMOJI`, line 16)
- Test: `tests/test_upgrade_rounds.py`

- [ ] **Step 1: Write failing tests**

```python
def test_every_round_kind_has_a_label():
    assert set(LABEL_BY_ROUND_KIND) == set(RoundKind)

def test_every_round_kind_has_an_emoji():
    assert set(KIND_EMOJI) == {k.value for k in RoundKind}

def test_upgrade_kind_vocabulary():
    assert RoundKind.UPGRADE.value == "upgrade"
    assert LABEL_BY_ROUND_KIND[RoundKind.UPGRADE] == "Upgrade round"
```

If an exhaustiveness test for the maps already exists from the FCFS branch, extend it there instead of duplicating — check `git grep -n "LABEL_BY_ROUND_KIND" tests/` first and say which you did in your report.

- [ ] **Step 2: Run them, confirm they fail** (`AttributeError: UPGRADE` / missing key)
- [ ] **Step 3: Add `UPGRADE = "upgrade"`** to `RoundKind` with a doc comment (a second nested campaign entered only by holders of a qualifying round's ticket), `"Upgrade round"` to `LABEL_BY_ROUND_KIND`, `"upgrade": "⬆️"` to `KIND_EMOJI`
- [ ] **Step 4: Tests pass.** The kind auto-appears in every `RoundKind` iteration dropdown (`concert_new.html`, `concert_edit.html`, `import_preview.html`) — no template edits needed for that
- [ ] **Step 5: Suite + lint**
- [ ] **Step 6: Commit**

```bash
git commit -m "Add the upgrade round kind with label and emoji"
```

---

### Task 2: Schema — `qualifies_round_ids` and its migration

**Files:**
- Modify: `src/app/db/models.py` (class `Round`, models.py:286)
- Create: the Alembic revision
- Test: `tests/test_upgrade_rounds.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_qualifies_round_ids_round_trips(session):
    """A JSON list of round ids survives ORM write + reload; NULL stays None."""
    # seed a concert, a lottery round r1, and an upgrade round with
    # kind=RoundKind.UPGRADE, qualifies_round_ids=[r1.id]
    # expire_all, reload, assert the list and that r1's own column is None
```

- [ ] **Step 2: Confirm it fails** (`TypeError: 'qualifies_round_ids' is an invalid keyword argument`)
- [ ] **Step 3: Add the column** below `applies_to`, mirroring it:

```python
qualifies_round_ids: Mapped[list | None] = mapped_column(JSON)  # optional round ids (UPGRADE kind only)
```

with a comment stating the semantics: only meaningful when `kind == UPGRADE`; empty/None means "any secured ticket on this concert qualifies"; plain JSON like `applies_to` — no FK, ids validated at the route boundary and filtered at read time.

- [ ] **Step 4: Generate and edit the migration**

```
uv run alembic revision --autogenerate -m "round qualifies_round_ids"
```

Then open the revision and verify: it is exactly one `batch_alter_table("rounds")` with `add_column(sa.Column('qualifies_round_ids', sa.JSON(), nullable=True))` and the matching `drop_column` in downgrade (shape of `alembic/versions/1430ba5bbc7e_concert_day_cancelled.py`); no `UTCDateTime` and no `import app.db.models` (remove/replace if autogenerate emitted them); ASCII only. **This is a plain nullable ADD COLUMN — if autogenerate emitted anything else (index changes, constraint churn), stop and investigate before committing.**

- [ ] **Step 5: `uv run alembic upgrade head`** against a scratch copy, then the test passes
- [ ] **Step 6: Suite + lint, commit**

```bash
git commit -m "Give rounds an optional qualifying-round set"
```

---

### Task 3: Pure eligibility — `domain/upgrades.py`

**Files:**
- Create: `src/app/domain/upgrades.py`
- Test: `tests/test_upgrade_rounds.py`

**Interfaces:**
- Produces: `def is_eligible(upgrade_round_id: int, qualifying_ids: list[int] | None, outcome_by_round: dict[int, LotteryOutcome]) -> bool`
- Pure: imports only `app.domain.types`. No I/O, no sqlalchemy.

Rules (from the spec): True when the user holds WON or PAID on any round in `qualifying_ids`; when `qualifying_ids` is empty/None, on **any round other than the upgrade round itself**; OR when `outcome_by_round[upgrade_round_id]` is APPLIED/WON/LOST/PAID (self-attestation — acting on the round is testimony of holding a ticket the app was not told about). NOT_APPLIED and absence never qualify.

- [ ] **Step 1: Failing tests** — write each as its own test function:
  - `test_qualifying_won_qualifies`, `test_qualifying_paid_qualifies`
  - `test_qualifying_applied_does_not`, `test_qualifying_lost_does_not`
  - `test_empty_set_accepts_any_other_secured_round`
  - `test_empty_set_ignores_the_upgrade_rounds_own_outcome_for_the_fallback` (own WON must not satisfy the *fallback* clause — it satisfies self-attestation instead; assert via NOT_APPLIED on the upgrade + no other outcomes -> False)
  - `test_self_attestation_applied_qualifies`, `test_not_applied_is_not_attestation`
  - `test_win_outside_the_qualifying_set_does_not_qualify` (non-empty set, WON on a round not in it)
- [ ] **Step 2: Confirm failure** (`ModuleNotFoundError`)
- [ ] **Step 3: Implement** (~15 lines + docstring explaining the self-attestation clause and the empty-set convention)
- [ ] **Step 4: Tests pass, suite + lint**
- [ ] **Step 5: Commit**

```bash
git commit -m "Derive upgrade eligibility from recorded outcomes"
```

---

### Task 4: Suppression, planner, auto-arm — the invariant-2 heart

**Files:**
- Modify: `src/app/db/service.py` — `_apply_outcome_suppression` (line 182), `_next_round_for_leg` (line 283), `_auto_arm_next_round` (line 310)
- Test: `tests/test_upgrade_rounds.py`

**No changes to `domain/reminders.py` or `sync_rule`'s reconciliation** — eligibility is one more filter on the candidate list, in the exact place cancellation and outcome suppression already sit.

- [ ] **Step 1: Write the failing tests.** Same fixture shape as the existing suppression tests (find them with `git grep -n "_apply_outcome_suppression\|outcome_suppression" tests/`). Seed: concert, one leg, base lottery round `r1` (closes, results, payment set), upgrade round `up` (kind=UPGRADE, qualifies_round_ids=[r1.id], its own closes/results/payment):

```python
async def test_eligible_users_upgrade_reminder_survives_base_paid(session):
    """THE regression the unmodified cross-round pass would cause: base WON
    then PAID secures the leg, which today suppresses every other round
    covering it -- including the upgrade the win just made enterable."""
    # record_round_outcome r1 WON, then PAID; concert-wide CLOSES rule
    # assert the queue still holds the rule's row for `up`'s close

async def test_ineligible_users_upgrade_rule_plans_nothing(session):
    # user B, no outcomes; round-scoped CLOSES rule on `up`; sync_rule
    # assert zero queue rows

async def test_recording_the_qualifying_win_makes_upgrade_reminders_appear(session):
    # user B from above records r1 WON via record_round_outcome
    # assert the queue row for `up` now exists (reinstate_user_rules re-sync)

async def test_two_sequential_payment_deadlines_on_one_concert(session):
    """One concert-wide PAYMENT rule; base r1 PAID, upgrade WON.
    Exactly one unsent payment row remains and it is the upgrade's --
    the dedupe key (rule_id, round_id, day_id, anchor) keeps them apart."""

async def test_upgrade_paid_clears_its_payment_row(session):
    # continue: record `up` PAID; assert no unsent payment rows remain

async def test_losing_an_upgrade_arms_nothing(session):
    # `up` APPLIED then LOST; assert no auto-created ReminderRule appeared

async def test_losing_a_base_round_never_arms_an_upgrade(session):
    # r1 LOST with `up` the only later round; assert no rule on `up`

async def test_upgrade_won_still_suppresses_ordinary_rounds(session):
    # a later general-sale round on the same leg; `up` WON; concert-wide
    # OPENS rule -> the general sale's row is suppressed (upgrade outcomes
    # still feed secured_by for OTHER rounds)
```

- [ ] **Step 2: Run, confirm each fails for the stated reason** (in particular the first: the row is *deleted* today)
- [ ] **Step 3: Implement in `_apply_outcome_suppression`:**
  - The `outcomes` dict for the whole concert is already loaded — reuse it for `is_eligible`; no new queries.
  - In the survivors loop: `if r.kind is RoundKind.UPGRADE:` — skip the `applies <= secured_elsewhere` continue (exemption), and `continue` instead when `not is_eligible(r.id, r.qualifies_round_ids, outcomes)`.
  - `secured_by` construction unchanged — upgrade WON/PAID still suppresses other ordinary rounds.
  - Same-round anchor pass (RESULTS/PAYMENT vs own outcome) unchanged and still applied to upgrade rounds.
  - Extend the docstring: three passes now, and why the exemption exists (a secured ticket is the prerequisite, not a substitute).
- [ ] **Step 4: Implement auto-arm guards:** `_auto_arm_next_round` returns early when `lost_round.kind is RoundKind.UPGRADE` (losing an upgrade ends that side campaign successfully); `_next_round_for_leg` filters `Round.kind != RoundKind.UPGRADE` from candidates.
- [ ] **Step 5: Tests pass, full suite + lint** (watch the existing suppression and auto-arm tests — they must not regress)
- [ ] **Step 6: Commit**

```bash
git commit -m "Plan upgrade-round reminders only for eligible users"
```

---

### Task 5: Board precedence

**Files:**
- Modify: `src/app/domain/board.py`, `src/app/db/service.py` (`board_cards` line 894, `discover_statuses` call site line 1504)
- Test: `tests/test_upgrade_rounds.py` (pure), existing board tests updated for the signature

**Interface change:** `column_for(outcomes: list[tuple[LotteryOutcome, bool]], has_open_round: bool)` — each outcome tagged `is_upgrade`. Update both callers (`board_cards`, `discover_statuses`); the module stays pure.

Precedence (extend `_RANK`, document in the module docstring using its own rationale — money owed outranks a ticket held): upgrade WON ranks **above** base PAID (rank 4 -> `Column.WON`); upgrade PAID ranks as SECURED; upgrade APPLIED ranks as base APPLIED (rank 1 — never demotes a secured base because max() wins); LOST/NOT_APPLIED still place nothing.

- [ ] **Step 1: Failing tests**
  - `test_base_paid_plus_upgrade_won_lands_in_won_pay`
  - `test_upgrade_paid_lands_in_secured`
  - `test_upgrade_applied_leaves_a_secured_base_in_secured`
  - `test_upgrade_lost_leaves_base_standing_untouched`
  - `test_open_upgrade_round_does_not_pull_secured_back_to_open` (through `board_cards`, has_open_round=True)
- [ ] **Step 2: Confirm failure** (base PAID + upgrade WON asserts WON, gets SECURED)
- [ ] **Step 3: Implement + update both callers and any existing tests calling `column_for` with the old shape**
- [ ] **Step 4: Suite + lint, commit**

```bash
git commit -m "Rank a won upgrade's payment above a secured base ticket"
```

---

### Task 6: Discover pills — two facts, two pills

**Files:**
- Modify: `src/app/db/service.py` (`DiscoverStatus` line 1406, `discover_statuses` line 1453), `src/app/web/templates/discover.html`, `src/app/web/static/style.css`
- Test: `tests/test_upgrade_rounds_web.py`

**Interface:** `DiscoverStatus` gains `upgrade_text: str | None = None` and `upgrade_tone: str | None = None`; tones gain `"accent"` (template class `s-up`, ported from the concept: accent color + accent wash).

Pill rules (exact texts from the concept; dates via the existing `_day_month`, countdowns via `_humanize_until`):

| Upgrade state (eligible user) | Result |
|---|---|
| open, no outcome | base pill + `Upgrade · Closes in 3d` (accent) |
| APPLIED | base pill + `Upgrade · Applied` (accent) |
| WON | **single** `Upgrade won — pay by 24 Jul` (danger) — replaces the base pill; `Upgrade won — payment due` when no deadline set |
| PAID | base `Secured` alone (upgrade PAID feeds the SECURED standing via Task 5) |
| LOST | base pill alone |
| ineligible / signed out | no upgrade pill |

The neutral no-standing pill prefers non-upgrade open rounds when choosing its featured round, falling back to the upgrade only when it is the only open one. The `status` **facet stays event-only** — an open upgrade round counts as open for everyone, signed in or out.

- [ ] **Step 1: Failing tests** — one per table row, driving `discover_statuses` directly, plus:
  - `test_signed_out_discover_has_no_upgrade_pill_and_same_facet`
  - `test_neutral_pill_prefers_the_non_upgrade_open_round`
  - a logged-in GET `/discover` render test asserting the `s-up` pill markup appears for the seeded state (and a logged-out GET still 200s)
- [ ] **Step 2: Confirm failure**
- [ ] **Step 3: Implement** — reuse the concert-wide outcomes dict already loaded (one query, no N+1); eligibility via `is_eligible`; render the second pill in `discover.html`'s status-line; add `.s-up`/accent styles to `style.css`
- [ ] **Step 4: Suite + lint, commit**

```bash
git commit -m "Show the upgrade campaign as its own pill beside the base standing"
```

---

### Task 7: Home rows, capture labels, concert page

**Files:**
- Modify: `src/app/db/service.py` (`capture_gates` line ~1086, `my_deadline_rows` line 1096, `concert_round_rows` line 1229), `src/app/web/templates/_capture_actions.html`, `_deadline_rows.html`, `_round_rows.html`
- Test: `tests/test_upgrade_rounds_web.py`

Behaviour:

- `capture_gates` gains `qualifies: bool = True`; `can_capture` is False for an upgrade round the user does not qualify for. Callers resolve eligibility from the outcome dicts they already hold.
- `my_deadline_rows` drops rows for upgrade rounds the user is ineligible for (a row whose buttons would be false testimony is noise). `upcoming_deadlines` (global, anonymous) is **unchanged** — the round's existence is a public fact.
- `_capture_actions.html`: for `RoundKind.UPGRADE` the apply/decline labels read `Entered upgrade` / `Skipping` (concept's exact text). **Same forms, same `POST /rounds/{round_id}/outcome`, same values** — labels only. No route changes.
- `_round_rows.html` (concert page): an ineligible signed-in user sees `Requires a ticket from: <qualifying round labels>` (labels resolved in `concert_round_rows`; empty set renders `Requires a ticket from an earlier round`) in place of capture buttons. Labels are user-controlled text — plain text content, invariant 7.

- [ ] **Step 1: Failing tests**
  - `test_home_coming_up_hides_upgrade_rows_from_ineligible_users`
  - `test_home_coming_up_shows_upgrade_row_once_qualified` (record r1 WON, row appears with `Entered upgrade`)
  - `test_entered_upgrade_posts_applied_through_the_one_write_path` (POST the existing route, assert `RoundOutcome` APPLIED — proving no new endpoint)
  - `test_concert_page_shows_requirement_line_for_ineligible_user`
  - `test_global_deadline_list_keeps_upgrade_rows_for_everyone`
  - logged-in GET render tests for `/` and the concert page with the seeded upgrade state
- [ ] **Step 2: Confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Suite + lint, commit**

```bash
git commit -m "Gate upgrade capture surfaces on derived eligibility"
```

---

### Task 8: Editor — the "Qualifies" chip row

**Files:**
- Create: `src/app/web/templates/_round_qualifier_chips.html`
- Modify: `src/app/web/templates/concert_edit.html`, `src/app/web/routes/concerts.py`, `src/app/web/static/style.css` (`.upgradebox`)
- Test: `tests/test_upgrade_rounds_web.py`

Mirror the `applies_to` mechanism from branch 2 **exactly** (`_round_leg_chips.html` + `parse_round_legs`, routes/concerts.py:271):

- One hidden `round_qualifiers` input per round row — space-separated round ids, positionally aligned with the other `round_*` parallel lists, defaulted like `round_legs`'s omission handling (routes/concerts.py:877-892 — study it and apply the same absent-field rule; describe your handling in your report).
- Chips: one toggle per **other saved round** on the concert, labelled with the round's label; simplification per the spec — a round created in the same submit has no id and cannot be a qualifier until saved. Chip toggling reuses the delegated-listener pattern of `_leg_chips_script.html` (no inline `on*`, `data-round-id` not `data-name`).
- The chip row sits inside the concept's `.upgradebox` callout ("Upgrade round. Only people holding a ticket from a qualifying round can enter." + `Qualifies` label) and is shown only while that row's kind `<select>` reads upgrade — client-side toggle keyed on the select's value, styles ported from the concept.
- `parse_round_qualifiers(values, valid_round_ids, self_id)` in routes/concerts.py: same-concert ids only, self-reference dropped, non-integers dropped, `None` when empty. Save path stores the result only when the submitted kind is UPGRADE; any other kind stores `None` (qualifiers for a non-upgrade kind are discarded, per spec).
- Pre-fill: like the leg chips, the hidden value carries the round's whole stored set so an id whose chip is not rendered still round-trips untouched.

- [ ] **Step 1: Failing tests**
  - `test_a_two_qualifier_upgrade_round_survives_an_edit_round_trip` (GET the edit page, POST back unchanged, assert both ids intact — the branch-2 regression shape, applied here from day one)
  - `test_self_reference_is_dropped`
  - `test_cross_concert_round_id_is_dropped`
  - `test_qualifiers_on_a_non_upgrade_kind_are_discarded`
  - `test_dangling_qualifier_id_is_dropped_on_read` (delete a qualifying round, render the reader page — no crash, id filtered)
  - editor logged-in GET render test showing the Qualifies chips for a seeded upgrade round
- [ ] **Step 2: Confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Suite + lint, commit**

```bash
git commit -m "Edit an upgrade round's qualifying set as chips"
```

---

### Task 9: Wishlist bookkeeping

- [ ] Per CLAUDE.md's wishlist rule, when this branch ships: move the upgrade-rounds entry in `WISHLIST.md` to Shipped with today's date, then do the full revision pass over the remaining entries (a shipped upgrade-rounds feature may raise or obsolete related outcome-tracking ideas). Note anything re-ranked in the PR description.

```bash
git commit -m "Record upgrade rounds as shipped in the wishlist"
```

---

## Verification

**Gates:** `uv run pytest -q` (638-passed baseline + new; the one known local failure stays), `uv run ruff check .` clean, `uv run alembic upgrade head` applies cleanly.

**Drive it** — `uv run python -m app.main`, blank `DISCORD_TOKEN`:

1. As editor: open a concert with a 最速先行 round, add a round, set kind "Upgrade round" — the Qualifies chips appear; tick 最速先行, save, reopen — still ticked.
2. As user: record 最速先行 WON then PAID. Discover shows `Secured` + the accent `Upgrade · Closes in …` pill; Home's Coming up shows the upgrade row with `Entered upgrade` / `Skipping`.
3. Press `Entered upgrade`; the pill becomes `Upgrade · Applied`. Record the upgrade WON — the card collapses to the single urgent `Upgrade won — pay by …` pill and the board card moves to Won — pay. Record PAID — back to Secured.
4. As a second user with no outcomes: no upgrade row on Home; the concert page shows "Requires a ticket from: 最速先行"; the global deadline list still lists the upgrade's close.
5. Confirm both times everywhere render dual JST + local.

## Out of scope

Do not add, even if the concept shows them: `ConcertSubscription` / follow state (branch 4, in progress), the onboarding "Do you hold this ticket?" tile (onboarding branch), ramen.events import heuristics for upgrade detection, any new outcome write path or Discord command surface.


## Resolved decisions (do not re-litigate)

- **Qualifier storage (Resolved 2026-07-19 with the owner).** Use an association table `round_qualifiers(upgrade_round_id,
  qualifying_round_id)` with NAMED FK and unique constraints, modelled on `TagMember`
  (`models.py:235`) -- NOT the JSON column the plan may currently assume. This gives real CASCADE
  integrity: deleting a qualifying round removes its qualifier links automatically, with no
  dangling-id filtering on read. Both FKs cascade on delete. The editor chip mechanism still works
  -- it just resolves chips to rows in this table instead of a JSON list.
- **Empty qualifier set (Resolved 2026-07-19 with the owner).** An upgrade round with NO qualifiers means "any secured
  (WON/PAID) ticket on this concert qualifies", mirroring `applies_to`'s empty-means-all-legs. An
  editor who adds an upgrade round without picking qualifiers gets the common real case, not a dead
  round.
