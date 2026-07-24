# Editor coherence — implementation plan

Spec: `docs/superpowers/specs/2026-07-24-editor-coherence-design.md`
(approved; demos merged as the design reference in PR #91). Branch:
`editor-coherence-impl`. Two sequential implementation tasks plus a docs
close-out — sequential because both tasks touch `style.css` and both
`.po` catalogues, and parallel edits there conflict.

## Task 1 — card anatomy + shared partials (the editor surfaces)

Extract the leg/round card markup into `_editor_leg_card.html` /
`_editor_round_card.html` and rebuild the card anatomy per the spec, in
`concert_new.html`, `concert_edit.html`, `import_preview.html` AND each
file's `<template>` blocks (the templates render the same partials with
blank values — six hand-rolled copies become two files).

New anatomy (the merged demo editor/import-preview frames are the
reference — match them):
- Top row: ja label input, then (leg) Cancelled chip / (round) kind
  select, then the kebab. The inline `button.x` dies.
- Kebab: `<details class="kebab">` + `.kmenu` + `.kitem danger` carrying
  the existing `data-remove-leg` / `data-remove-round` attributes so the
  current remove handlers and tests keep their hooks. Document-level
  click closes any open kebab (extend the existing shared editor JS).
- Variants row (`.vary`): EN + 中文 `.vfld` fields; on round cards the
  `[data-open-phrases]` "Remembered" chip moves to the end of this row.
- Leg fields row unchanged in content (venue select, + New venue, doors,
  starts; import's per-leg hint line stays).
- CSS: port the demo's `.cardtop`/`.vary`/`.kebab`/`.kmenu`/`.kitem`
  rules into `style.css` using the app's tokens; 3px radius on
  cards/chips (guard test); anything phone-specific goes INSIDE the one
  `@media (max-width: 700px)` section.
- Variant guard/gaps untouched (attributes ride along on the moved
  fields).

Tests: keep existing editor tests passing (selector updates where they
asserted the old inline ×); add render assertions that each surface
serves the kebab and no inline remove button sits beside the Cancelled
toggle.

## Task 2 — sentence builders on locale-ordered slot templates

- New helper `sentence_slots(pattern, **slot_html)` — split a TRANSLATED
  pattern on `{name}` placeholders, emit text + the caller's rendered
  controls in pattern order. Implement as a small Python function
  registered as a Jinja global (like `dual_lines`); unit-test the
  splitter directly (unknown placeholder, repeated, adjacent, CJK text).
- Welcome (`remrow` macro + the JS row builder) and Preferences
  (`sentence_fields` macro) render through it. The JS row builder stops
  assembling English DOM: clone a server-rendered translated
  `<template id="remrule-template">` and set select values (kills the
  hardcoded before/after bug).
- Pattern msgids (exact EN wording may be tuned to read naturally; keep
  placeholders):
  - welcome: `"Remind me {offset} {direction} {anchor}."`
    ja start point: `"{anchor}の{offset}{direction}に通知。"`
    zh start point: `"{anchor}{direction}{offset}提醒我。"`
  - preferences: `"Remind me {days} day(s) {hours} hour(s) {direction} each {anchor}."`
    ja start point: `"各{anchor}の{days}日{hours}時間{direction}に通知。"`
    zh start point: `"每次{anchor}{direction}{days}天{hours}小时提醒我。"`
- Moment rows (offset 0:0 / "when") hide the direction select exactly as
  today; the ja/zh patterns must still read naturally with that slot
  empty — report the final msgstrs for owner review.
- pybabel extract/update both catalogues; fill every new msgid; retired
  fragment msgids fall out naturally; `test_i18n_catalogues.py` green.

## Task 3 — docs close-out (orchestrator)

CLAUDE.md UI-conventions note for the kebab pattern (single-purpose,
destructive only) and the sentence-slot mechanism; WISHLIST ship move +
revision pass; README line. Final whole-branch review before PR.

## Verification bar (every task)

`uv run --isolated pytest -q` (only the two known env issues may fail),
`uv run --isolated ruff check .` clean, catalogue hygiene green, and the
3px-radius guard untouched.
