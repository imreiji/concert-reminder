# Concert Page and Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the concert page say where you stand, and make the editor able to express a round that applies to several legs — which it currently cannot.

**Architecture:** Reuse branch 1's capture-rule gates rather than reimplementing them; replace the reader's six-column round table with per-leg round rows; replace the editor's free-text leg matching with explicit `applies_to` chips.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx, SQLAlchemy async, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-19-concert-page-and-editor-design.md`

## Global Constraints

- `uv run pytest -q` must pass and `uv run ruff check .` must be clean before every commit.
- Baseline: **584 passed, 1 failed**. The failure is `tests/test_crud.py::test_test_dm_when_bot_disabled` — the repo-root `.env` sets a real `DISCORD_TOKEN` while the test assumes empty. Pre-existing, local-only, CI green, **OUT OF SCOPE**. Verify against reality, not this plan's arithmetic.
- TDD: failing test first, run it, confirm it fails for the right reason, then implement.
- **No schema change, no migration.** `applies_to` already holds the right ids.
- `RoundOutcome` writes go through `record_round_outcome` via the existing `POST /rounds/{id}/outcome`. No second write path (invariant 2).
- Business logic in `db/service.py`; routes assemble context; `src/app/domain/` stays pure.
- `routes/imports.py` stays registered before `routes/concerts.py` in `web/app.py`.
- `edit_concert` keeps calling `snapshot_concert` **before** mutating and `record_concert_edit` **after** — reversed, every diff reads as unchanged.
- Invariant 3: `duplicate_concert` keeps `expand=False`.
- Invariant 7: editor URLs through `form_url`; picker data `| tojson` never `| safe`; no user-controlled text in inline `on*` handlers, and `data-name` collides with `base.html`'s `filterChips()`.
- Times dual, JST first, via `fmt_dual`. Sentence case.
- DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener.

## How to read this plan

The interactive concept at `https://claude.ai/code/artifact/ea939428-b99e-43e7-8664-fa276431baba` is the **reference implementation for markup and CSS** — open the **Home** view for the round-row shape and the **Editor** view for the chips and folds. Port from it; do not redesign.

Tasks name exact signatures, behaviours and test cases. Where a test is named but not written out, write it to the described behaviour and say so in your report if the description was ambiguous.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/db/service.py` (modify) | `concert_round_rows` — per-leg round rows carrying outcome and the capture gates. |
| `src/app/web/routes/concerts.py` (modify) | Detail route context; editor form save path for `applies_to`. |
| `src/app/web/templates/concert_detail.html` (rewrite) | Header, standing, legs. |
| `src/app/web/templates/_round_rows.html` (new) | One leg's rounds. Replaces `_performances.html`'s table. |
| `src/app/web/templates/_capture_actions.html` (new) | The shared button rules, used by both `_deadline_rows.html` and `_round_rows.html`. |
| `src/app/web/templates/concert_edit.html` (rewrite) | Rounds first, folds, leg chips. |
| `src/app/web/templates/_leg_picker_script.html` (delete) | Superseded by explicit chips. |

---

### Task 1: Shared capture rules and per-leg round rows

**Files:**
- Modify: `src/app/db/service.py`
- Create: `src/app/web/templates/_capture_actions.html`
- Modify: `src/app/web/templates/_deadline_rows.html` (use the new partial)
- Test: `tests/test_concert_rows.py`

**Interfaces:**
- Consumes: `_round_has_opened` (`service.py:1056`), and the `can_capture` / `can_report_result` gates already resolved for deadline rows (`service.py:1132-1139`). **Reuse them — do not write a second copy of these rules.**
- Produces: `async def concert_round_rows(session, user_id, concert, now=None) -> tuple[list[LegRounds], list[RoundRow]]` returning per-leg groups plus the all-legs group, where `RoundRow` carries `round_`, `outcome`, `can_capture`, `can_report_result`, and `primary_anchor`.

The capture-button rules are currently inline in `_deadline_rows.html`. Extract them to
`_capture_actions.html` taking a row-like object, and have both callers include it. Two copies of
"which button shows when" is how they drift.

Grouping: a round goes under a leg if that leg's id is in `applies_to`; a round whose `applies_to`
is empty or covers **every** live leg goes in the all-legs group. A round covering some-but-not-all
legs appears under each of those legs.

- [ ] Steps 1-6: failing tests, verify failure, implement, verify pass, suite + lint, commit.

Tests: grouping for single-leg / multi-leg / no-leg / every-leg rounds; a cancelled leg still
yields its group; `can_capture` false for an unopened round; `can_report_result` true only once
results are due; outcome is per-user.

```bash
git commit -m "Share the capture rules and add per-leg round rows"
```

---

### Task 2: Concert page header

**Files:**
- Modify: `src/app/web/templates/concert_detail.html`, `src/app/web/routes/concerts.py`
- Test: `tests/test_concert_page.py`

Lineage `Franchise · **Group**` above the title; title drops the group when the lineage carries it.
**Remove the date range and venue from the header** — they move to legs in Task 3.

Performers panel: group chip then members, labelled with where they came from. Plain chips — the
eventernote links need branch 3.

Links row names destinations: `eventernote · official site · ramen.events`. `Edit event` and
`Export YAML` in the header action row, editor-gated.

Tests: lineage renders F · G, group-only, and neither; performers panel shows materialised
membership including a pruned member; a non-editor sees no editor controls; the source link reads
`ramen.events`.

```bash
git commit -m "Lead the concert page with lineage and performers"
```

---

### Task 3: Concert page body

**Files:**
- Modify: `src/app/web/templates/concert_detail.html`
- Create: `src/app/web/templates/_round_rows.html`
- Delete: `src/app/web/templates/_performances.html` (confirm nothing else includes it)
- Test: `tests/test_concert_page.py`

**Next for you** — nearest moment on this concert needing the reader, with their standing. Omitted
entirely when they have no standing and nothing is open.

**Legs**, each with its own date, doors and venue. Cancelled legs dimmed and badged, never hidden.
Rounds nested per Task 1's grouping, all-legs group last.

**Round rows** replace the table: `your status` · `round + kind` · `next date bold, others small` ·
actions from `_capture_actions.html`.

Tests: a two-venue concert renders two different venues; a cancelled leg is dimmed with its rounds
still visible; each capture state renders the right controls; "Next for you" is absent for a reader
with no standing and nothing open; no horizontal scroll wrapper remains.

```bash
git commit -m "Replace the rounds table with per-leg round rows"
```

---

### Task 4: `applies_to` as leg chips — the correctness fix

**Files:**
- Modify: `src/app/web/templates/concert_edit.html`, `src/app/web/routes/concerts.py`
- Delete: `src/app/web/templates/_leg_picker_script.html`
- Test: `tests/test_editor_legs.py`

**This is the task that fixes real data loss.** `resolve_round_leg` (`routes/concerts.py:240`)
matches typed text against each day's city or label and returns **every** match, so `applies_to`
genuinely holds several ids. But `round_leg_display` (`:253`) pre-fills from `applies_to[0]` alone
— so **a round covering two legs collapses to one on the next save.**

- [ ] **Step 1: Write the failing test first**

```python
async def test_a_two_leg_round_survives_an_edit_round_trip(client):
    """The regression the current form cannot pass: open the editor on a round
    whose applies_to covers both legs, save without touching it, and both ids
    must still be there."""
    # seed a concert with two legs and one round applying to BOTH
    # GET the edit page, POST it back unchanged
    # assert set(round_.applies_to) == {day1.id, day2.id}
```

Write it out in full. Run it and **confirm it fails against the current form** — that failure is
the whole justification for this task. Quote the failure in your report.

- [ ] **Step 2: Replace the mechanism**

Each round row gets one toggle chip per live leg, pre-selected from the round's real `applies_to`.
The save path takes the selected ids directly. Delete `resolve_round_leg`, `round_leg_display` and
`_leg_picker_script.html` once nothing references them.

Submitting a set of ids per repeatable round row needs a form encoding that survives multiple rows
— check how the existing repeatable round fields are parsed before choosing one, and describe what
you chose in your report.

- [ ] **Step 3-6: verify, suite, lint, commit**

Tests: the round-trip above; selecting no legs stores empty/None and renders in the all-legs group;
selecting every leg round-trips; a leg deleted while a round referenced it does not leave a dangling
id.

```bash
git commit -m "Edit applies_to directly instead of matching typed leg text"
```

---

### Task 5: Editor restructure

**Files:**
- Modify: `src/app/web/templates/concert_edit.html`
- Test: `tests/test_editor_legs.py`

Rounds and legs first with `Add a round` primary; identity, details and links, tags, and edit
history as folds whose summaries show their contents. Cancelled becomes a leg toggle rather than a
buried `<select>`. Duplicate and delete move to a separated danger row, duplicate stating that it
copies tags and details but not rounds or legs.

Tests: an editor render test; the cancelled toggle round-trips; duplicate still passes
`expand=False`; audit ordering still produces a real diff.

```bash
git commit -m "Put rounds first in the editor and fold the rest"
```

---

## Verification

**Gates:** `uv run pytest -q` (584 baseline + new) and `uv run ruff check .` clean.

**Drive it** — `uv run python -m app.main`, blank `DISCORD_TOKEN`:

1. Open a two-leg concert. Each leg shows its own date and venue; rounds sit under the right legs.
2. Record an application from a round row; the status changes in place.
3. Open the editor, tick a **second** leg on a round, save, reopen — **both** legs still selected.
   This is the regression that motivated the branch.
4. Mark a leg cancelled; it dims and badges rather than disappearing, and its rounds stay visible.
5. Confirm no horizontal scrolling on a phone width.

## Out of scope

Do not add, even if the concept shows them: the **Following toggle** and **per-leg opt-out**
(need `ConcertSubscription`, branch 4), **upgrade rounds** (branch 5), **eventernote links on
performer chips** (need `eventernote_url` on tags, branch 3).
