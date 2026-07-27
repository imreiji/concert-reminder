# A concert whose every leg is cancelled stops asking you to act

Date: 2026-07-27. Status: designed with the owner (one decision recorded
below), pending implementation. Branch `cancelled-concerts`, off `main`.
WISHLIST Proposed #2.

## Problem

`is_round_cancelled` implicitly cancels a round only when every leg in its
`applies_to` is cancelled, and a GENERAL round — empty/None `applies_to` —
is deliberately exempt, because it is tied to no leg. That exemption is
right for a live concert and wrong for a dead one. On a concert whose
every leg is cancelled, a general round stays "live" everywhere, so the
concert:

- keeps its "Next for you" strip on the concert page,
- sits in the board's *Open now* column,
- offers capture buttons on its Coming up row — inviting an APPLIED press
  that `record_round_outcome` will never let the reader take back
  (invariant 2: starting states apply once),
- and, not noted when the entry was filed, **still plans reminders**, so
  the scheduler DMs "apply now" about a show that is not happening.

The per-leg fold (2026-07-27) made it starker rather than causing it: the
leg's body now folds to nothing, so the page shows an urgency strip above
a leg with visibly no rounds on it.

## The rule already exists — on one surface

`discoverable_concert_criterion()` (`db/service.py`) is exactly this
question in SQL:

```python
~has_any_day | has_live_day
```

"no days at all, OR at least one live day" — so a concert with days where
every one is cancelled is already hidden from Discover, and a dateless
draft is already exempt. This work does not invent a rule. It adds the
Python twin of that criterion and threads it through the surfaces that
never got it.

**The fix is NOT to widen `is_round_cancelled`.** A general round on a
multi-leg concert with one cancelled leg must stay live. This is the
concert-level question that predicate cannot answer.

## Owner decision (2026-07-27)

**The board keeps the card only when the reader has standing.** If they
applied, won or paid, the card stays — badged Cancelled, never in *Open
now* — because a cancelled show you hold a ticket for is news, not noise.
With no standing the concert leaves the board entirely, matching what
Discover already does. (Always-stay was rejected: the board would fill
with dead events the reader never had a stake in, the opposite of the
de-crowding just shipped.)

## A. The predicate

```python
def all_legs_cancelled(days: Sequence[ConcertDay]) -> bool:
    """True when the concert HAS legs and every one is cancelled."""
```

Placed beside `discoverable_concert_criterion` with a docstring naming it
as that criterion's Python twin, and pinned by an **agreement test**: over
the same fixtures, the SQL criterion and this predicate must classify
every concert identically. That test is what stops the two drifting — the
same discipline `_wants_you` and `capture_gates` already carry.

Takes the days the callers have already loaded; issues no query.

## B. Surfaces

1. **The planner** (`sync_rule`). A fully-cancelled concert contributes no
   live rounds, so `plan_for_rule` yields nothing and the existing
   "no longer planned → delete" pass clears queued reminders. This is
   beyond the WISHLIST entry's three surfaces and deliberately in scope:
   leaving it out would keep the worst instance of the lie — a DM about a
   dead show. `notify_newly_cancelled_legs` runs BEFORE `sync_concert`, so
   the reader is still told the legs died; they simply stop being nagged
   afterwards.
2. **Coming up** (`upcoming_deadlines`). Rows for a fully-cancelled
   concert are dropped at the source. Because Discover's public deadline
   list calls the same function, this also makes that list agree with the
   grid above it, which already hides these concerts — a consistency fix
   that falls out for free.
3. **The board** (`board_cards`). A fully-cancelled concert passes
   `has_open_round=False` into `column_for`. That single change delivers
   both halves of the owner decision: with no standing, `column_for`
   returns None and the existing `if column is None: continue` already
   drops the card; with standing, the outcome ranks place it in APPLIED /
   WON / SECURED and never OPEN. `BoardCard` gains `cancelled: bool` for
   the badge.
4. **The concert page**. `concert_next_moment` returns None (no urgency
   strip over a dead event), and the capture gates shut so no round offers
   buttons — threaded the way `covered` already is, not by a second rule.
   The page also gains a `.banner.dgr` callout stating the event is
   cancelled: the legs are each dimmed today, but nothing says the whole
   thing is off, and per the callout grammar a banner is the shape for
   "needs attention".

## C. What does not change

- `is_round_cancelled` and its every-leg rule.
- Invariant 2: cancelled legs stay rows, never deleted; `applies_to`
  consumers keep resolving.
- `notify_newly_cancelled_legs` and the cancellation DM.
- Recorded outcomes. A cancelled concert never erases what the reader
  recorded — the board card that survives is precisely that record.
- The concert page stays reachable at its URL, as Discover-hidden
  concerts already are.
- No schema change, no migration.

## Testing

- Agreement: the predicate and `discoverable_concert_criterion` classify
  the same fixtures identically (live concert, one-leg-cancelled concert,
  all-legs-cancelled concert, dateless draft).
- Planner: a queued reminder on a general round is cleared when the last
  live leg is cancelled and `sync_concert` runs; a concert with one live
  leg remaining keeps its reminders (the every-leg rule must not
  over-fire).
- Coming up: rows vanish for a dead concert, both on Home and in
  Discover's public list.
- Board: no standing → no card; standing → card present, badged, and NOT
  in the open column; a one-live-leg concert is untouched.
- Concert page: no "Next for you" strip; no capture buttons on any round;
  the cancelled banner renders; a partially-cancelled concert still shows
  all three.
- i18n: the badge and banner strings in both catalogues, no fuzzy.
