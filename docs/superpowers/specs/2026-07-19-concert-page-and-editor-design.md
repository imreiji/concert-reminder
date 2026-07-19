# Concert page and editor

Date: 2026-07-19

Branch 2 of the UI/UX refactor. Branch 1 (Home / Discover split) shipped in PR #45.

## Problem

### The reader page never says where you stand

`concert_detail.html` renders each round as a row in a six-column table — Round / Opens / Closes /
Results / Payment / calendar — inside a horizontal-scroll wrapper, repeated per leg.

Two problems. Most cells are empty, because a round rarely has all four timestamps set. And
nowhere on the page does it say **your** standing, even though `RoundOutcome` has tracked
applied/won/lost/paid per user per round since before this refactor. Home now leads with that;
the concert page still cannot show it.

The header also puts one date range and one venue under the title. For a two-day run at one venue
that is mild repetition; for a tour with different cities it is **wrong** — the legs disagree with
the summary.

And the group is buried. `concert_detail.html:6` leads with `concert.title`, which is often a long
subtitle (*ノーバディーズ・パーフェクト*), while the thing a reader recognises — Aqours — appears
only as one chip among franchise and venue chips. The performers, up to nine of them, are in that
same undifferentiated chip row.

### The editor form buries the common edit and loses data

`concert_edit.html` is one flat form: identity, details, tags, then days, then rounds last. But the
common edit is **"a new round was just announced"**, which means scrolling past twenty fields you
are not touching.

Worse, the round-to-leg relationship goes through free-text matching. `resolve_round_leg`
(`routes/concerts.py:240`) matches a typed string against each day's city or label and returns
**every** match, so `applies_to` really can hold several ids. But `round_leg_display`
(`routes/concerts.py:253`) pre-fills the form from `applies_to[0]` alone.

**So a round that applies to two legs silently collapses to one on the next save.** The data model
supports a set; the form cannot express one.

## Approach

Rebuild both pages around the structure the data already has: a concert is a set of legs, and a
round applies to some of them.

- **Reader page:** lead with lineage and performers, state your standing, and render each leg with
  its own date and venue and its own rounds — each round a row showing your status, one prominent
  date, and the actions to record what you did.
- **Editor:** rounds and legs first, everything else folded; `applies_to` edited directly as leg
  chips instead of inferred from typed text.

Both run on existing data. **No schema change.**

## Scope — reader page

**Header.** Franchise and group above the title as `Love Live! Sunshine!! · **Aqours**`, following
the existing tile rule ("F · G"). The title drops the group if it is already in the lineage. **Date
and venue leave the header entirely** — they belong to legs, which is the only place they are
reliably true.

**Performers** get their own labelled panel rather than sharing a chip row with franchise and
venue: the group chip first, then the members. The label states where they came from — *"9 members,
from the Aqours group tag"* — which quietly explains group expansion on the one page where an
editor might wonder why a member is missing.

Note the panel shows members **as materialised at attach time** (invariant 3), so it can legitimately
differ from the group's current membership.

**Links** row names its destinations: `eventernote · official site · ramen.events`. The current
template labels `source_url` as "source", which says nothing; the source is ramen.events and PR #39
made it actually populate on import.

**Next for you** — one block, the nearest moment on this concert that needs the reader, with their
standing. Same idea as Home's "Up next", scoped to one campaign. Omitted entirely when the reader
has no standing and nothing is open.

**Legs.** One section per `ConcertDay`, each carrying **its own** date, doors, and venue. Cancelled
legs render dimmed and badged, never hidden — invariant 2 requires the row to keep existing.

**Rounds** nest under the legs they apply to, replacing the table. Each round is a row:

`your status` · `round name and kind` · `the next meaningful date` · `actions`

The date column shows the next relevant moment in bold with the others demoted beneath it —
`Closes Wed 23 Jul` / small: `Result 28 Jul · pay by 3 Aug`. Same information, no horizontal
scroll, and the thing you need is the thing you read first.

A round applying to every leg (or to none) renders in a **"Both days" / "All legs"** section rather
than being duplicated under each.

**Capture actions** follow exactly the rules branch 1 established for deadline rows, and must reuse
that logic rather than reimplementing it:

| State | Actions |
|---|---|
| round not yet open | none — "Not open yet" |
| open, no outcome | `I have applied` / `Not applying` |
| `APPLIED`, result not due | none — "Nothing to do" |
| `APPLIED`, result due or passed | `I won` / `I lost` |
| `WON` | `Paid` |

This is safe here for the same reason it was safe there: a row is exactly one round, so "applied"
has one meaning.

**Editor actions** — `Edit event`, `Export YAML` — sit in the header action row, editor-gated. They
do **not** belong in the site nav: editing is something you do to a specific concert.

## Scope — editor

**Order inverts.** Rounds and legs first, with `Add a round` as the primary action. Identity,
details and links, tags, and edit history all become collapsed folds whose summaries show their
contents.

**`applies_to` becomes leg chips.** Each round carries a toggle chip per leg, and the round is
saved with exactly the ids selected. This replaces `resolve_round_leg` / `round_leg_display` and
the `_leg_picker_script.html` text matching.

This is the correctness fix: a round covering two legs currently collapses to one on save. Removing
the text indirection also removes a documented fragility — `round_leg_display`'s docstring warns
that its label-first preference must stay in sync with the client-side `legOptionFor` or a round's
leg silently fails to pre-select.

**Migrating existing data needs no migration**: `applies_to` already holds the right ids. Only the
form's editing mechanism changes.

**Cancelled becomes a toggle** on the leg rather than a `<select>` buried among the day's fields.

**Duplicate and delete** move to a clearly separated danger row, with duplicate stating what it
copies — per invariant 3 it re-attaches the already-pruned tag set with `expand=False`, and it
copies no rounds or legs.

## Out of scope

Named because the design assumes them and they must not leak in:

- **Following toggle and per-leg opt-out.** Both need `ConcertSubscription` — branch 4. The concert
  page shows no follow state in this branch.
- **Upgrade rounds**, including the qualifying-round set — branch 5.
- **eventernote links on performer chips.** Needs `eventernote_url` on tags — branch 3. Performer
  chips render as plain chips here.
- Tags page, preferences, onboarding.

## Constraints

- No schema change, no migration.
- `RoundOutcome` writes go through the existing `record_round_outcome` and the
  `POST /rounds/{id}/outcome` route branch 1 added. No second write path (invariant 2).
- Capture-action state rules are shared with `_deadline_rows.html`, not duplicated.
- `routes/imports.py` stays registered before `routes/concerts.py`.
- Invariant 3: duplicate keeps `expand=False`; the performers panel reflects materialised
  membership.
- Invariant 7: editor URLs through `form_url`; picker data via `| tojson`; no user-controlled text
  in inline `on*` handlers — and note `data-name` collides with `base.html`'s `filterChips()`.
- `edit_concert` must keep calling `snapshot_concert` **before** mutating and `record_concert_edit`
  **after**, or every diff reads as unchanged.
- Times dual, JST first, via `fmt_dual`. Sentence case.
- Every page needs a logged-in GET render test.

## Testing

- A round with `applies_to` covering two legs **survives an edit round-trip with both ids intact** —
  the regression the current form cannot pass.
- A round with empty `applies_to` renders in the all-legs section and stays unassigned on save.
- Each capture-action state renders the right controls, reusing branch 1's rules.
- A cancelled leg renders dimmed and badged, and its rounds remain visible.
- Legs render their own venue; a two-venue concert shows two different venues.
- Performers render from materialised membership, including a concert where a member was pruned.
- Editor page renders for an editor and 404s or redirects for a non-editor.
- Reader page renders for a signed-in non-editor with no editor controls.
- Audit ordering (`snapshot` before, `record` after) still produces a real diff.

## Verification

Drive it: open a concert with two legs, record an application from a round row, confirm the status
changes in place. Open the editor, tick a second leg on a round, save, reopen — both legs still
selected. Mark a leg cancelled and confirm it dims rather than disappearing.
