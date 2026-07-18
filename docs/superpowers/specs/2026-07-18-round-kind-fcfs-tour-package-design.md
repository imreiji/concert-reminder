# RoundKind: first-come-first-served and overseas tour package

## Context

Raised while comparing a fan-written Discord ticketing-process guide
against the current domain model. Two discrepancies surfaced:

1. `RoundKind.GENERAL_SALE`'s own code comment says "first-come-first-
   served," but the guide describes "general rounds" as *still a lottery*
   — free to enter, no serial code required, apply-and-wait for a
   results announcement, "usually more competitive." True
   first-come-first-served rounds are a distinct mechanic the guide also
   describes: not a lottery, buy tickets outright the instant the round
   opens, always the very last round, and not guaranteed to occur (only
   happens if tickets remain after the lottery rounds). Confirmed via
   `bot/messages.py`'s `KIND_EMOJI` dict that `general_sale` already maps
   to 🏃 — the "race" emoji was applied to the wrong concept.
2. The "overseas tour package" (gaijin pack) — a hotel+ticket bundle sold
   via its own lottery, structurally distinct from the eplus serial-code
   system (separate application process, pay-only-if-selected, up to a
   2-person joint application, its own cancellation policy, not
   guaranteed to exist for any given concert) — has no representation in
   `RoundKind` at all.

Confirmed via grep that `RoundKind` is a pure classification label today:
no suppression, auto-arm, or anchor logic anywhere branches on a
specific kind value. It's read in exactly these places: the round-kind
`<select>` in `concert_edit.html`/`concert_new.html`/`import_preview.html`,
the badge in `_performances.html`, the ramen.events import heuristics in
`domain/ingest.py`, and the DM embed's emoji lookup (`KIND_EMOJI`) in
`bot/messages.py`. This means adding two new kinds is purely additive —
nothing existing depends on today's specific set of values.

## Non-goals

- No `Round` schema changes. The existing four optional timestamps
  (`opens_at_utc`/`closes_at_utc`/`results_at_utc`/
  `payment_deadline_at_utc`) plus `applies_to`/`url`/`notes` already fit
  both new kinds' lifecycles — a tour package's apply → (if selected) pay
  → attend flow maps directly onto `RoundOutcome`'s existing
  `NOT_APPLIED → APPLIED → (WON | LOST) → PAID` sequence, no new states
  needed.
- No companion/2-person joint-application tracking. This app tracks one
  Discord account's personal deadlines and outcomes; a tour package's
  "up to one companion, share the room and seats" detail is about how
  the *user* coordinates their own eplus/tour-package application, not
  something this app brokers or needs to model per-booking-group.
- No cancellation-policy tracking. Cancelling an application or a won
  package (and getting a refund) is a real-world action the user takes
  with the ticketing/tour operator; the app has no "cancel my
  application" feature for any round kind today, and this doesn't
  introduce one.
- No behavioral differences by kind in `sync_rule`'s suppression/auto-arm
  logic, or in `plan_for_rule`. `RoundKind` remains a pure classification
  label after this change, exactly as it is today — only the *set* of
  labels changes, not what the field does.
- No minute-level reminder offsets. Already logged as a separate,
  lower-priority wishlist idea; explicitly out of scope here.
- `GENERAL_SALE`'s stored value and existing data are untouched — no
  migration, no reclassification pass over existing rounds already
  tagged `general_sale`. Only its *meaning going forward* is clarified
  (free-to-enter lottery, not first-come-first-served); if any
  already-entered round was actually intended as FCFS, that's a manual
  editor fix, not something this change does automatically.

## Section 1: Two new `RoundKind` values

`domain/types.py`'s `RoundKind` enum gains two members, alongside the
existing seven (which are otherwise untouched):

- `FCFS_SALE = "fcfs_sale"` — true first-come-first-served: buy outright
  the instant the round opens, no application/lottery step. Per the
  guide, always the last round for a concert and not guaranteed to
  happen.
- `TOUR_PACKAGE = "tour_package"` — the overseas tour package lottery
  track (hotel + tickets bundle, separate from the eplus serial-code
  system).

`GENERAL_SALE`'s doc comment changes from "一般発売, first-come-first-served"
to something accurate: "一般発売, a free-to-enter lottery round requiring
no serial code — NOT first-come-first-served (see FCFS_SALE)."

## Section 2: Display — a label-override dict + emoji entries

Every `RoundKind` label in the UI today is auto-derived inline via
`{{ k.value.replace("_", " ") | capitalize }}`, repeated across 5
template call sites. This breaks for the new values: `fcfs_sale` would
render as "Fcfs sale" and `tour_package` as "Tour package" (dropping
"Overseas"). Rather than patch around this per-kind, this adds a proper
label table, mirroring the existing `LABEL_BY_ANCHOR` pattern exactly:

- New `LABEL_BY_ROUND_KIND: dict[RoundKind, str]` in `db/service.py`,
  covering all 9 kinds. The 7 existing entries get the *exact* text
  they render today (a pure regression guard — no visible change for
  them), e.g. `RoundKind.ELIGIBILITY_ITEM_SALE: "Eligibility item sale"`.
  The 2 new ones: `RoundKind.FCFS_SALE: "First come, first served"`,
  `RoundKind.TOUR_PACKAGE: "Overseas tour package"`.
- `web/app.py` wires it into `templates.env.globals["round_kind_label"]`
  the same way `deadline_label` is already wired for `Anchor`.
- All 5 existing round-kind template sites (`concert_edit.html` ×2,
  `import_preview.html` ×2, `_performances.html` ×1) switch from the
  inline derivation to `{{ round_kind_label(k) }}`. `ConcertKind`'s
  label sites are untouched — this only affects `RoundKind`.

`bot/messages.py`'s `KIND_EMOJI` dict gains two entries for the DM
embed/text emoji lookup: `"fcfs_sale": "🏁"`, `"tour_package": "✈️"`.
`general_sale`'s existing entry changes from `"🏃"` to `"🎫"` — the racing
emoji was the original conflation's mark and now belongs to `fcfs_sale`;
`general_sale` gets a plain-ticket emoji distinct from `lottery_round`'s
`"🎟️"` even though both are lotteries, since one requires a serial code
and the other doesn't.

## Section 3: Import heuristics — `domain/ingest.py`

The keyword→kind table currently maps the text `"first-come"` to
`RoundKind.GENERAL_SALE` — the same conflation being fixed everywhere
else. It moves to `RoundKind.FCFS_SALE`. Two new keyword entries are
added for the tour package: `"tour package"` and `"overseas"`, both
mapping to `RoundKind.TOUR_PACKAGE`. Everything else in the keyword
table (lottery/抽選, stream/配信, result/announce, payment) is untouched.

## Testing

- `domain/ingest.py`: a test confirming `"first-come"` now classifies as
  `FCFS_SALE`, not `GENERAL_SALE`; new tests confirming `"tour package"`
  and `"overseas"` classify as `TOUR_PACKAGE`; existing keyword tests
  (lottery, stream, results, payment, general sale via non-"first-come"
  phrasing) continue passing unchanged.
- `db/service.py`: a test asserting `LABEL_BY_ROUND_KIND` has an entry
  for every `RoundKind` member (a completeness guard so a future new
  kind can't silently ship without a label and crash the template with
  a `KeyError`), plus explicit assertions for the two new labels' exact
  text.
- HTTP-level: the existing logged-in GET render tests for
  `/concerts/new`, `/concerts/{event_id}/edit`, and `/concerts/import`
  continue passing (confirms the template swap didn't break rendering),
  plus one assertion per page that "First come, first served" and
  "Overseas tour package" both appear as options in the round-kind
  `<select>`.
- `bot/messages.py`: existing `build_reminder_message`/
  `build_new_event_message` emoji tests continue passing unchanged for 6
  of the 7 existing kinds; the `general_sale` emoji test is updated to
  expect `"🎫"` instead of `"🏃"` (a deliberate, intentional change, not a
  regression — see Section 2); new assertions for the two new kinds'
  emoji (`"🏁"`, `"✈️"`).
