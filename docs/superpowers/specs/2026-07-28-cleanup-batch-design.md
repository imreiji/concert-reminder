# Cleanup batch: five debts, two owner rulings

Date: 2026-07-28. Status: designed with the owner (two rulings recorded
below), pending implementation. Branch `cleanup-batch`, off `main`.
Clears WISHLIST Proposed #2, #3, #4, #5 and #7, and closes #6 as a
decision rather than work.

Three of these are debts the 2026-07-27 arc created or surfaced. They are
cheapest now, while the context that produced them is still fresh, and
none of them touches the behaviour that shipped this week — this is a
tidying pass, not a redesign.

## Owner rulings (2026-07-28)

1. **A tag attached to a dead concert notifies nobody and applies
   nothing.** Both the "new event" DM and the preset auto-apply are
   suppressed when `all_legs_cancelled`. Rejected: rewording the notice
   (a notice nobody can act on still costs a msgid in three languages)
   and keeping the preset (invisible rules on a dead event, justified
   only by a revival that may never come).
2. **A dead board card keeps one badge, not per-rung marking.** Left
   exactly as shipped: the board is a scanning surface, one badge per
   card is what a badge is for, and the concert page one click away
   labels every round properly. WISHLIST #6 moves to **Rejected** with
   this reason — it was raised as an owner eyeball and the eyeball says
   no.

## A. `event_id` slugs prefer `title_en` (was #2)

`generate_event_id` slugifies `title`; `slugify` strips everything
outside `[a-z0-9]`, so a Japanese-only title collapses to the `"concert"`
fallback and imports mint `concert-2`, `concert-3` — unique but
meaningless in URLs that exist to be the human-readable identity
(invariant 6). Since the trilingual rule made `title_en` mandatory at
every create boundary, prefer it and fall back to `title`.

No backfill: `event_id` is editor-owned after creation, and rewriting a
live URL would break links people hold.

## B. The unfollow dialog stops overstating (was #3)

`_following_toggle.html`'s heavy confirmation has three branches. The
dead-concert branch was fixed on the `cancelled-concerts` branch; the two
LIVE ones still promise "we'll remove that mark and the payment
reminder" / "…and its reminders". The reminder half is true. The mark
half is not — an opt-out never deletes a `RoundOutcome` (invariant 8, and
`routes/subscriptions.py` says so in its own docstring: it forfeits the
reminder, not the record, deliberately, so unfollowing a won ticket is
one confirmed press rather than a two-step chore).

A reader who believes the sentence thinks unfollowing erases the ticket
they recorded — exactly the fear that stops them pressing it. Reword both
so the reminder loss is still named (that is why the confirmation is
heavy) and the record is stated as surviving.

## C. Nothing is announced about a dead concert (was #4)

`handle_newly_tagged` is the notify-and-apply pipeline behind every tag
attach — including the automatic ones `sync_concert_venue_tags` performs
during a venue rollup. It never asks whether the concert is happening, so
attaching a tag to a dead one DMs every follower a 🆕 notice with an
"Apply here" button and quietly applies their preset.

Per ruling 1: when `all_legs_cancelled`, `handle_newly_tagged` queues no
notification and applies no preset. The predicate already exists; this is
one more consumer, asked once at the top rather than per subscriber.

This is the tenth surface of the dead-concert rule. It was not on the
`cancelled-concerts` branch's list because it fires on **tagging**, not
on cancelling.

## D. Expanded folds survive an htmx swap (was #5)

`POST /concerts/{event_id}/legs/{day_id}/opt-out` re-renders
`_round_rows.html` as a whole-region `outerHTML` swap and passes no fold
state, so a reader who expanded a leg's history and then toggles a leg
off watches every fold on the page snap shut.

**`open_round_id` is the wrong instrument and reaching for it is the trap
this entry exists to flag** — it reopens the fold that OWNS a written
round, and an opt-out writes no round. The fix is general: preserve
whichever folds were open across a swap of the region.

Mechanism, client-side, in `base.html` beside the existing htmx
listeners: every `<details>` in a swappable region carries a stable
`data-fold` key; on `htmx:beforeRequest` the open keys within the target
are collected, and on `htmx:afterSettle` the matching folds are reopened.
Generic — it covers the outcome routes' folds and Home's blocks for free,
with no per-caller plumbing.

**`open_round_id` stays.** It is server-rendered, so it is the half that
works with JS disabled; the client mechanism is the half that generalises.
They are complements, not duplicates, and the existing tests for it stand.

Keys: `leg-{day_id}` for a leg's round fold, `block-{event_id}` for a
concert block on Home, `more-concerts` for Home's page-level fold.

## E. Importer review debt (was #7)

Four leftovers batched so they stop being rediscovered:

1. `yaml_import.py`'s `DraftError` message interpolates
   `{exc or 'nesting too deep'}` — exceptions are always truthy, so the
   fallback is dead code.
2. `_text`'s container guard blanks silently where `_dt`'s warns, so a
   container value for organizer/notes/labels/urls leaves no drift
   warning. Warn WITHOUT stringifying the value — the stringify is what
   the alias-fan-out DoS fix removed.
3. `match_tag_ids_by_name`'s docstring states neither the first-tag-wins
   collision order nor that blank names drop from both output lists.
4. `preferences.html`'s preset-item edit form writes its action with
   BACKSLASHES where every sibling uses `/`. Browsers fold `\` to `/` in
   a path so it works today; it is a typo waiting to confuse.

## What does not change

- The dead-concert rule itself, `all_legs_cancelled`, or any of the nine
  surfaces already threaded.
- Existing `event_id` values.
- `open_round_id` and its tests.
- Invariant 8: opt-outs stay overrides that never delete a record — this
  makes the copy match the code, not the other way round.
- No schema change, no migration.

## Testing

- Slugs: a Japanese-only title with `title_en` present slugs from the
  English; without one, the existing fallback still applies; an existing
  concert's id is untouched by an edit.
- Dialog: both live branches state the record survives; the reminder loss
  is still named; the dead branch is unchanged.
- Tag attach: attaching to a dead concert queues no `Notification` and
  creates no `ReminderRule`; attaching to a live one is unchanged
  (the regression half matters more than the fix half here).
- Folds: a leg opt-out preserves an expanded fold; an outcome press
  preserves it; a fold that was closed stays closed.
- Importer: the container guard warns; the docstring items are prose.
- i18n: the two reworded msgids filled in both catalogues, no fuzzy.
