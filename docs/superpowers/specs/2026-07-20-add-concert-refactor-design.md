# Add-concert page refactor

Date: 2026-07-20

Brings `concert_new.html` (the create form) into line with the redesigned editor
(`concert_edit.html`, PR #46) and the demo token layer (PR #51). The create form is the last
surface still on the old flat design AND the old, buggy leg mechanism.

## Problem

Two problems, one cosmetic and one a real correctness bug.

**Cosmetic:** `concert_new.html` is a flat, always-open form with identity first and rounds last,
predating the editor redesign. It shares none of the editor's card/fold/chip visual language, so the
two pages that do the same job (author a concert's rounds and legs) look unrelated.

**Correctness:** the create form still binds each round to a leg through a single `round_leg`
`<select>` and the server's `resolve_round_leg` text-matcher (`routes/concerts.py:256,630`). That is
the exact mechanism the editor redesign replaced because it silently collapses a round that applies
to two legs down to one on save. The create path was never migrated, so a multi-leg round cannot be
authored at creation time either -- the same data loss, on the other door.

The editor's replacement -- leg *chips* whose value is a space-separated list of `day_key`s, parsed
by `parse_round_legs` with a `key_to_day_id` map -- was deliberately built to handle legs that have
no database id yet (a leg added in the browser this session). On the create form **every** leg is
id-less until save, so that mechanism fits the create case perfectly and needs no new code.

## Approach

Rebuild `concert_new.html` on the editor's shared components and adopt its visual language, and
migrate `create_concert` to the same chip-parsing the editor uses. Reuse, do not reinvent:
`_leg_chips_script.html`, `_round_leg_chips.html`, `_round_qualifier_chips.html`,
`_qualifier_chips_script.html`, `parse_round_legs`, `parse_round_qualifiers`, `build_day`,
`build_round`. No schema change.

### Layout (decided with the owner): identity-first, fold the extras

Creation is the inverse of editing: identity is the first thing and every required field must be
filled, so the editor's "rounds first, identity folded" inversion is wrong here -- a creator would
have to expand a fold to type the title. Instead:

- **Open at the top:** Title (JP), Event ID, and the first Performance -- the required spine.
- **Card sections** (like the editor): Performances as `.eleg` cards, Rounds as `.redit` cards with
  leg chips, each with its primary `+ Add` action.
- **Folded (optional only):** a details/links fold (Title EN, organizer, kind, categories,
  eventernote/official/source URLs), Tags, Performers, Notes.
- The event-id-from-title suggestion JS and the "import from ramen.events" link stay.

### Mechanism migration (the correctness fix)

- `concert_new.html`: drop the `round_leg` `<select>` and `_leg_picker_script.html`; render each
  round's legs via `_round_leg_chips.html` (hidden `round_legs` input + `[data-leg-chips]` +
  `[data-no-legs]`) and, for upgrade rounds, `_round_qualifier_chips.html`; render each performance
  as an `.eleg` card carrying the hidden `day_key`/`day_cancelled` inputs and the cancel toggle that
  `_leg_chips_script.html` drives; include `_leg_chips_script.html` in place of the old picker.
- `create_concert` (`routes/concerts.py`): accept `round_legs`, `round_qualifiers`, and `day_key`
  instead of `round_leg`; build `key_to_day_id` from the day loop exactly as `edit_concert` does;
  resolve each round's `applies_to` via `parse_round_legs(value, valid_day_ids, key_to_day_id)` and
  its qualifiers via `parse_round_qualifiers`. This is the same post-flush, key-mapped resolution the
  editor already proved.
- Delete the now-unused `resolve_round_leg` and `_leg_picker_script.html` once create is the last
  caller gone (verify nothing else references them -- the `:401` mention is a comment in a display
  helper, confirm it does not call it).

## Out of scope

- The editor and concert pages themselves (already done).
- Any change to import (`imports.py`) -- it composes its own rows; leave it.
- Group-tag expansion: `create_concert_row` keeps `expand=False` (invariant 3) -- the create form's
  artist list stays authoritative. Unchanged.

## Constraints (invariants)

- Invariant 1: performance/round times entered in JST; no naive datetimes.
- Invariant 3: `expand=False` on create stays.
- Invariant 7: leg chips are built client-side with `.textContent` and delegated listeners (leg
  labels are user-controlled); no user text in inline `on*` handlers; tag-picker data via `| tojson`.
- Invariant 6: `event_id` still validated; `"new"`/`"import"` reserved (existing `validate_event_id`).
- No schema change. Business logic stays in service/route; templates assemble.
- Sentence case; the page keeps a logged-in GET render test.

## Testing

- **The regression the old form cannot pass:** create a concert with two performances and one round
  whose chips select BOTH legs; after save, that round's `applies_to` holds both day ids. (Old
  `round_leg` select could only ever store one.)
- A round with no legs selected saves with `applies_to = None` (all-legs group).
- An upgrade round created with a qualifier chip selecting another round stores the qualifier
  (rounds created in the same submit have ids after the flush -- qualifiers reference saved rounds).
- A blank trailing performance/round row is skipped (existing behaviour preserved).
- Logged-in GET render of `/concerts/new`; the event-id suggestion and import link still present.
- `create_concert_row` still attaches tags with `expand=False`.

## Verification

Drive it: open `/concerts/new`, add two performances and a round, tick both legs on the round, create,
then open the concert's editor -- both legs still selected. Toggle dark mode on the create page and
confirm it matches the editor. Confirm the old `round_leg` select and `_leg_picker_script.html` are
gone and the suite is green.
