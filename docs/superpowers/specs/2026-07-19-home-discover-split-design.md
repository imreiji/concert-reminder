# Home / Discover split

Date: 2026-07-19

## Problem

The index page answers **"what's on?"** — a filterable catalogue of every concert, with a
tag sidebar, a search box, and a chronological deadline list underneath. That is a discovery
product.

But the data model describes a different one. The app tracks round kinds (FC presale,
general presale, lottery, first-come, tour package), per-user outcomes
(`RoundOutcome`: applied / won / lost / paid), and automatically arms the next round when
someone loses. Japanese ticketing is not one deadline per show — it is a ladder of rounds, and
losing one does not end the campaign.

So the question a returning user actually has is not "what concerts exist" — they already know
which artists they follow. It is **"where do I stand, and what closes next?"** The hero line
already promises this ("Never say できませんでした again"); the page underneath does not deliver it.

## Approach

Split the index into two pages by the question each answers:

- **`/` — Home.** Where you stand. What closes next, your campaigns by status, your upcoming
  deadlines with the actions to record what you did, and a door out to discovery.
- **`/discover` — Discover.** What's on. Today's index content, given its own page: the tag
  sidebar, the search box, sorts, and the tile grid.

Both are driven by data that already exists. **This change adds no columns and no migration.**

Rejected: keeping one page with the personal section stacked above the catalogue. The catalogue
is tall, the sidebar is sticky, and the two have different filter state — merging them is what
produced the current page's confusion about what it is for.

## Scope

### Home, signed in

Four blocks, in order.

**1. Closes next.** The single nearest actionable deadline across everything the user is
tracking. Concert, round, dual JST + local time, and a countdown. One item, not a list — the list
is block 3.

**2. Your campaigns.** A four-column board, one card per concert:

| Column | Contents |
|---|---|
| Open now | a round is open and the user has no outcome on it |
| Applied | outcome `APPLIED`, awaiting a result |
| Won — pay | outcome `WON`, payment not yet recorded |
| Secured | outcome `PAID` |

A concert appears in **one** column, chosen by the most advanced outcome across its rounds:
`PAID > WON > APPLIED > open`. A concert with an explicit `NOT_APPLIED` on every round and
nothing open does not appear at all.

Each card shows the artist, title, venue, date, and its round ladder — past rounds struck
through, the live one marked, future ones greyed.

**Volume cap.** "Open now" is limited to the 12 soonest by deadline, with a
"+N more" link to Discover filtered to open rounds. A user following a large franchise can match
dozens of concerts; an uncapped column would turn the board back into a catalogue.

**3. Coming up.** A five-column table of upcoming deadlines across tracked concerts:

`Your status` · `Closes` (dual JST + local) · `Concert` · `What happens` · actions

Actions are the capture surface, and vary by state:

| State | Actions |
|---|---|
| Open round, no outcome | `I have applied` / `Not applying` |
| `APPLIED`, awaiting result | none — render "Nothing to do" |
| `WON`, payment pending | `Paid` only |

`Not applying` records `NOT_APPLIED` **for that round on that leg only**, then opens a follow-up
offering the wider action: *"Later rounds for this concert will still reach you"* with
`That's right` / `Skip this concert entirely`. The narrow action is instant; the wider one takes a
second deliberate press.

Capture actions are **not** placed on board cards. A board card is a whole campaign with a
multi-rung ladder, so "applied" there is ambiguous; a deadline row is exactly one round, where it
has one meaning. A destructive control inside something being scanned to read is also a mode error.

**4. Discovery teaser.** A short block — catalogue count, one sentence, a button to
`/discover` — plus four cards as a taste. Discovery still exists; it stops being the front door.

### Home, signed out

Unchanged from today: the hero, and nothing else. The current index already gates all content
behind `if user:`, so this is not a regression. Add a link to `/discover` so an anonymous visitor
has somewhere to go.

### Discover

Today's index content, moved to `/discover`, **public**:

- Tag sidebar: franchise / group / artist, and venues grouped by region
- Free-text search box
- Sort controls, plus a new **Next deadline** sort
- Tile grid

**New: a round-status facet** — `Open now` / `Opening soon` / `Not tracking`. Filtering by whether
you can act is more useful on a discovery page than taxonomy alone.

**Cards carry one status pill**, merging the event's round state with the user's standing:

| Situation | Pill |
|---|---|
| User applied | `FC presale · Applied` (green) |
| User won, payment due | `Won — pay by 22 Jul` (urgent, standing only) |
| User secured | `Secured` (green) |
| No standing | `Lottery R1 · Closes in 4d` / `FC presale · Opens in 13d` / `All rounds closed` (neutral) |

Colour encodes who owes the next move: green = you are covered, urgent = you owe an action,
neutral = you have no standing. One pill, not two — the user's standing *replaces* the countdown
rather than sitting beside it.

**Signed out, Discover renders without the status pills** — event round state only. It is
therefore per-user and cannot be an anonymously cached page.

### Navigation

`base.html` header gains **Home · Discover · Tags**. Nothing else moves into the nav.

## Out of scope

Each is a later branch and must not leak into this one:

- `ConcertSubscription` and per-leg opt-out as real tables. Until then, "tracked" means
  **matches one of the user's tag subscriptions**, derived at query time.
- Upgrade rounds.
- Concert-page and editor redesigns.
- Tags page.
- Preferences restructure.
- Onboarding refactor.

## Constraints

- No new columns, no migration.
- `RoundOutcome` writes go through the existing `record_round_outcome`, which enforces
  `APPLIED -> (WON | LOST) -> PAID`. Do not bypass it or add a second write path.
- Times render dual, JST first, via `fmt_dual`.
- `routes/imports.py` stays registered before `routes/concerts.py` in `web/app.py`.
- Editor-supplied URLs go through `form_url`; tag names into the picker's inline script use
  `| tojson`, never `| safe`; no user-controlled text in inline `on*` handlers (invariant 7).
- Every page needs a logged-in GET render test; `/discover` additionally needs a **logged-out**
  one, since it is public.
- Sentence case throughout.

## Testing

- Board placement: a concert with outcomes on several rounds lands in the column for its most
  advanced one, and the `PAID > WON > APPLIED > open` precedence is asserted directly.
- A concert with `NOT_APPLIED` everywhere and nothing open does not appear on the board.
- The "Open now" cap renders 12 and reports the remainder.
- `I have applied` writes `APPLIED` and the row re-renders as "Nothing to do".
- `Not applying` writes `NOT_APPLIED` scoped to that round, and rounds on other legs of the same
  concert are untouched.
- `Paid` is only offered from `WON`.
- Home signed out renders the hero and 200s.
- Discover renders signed out with no status pills, and signed in with them.
- Deadline ordering and dual-time rendering.

## Verification

Beyond the suite, drive it: sign in, record an application from Coming up, confirm the concert
moves columns on the board and the row loses its buttons. Sign out, confirm Home is the hero and
Discover still browses.
