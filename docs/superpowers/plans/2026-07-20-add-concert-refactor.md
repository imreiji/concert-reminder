# Add-concert Page Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the create form on the editor's card/chip/fold visual language and migrate its leg binding from the buggy `round_leg` text-match to the editor's leg chips, fixing the same multi-leg-collapse data loss on the create path.

**Architecture:** Reuse the editor's shared components and route helpers wholesale (`_leg_chips_script.html`, `_round_leg_chips.html`, `_round_qualifier_chips.html`, `parse_round_legs`, `parse_round_qualifiers`, `build_day`/`build_round`). Route and template change together; keep their form field names in lockstep. No schema change.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy async, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-20-add-concert-refactor-design.md`

**Reference implementations:** `concert_edit.html` (the target visual language + card/fold structure), `_leg_chips_script.html` (the client mechanism the create form must satisfy: `#day-rows .eleg` cards with hidden `day_key`/`day_cancelled`, `#round-rows` `.redit` cards with `[data-leg-chips]`), and `edit_concert` (`routes/concerts.py:868+`, the `key_to_day_id` + `parse_round_legs` server pattern).

## Global Constraints

- `uv run pytest -q` passes and `uv run ruff check .` clean before each commit. Baseline: run the suite first and record the number; the only expected failure is the local-only `tests/test_crud.py::test_test_dm_when_bot_disabled` (real DISCORD_TOKEN in .env -- OUT OF SCOPE). Verify against reality.
- TDD: the multi-leg regression test is written FIRST and must fail against the current `round_leg` form before the migration.
- Invariants 1/3/6/7 per the spec. No schema change.
- Route params and template field names MUST match -- change them together.
- ASCII CSS comments; sentence case; comment WHY not WHAT.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/web/routes/concerts.py` (modify) | `create_concert`: swap `round_leg` -> `round_legs`/`round_qualifiers`/`day_key`; `key_to_day_id`; `parse_round_legs`/`parse_round_qualifiers`. Delete `resolve_round_leg` when unreferenced. |
| `src/app/web/templates/concert_new.html` (rewrite) | Identity-first layout; `.eleg`/`.redit` cards; leg + qualifier chips; folds for optional fields; `_leg_chips_script.html`. |
| `src/app/web/templates/_leg_picker_script.html` (delete) | Superseded; only `concert_new.html` includes it. |
| `tests/test_editor_legs.py` or `tests/test_crud.py` (extend) | The create-path multi-leg round-trip + qualifier + no-leg tests. |

---

### Task 1: Migrate the create route to leg chips

**Files:**
- Modify: `src/app/web/routes/concerts.py` (`create_concert`, ~line 544-636)
- Test: add to the create-form test module (find where `POST /concerts` is already tested)

**Interfaces consumed (do NOT reimplement):** `parse_round_legs(value, valid_day_ids, key_to_day_id)` (`concerts.py:285`), `parse_round_qualifiers(value, valid_round_ids, self_id)` (`:333`), `build_day`, `build_round`. Study how `edit_concert` (`:868+`) builds `key_rows`/`key_to_day_id` after the day flush and resolves `applies_to`/qualifiers after the round flush; mirror that shape.

- [ ] Step 1: Write the failing regression test FIRST -- `POST /concerts` with two performances (each carrying a `day_key`, e.g. `new-a`/`new-b`) and one round whose `round_legs` value is `"new-a new-b"`; after the request, load the concert and assert the round's `applies_to == {both day ids}`. Run it against the CURRENT route (which takes `round_leg`, not `round_legs`) and confirm it fails -- that failure is the justification for this task. Quote it in the report.
- [ ] Step 2: Migrate the route:
  - Replace the `round_leg: list[str]` Form param with `round_legs: list[str]`, `round_qualifiers: list[str]`, and `day_key: list[str]` (all `Form(default=[])`).
  - In the day loop, collect `(key, day)` rows and, after `session.flush()`, build `key_to_day_id` (first key wins, per `edit_concert`). Compute `valid_day_ids = {d.id for d in days}`.
  - In the round loop, after the round flush, set each round's `applies_to = parse_round_legs(legs_value, valid_day_ids, key_to_day_id)` and qualifiers via `parse_round_qualifiers` for upgrade rounds -- following `edit_concert`'s two-phase (flush rounds, then resolve) ordering so same-submit qualifier references resolve. Keep the blank-trailing-row skip.
  - Pad `round_legs`/`round_qualifiers`/`day_key` on whole-array omission exactly as `edit_concert` does (older/blank submitters), but leave a partial array alone so the strict zip raises rather than sliding a row.
- [ ] Step 3: Add the no-leg test (empty `round_legs` -> `applies_to is None`) and the upgrade-qualifier test (a round created with a qualifier chip referencing another same-submit round stores it).
- [ ] Steps 4-6: verify all pass, suite + lint, commit.

```bash
git commit -m "Bind create-form rounds to legs by chip instead of text match"
```

---

### Task 2: Rebuild the create template on the editor's language

**Files:**
- Rewrite: `src/app/web/templates/concert_new.html`
- Delete: `src/app/web/templates/_leg_picker_script.html` (confirm `concert_new.html` was its only includer)
- Test: extend the create-form render test

Port the editor's card/fold/chip markup, in the identity-first order the spec fixes:
- Open spine: Title (JP), Event ID, first Performance.
- Performances as `.eleg` cards (each with hidden `day_key`, hidden `day_cancelled`, the
  `[data-cancel-toggle]` control, `[data-remove-leg]`), matching what `_leg_chips_script.html`
  queries; `+ Add performance` via `addRow`.
- Rounds as `.redit` cards including `_round_leg_chips.html` (and `_round_qualifier_chips.html` for
  the upgrade case) instead of the `round_leg` select; `+ Add round`; `[data-remove-round]`.
- Folds (`<details class="fold">`) for the optional details/links, Tags, Performers, Notes.
- Include `_leg_chips_script.html` (NOT `_leg_picker_script.html`); keep the event-id suggestion JS
  and the import link.
- Remove-buttons use the `[data-remove-*]` delegated pattern, NOT inline `onclick=...remove()`.

- [ ] Step 1: Extend the render test -- GET `/concerts/new` renders, emits `[data-leg-chips]`, has NO `name="round_leg"` and no `_leg_picker_script`, and the event-id suggestion + import link are present. Confirm it fails on the current template.
- [ ] Steps 2-4: rewrite the template, delete `_leg_picker_script.html`, run the render test + the Task-1 route tests together (they exercise the same field names end-to-end), verify.
- [ ] Steps 5-6: suite + lint, commit.

```bash
git commit -m "Rebuild the add-concert page on the editor's card and chip language"
```

---

### Task 3: Remove the dead text-match helper + docs

**Files:** modify `src/app/web/routes/concerts.py`; `CLAUDE.md` if warranted.

- [ ] Step 1: Confirm `resolve_round_leg` now has no callers (grep; the `:401` mention is a comment in a display helper -- confirm it is not an actual call). If clean, delete `resolve_round_leg`.
- [ ] Step 2: If CLAUDE.md documents the create form's field set or the old leg mechanism, update it; note the create path now uses the same chip binding as the editor. Update the test-count line to the real number. ASCII-only.
- [ ] Steps 3-4: suite + lint, commit.

```bash
git commit -m "Remove the retired text-match leg resolver"
```

---

## Verification

**Gates:** `uv run pytest -q` (baseline + new) and `uv run ruff check .` clean.

Drive it (`uv run python -m app.main`, blank `DISCORD_TOKEN`):
1. Open `/concerts/new`; identity + first performance are open, extras folded.
2. Add a second performance and a round; tick BOTH legs on the round; create.
3. Open the new concert's editor -- both legs are still selected (the regression the old form failed).
4. Toggle dark mode -- the create page matches the editor.
5. `grep -r round_leg\b src/app/web` shows only `round_legs`; `_leg_picker_script.html` is gone.
