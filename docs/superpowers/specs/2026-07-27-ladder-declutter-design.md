# Declutter the campaign ladder: board cards and the concert page

Date: 2026-07-27. Status: **implemented (2026-07-27)** -- four tasks on
branch `ladder-declutter`, deviations recorded at the foot of this file.
Designed with the owner (three decisions
recorded below). Branch `ladder-declutter`, off
`main` (both #97/#98 merged). Covers WISHLIST #1 (board ladder) plus a
new concert-page entry raised by the owner the same day, and carries one
piece of recorded performance debt.

## Problem

A concert mid-campaign has many rounds — 最速先行, 1次, 2次, a
convenience-store presale, 一般発売, FCFS, an upgrade — and both surfaces
that show a campaign render one line per round:

- **Board cards** render one ladder rung per round, so a round-heavy
  concert makes an extremely long tile. Worst in the tablet/phone swipe
  rails, where one tall card stretches the whole row.
- **The concert page** renders every round under every leg it applies to
  (post-per-leg-outcomes), so a 3-leg × 6-round concert is 18 rows, most
  of them settled history.

Home's "Coming up" was de-crowded on 2026-07-27 (#98). This is the same
problem at the other two scales, and it should use the same vocabulary.

## Owner decisions (2026-07-27)

1. **Concert page: one fold per leg**, not a single page-level toggle —
   you expand only the leg you care about, matching how the page is
   already structured (rounds grouped by leg) and how Home's blocks work.
2. **A settled leg keeps its receipt as a full row**: the round that won
   the ticket still renders normally (pill, date, round name) with its
   history folded beneath. The leg does not collapse to a summary line —
   you can always see which round got you in without expanding.
3. **The board caps without expanding**: at most the two rungs that
   matter plus a plain, non-interactive "+N earlier rounds" line. Every
   card stays the same height, which is what makes a kanban scannable;
   the full ladder is one click away on the concert page.

## A. The rule: what still bears on you

ONE rule drives the concert page's fold. A round stays visible on a leg
when any of these holds:

1. **It explains your standing.** The round that secured this leg
   (its `leg_result` is WON, or the round outcome is WON/PAID with the
   no-rows-means-all convention covering it) — the receipt, per owner
   decision 2. Stays visible even once PAID and fully settled.
2. **It still wants something from you** — `_wants_you(outcome,
   can_capture, closes_at_utc, now)`, the predicate shared since #98 by
   Home's block lead and the concert page's "Next for you" strip. True
   for APPLIED (awaiting a result), WON (owing payment), and an open
   round you have not entered.
3. **It is an upgrade round you are eligible for.** Eligibility is
   already derived per viewer (`_eligible_upgrade_ids`); an upgrade you
   cannot enter (`upgrade_locked`) folds like anything else.
4. **It is the next round you could still enter**, when the leg is NOT
   secured. `_wants_you` gates on `can_capture` (the round has OPENED),
   so without this clause the upcoming ladder would vanish entirely.
   Exactly one such round shows — the soonest unopened one; the rest
   fold. On a secured leg no upcoming base round shows at all (they are
   moot, which is what `covered_round_ids` already computes).
   **Upgrades are excluded from this slot entirely** (ruling, task 3
   review): a locked upgrade is not enterable, so handing it the single
   slot would fold the base round you actually could enter and invert
   clause 3; an eligible upgrade needs no slot, since clause 3 already
   shows it unconditionally. So the candidates for clause 4 are base
   rounds only.

Everything else folds: lost rounds, skipped (NOT_APPLIED) rounds, **covered
rounds you did NOT enter**, locked upgrades, and every unopened round after
the next one.

**Decision (task 3 review):** a round that is covered *and* carries a
recorded APPLIED stays VISIBLE, via clause 2. A pending application is an
open obligation regardless of what else you hold — the result is still
coming and it is still yours to act on. `_needs_you`'s covered veto is a
RANKING rule for the one-moment strip ("what should the single lead row
be?"), not a hiding rule, so it does not carry over here. This prose was
corrected to match the shipped `_wants_you` behaviour, not the other way
round, and the divergence is pinned by a test.

**A cancelled leg folds entirely** (header plus fold, nothing visible):
nothing on it can bear on anyone. The leg row itself stays — invariant 2
keeps cancelled legs rendered and dimmed, and this does not change that.

This rule is deliberately not branched on won-vs-not-won. A won leg
yields "receipt + payment line + eligible upgrade, history and moot
sales folded"; an unsecured leg yields "what I'm awaiting + what's open
+ the next one I could enter, losses folded". Same rule, both stories.

## B. Fold presentation (concert page)

One `<details class="moreround">` per leg, closed by default, native
element — the same mechanic #98 shipped and styled at all three widths.

Summary: `+{n} more round(s)` via ngettext, plus **state chips** naming
the composition — each chip its OWN msgid with its own plural
(`{n} lost`, `{n} skipped`, `{n} covered`, `{n} upcoming`). Deliberately
chips rather than a composed sentence: assembling "3 earlier rounds — 2
lost, 1 skipped" from fragments is a word-order trap in ja/zh, and the
project's i18n rule is that translators own word order. Chips make the
ordering a layout question instead of a translation question.

Folded rounds render as the SAME row markup they do today (shared
partial, `capture_actions` unchanged) — the fold hides them, it never
changes them, and a folded round's capture form stays in the DOM and
reachable, exactly as Home's does.

Ordering inside the fold: chronological.

## C. Board cards

Rung selection becomes a pure function in `domain/board.py`, beside
`column_for` and `pill_tone`:

```python
VISIBLE_RUNGS = 2

def visible_rungs(rungs: list[Rung]) -> tuple[list[Rung], int]
```

Returns the rungs to render plus the count hidden. It keeps the rung
that explains the card's column (the last non-`todo` rung — the state
the column is named for) and the next actionable one after it (the first
`live` or `todo` rung), in ladder order, capping at `VISIBLE_RUNGS`.
When fewer rungs exist than the cap, everything shows and the count is
zero.

`Rung` stays where it is (`db/service.py`) — the function takes the
already-built list, so `domain/board.py` gains no ORM import and the
no-I/O rule holds.

The hidden count renders as plain text (`+{n} earlier round(s)`,
ngettext), NOT a `details`: owner decision 3. Board cards remain
read-only orientation, so nothing needs to be reachable from them —
capture lives on Coming-up rows (an existing, deliberate invariant).

## D. Performance debt carried in this arc

`covered_round_ids(session, user_id, concert_id)` is called once per
secured concert in `my_deadline_rows` (and once per page on the concert
page), costing ~6 statements each. #98's wider fetch amplified this:
Home measures **42 statements** on a 12-concert page, pinned by
`test_my_deadline_blocks_query_count_is_pinned` at `<= 45`.

Add the batched sibling:

```python
async def covered_round_ids_by_concert(
    session, user_id, concert_ids: set[int]
) -> dict[int, set[int]]
```

built from one `secured_day_ids_by_round`-shaped pass over all the given
concerts at once, with `covered_round_ids` kept as a thin single-concert
wrapper so no call site is forced to change. `my_deadline_rows`' loop
becomes one call. The existing query-count test's bound tightens to
whatever the new reality measures — measure it, then pin it.

This is a pure refactor: `_covered_from_secured` (the shared fold used
by both the planner and the read side) is untouched, so planner and
read-side semantics cannot drift.

## E. What does NOT change

- `capture_gates`, `capture_actions`, and which button shows when.
- `_wants_you` itself — this is its third consumer, not a redefinition.
- The per-leg round grouping, cancelled-leg rendering, upgrade locking.
- Home's blocks (#98), Discover's flat list, the DM flow.
- The board's columns, ordering, `OPEN_COLUMN_LIMIT`, or pill tones.

## Testing

- Pure: `visible_rungs` — fewer than the cap, exactly the cap, many
  rungs (keeps the column-explaining rung and the next actionable),
  all-settled ladder, count correctness.
- Service: per-leg visibility — a secured leg keeps its receipt and
  folds history and moot upcoming rounds; an unsecured leg shows
  awaited/open plus exactly ONE upcoming; an eligible upgrade always
  shows and a locked one folds; a cancelled leg folds everything; a
  viewer with no standing still sees the open round and the next one.
- Agreement: the fold rule consumes `_wants_you`, so one test asserts
  the concert page and Home answer identically for shared inputs (the
  #98 test extended, not duplicated).
- Page: fold present with the right count and chips; a folded round's
  capture form present in the DOM; no fold rendered when nothing folds.
- Board: card renders at most `VISIBLE_RUNGS` rungs plus the count line;
  the count line is NOT a `details` (owner decision 3).
- Query count: the batched helper's real number measured and pinned;
  planner suppression suites stay green untouched (the refactor's
  equivalence evidence).
- i18n: both catalogues, plurals intact for every new msgid.
- Mobile/tablet: rules inside the existing sections only; the
  breakpoint-count guard (6) stays green.

## Implementation deviations (recorded)

1. **§C's rung selection was wrong and is now standing-based.** The spec
   said "the last non-`todo` rung — the state the column is named for",
   which reads by POSITION. On `[lost, won, live]` — an ordinary
   mid-campaign ladder whose card sits in "Won — pay" because money owed
   outranks a round you could still enter — position surfaces the live
   rung and hides the win, leaving the card naming a column nothing on it
   explains. `visible_rungs` instead ranks by `column_for`'s own
   precedence (won-upgrade > paid > won > applied, later rung wins a tie),
   falling back to the last non-`todo` rung only when NO standing exists
   and to the head of the ladder when nothing has happened at all. This
   forced `Rung` to carry `is_upgrade`, mirroring `_UPGRADE_WON_RANK`, or
   the same failure returns in the upgrade corner (`[paid, todo,
   won-upgrade]`).
2. **The hidden-count copy is "+N more round(s)", not "+N earlier
   rounds".** The spec's wording is false: the state rung can sit
   mid-ladder, so hidden rungs are not all earlier ones. It reuses the
   existing msgid from the Coming-up fold rather than minting a pair
   (the orphaned "earlier" pair was deleted from both catalogues).
3. **§A clause 4 excludes upgrades; clause 2 keeps covered+APPLIED
   visible.** Both are recorded inline in §A above.
4. **`routes/outcomes.py` passes `open_round_id` into the concert-page
   fragment** (not in the plan). Without it, answering a round from
   inside a fold swapped the region, the fold came back closed, and the
   row the reader had just acted on left the screen — the bug #98 fixed
   for Home, reintroduced by this fold. Same server-side mechanic: the
   round id is enough to find its fold, so no JS and no client-held
   state.
5. **`_fold_counts` is deliberately partial.** A round that opened,
   closed and was never recorded matches no chip kind, so the chips can
   sum to less than the summary's number. The number is `len(folded)`;
   the chips explain what they can.

Known, accepted, owner-glance items (not defects): a folded LOCKED upgrade
is tallied under "upcoming", which reads as "you can enter this later"; a
cancelled leg's summary can name rounds that will never happen; and
`_rung_state` maps NOT_APPLIED to `todo` (pre-existing), so a skipped,
already-closed round can win the "next actionable" rung ahead of a genuinely
open one. Separately, `routes/subscriptions.py` renders `_round_rows.html`
with no `open_round_id`, so a leg opt-out toggle collapses every expanded
fold on the page — a different problem from deviation 4 (there is no written
round to reopen around), needing general expanded-state preservation rather
than another id.
