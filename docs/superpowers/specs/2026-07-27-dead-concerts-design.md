# A concert whose every leg is cancelled stops asking you to act

Date: 2026-07-27. Status: **implemented (2026-07-27)**, branch
`cancelled-concerts` off `main`. Designed with the owner (one decision
recorded below); the deviations the build forced are recorded at the
bottom. WISHLIST Proposed #2, now Shipped.

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

## Implementation deviations (recorded)

Four things the build changed about this document. Everything else shipped
as written above.

### 1. §B3's method was insufficient (the one real design defect)

"A fully-cancelled concert passes `has_open_round=False` into `column_for`"
does **not** deliver the owner decision, and the reason is the ruling's own
motivating case. `board_cards` gathers a card's outcomes from its *live*
rounds, and on a dead concert `is_round_cancelled` has already dropped every
round that names a leg. So only standing on a GENERAL round survived to reach
`column_for` — while a 先行 lottery, which names its legs, is the common real
shape and exactly the ticket the owner wanted kept on the board. One case even
regressed: leg-bound standing beside an open general round used to render a
mis-columned card and now rendered none at all.

**Ruling (controller, executing the owner's stated intent rather than
re-asking):** on a dead concert the card's rounds are ALL of the concert's
rounds — feeding outcomes AND ladder rungs from one local — so the card is
placed by the reader's real standing and its ladder contains the winning rung.
`has_open_round` stays False and the single `column_for` exit stays; the wider
set narrows the open question, never widens it, so it cannot leak a card to
someone with no standing.

### 2. The countdown is suppressed on a dead card

Falling out of the same fix: `next_deadline` is None for a dead card, and
`open_round_ids` is empty, so no rung reads "open" beside the Cancelled badge.
A badged card reading "closes in 3 days" is the same lie the branch exists to
remove. Not in §B3, which only specified the badge.

### 3. The branch grew well past four surfaces

The WISHLIST entry named three, §B named four; the shipped predicate is asked
in eleven places across nine surfaces (the planner asks it twice, once per rule
scope, and so does the concert page), all but the planner's found by review
rather than by design:

| Surface | Seam | In §B? |
| --- | --- | --- |
| The planner | `sync_rule` (both scopes) | yes |
| Coming up | `upcoming_deadlines` | yes |
| The board | `board_cards` | yes |
| The concert page | `concert_round_rows`, `concert_rounds_context` | yes |
| The cancellation notice | `notify_newly_cancelled_legs` | no |
| The bot's `/upcoming` | `upcoming_rounds` | no |
| `ShowDeadlinesButton` (the page's DM twin) | `bot/views.py` | no |
| The follow toggle | `following_toggle_context` | no |
| `/setup`, all three screens | `_tracked_upcoming_concerts` | no |

(The branch's running ledger counted "seven" — it folded `ShowDeadlinesButton`
into the concert page and took the notice and `/upcoming` as one arrival.)

The final-review wave then added two more, both Discord and both the same
shape as `ShowDeadlinesButton`: `ReinstateRemindersButton` (the button ON the
cancellation DM, which reported `reinstate_user_rules`'s rule count as
reminders re-armed and so promised notifications a dead concert can never
send) and the cancellation DM's own prose (`LegCancelledContext` now carries
the concert-level fact, so whole-event death reads as more than "a performance
was cancelled"). Thirteen places across eleven surfaces as shipped.

Two of those were more than tidying. The **cancellation notice** was a
silent-loss hole: the planner's new branch deletes a dead concert's queued
reminders, and without widening the notice the reader lost reminders with no
word said. And **`/setup` was genuinely broken**, not accidentally safe:
`_round_asks_application` carries its own eligibility rule, never goes through
`capture_gates`, and nothing upstream filtered dead concerts — so a dead
concert with a general round closing next week reached the applications screen
and offered to record an APPLIED that `record_round_outcome` would never let
the reader take back. That is §B4's stated harm, on a screen this spec never
looked at.

### 4. The follow toggle resolves the fact independently

`following_toggle_context` calls `all_legs_cancelled` itself rather than
reading the page's answer, because `POST /concerts/{event_id}/subscription`
re-renders `_following_toggle.html` **standalone**: reading the page's copy
would have looked right on the GET and reverted to the stale promise on the
first toggle. Two calls over the same legs, no rule restated, pinned by an
`HX-Request` fragment test. The same context also carries the unfollow
dialog's dead-concert copy, for the same reason.

### 5. `dekimasen-demo.html` is deliberately NOT reconciled

Judged and declined. The rule is that the demo is updated when the shipped
design *moves*; this build added a state, not a component, and nothing in the
demo is now wrong. The Cancelled badge is the same `span.badge.cancelled` the
concert page's leg headings have carried since the cancelled-leg build, reused
verbatim on the board card's title — not a new element, and the demo already
carries that silhouette (`.who .tag`). The banner is `.banner.dgr`, the danger
tone of the callout grammar the demo already shows in its warn tone
(`.banner-warn`). Adding a permanently dead card to the demo board would also
cost more than it pays: that board exists to show the four columns healthily
populated and the ladder vocabulary readable, and a static card with no
countdown and a blank rung status would read as a rendering bug with no
explanation attached.
