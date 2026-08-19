# Minute-level reminder offsets — design

Date: 2026-08-19. WISHLIST #1, on top of the Proposed list through eight
displacements and never once judged less valuable.

## The gap

`ReminderRule` and `PresetItem` carry `offset_days` + `offset_hours` and
nothing finer. The scheduler already ticks every 60s, so delivery precision
was never the limit — the data model is. `RoundKind.FCFS_SALE` (2026-07-18)
is the case that makes it bite: a first-come-first-served sale is exactly
where "remind me 5 minutes before it opens" beats "3 hours before". The
onboarding wizard's fine-tune list had to ship without the demo's
"30 minutes" entry for the same reason.

Two things found while exploring, both folded into this build:

* **Every surface that DESCRIBES a rule reads `offset_days` alone.** A
  preset item at "3 hours before" renders as "Same day" on the concert page
  (`_rules.html`) and as `same-day` in `/myreminders`. That is a live defect
  today, not something minutes introduce — minutes only make it reachable
  from more places.
* **`apply_preset` dedupes on `(anchor, offset_days,
  offset_hours)`** (`db/core.py`). Add a column and forget this tuple and
  "30 minutes before closes" collides with "at closes": the second rule is
  silently not created, and nothing anywhere reports it.

## Owner rulings (2026-08-19)

1. **The boxes stay.** A single named-duration list (the demos' shape, and
   the recommendation this design opened with) was rejected: the two-box
   sentence is the shape he wants.
2. **The second box becomes an HH:MM box**, not a third box and not a
   29-entry select — free-form `h:mm` text with native pattern validation.
3. **The wizard's fine-tune step gains its sub-hour options in this build**,
   rather than being filed for later.

## Data model

`offset_minutes: Mapped[int] = mapped_column(default=0, server_default="0")`
on `ReminderRule` and on `PresetItem`. Signed exactly like its siblings —
the sign carries before/after, and every write site already computes
`sign * days, sign * hours`.

**Canonical form:** anything under a day is stored decomposed,
`hours = total // 60`, `minutes = total % 60`. One representation only, so
existing rows (whole hours, minutes 0) are already canonical and no backfill
runs. A migration that only adds columns needs no `drop_constraint`, so the
legacy-anonymous-constraint hazard in CLAUDE.md does not apply here.

Deploy: `alembic upgrade head`, not the plain restart.

## `app/offsets.py` — one module for parse, format and describe

New module directly under `src/app/`, above `db/`, importing only stdlib and
`app.i18n`. Both shells import it; nothing in `db/` does.

* `parse_hhmm(text) -> tuple[int, int]` — accepts `"0:30"`, `"00:30"`,
  `"3"` (bare = hours, matching what the box replaced) and `""` (zero).
  Rejects minutes > 59 and hours > 23 with `ValueError`.
* `format_hhmm(hours, minutes) -> str` — `"0:30"`, `"3:00"`. What the box
  re-renders with, so a saved rule reads back exactly as typed.
* `describe_offset(days, hours, minutes) -> str` — the translated phrase:
  "Same day", "30 minutes before", "1 hour 30 minutes before", "3 days
  before", "3 days 6 hours before". It takes the SIGNED stored values and
  derives before/after from them; it does not also accept a `direction`
  argument, because two sources for one fact is a way for a caller to
  disagree with the database. `ngettext` throughout.

It lives above `domain/` rather than inside it because `describe_offset`
needs gettext and no `domain/` module imports `app.i18n` today. Splitting
parse (pure) from describe (translated) across two modules was considered
and rejected: five lines of parsing do not earn a second home, and one
module keeps the round trip — parse, store, format, describe — readable in
one screen.

## Surfaces

**Preferences preset editor** (`preferences.html`, three POST routes). The
`hours` select becomes the HH:MM box; the `days` select is untouched. The
box posts ONE field, `time`, as `h:mm` text; the route parses it into the
two columns. `<input type="text" inputmode="numeric" pattern="..."
placeholder="0:30" title="...">` — native validation, no JS, and a value
that slips past it answers 422 the way `form_url` does.

The sentence msgid changes: `{hours} hour(s)` becomes `{time}`, because the
unit now lives in what the user types. Both catalogues need the new pattern
written by hand; the old translations for the old msgid are lost by
construction, which is the documented cost of any English copy edit.

**Concert page** (`_rules.html`, `POST /concerts/{event_id}/rules`). The
add-form's bare `days_before` number input gains the same two-control shape,
so "5 minutes before this sale opens" can be typed where you are actually
looking at the sale. Its rule LIST switches to `describe_offset`, which is
what removes the "Same day" misreport.

**Discord** (`bot/cogs/reminders.py`). `/remindme` gains an optional
`minutes_before` (0-1439) beside `days_before`, stored decomposed.
`/myreminders` switches to `describe_offset`, closing the same misreport.

**Welcome wizard** (`routes/welcome.py`, `welcome.html`). `OFFSET_OPTIONS`
gains `5 minutes` and `30 minutes`; its `"days:hours"` encoding widens to
`"days:hours:minutes"`, and `create_preset_from_rules`' rule tuples and
`PRESET_TEMPLATES` widen with it. The wizard KEEPS its curated select rather
than adopting the editor's HH:MM box, and this divergence is deliberate:
onboarding offers a short list of good answers, the editor is where an exact
one gets typed. It also moves the wizard TOWARD
`dekimasen-onboarding-demo.html`, whose `OFFSETS` list has carried a `30m`
entry all along — so no demo frame needs re-syncing, and the main demo has
no preset-editor frame at all.

## Testing

* Planner: a minute offset moves `fire_at_utc` by exactly that much, in both
  directions, and a mixed `1d 2h 30m` rule fires where arithmetic says.
* `apply_preset`: a preset holding both "at closes" and
  "30 minutes before closes" creates TWO rules. The mutation this must not
  survive is dropping `offset_minutes` from the dedupe tuple.
* `parse_hhmm`: the accepted forms, and that `"0:75"`, `"24:00"` and
  `"abc"` raise rather than clamp.
* `describe_offset`: a table including the legacy rows that misreport today
  (days 0, hours 3 -> "3 hours before", never "Same day").
* Routes: preset item create/edit, concert-page rule create, wizard preset
  create — each round-tripping a sub-hour value through storage and back
  into the rendered box.
* Render tests for `preferences.html`, `_rules.html`, `welcome.html`.
* A migration test upgrading and asserting the new columns default to 0.
* Catalogue completeness is already enforced by
  `tests/test_i18n_catalogues.py`.

## Out of scope

Snooze granularity, per-rule quiet hours, and any change to how the queue
dedupes reminders. The `RoundKind` cosmetic-members question (WISHLIST #3)
is untouched.

## Bookkeeping

WISHLIST #1 to Shipped with a full re-rank; a README line, since this one IS
user-facing; `docs/architecture.md` entries for `app/offsets.py` and for the
dedupe-tuple trap; a CLAUDE.md layout line for the new module.
