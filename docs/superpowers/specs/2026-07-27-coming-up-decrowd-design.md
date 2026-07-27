# De-crowd "Coming up": one row per round, one block per concert

Date: 2026-07-27. Status: designed with the owner (three decisions
recorded below), pending implementation. WISHLIST Proposed #1. Branch
`coming-up-decrowd`, stacked on `per-leg-outcomes` (PR #97) because this
de-crowds the row set that branch reshaped.

## Problem

`upcoming_deadlines` emits one row per future ANCHOR, so a single round
carrying opens/closes/results/payment takes up to four of Home's ten
"Coming up" slots, all naming the same round. On top of that a concert
mid-campaign has several rounds ahead of it at once, so one concert can
fill the list by itself. The 2026-07-21 mobile retrofit turned each row
into a bordered card, so the same budget now costs screens of scroll on a
phone rather than pixels of table height on desktop.

The correctness half shipped 2026-07-19 (capture gated on
`can_capture`, so the duplicate rows stopped offering independent
buttons); what remains is purely the row budget. Per-leg outcomes
(2026-07-27) removed some rows via covered-round suppression, so this
de-crowds the POST-suppression set.

## Owner decisions (2026-07-27)

1. **Concert block + fold** (over a flat per-round list, and over one
   row per concert): each concert is one block, led by the round that
   needs the viewer soonest, with its remaining rounds behind a
   client-side "+N more rounds" expander that keeps their capture
   buttons intact.
2. **Standing first, then time** for the lead: rounds the viewer has
   live standing on (APPLIED awaiting a result, WON owing payment)
   outrank rounds they have not entered; soonest first within each
   group. Money owed therefore leads its block instead of hiding behind
   a general sale that opens sooner.
3. **10 concerts, 6 visible**, with a page-level "+N more concerts"
   expander revealing the rest client-side.

## A. Where the collapse happens

`upcoming_deadlines` is UNCHANGED. Discover's public "Coming up soon"
list calls it directly (`web/routes/discover.py:241`), and a flat
chronological list is the right shape for a catalogue nobody has
standing on. The grouping is Home's, so it lives in Home's path.

New in `db/service.py`, beside `my_deadline_rows`:

```python
@dataclass(frozen=True)
class ConcertBlock:
    event_id: str
    concert_title: str
    venue: str | None
    starts_at_utc: datetime | None      # next live performance date
    lead: DeadlineRow
    others: tuple[DeadlineRow, ...]     # empty when the concert has one row

async def my_deadline_blocks(
    session, user_id, now=None, limit=DEADLINE_ROWS_LIMIT, concert_ids=None,
) -> list[ConcertBlock]
```

`my_deadline_rows` stays — the per-row decoration (outcomes, gates,
venue, `capture_days`, covered/upgrade filtering) is exactly what a block
member needs, so blocks are built ON it rather than beside it. No second
derivation.

**Per-round collapse is free.** `upcoming_deadlines` emits only FUTURE
anchors in chronological order, so "keep the first row per `round_id`"
IS the moment `_primary_anchor` picks on the concert page — the two
surfaces agree by construction, not by a duplicated rule. Rows with no
`round_id` (an EVENT_START row derived from a `ConcertDay`) are not
collapsed: they are the show itself, one per leg, and carry no round to
collapse onto.

**Fetch width.** Today `my_upcoming_deadlines` truncates to `limit`
BEFORE decoration, so grouping afterwards would under-fill (ten anchor
rows can be two concerts). `my_deadline_blocks` therefore requests a
wider anchor window internally — `limit * ANCHOR_FAN_OUT` where
`ANCHOR_FAN_OUT = 6` (four anchors plus headroom for multi-round
concerts) — then collapses, groups, and caps at `limit` CONCERTS. The
window is a bound on work, not a promise: a viewer with more than
`limit * 6` future anchors sees the soonest `limit` concerts, which is
the same guarantee today's truncation gives.

## B. One lead rule, two shapes

The concert page already answers "which round wants me first"
(`_needs_you` + `_next_moment_key`, `db/service.py:2893`), but both take
a `RoundRow`. Generalize the predicate to primitives so both row shapes
feed it:

```python
def _wants_you(outcome: LotteryOutcome | None, can_capture: bool,
               closes_at_utc: datetime | None, now: datetime) -> bool
```

`_needs_you(row, now)` becomes a one-line adapter over it, and the block
builder passes a `DeadlineRow`'s equivalents. Rule unchanged: APPLIED or
WON is live standing; any other recorded outcome is settled; no standing
counts only while the round is open (`can_capture` and not yet closed).

Ordering: lead = the block's rows sorted by `(not wants_you, moment)`,
first wins. `others` keep chronological order. Blocks sort by their
lead's moment, ties broken by event_id for determinism.

A block whose only row is an EVENT_START row leads with it (the show is
a legitimate coming-up item) and renders no capture form, exactly as
that row does today.

## C. Rendering

`_deadline_rows.html` becomes block-structured and KEEPS its outer
`<div id="deadline-rows">` — that div is the htmx target for
`POST /rounds/{id}/outcome`'s `outerHTML` swap, and the response must
carry the identical structure (its `#board`/`#board-summary`
out-of-band fragments are untouched by this change).

Per block: a header line (concert title linking to the concert page,
venue, next performance date — the fields `DeadlineRow` already
carries), the lead row, then, when `others` is non-empty, a
`<details class="morerounds">` whose summary reads "+N more rounds" and
whose body is the remaining member lines. Every member line — lead and
folded alike — renders through the existing `capture_actions` macro with
the same `#deadline-rows` target, so which button shows when is still
decided in exactly one place.

Page level: blocks 1-6 render directly; 7-10 sit inside a second
`<details class="moreconcerts">` with a "+N more concerts" summary. Both
folds are closed by default (that is the point of the feature) and use
the same native `details` mechanic as the kebab menu and filter sheet —
no JS, so both degrade correctly.

Two new msgids, both plural-aware via `ngettext`: "+{n} more round(s)"
and "+{n} more concert(s)". Hand-filled in ja and zh.

## D. Budget

`DEADLINE_ROWS_LIMIT = 10` keeps its name and its role as the ONE
constant shared by `GET /` and `POST /rounds/{id}/outcome` (neither
passes a limit, so the swap can never change the list length), but now
counts CONCERTS. A new `VISIBLE_BLOCKS = 6` governs the page-level fold.
Both live beside each other in `db/service.py` with the existing comment
explaining why the constant is shared.

## E. Routes

Two call sites change together, and only these two:
`web/app.py`'s home handler and `web/routes/outcomes.py`'s
`_outcome_response`. Both swap `my_deadline_rows` for
`my_deadline_blocks` and pass `blocks` to the template. Discover is
untouched. `my_deadline_rows` remains public and tested — it is the
layer blocks are built on.

## F. Mobile and tablet

Every phone rule goes inside the existing single
`@media (max-width: 700px)` section and every tablet rule inside the
`701-1040px` band — no new top-level breakpoints
(`test_theme_and_tokens.py` pins the count at 6, and that pin stands).
The tablet band's `data-happens` fold (which folds the what-happens
column into the title line) must keep working on member lines. On the
phone the block header becomes the card's title row and member lines
keep the existing full-width 44px action buttons.

## Testing

- Service: per-round collapse keeps the soonest anchor and drops the
  rest; standing beats time for the lead (payment leads over a sooner
  unentered sale); a settled round never leads; blocks ordered by lead
  moment; concert cap at `DEADLINE_ROWS_LIMIT` with the wider internal
  window actually filling it; a one-round concert yields empty `others`;
  an EVENT_START-only concert leads with the show.
- Shared rule: `_needs_you` and the block lead agree on the same inputs
  (one test asserting both surfaces answer identically), so a future
  edit cannot drift them.
- Page: block header renders once per concert; a folded round's capture
  form is present in the DOM (reachable, just collapsed); no fold link
  for a single-round concert; the page-level fold appears only past 6.
- htmx parity: `POST /rounds/{id}/outcome` returns the same block
  structure with the `#deadline-rows` target intact and the two
  out-of-band fragments unchanged.
- i18n: both catalogues complete, plural forms intact
  (`test_i18n_catalogues.py` enforces).
- Mobile: block header and fold render inside the phone section; the
  breakpoint-count guard still passes.

## Out of scope

- Discover's flat deadline list (deliberately unchanged).
- The board, the concert page, and the DM flow.
- Sorting or filtering controls on Coming up.
- WISHLIST #2 (board ladder collapse) — a separate entry, though this
  ships the fold vocabulary it may reuse.
