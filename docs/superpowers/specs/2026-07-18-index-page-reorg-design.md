# Index page reorganization design

## Context

The last item from this session's brainstorm sequence (Tags page redesign
shipped as PR #22; the `ConcertDay.cancelled` foundation it and this both
depend on shipped as PR #21). The owner's original ask: restructure the
index page (`index.html`/`web/app.py`'s `index` route) so concerts with a
currently-open application round show first, followed by a flattened,
chronological list of upcoming deadlines across every concert — inspired
by https://mting314.github.io/event-tracker/index.html's "Open & upcoming"
/ "Upcoming" grouping.

## Non-goals

- Any change to the Tags page, reminder planning, or notification
  behavior — this spec touches only the index page's query/template and
  two small, closely-related deadline-listing fixes elsewhere (below).
- A materialized/cached deadline table. This app's actual scale (a
  $5-10/mo hobby deployment, per an earlier scaling review this session)
  makes a live query per page load trivially cheap — building a
  `reminder_queue`-style outbox for this would be solving a load problem
  that doesn't exist.
- Changing `user_calendar_events` (the personal calendar feed) or its
  per-user, reminder-rule-scoped semantics. The new list this spec adds is
  global/public, not personalized — a genuinely different audience, not a
  variant of the personal feed.

## Included alongside the reorg (owner-approved scope additions)

Three small, closely-related fixes are bundled into this spec since it
already touches the same deadline-listing logic:

1. **Index "Event date" sort key currently ignores `cancelled`.** Display
   and the header date-range summary already exclude cancelled legs
   (PR #21); the `sa_func.min(ConcertDay.starts_at_utc)` used for sort
   order doesn't yet, so a cancelled leg's date can still determine where
   a concert sorts.
2. **`upcoming_rounds` (powers Discord's `/upcoming`) doesn't exclude
   cancelled rounds at all.** A cancelled lottery still appears there
   today. Fixed using the same `_is_round_cancelled` helper this spec's
   new function also uses.
3. **`ShowDeadlinesButton` (bot/views.py, the "Show all deadlines" DM
   button) lists a cancelled leg/round identically to a live one.** Gets a
   "(cancelled)" suffix on any entry that's cancelled.

## Section 1: "Open & upcoming" bucketing

**Definition of "open right now":** a round has `closes_at_utc` set and in
the future, AND `opens_at_utc` is either unset or already passed. A round
with only `results_at_utc`/`payment_deadline_at_utc` set is never "open"
on its own — you can't apply to those, they're pending actions on an
already-closed round. A round must also not be implicitly cancelled (the
existing `_is_round_cancelled` rule from PR #21: every day in its
`applies_to` is cancelled) to count.

**Query change:** the index route's `select(Concert)` gains
`.options(selectinload(Concert.rounds))` alongside the existing
`.days` eager-load — no N+1, one extra join-style load.

**Bucketing:** a concert is **Open & upcoming** if any of its non-cancelled
rounds is "open" per the definition above. Everything else currently
visible (i.e., not fully-cancelled — PR #21's existing hide-logic is
unchanged) is **Upcoming**.

**Rendering:** both buckets reuse the exact same tile markup as today,
split into two `<div class="tiles">` blocks under two headings ("Open &
upcoming" first). The existing sort control (Event date / Recently added)
stays orthogonal to bucketing — it governs order *within* each bucket;
bucketing alone decides which section a tile lands in. The existing tag
filter and free-text search (both client-side, via `data-tags`/
`data-search`) apply identically to both tile grids, unchanged mechanism.

## Section 2: The chronological deadline list

**New service function**, `db/service.py`:

```python
@dataclass(frozen=True)
class UpcomingDeadline:
    concert_title: str
    event_id: str
    label: str          # e.g. "最速先行", "Day 1"
    anchor: Anchor
    at_utc: datetime
    url: str | None = None


async def upcoming_deadlines(
    session: AsyncSession, now: datetime | None = None, limit: int = 10
) -> list[UpcomingDeadline]:
    ...
```

Structurally similar to `user_calendar_events`, but global (not
reminder-rule-scoped) and emits **one row per set timestamp field**, not
one row per round — a round with both `closes_at` and
`payment_deadline_at` set contributes two independent rows ("closes" and
"payment due"), each only if it's in the future. Concretely:

- Fetch every non-cancelled `ConcertDay` with a future `starts_at_utc` —
  each becomes one `EVENT_START` row.
- Fetch every `Round`, exclude implicitly-cancelled ones (via a single
  batched "which day ids are cancelled" query up front — not per-concert,
  matching this codebase's established batch-not-N+1 convention from an
  earlier scaling pass this session), and for each of its four timestamp
  fields (`opens_at_utc`/`closes_at_utc`/`results_at_utc`/
  `payment_deadline_at_utc`) that's set and in the future, emit one row
  with the corresponding `Anchor` value.
- Sort all rows chronologically by `at_utc`, return the first `limit`.

**Label vocabulary:** a new, small, purpose-built dict maps `Anchor` to a
list-row label — e.g. `{Anchor.OPENS: "opens", Anchor.CLOSES: "closes",
Anchor.RESULTS: "results announced", Anchor.PAYMENT: "payment due",
Anchor.EVENT_START: "event"}`. This is deliberately **not** a reuse of
`bot/messages.py`'s existing `ANCHOR_VERB` — that dict only covers
OPENS/CLOSES/EVENT_START (built for reminder-message sentence structure,
"closes in 3 days") and would need awkward retrofitting for a static list
row's phrasing. Two small, independently-worded dicts for two different
contexts is the right call here, not a shared abstraction forced across
different copy needs.

**Rendering:** a third section below both tile grids — a plain
chronological list, each row showing the concert title, the deadline
label, and its dual JST/local time (`fmt_dual`, already a template
global). Tag filter and free-text search apply here too, via the same
`data-tags`/`data-search` attributes per row, reusing the identical
client-side filtering JS already on this page.

## Section 3: The three included fixes

1. **Sort-key fix**: the `sa_func.min(ConcertDay.starts_at_utc)` used for
   `sort=event` ordering gains a `ConcertDay.cancelled.is_(False)`
   condition, matching the display/date-range logic's existing exclusion.
2. **`upcoming_rounds` fix**: gains the same `_is_round_cancelled`-based
   exclusion `upcoming_deadlines` uses, so `/upcoming` stops surfacing
   cancelled lotteries.
3. **`ShowDeadlinesButton` fix**: appends `" (cancelled)"` to any
   round/day line it lists that's cancelled (via `_is_round_cancelled` for
   rounds, `day.cancelled` directly for days), rather than presenting it
   identically to a live entry.

## Testing

- **Service-layer**: `upcoming_deadlines` seeded with a mix of
  cancelled/live rounds and days across multiple concerts, past and future
  timestamps, and one round with multiple set timestamp fields (confirming
  it produces multiple independent rows) — assert the correct set, correct
  chronological order, and correct truncation at `limit`.
- **Service-layer**: `upcoming_rounds` gains a test confirming a cancelled
  round is excluded (mirroring the existing due_reminders/sync_rule
  cancelled-exclusion test shapes from PR #21).
- **HTTP-level**: index page bucketing (a concert with a currently-open
  round appears in "Open & upcoming" before one without); the sort-key fix
  (a concert whose only live-sortable date is on a cancelled leg no longer
  sorts by that leg's date); the chronological list rendering (correct
  rows, correct order, correct truncation); tag/search filtering applying
  to the new list section identically to the tile grids.
- **`ShowDeadlinesButton`**: not independently unit-tested, consistent
  with this file's existing precedent — none of `RemoveRemindersButton`/
  `ApplyDefaultButton`/`SnoozeButton`'s callbacks are unit-tested either
  (discord.py button callbacks aren't easily driven without a live
  interaction object); this fix is a small, low-risk string-formatting
  change reviewed by inspection, matching how this codebase already
  treats this class of code.

## Open questions for the implementation plan (not blocking this spec)

- Exact heading copy ("Open & upcoming" vs "Open & Upcoming" — sentence
  case per this project's UI convention, so "Open & upcoming") and the
  chronological list's section heading — cosmetic, decide during
  implementation.
- Whether the chronological list needs its own empty-state message (no
  upcoming deadlines at all) — likely yes, matching the existing
  `#no-match` pattern already on this page, decide during implementation.
