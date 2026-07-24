# Editor and concert pages coherence pass — design spec

Date: 2026-07-24. Status: approved direction (owner picked all four forks
in the brainstorm); demo reconciliation in progress; implementation
deferred until the owner's usage window resets.

## Problem

The trilingual arc and the draft-import build changed the editor
piecemeal: every label became a ja/EN/中文 trio, legs grew venue pickers
and hint plumbing, rounds grew phrase pickers — all bolted onto cards that
were never re-composed. Concretely:

1. The leg card's top row does five jobs (ja label, EN field, 中文 field,
   Cancelled toggle, × remove) — and the × sits directly beside Cancelled,
   a destructive control shoulder-to-shoulder with a state toggle. Killing
   that adjacency is NON-NEGOTIABLE scope (owner).
2. The three editor surfaces (concert_new, concert_edit, import_preview)
   hand-roll near-identical leg/round card markup THREE times each (once
   in the loop, once in the `<template>`), so every card change is a
   six-site edit and the surfaces drift.
3. The concept demo (`dekimasen-demo.html`, the design source of truth)
   predates i18n entirely — no trios, no phrase picker, no variant guard —
   and shows a *nested* editor (rounds inside leg cards) that was never
   built. The demo and the app disagree about the design.
4. The sentence-style reminder-rule builders (welcome's `remrow`,
   Preferences' `sentence_fields`) interleave translated fragments between
   selects in FIXED English word order, so ja/zh read ungrammatically; and
   welcome's client-side row builder writes untranslated English
   "before"/"after" into rows it creates.

## Decisions (owner, 2026-07-24 brainstorm)

**D1 — Structure: keep flat, redesign the cards.** Performances stay one
list, rounds stay a second list bound by applies-to chips (they model
multi-leg rounds honestly). The demo's nested-editor concept is retired;
the demo gets rebuilt to match the shipped structure ("when the shipped
design deliberately moves, update the demo" — CLAUDE.md).

**D2 — Destructive actions move into a kebab menu (⋯), top-right.** The
inline × dies on both card types. The kebab is the card's only top-right
control; Cancelled remains a chip but is separated from it (see anatomy).
Remove stays one confirm away, but can no longer be hit by a mis-aimed
click at Cancelled.

**D3 — Variant fields get a second row, always visible.** The ja field is
the top row's single text input (it is the source of truth and
mandatory); EN and 中文 sit on an aligned second row. Nothing hides
behind an expander, so the all-three-or-none guard keeps pointing at
visible fields.

**D4 — Sentence builders render through locale-ordered slot templates.**
Each builder's sentence becomes ONE translatable pattern string with
placeholders; the renderer splits the pattern and inserts the real
`<select>`s at the placeholder positions. Translators control word order:

    en  "Remind me {offset} {direction} {anchor}."
    ja  "{anchor}の{offset}{direction}に通知。"
    zh  "{anchor}{direction}{offset}提醒我。"

(Preferences' variant has {days}/{hours} slots; same mechanism.)

## Card anatomy

### Leg (performance) card — `.eleg`

    ┌────────────────────────────────────────────────────────┐
    │ [ラベル ja ______________]   [Cancelled]          [⋯]  │   top row
    │ EN [______________]  中文 [______________]             │   variants row
    │ [venue ▾] [+ New venue]   Doors [__] Starts [__]       │   fields row
    └────────────────────────────────────────────────────────┘

- Top row: ja label input (flex-grows), then a gap, then the Cancelled
  chip, then the kebab. The chip and kebab are separated by fixed margin;
  the destructive action is inside the menu, never inline.
- Variants row: `label.vfld` EN + 中文, same widths, aligned under the ja
  input. Import preview's per-leg venue-hint notice also lives at the end
  of this row region (unchanged content).
- Fields row: venue select, + New venue chip, doors, starts (unchanged).

### Round card — `.redit`

    ┌────────────────────────────────────────────────────────┐
    │ [ラベル ja ______________]   [kind ▾]             [⋯]  │   top row
    │ EN [____________] 中文 [____________] [Remembered]     │   variants row
    │ [applies-to leg chips]  ([qualifier chips if upgrade]) │
    │ [Opens][Closes][Results][Payment]                      │   times row
    │ [URL __________]  [Notes __________]                   │   extra row
    └────────────────────────────────────────────────────────┘

- The phrase-library "Remembered" button moves to the END of the variants
  row — it fills exactly those three fields, so it lives beside them.
- Kind select stays on the top row (it changes card shape via the
  upgrade qualifier box, so it belongs at the top).

### Kebab pattern (new, minimal)

`<details class="kebab">` + `<summary>⋯</summary>` + a small `.kmenu`
card — native disclosure, no framework. One JS nicety: a document-level
click handler closes any open kebab when clicking elsewhere. Menu items
are plain buttons: "Remove this performance" / "Remove this round"
(danger-tinted), reusing the existing remove handlers. Styled inside the
existing token vocabulary (`--raise`, `--shadow`, 3px radius). This is
the app's first overflow menu; keep it single-purpose (no burying
non-destructive actions in it).

## Shared partials (the drift killer)

Extract the card markup each surface hand-rolls into two partials:

- `_editor_leg_card.html` — renders one `.eleg` from a context dict
  (blank for create, populated for edit, hint-carrying for import).
- `_editor_round_card.html` — same for `.redit` (flag for qualifier
  chips: edit/new yes, import no).

Each surface's `<template id="…-row-template">` wraps the SAME partial
with blank values, ending the loop-vs-template duplication. Six card
copies become two files. Add-row JS is untouched (it clones templates).

## Sentence-builder mechanism

- New Jinja macro `sentence_slots(pattern, slots)` splits the translated
  pattern on `{name}` placeholders and emits text nodes + the caller's
  rendered selects in pattern order. Both `remrow` (welcome) and
  `sentence_fields` (preferences) render through it.
- Pattern msgids (new, translatable):
  - welcome: `"Remind me {offset} {direction} {anchor}."`
  - preferences: `"Remind me {days} day(s) {hours} hour(s) {direction} of each {anchor}."`
    (exact EN wording finalized at implementation; the mechanism is the
    point — EN keeps current reading, ja/zh reorder freely.)
- The "moment" rows (offset 0:0) hide the direction select exactly as
  today; the ja/zh patterns must read naturally with it hidden (ja
  「{anchor}の当日に通知。」 degenerate form — verify at implementation).
- Welcome's client-side row builder stops assembling English DOM: it
  clones a `<template id="remrule-template">` server-rendered through the
  same macro (translated), then sets select values. This kills the
  hardcoded "before"/"after" bug.

## Surface alignment items

- concert_new / concert_edit / import_preview all adopt the new cards via
  the shared partials; surface-specific bits (import hints, edit history,
  variant guard on create boundaries only) are unchanged.
- concert_detail (viewer) is OUT OF SCOPE — it already matches its demo
  frame closely and none of the four decisions touch it.
- The variant-gap notice (`_variant_gaps.html`) and guard are unchanged;
  D3 just guarantees the fields they point at are visible.

## Demo reconciliation (design source of truth)

- `dekimasen-demo.html` EDITOR frame: rebuild to the shipped flat
  structure with the new card anatomy — trios on the variants row,
  Cancelled chip + kebab, phrase "Remembered" button, applies-to chips in
  the shipped `.leg-chips` vocabulary, variant-gaps banner example. Remove
  the stale concert-level Venue row from the Tags fold (venue-to-tags
  phase 5 dropped it).
- `dekimasen-onboarding-demo.html` IMPORT PREVIEW frame: same card
  anatomy, per-leg venue selects with a hint example, trios, Tags fold
  moved LAST and venue-row-free (matching shipped), button copy aligned
  ("Create event", "Add a round").
- Both frames keep the demo's self-contained single-file, token-driven
  style; the demo's retired `.elegs`/`.elegcard` nested vocabulary goes
  away in the editor frame.

## Acceptance (for the implementation phase)

1. No × adjacent to Cancelled anywhere; remove lives only in kebabs.
2. One source of card markup per card type (partials), used by all three
   surfaces and their `<template>`s.
3. ja/zh sentence rows read grammatically (pattern order verified by a
   native check of the two pattern msgstrs per locale).
4. JS-added reminder rows are fully translated in all three locales.
5. Variant guard/gaps behavior unchanged; all existing tests pass; new
   render tests cover the kebab and the slot-template splitter.
6. Both demo frames match the shipped design (reviewed side by side).
