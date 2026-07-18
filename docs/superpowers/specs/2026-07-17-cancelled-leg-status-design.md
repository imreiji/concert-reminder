# Cancelled-leg status design

## Context

This started as a request to redesign the Tags page (`tags.html`), which is
genuinely getting clumsy and will keep growing (artists fastest, per the
owner). That redesign is **deferred to its own follow-up spec** — partway
through, the conversation surfaced a dependency: the owner wants the Tags
page's "add member to group" action to optionally prompt "retroactively add
this artist to all active events" that have the group attached, and "active"
needed a real, non-ad-hoc definition instead of scattered date comparisons.

That, in turn, surfaced a bigger and more valuable feature: the index page
should eventually show **open-and-upcoming events first, then a flattened
chronological list of things happening soon** (lottery deadlines, payment
deadlines, event starts), mirroring the layout of
https://mting314.github.io/event-tracker/index.html. That reorganization is
**also deferred to its own follow-up spec** — it depends on this one (you
need a real "is this leg still live" signal before you can decide what
counts as "open and upcoming"), but is a large enough piece of UI work to
plan separately.

This spec is the foundation both of those depend on: a way to mark an
individual performance ("leg") cancelled, and have that propagate correctly
through reminders, display, and the concert's date range — without breaking
anything that currently assumes every `ConcertDay`/`Round` it sees is live.

**Explicitly out of scope for this spec:**
- The Tags page redesign itself.
- The index-page reorganization (open-and-upcoming tiles + chronological
  deadline list).
- A "Postponed" status. Originally proposed, then dropped: the owner
  decided a rescheduled leg is simpler to model as "cancel the old leg,
  create a new one with the new date" than as an in-place date-wipe with a
  confirmation dialog. That whole mechanic (confirmation popup, wiping
  `ConcertDay`/`Round` timestamps, waiting for a new date to be entered) was
  designed and then explicitly discarded — do not resurrect it without
  re-opening this decision with the owner.
- A retroactive "add this artist to active events" action on the Tags page
  (the thing that motivated this spec) — that's part of the deferred Tags
  page redesign, which can now build on the `cancelled` flag this spec adds.

## Why per-leg, not per-concert

Earlier drafts of this spec put the status on `Concert` itself. The owner
corrected this: **a "concert" in this app is a container; the leg
(`ConcertDay`) is the thing that actually gets cancelled or not.** A
multi-city tour can lose one city's date without the tour itself being
cancelled. All the design below operates at the `ConcertDay` level; nothing
is added to `Concert`.

## Data model

Single additive column:

```python
# ConcertDay
cancelled: Mapped[bool] = mapped_column(default=False, server_default="0")
```

No enum, no third state, no column on `Concert`, no column on `Round`.

**Rounds have no status field of their own.** A round is *implicitly*
treated as cancelled, for both reminder planning and display purposes, when:

- `round.applies_to` is non-empty, AND
- every `ConcertDay` id in `applies_to` belongs to a leg with `cancelled =
  True`.

A round with `applies_to = None`/`[]` ("General" — not tied to a specific
leg, e.g. a tour-wide goods sale) is **never** auto-cancelled by this rule,
regardless of any leg's status. A round spanning multiple legs where only
some are cancelled stays active (the other leg(s) still need it).

## The bug this design has to avoid

`concerts.py`'s `group_rounds_by_day()`:

```python
def group_rounds_by_day(concert):
    by_day = {d.id: [] for d in concert.days}
    general = []
    for r in concert.rounds:
        if r.applies_to:
            for day_id in r.applies_to:
                if day_id in by_day:
                    by_day[day_id].append(r)
        else:
            general.append(r)
    return by_day, general
```

This function is unaffected by adding `ConcertDay.cancelled` **as long as
cancelled legs are never removed from `concert.days`** (only marked). A
round referencing a cancelled day's id still finds that id in `by_day` (the
day row still exists) and renders under that day's now-visibly-cancelled
heading. This is the same reasoning that killed the earlier "wipe dates"
design for Postponed: deleting day rows (rather than flagging them) is what
created a dangling-reference risk here. Marking-not-deleting sidesteps it
entirely. No code change needed in this function.

Every other place that reads `applies_to` or day/round timestamps
(`round_leg_display`, YAML export, `.ics` export, the calendar feed,
`is_round_past`/`is_day_past`, the index sort) already tolerates `None`/
empty gracefully — confirmed by inspection during this brainstorm. None of
them need changes for the *existence* of a cancelled flag; they need
changes only where "cancelled" should now be treated like "doesn't count"
(reminder planning, index bucketing) — covered below.

## Reminder planning

`sync_rule`/`sync_concert` (`db/service.py`) build `DayInfo`/`RoundInfo`
dataclasses (from `domain/reminders.py`) and hand them to the pure planner
(`plan_for_rule`). The domain layer stays pure and never learns the concept
of "cancelled" — instead, **service.py filters cancelled legs and their
implicitly-cancelled rounds out of the candidate list before constructing
`DayInfo`/`RoundInfo`**, the same boundary the domain/service split already
enforces for everything else in this codebase.

With cancelled entries filtered out before planning, the *existing*
"planned & queued → keep/update; no longer planned → delete" sync semantics
(the Queue Sync invariant, unchanged) do all the work: a reminder tied only
to a now-cancelled leg or round naturally gets its `reminder_queue` row
deleted on the next sync, with no new suppression flag or special-casing
required anywhere in the sync logic itself.

## Notification on cancel

When cancelling a leg causes a user to end up with **zero** remaining
planned queue rows as a direct result (their `EVENT_START` rule on that
day, or a rule tied only to a round that's now implicitly cancelled) — as
opposed to a concert-wide rule that still has other live legs/rounds to
plan against — that user gets one DM via the existing `notifications`
outbox table, with a "reinstate" button following the exact pattern already
established in `bot/views.py` (`dk:remove:{concert_id}` etc.): a
`discord.ui.DynamicItem` keyed on a `custom_id`, re-hydrated from the regex
at click time, state re-checked live rather than trusted from the label.

The reinstate action does **not** un-cancel the leg (that's an editor
action, done by flipping the checkbox back). It re-runs `sync_rule` for
that user's still-intact `ReminderRule` rows on this concert — "still
intact" because **cancelling a leg never deletes `ReminderRule` rows**,
only the `reminder_queue` rows that were planned from them. If the leg is
still cancelled when they click reinstate, sync will find nothing to plan
and the button is effectively a no-op that says so; if an editor has since
un-cancelled it, reinstate re-arms the reminder normally.

Determining "did this user end up with zero remaining planned rows *because
of this cancellation specifically*" is a before/after diff: capture the set
of queue-row ids about to be deleted for this leg + its implicitly-cancelled
rounds, resolve to affected `user_id`s via `ReminderRule`, delete, then for
each affected user check whether they still have *any* other
`reminder_queue` row tied to this same concert. If not, queue the
notification.

## Editing UX

**Concert edit page** (`concert_edit.html`): each day row is one of several
repeatable rows submitted as parallel form arrays and reconciled by
`zip(day_id, day_label, day_starts_at, ...)` in `edit_concert`. A checkbox
does not fit this pattern safely — an *unchecked* checkbox submits no
value at all, which desyncs the positional zip the moment any row's box is
unchecked. `round_kind` already solves an equivalent problem with a
`<select>` per row (selects always submit a value regardless of which
option is chosen). `day_cancelled` follows the same pattern:

```html
<select name="day_cancelled">
  <option value="false">Scheduled</option>
  <option value="true">Cancelled</option>
</select>
```

Added to both the existing day rows and the `<template id="day-row-template">`
used for newly-added rows (defaulting to Scheduled). `edit_concert`'s
existing per-row reconciliation loop gains one more field to read
alongside `day_id`/`day_label`/etc.

**Concert detail page** (`_performances.html`): a cancelled leg's `<h3
class="leg-heading">` gets a `Cancelled` badge — reusing the existing
`.badge` chip class (already used for "admin"/"editor" in the nav), recolored
to the existing `--danger` red via a `.badge.cancelled` rule — in addition
to the existing struck-through treatment shared with "past" legs. The badge
matters because strikethrough alone doesn't distinguish "already happened"
from "cancelled," which look confusingly similar without it. Rounds
underneath don't need their own badge — they already render nested under
the now-clearly-marked heading.

No separate one-click "cancel this leg" quick action outside the edit
form — an editor learning about a cancellation is already going to the edit
page to adjust that leg's other details, so a fast-path button would be
solving a problem that doesn't exist yet.

## Index-page bucketing (partial — full reorg is a separate spec)

This spec does not implement the open-and-upcoming/chronological-list
reorg, and does **not** build a "does this concert have an open round right
now" signal — that concept doesn't exist anywhere in the codebase today,
and its home is squarely the deferred index-reorg spec.

What this spec does need: `concert_date_range()` (which exists today) must
**exclude cancelled legs** from its computation, so that:

- A concert where every leg is cancelled has no valid dates left, and falls
  out of the default index view (still reachable via direct link or
  search) — same treatment a concert with zero legs entered gets today.
- A concert with a mix of cancelled and live legs computes its displayed
  date range from the live legs only.

This is the minimum change so the flag has any visible effect at all on
today's index page. When the later index-reorg spec builds the "has an open
round right now" signal, it should exclude implicitly-cancelled rounds the
same way this spec excludes cancelled legs from the date range — noted here
as forward guidance, not built now.

## Migration

One additive `add_column` on `concert_days`, `cancelled BOOLEAN NOT NULL
DEFAULT 0` — the simplest kind of migration this project does. Verified the
same way every prior migration here has been: scratch DB, upgrade from the
prior head, upgrade to head, confirm the column via `PRAGMA
table_info('concert_days')`, then applied to the real dev `app.db`.

## Testing

- **Service-layer** (`tests/test_service.py`): seed a concert with two legs
  (one cancelled, one not) and rounds covering all three `applies_to`
  shapes (tied only to the cancelled leg, tied to both legs, "General").
  Assert `sync_concert` clears/skips queue rows only for the cancelled leg
  and the round tied solely to it; the both-legs round and the General
  round are untouched. Assert a user left with zero remaining queue rows
  for the concert gets a notification queued; a user with other live
  legs/rounds does not.
- **HTTP-level** (`tests/test_crud.py`): the edit page's `day_cancelled`
  select round-trips correctly (submitted `true`/`false`, persisted,
  re-rendered selected); the detail page shows the `Cancelled` badge only
  on the cancelled leg; a concert whose single leg is cancelled drops out
  of the default index listing, and a concert with one cancelled and one
  live leg computes its displayed date range from the live leg only.
- **Migration test** (`tests/test_migration_*.py`): scratch-DB pattern
  matching every prior migration test in this repo — confirm the column
  exists with the right default after upgrading.

## Open questions for the implementation plan (not blocking this spec)

- Exact wording/embed shape for the cancellation DM and the reinstate
  button's response messages — cosmetic, can be decided during
  implementation following the tone of the existing `bot/views.py` buttons.
- Whether the reminder-picker UI (`_rules.html`) should exclude
  round-anchors that are already implicitly cancelled when a user is
  setting up a *new* reminder. Minor polish, not required for correctness.
