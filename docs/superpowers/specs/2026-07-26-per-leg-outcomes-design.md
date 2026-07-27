# Per-leg outcome truth: covered rounds stop asking, wins record per day

Date: 2026-07-26. Status: designed with the owner (four decisions recorded
below), pending spec review. WISHLIST Proposed #1 — two of the six
2026-07-26 usage-feedback pain points share this root.

## Problem

Two owner complaints, one modeling gap.

1. **The secured-user nag.** Once a user has WON/PAID a ticket for a leg,
   later rounds covering that leg still offer capture buttons. The
   suppression already exists on the reminder side —
   `_apply_outcome_suppression`'s secured-elsewhere pass drops those rounds
   from the DM planner — but it was never threaded into the read-side
   gates (`capture_gates`, `my_deadline_rows`, `concert_round_rows`,
   `/setup`'s `_round_asks_application`), so the UI keeps asking about
   rounds the user's own standing makes irrelevant.
2. **Whole-round wins are a lie for multi-leg rounds.** `RoundOutcome` is
   per (user, round) with no day resolution, but a real lottery resolves
   per performance: one 先行抽選 covering Sat+Sun can come back "won Sat,
   lost Sun". Today that is unrepresentable, so "covered" cannot be
   computed truthfully for multi-leg rounds — which is why these two ship
   together. The owner also dislikes the concert page's separate
   "all legs" section; per-leg outcomes make per-leg display natural.

## Owner decisions (2026-07-26)

1. **Discord-first capture.** Most interactions should happen in Discord;
   the web mirrors it as a catch-up, and should still look good.
2. **Per-leg win buttons.** On results, the DM offers
   `[Won (all)] [Won (day)]… [Lost (all)]`; the same structure appears as
   a popup on the concert page if the user hasn't responded in Discord.
3. **Follow-up step for partial wins**, including payment: unresolved days
   get `[Lost (day)] [Not going (day)]` (Not going writes the existing
   per-leg opt-out), then `[Paid by card already] [Not yet — remind me]`
   (card payment settles at win time in these lotteries, so asking here
   collapses WON → PAID and silences the payment reminder).
4. **Scale assumption:** rounds cover at most ~3 legs in practice (a
   Fri–Sat–Sun event); tour lotteries generally cover ≤2. The
   **progressive** flow was chosen over a per-day grid (preview mockups
   compared): the message re-renders down to only unresolved days after
   each press, with a "Lost the rest" shortcut. Taps: won-all 1,
   won-1-of-3 2, won-2-of-3 3.

## A. Data model

`RoundOutcome` stays per (user, round) as the campaign state. The
`NOT_APPLIED/APPLIED → (WON|LOST) → PAID` sequence in
`record_round_outcome` is unchanged; PAID stays round-level (payment
covers whatever was won on the round).

New table **`round_outcome_days`** (`RoundOutcomeDay`):

| column | type | notes |
|---|---|---|
| id | Int PK | |
| user_id | BigInteger, FK `users.discord_id`, ondelete=CASCADE | |
| round_id | Int, FK `rounds.id`, ondelete=CASCADE | |
| day_id | Int, FK `concert_days.id`, ondelete=CASCADE | |
| result | Enum(`LegResult`: WON, LOST) | stored as value strings like every other enum |
| updated_at | UTCDateTime | `_now`, onupdate |

Unique index on (user_id, round_id, day_id). New `LegResult` StrEnum in
`domain/types.py` (deliberately NOT reusing `LotteryOutcome`: a day
resolves only won-or-lost; applied/paid stay round concepts).

**No-rows-means-all convention** (matches `applies_to` and
`round_qualifiers`): a round outcome of WON with **zero** day rows means
every covered day was won; LOST with zero rows means every covered day
was lost. Day rows exist only when resolution is explicit/partial. Every
existing production outcome therefore stays valid with **no backfill**.

**Secured-days derivation** (single shared helper in `db/service.py`,
used by both the reminder planner and the read-side gates): for a round
whose outcome is WON or PAID — the WON day rows if any exist, else all
days the round covers (live legs; empty `applies_to` = all, as today).
"Not going" is NOT stored here — it writes the existing `LegOptOut`
through `set_leg_opt_out`, so it compounds across all future rounds for
that day (the owner's "only going to one day" case).

Writes stay funneled: a new `record_round_day_result(session, user_id,
round_id, day_id, result)` sibling of `record_round_outcome` maintains
both layers — first WON day row flips the round outcome APPLIED → WON;
"Lost the rest" writes LOST rows for the unresolved days and sets the
round outcome LOST only when no WON rows exist. Day ids are re-validated
against the round's covered days server-side (a forged id is a 404-shaped
no-op, consistent with the setup flow's recompute-server-side rule).
Every write path (Discord buttons, web popup, inline forms) calls these
two functions and nothing else — invariant 2's single-writer rule.

## B. Discord capture (primary surface)

Single-leg rounds keep today's `[I won] [I lost]` pair exactly.

Multi-leg rounds' results DM opens with
`[Won (all)] [Won (Fri)] [Won (Sat)] [Won (Sun)] [Lost (all)]`
(≤5 buttons at the 3-leg practical max — one Discord row-pair). Each
press **edits the same message** down to the unresolved remainder:

```
Fri ✅ won · what about Sat & Sun?
[Won Sat] [Lost Sat] [Not going Sat]
[Won Sun] [Lost Sun] [Not going Sun]
[Lost the rest]
```

then, once resolved with at least one win:

```
All resolved · payment?
[Paid by card already] [Not yet — remind me]
```

The payment question is asked after single-leg wins too. "Paid by card
already" records PAID (existing WON → PAID transition), which already
suppresses that round's PAYMENT-anchor reminders. "Not yet" just
dismisses — the payment reminder flow is unchanged.

**No conversation state.** Every render is a pure function of DB rows
(outcome + day rows + leg opt-outs), in the existing
`views.py`/DynamicItem philosophy: state is re-checked at click time,
never trusted from the label, so a restart mid-flow or a stale message
resolves correctly. New custom_ids extend the `dk:` namespace with
two-id patterns:

```
dk:wonall:{round_id}        dk:lostall:{round_id}
dk:wonday:{round_id}:{day_id}
dk:lostday:{round_id}:{day_id}
dk:skipday:{round_id}:{day_id}     (Not going → set_leg_opt_out)
dk:lostrest:{round_id}
dk:paidnow:{round_id}       dk:paylater:{round_id}
```

Existing `dk:won:{rid}`/`dk:lost:{rid}` remain registered so buttons on
already-sent single-leg DMs keep working. Ignoring a follow-up never
nags: the won day is secured, and only a genuinely new round for the
unresolved day asks again.

## C. Web catch-up popup

Opening a concert page with at least one round in state APPLIED whose
results moment (or, failing that, close) has passed and whose days are
unresolved shows a `<dialog>` (bottom sheet on phone, per the existing
dialog rules) with the identical structure and copy as the DM flow —
built server-side into the page (no extra request), one form per button
POSTing to the extended `POST /rounds/{round_id}/outcome` (new optional
`day_id`/`action` fields). JS-off fallback: the same controls render
inline on the round's row, so the dialog is enhancement, not gate. The
popup shows at most one round at a time (soonest results first); the
existing htmx OOB contract (`#board`/`#board-summary`/standing strip)
carries the swap after each write.

## D. Covered rounds stop asking

The secured-elsewhere derivation is factored out of
`_apply_outcome_suppression` into the shared secured-days helper and
threaded into the read side:

- `my_deadline_rows` drops rows for rounds every one of whose covered
  days the user has secured through some other round (mirroring the
  planner, so Coming up and the DM stream agree).
- `concert_round_rows` renders such rounds in a quiet "Covered ✓" state
  — visible, no buttons (`capture_gates` gains the covered input the
  same way it gained `qualifies` for upgrades).
- `/setup`'s `_round_asks_application` excludes covered rounds.

UPGRADE rounds keep their existing exemption: holding a secured ticket
is the prerequisite to see them, never a reason to hide them.

## E. All-legs section removed

`concert_round_rows` stops returning a separate all-legs list: every
round renders under each live leg it applies to, showing the viewer's
per-day standing for that leg (won/lost/covered/opted-out). A 2-day
concert where every round covers both days shows each round twice —
accepted deliberately; each leg reads as a complete story. Cancelled-leg
rules, `_primary_anchor`, and the upgrade lock line are unchanged;
`_round_rows.html` and its three calling routes move together (the
shared context-builder keeps them in lockstep).

## F. Sharper edges that fall out

- **Per-day auto-arm:** today `_auto_arm_next_round` fires only on a
  whole-round LOST. It now also fires when a day resolves LOST inside a
  partial win — win Sat, lose Fri → the next round covering Fri gets
  armed. Not-going days never arm anything.
- **Suppression exactness:** `_apply_outcome_suppression`'s pass 2 uses
  exact secured days instead of over-approximating from the round's
  whole `applies_to`.
- **Upgrade eligibility** stays round-level (a partial win still holds a
  ticket in the qualifying round); `board.column_for` and
  `discover_statuses` are unchanged in shape — WON is WON.

## i18n

Every new button label, DM line, and popup string is a new msgid in both
catalogues (`test_i18n_catalogues.py` enforces; fuzzy counts as
missing). Day names in button labels come from the leg's `label` via
`loc_field` with the recipient's `user.language` (DM side) or
`get_locale()` (web side) — the standard three-pattern doctrine.

## Migration

One new table, autogenerate + the standard hand-edit (UTCDateTime →
`sa.DateTime()`, drop the models import). Purely additive: no
constraint drops, so the legacy-anonymous-constraint ritual does not
apply. No backfill by design (§A). Normal deploy order.

## Testing

- Service: secured-days derivation (no-rows-means-all, partial, opt-out
  interplay), `record_round_day_result` transitions (first-won flip,
  lost-the-rest, forged day ids), suppression exactness, per-day
  auto-arm, covered gates in `my_deadline_rows`/`capture_gates`.
- Views: progressive re-render through fake interactions (existing
  fake-bot pattern) — initial set, post-press remainder, payment step,
  stale-message press after resolution.
- Web: popup render on a pending-results concert (plus the mandatory
  logged-in GET), extended outcome route (day actions, OOB contract
  preserved), JS-off inline forms.
- Migration: table creation + FK behavior under `PRAGMA foreign_keys=ON`.

## Out of scope (decided)

- Per-day APPLIED (owner chose per-round applied; the rare
  entered-one-day-only case is handled at results time).
- Per-day PAID.
- WISHLIST #2 (Coming-up de-crowding — builds on this, separately),
  #3 (board ladder), #4 (performer grouping), #5 (admin export).
