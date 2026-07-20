# Onboarding and untouched-pages build

Date: 2026-07-20

Turns the concept demo (`docs/superpowers/demo/dekimasen-onboarding-demo.html`, PR #54) into real
pages. It covers every user-facing surface the six-branch redesign + the reconciliation never
touched, plus a signed-out home that was only ever the hero line.

## Problem

The UI reconciliation (PRs #45-#52) brought the app's main surfaces onto a shared design system --
dark mode, cards, chips, folds, the two-line time render. Left behind, still on pre-redesign markup:

- **Signed-out home** is just the hero line -- no explanation of what the app is, no way in for a
  stranger beyond "sign in".
- **The welcome wizard** (`welcome.html`) -- a new user's FIRST screen -- is old-style, and it hands
  off directly into the redesigned `/setup` flow, so onboarding has a visible seam right where first
  impressions form.
- **Import** (`import_form.html`) and **import preview** (`import_preview.html`) are old flat forms;
  the preview still uses the pre-refactor `.row-item` day/round rows the create page just shed, and
  binds no rounds to legs at all.
- **Retroactive apply** (`retroactive_apply.html`) and the **privacy/terms** pages inherit the token
  layer but not the design vocabulary.

The concept demo settles the design for all of these (signed-out landing, a walkable new-user flow,
the import pages, retroactive-apply, legal), on the shipped design system. This spec builds them.

## Approach

Build each surface to match the demo, reusing the shipped components and tokens exactly (as the
reconciliation did against the first demo). The demo is the reference implementation for markup and
CSS -- port from it, do not redesign. Two surfaces also touch the backend; the rest is presentation.

**Out of scope -- multi-language i18n.** The demo carries a language switcher (EN / 中文 / 日本語)
as a placeholder. Real internationalisation -- extracting every template string into locale
catalogues, a language preference, translated copy, localized dates -- is a large separate project
and is NOT part of this build. The pages ship in English; building them first makes a later i18n pass
cleaner (the strings live in redesigned templates). The switcher is not added to the real header
here. Flagged for the wishlist as its own effort.

## Scope

### 1. Signed-out home (landing) -- presentation

Rebuild the signed-out branch of `home.html` (handler in `web/app.py`) into the demo's landing page:
the hero and its promise, the "track the whole campaign, not one date" value prop, a short "how it
works", the four-column campaign board as the visual thesis, a taste of Discover (real public cards),
a catalogue stat line, and the "Sign in with Discord" call to action (hero + foot). The signed-IN
home is unchanged. The public Discover cards reuse the data `/discover` already exposes; the board
"thesis" on the landing is illustrative (static sample), not a live per-user board.

### 2. Welcome wizard -- presentation + a preset-creation step

Rebuild `welcome.html`'s five steps (`routes/welcome.py`) in the card/chip vocabulary, matching the
demo and flowing seamlessly into the already-redesigned `/setup`:

- **Follow artists** -- the franchise/group/member subscribe chips, restyled (keep the existing
  `/subscriptions` post mechanism and `filterChips` search).
- **Default reminders** -- the demo's settled design: three preset cards (Relaxed / Standard
  [recommended] / On the ball) as starting templates, plus a fine-tune list where each rule reads as
  a grammatical sentence ("Remind me when applications open" / "3 days before the deadline" / "when
  results come out" / "1 day before the payment deadline"), editable and removable, with "add a
  reminder". Selecting a card seeds the rules; the anchors are the real five (Opens / Closes /
  Results / Payment / Show), and the DEFAULT reminds once for Opens/Results/Payment (Closes gets the
  couple, nothing on Show). This maps to the real model: creating a `ReminderPreset` with `PresetItem`
  rows via the existing preset routes/service -- do NOT add a second preset write path. The three
  card templates are rule sets the step materialises on submit.
- **Timezone / Test DM / Calendar feed** -- restyled to the demo, reusing the existing timezone,
  test-DM, and calendar-feed routes unchanged.
- The wizard's "skip setup" escape and its `POST /welcome/advance` -> `/setup` handoff stay.

### 3. Import (`import_form.html`) -- presentation

The ramen.events URL paste screen, restyled to the demo. Keep the SSRF-guarded fetch and its
`pattern=` exactly (invariant: https + ramen.events host only). No behaviour change.

### 4. Import preview (`import_preview.html`) -- presentation + leg binding

Rebuild the parsed-draft review in the SAME day-card / round-card / leg-chip vocabulary the new
create/edit pages use (the demo's import-preview view), with the warnings list and the "nothing
saved yet" framing. This means `import_commit` (`routes/imports.py`) adopts the `round_legs` /
`day_key` / `parse_round_legs` binding `create_concert` now uses, so an editor can assign a round to
several legs during import review -- which the old flat form could not express at all. Reuse the
shared parsers; no second binding mechanism. Keep the `source_url` hidden-field round-trip and its
re-validation (invariant 7).

### 5. Retroactive apply (`retroactive_apply.html`) -- presentation

The "add {member} to N active events?" confirmation, restyled to the demo (the affected concerts as
cards/rows, a clear info note that it adds only the new member and does not re-expand or un-prune --
invariant 3 -- with Apply / Skip). No behaviour change; the existing retroactive-apply route stays.

### 6. Privacy / Terms -- presentation

Long-form legal prose given the demo's consistent framing/typography. Content unchanged (they were
written against the real schema). Still public (`current_user`, no `require_user`).

## Constraints (invariants)

- Reuse the shipped design system: tokens, dark mode (both directions), 3px radius, chips, folds,
  `.eyebrow`, `.num`, the two-line time render. Both themes styled.
- Invariant 1: times dual, JST first; no naive datetimes. Invariant 3: `create_concert_row` stays
  `expand=False`; retroactive apply keeps its member-only semantics. Invariant 4: no direct DMs from
  routes (the test-DM diagnostic is the existing carve-out). Invariant 7: editor URLs via `form_url`;
  picker data `| tojson`; no user text in inline `on*` handlers; leg/preset chips built client-side
  via `.textContent`. Invariant 2/8: no second preset, outcome, or subscription write path.
- `routes/imports.py` stays registered before `routes/concerts.py`.
- Business logic in `db/service.py`; domain pure; routes assemble context. Sentence case. ASCII in
  configs.
- Every page keeps/gains a logged-in GET render test; the public pages (landing signed-out, privacy,
  terms) get a logged-out render test.

## Testing

- Landing: signed-out `/` renders the landing (hero, how-it-works, Discover taste, CTA) and 200s;
  signed-in `/` still renders the board.
- Welcome: each step renders; the preset step materialises a `ReminderPreset` with the chosen
  template's `PresetItem`s on submit, and a fine-tuned/added rule persists; the wizard still advances
  into `/setup`; skip-all still lands on `/`.
- Import preview: a round with chips selecting two legs round-trips through `import_commit` with both
  day ids in `applies_to` (the binding the old form could not express); a round with no legs commits
  as all-legs; the `source_url` hidden field re-validates.
- Retroactive apply and legal pages: render tests; retroactive apply still adds only the member.
- Dark mode: the new pages emit no hardcoded light colors on themed surfaces.

## Verification

Drive it: signed out, open `/` and confirm a real landing page, then sign in and walk the welcome
wizard end to end into the board with no visible seam; pick a preset card, fine-tune a sentence, and
confirm the default preset is created with those rules; import a ramen.events URL, assign a round to
two legs in the preview, commit, and confirm both legs stuck; toggle dark mode on every new page.
