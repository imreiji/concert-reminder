# Onboarding and untouched-pages Build Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the signed-out landing, the welcome wizard, import + import preview, retroactive apply, and the legal pages to match the concept demo, on the shipped design system.

**Architecture:** Port from the demo (the reference implementation for markup/CSS), reusing the shipped tokens/components. Two surfaces touch the backend (welcome preset creation via the existing `ReminderPreset`/`PresetItem` model; import-commit leg binding via the shared `round_legs`/`parse_round_legs`); the rest is presentation. No i18n (out of scope, see spec).

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx, SQLAlchemy async, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-20-onboarding-pages-design.md`

**Reference (read for every task):** the demo at
`C:/Users/jiash/AppData/Local/Temp/claude/E--click-clack-clan-concert-reminder/daa393df-1607-4832-8c2e-505c901e414a/scratchpad/dekimasen-onboarding-demo.html`
(also committed at `docs/superpowers/demo/dekimasen-onboarding-demo.html` once PR #54 merges).
Its `<style>` head defines the design vocabulary; the shipped `static/style.css` already carries the
same tokens/components, so reuse those and only add view-specific rules. Demo view line ranges:

| Surface | Demo lines |
|---|---|
| Landing (signed-out home) | 516-670 |
| Welcome: follow artists | 675-731 |
| Welcome: default reminders (cards + sentence fine-tune) | 732-808 |
| Welcome: timezone / test DM / calendar | 809-900 |
| (setup + board handoff -- already shipped, reference only) | 901-1117 |
| Import | 1118-1147 |
| Import preview | 1148-1293 |
| Retroactive apply | 1294-1334 |
| Privacy / Terms | 1335-end |

## Global Constraints

- `uv run pytest -q` passes and `uv run ruff check .` clean before every commit. Baseline: run the
  suite first, record it; only the local-only `tests/test_crud.py::test_test_dm_when_bot_disabled`
  (real DISCORD_TOKEN in .env) may fail -- OUT OF SCOPE. Verify against reality.
- Reuse the shipped design system; both themes; no hardcoded light colors on themed surfaces.
- Invariants 1/3/4/7 and no second write path (2/8) per the spec.
- `routes/imports.py` stays registered before `routes/concerts.py`.
- Business logic in `db/service.py`; domain pure. Sentence case; ASCII configs. Comment WHY not WHAT.
- Every page keeps a logged-in GET render test; public pages also a logged-out one.

## File Structure

| File | Task |
|---|---|
| `templates/home.html`, `web/app.py`, `static/style.css` | T1 landing |
| `templates/import_form.html` | T2 |
| `templates/import_preview.html`, `web/routes/imports.py` | T3 |
| `templates/retroactive_apply.html` | T4 |
| `templates/privacy.html`, `templates/terms.html` | T5 |
| `templates/welcome.html`, `web/routes/welcome.py`, `db/service.py` | T6 |
| `CLAUDE.md`, `WISHLIST.md` | T7 |

---

### Task 1: Signed-out landing home

**Files:** modify `templates/home.html`, the `/` handler in `web/app.py`, `static/style.css`. Test: `tests/test_home.py` (extend).

Port demo lines 516-670. Rebuild the **signed-out** branch of `home.html` into the landing page:
hero + promise, value prop, "how it works", the four-column campaign board as an illustrative
(static sample) thesis, a Discover taste (real public cards reusing the data `/discover` exposes -- the
handler supplies a few), a catalogue stat line, and Sign-in-with-Discord CTAs. The signed-IN home is
UNCHANGED. Drop the demo's "Take the tour" button and its language switcher (out of scope).

- [ ] Failing tests: signed-out `/` emits the landing (hero, a "how it works" section, a Discover
  taste, the CTA) and 200s; signed-in `/` still renders the board (unchanged). Then implement, verify.

```bash
git commit -m "Build the signed-out landing home"
```

---

### Task 2: Import form

**Files:** modify `templates/import_form.html`. Test: `tests/` import render test (extend).

Port demo lines 1118-1147. Restyle the ramen.events URL paste screen to the demo. KEEP the
`pattern="https://ramen\.events/.*"` and the POST target exactly (SSRF guard unchanged). No behaviour
change.

- [ ] Failing render test (GET `/concerts/import`), implement, verify.

```bash
git commit -m "Restyle the import form to the design system"
```

---

### Task 3: Import preview + leg binding

**Files:** modify `templates/import_preview.html`, `web/routes/imports.py` (`import_commit`). Test: `tests/test_import*.py` (extend).

Port demo lines 1148-1293. Rebuild the parsed-draft review in the day-card / round-card / leg-chip
vocabulary (mirroring `concert_new.html` / `concert_edit.html`), with the warnings list and the
"nothing saved yet" framing. Reuse `_round_leg_chips.html` and `_leg_chips_script.html`; render each
parsed day as an `.eleg` card carrying a `day_key`, each parsed round as a `.redit` card with leg
chips. `import_commit` adopts the `round_legs`/`day_key` + `parse_round_legs`/`key_to_day_id` binding
`create_concert` uses (mirror it exactly -- build `key_to_day_id` after the day flush, resolve
`applies_to` after). Keep the `source_url` hidden field + its re-validation (invariant 7); keep
`create_concert_row(expand=False)`.

- [ ] Failing test FIRST: a preview POST to `import_commit` with two days (`day_key` new-a/new-b) and
  one round `round_legs="new-a new-b"` stores that round's `applies_to` as BOTH day ids (the binding
  the old flat form could not express). Confirm it fails against the current route, then implement.
- [ ] Also: no-leg round commits as all-legs; the `source_url` round-trip still re-validates. Verify.

```bash
git commit -m "Rebuild import preview with day/round cards and leg chips"
```

---

### Task 4: Retroactive apply

**Files:** modify `templates/retroactive_apply.html`. Test: render test (extend).

Port demo lines 1294-1334. Restyle the "add {member} to N active events?" confirmation -- affected
concerts as cards/rows, an info note that it adds ONLY the new member and does not re-expand or
un-prune (invariant 3), Apply / Skip. No behaviour change; existing route stays.

- [ ] Failing render test, implement, verify.

```bash
git commit -m "Restyle the retroactive-apply confirmation"
```

---

### Task 5: Privacy and terms

**Files:** modify `templates/privacy.html`, `templates/terms.html`. Test: render tests (extend).

Port demo lines 1335-end. Give the long-form prose the demo's consistent framing/typography. Content
UNCHANGED. Still public (`current_user`, no `require_user`); keep the logged-out render tests green.

- [ ] Confirm both render signed out, implement framing, verify.

```bash
git commit -m "Frame the privacy and terms pages in the design system"
```

---

### Task 6: Welcome wizard

**Files:** modify `templates/welcome.html`, `web/routes/welcome.py`, `db/service.py`. Test: `tests/test_welcome*.py` (extend/create).

Port demo lines 675-900. Rebuild the five steps in the card/chip vocabulary, matching the demo and
flowing into the already-redesigned `/setup`:

- **Follow artists** (675-731): restyle the subscribe chips; keep the `/subscriptions` POST and
  `filterChips` search.
- **Default reminders** (732-808): the settled design -- three preset cards (Relaxed / Standard
  [recommended] / On the ball) as templates, plus the sentence fine-tune list (each rule a grammatical
  sentence over the five anchors; default reminds once for Opens/Results/Payment, Closes gets the
  couple, nothing on Show). On submit, MATERIALISE a `ReminderPreset` + its `PresetItem`s via the
  existing preset service (no second write path). Add a `db/service.py` helper that creates a preset
  from a chosen template's rule list + any fine-tuned rows; the three templates are rule sets defined
  once. Sets it as the user's default.
- **Timezone / Test DM / Calendar** (809-900): restyle; reuse the existing timezone, test-DM, and
  calendar-feed routes unchanged.
- Keep the skip-setup escape and `POST /welcome/advance` -> `/setup` handoff.

- [ ] Failing tests: each step renders; submitting the preset step creates a `ReminderPreset` with
  the template's items and marks it default; a fine-tuned/added rule persists; the wizard advances
  into `/setup`; skip-all lands on `/`. Confirm fails, implement, verify.

```bash
git commit -m "Rebuild the welcome wizard on the design system"
```

---

### Task 7: Docs

**Files:** `CLAUDE.md`, `WISHLIST.md`.

- [ ] Note the built surfaces in CLAUDE.md where relevant (the welcome wizard now shares the setup
  design; import preview binds legs like the editor); update the test-count line to the real number.
- [ ] WISHLIST.md: add multi-language i18n (EN / 中文 / 日本語) as its own Proposed entry with the
  date and the "own large project" note; full re-rank. ASCII-only.

```bash
git commit -m "Document the onboarding build; log i18n as its own effort"
```

---

## Verification

**Gates:** `uv run pytest -q` (baseline + new) and `uv run ruff check .` clean.

Drive it: signed out, `/` is a real landing page; sign in and walk the welcome wizard end to end into
the board with no seam; pick a preset card, fine-tune a sentence, confirm the default preset is
created with those rules; import a ramen.events URL, assign a round to two legs in the preview,
commit, confirm both legs stuck; toggle dark mode on every new page. Then a per-view pass against the
demo to confirm parity.
