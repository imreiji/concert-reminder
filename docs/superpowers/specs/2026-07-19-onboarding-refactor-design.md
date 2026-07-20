# Onboarding refactor: the first-run capture flow

Date: 2026-07-19

Branch 6 (last) of the UI/UX refactor. Branch 1 (Home / Discover split) and
branch 2 (concert page and editor) have shipped. **Branch 4
(`ConcertSubscription` + Preferences restructure) is a hard dependency and
must merge first** — this branch consumes its model and service API and adds
no schema of its own. Branch 5 (upgrade rounds) is NOT a dependency; it gets
a documented hook here, nothing more.

## Problem

The `/welcome` wizard (first-run guided setup, shipped 2026-07-18) sequences
five *configuration* actions: follow tags, default preset, timezone, test DM,
calendar feed. It ends by dropping the user on Home — a board that branch 1
rebuilt around per-round standing (`RoundOutcome`) and tracked concerts.

But the wizard never captures *standing*. A brand-new user who already
applied to two lotteries last week lands on a board that says they have done
nothing, follows every concert their tags imply (including dates they are
not going for), and only converges on reality as DM buttons trickle in over
weeks. The board's first render — the moment that should sell the app — is
its least accurate one.

Branch 4 gives us the missing verb (a per-concert prune, as a
`ConcertSubscription` opt-out override) and `RoundOutcome` has carried
"applied" since lottery-outcome tracking. What is missing is a single guided
pass that collects both at the moment the user has just told us who they
follow.

## Approach

A separate three-screen **capture flow** at `/setup`, run AFTER the wizard,
not as a wizard step. The wizard configures; this flow captures state; the
reveal at the end is the payoff for both. Reference implementation for
markup, CSS and copy: the concept artifact
`https://claude.ai/code/artifact/ea939428-b99e-43e7-8664-fa276431baba` —
the **Setup** view, reached via its header.

1. **`GET /setup` — "We found N upcoming concerts for you."** A pruning
   pass framed as a result, not a form. Every upcoming concert implied by
   the user's tag subscriptions renders as a tile, default ON (lit); each
   tile says why it is there (the subscribed tag that matched, as its
   eyebrow) and shows venue, date, and its nearest round moment. Switching
   a tile off marks it pruned; Continue submits the batch. Pruning writes a
   branch-4 opt-out override — the same row the concert page's Following
   toggle and Preferences' "N pruned" count read.
2. **`GET /setup/applications` — "Already applied to any of these?"** Asked
   only for rounds on SURVIVING concerts that the user could still be in:
   open now, or closed and awaiting a result. The middle-path rule: a
   closed round whose result moment has passed is never asked about — "a
   past round you lost does not change what happens next." Checking a tile
   and finishing records `APPLIED` through `record_round_outcome` — the
   one and only write path. Skipped tiles get no write at all: the
   existing DM backstop picks them up (see below).
3. **`GET /setup/ready` — "Your board is ready."** The reveal: tallies
   (tracking / applied / payment due / next deadline), one narrative line
   when a payment is pending, and a "Go to my board" button to `/`.

### The state-machine decision

**`onboarding_step` is not extended and its semantics do not change.** The
three screens are three plain GET routes behind `require_user`, each
rendering purely from DB state (subscriptions, overrides, outcomes). There
is no capture-flow step counter at all:

- **Tamper-safe by construction.** Nothing reads a step from user input —
  there is no step. `onboarding_step` keeps its exact current writers
  (`/welcome/advance`, `/welcome/skip-all`) and its exact current meaning
  (0–4 wizard in progress, ≥5 done).
- **Re-runnable by construction.** `GET /setup` at any time renders the
  current truth: previously pruned concerts start OFF, rounds already
  answered simply do not appear on screen 2. Nothing is reset; existing
  choices are the starting point. This is what makes the Preferences
  "Run first-time setup again" entry point (added by branch 4 — coordinate,
  do not duplicate; its link target is `GET /setup`) work with zero extra
  machinery.
- **Idempotent.** Every write is either an override diff (prune/unprune)
  or `record_round_outcome`, which already refuses to overwrite a starting
  state. Replaying a screen's POST changes nothing.

**Rejected:** extending `onboarding_step` to 5/6/7 = capture screens, ≥8 =
done. It retroactively redefines the deployed value 5 (every existing user
sits at exactly 5 = "done"), forces `/welcome`'s done-redirect and
`skip-all` into new semantics, and makes re-running require winding the
counter back — a reset, which the flow must not do. The step machine buys
nothing the URLs don't already provide.

### The wizard handoff

One change to `web/routes/welcome.py`: when `POST /welcome/advance` crosses
into done (`onboarding_step` reaches `TOTAL_STEPS`), it redirects to
`/setup` instead of `/welcome` (which would bounce to `/`). Everything else
is untouched:

- `POST /welcome/skip-all` still jumps to done and redirects to `/` — "skip
  setup entirely" skips the capture flow too.
- `GET /welcome`'s done-redirect stays `/` (its existing test asserts this).
- The first-login-only redirect in `auth.py` is untouched.

### Screen 1 — the pruning pass

- **The set:** branch 4's tracked-concert derivation (tag-implied, minus
  opt-outs, plus explicit opt-ins), filtered to *upcoming*: at least one
  live (non-cancelled) day starting in the future, or any round anchor
  (open/close/result/payment) in the future. Ordered soonest-next-moment
  first, same rationale as the board.
- **Tile contents** (per the concept): eyebrow = the subscribed tag name(s)
  that put it here; title; venue + first live date; a status line with the
  nearest round (`FC presale · closes …`). Times render dual via `fmt_dual`
  (invariant 1) — the concept's bare relative phrases ("closes in 6h") are
  a deliberate deviation we do not copy for timestamps.
- **Lede:** "We found N upcoming concerts for you. Because you follow …"
  listing the distinct matched tag names, plus the reassurance that
  switching off just stops us chasing them and can be changed later.
- **Tiles are checkboxes**, lit/dimmed via CSS on `:checked` — no per-tile
  JS, no round trip per toggle, degrades to plain checkboxes without JS.
  A previously pruned concert renders unchecked (the re-run case).
- **Continue** submits one form: checked ids as `keep`, all rendered ids as
  hidden `shown` fields. The server recomputes the tracked set and acts
  only on `shown ∩ tracked`: unchecked → write an opt-out override (if not
  already pruned); checked-and-currently-pruned → clear the override. Both
  writes go through branch 4's service functions ONLY — this branch never
  touches the `ConcertSubscription` table directly, and never invents its
  own queue-sync (invariant 2 handling for a prune belongs to branch 4's
  function). Tampered ids can at worst edit the tamperer's own overrides —
  the same authority every other surface already grants them.
- **Empty state:** zero tiles renders "We didn't find any upcoming concerts
  for the tags you follow yet" with a link to `/discover` and a Continue
  that proceeds normally.

### Screen 2 — the applications pass

- **Eligibility predicate** (one function, in `db/service.py`, reusing the
  existing helpers — no second copy of round-timing rules): a round is
  asked about iff its concert survives screen 1's set, the round is not
  cancelled (`is_round_cancelled`), the user has no `RoundOutcome` on it,
  `_round_has_opened` is true, it carries at least one timestamp, and
  `_result_moment` is either unset or still in the future. Status label:
  "Still open" when `_round_is_open`, else "Awaiting result".
- One tile per (concert, round). Default OFF. Checking = "I applied".
- **Finish** submits checked round ids; the server recomputes the
  qualifying set and calls `record_round_outcome(session, user_id,
  round_id, LotteryOutcome.APPLIED)` for each checked id inside it. Ids
  outside the set are ignored — which also server-enforces the middle-path
  rule against forged ids for decided rounds. **No second RoundOutcome
  write path**; `record_round_outcome` already owns the sequence rule and
  the reminder-rule resync (invariant 2).
- Unchecked tiles write nothing — deliberately. The footer says so:
  "Anything you skip, we will ask about by DM when its result lands."
- **Branch 5 hook (documented, not built):** when upgrade rounds exist, an
  open upgrade round widens this predicate to also ask about its qualifying
  *closed* round ("Do you hold this ticket?"), the one exception to the
  middle-path rule. The predicate function carries a comment marking the
  spot; nothing else in this branch anticipates it.

### Screen 3 — the reveal

Tallies over the surviving tracked set, computed fresh from the DB:

| Tally | Definition |
|---|---|
| tracking | count of surviving tracked upcoming concerts |
| applied | user's `APPLIED` outcomes on those concerts' live rounds |
| payment due | `WON` outcomes whose round's payment deadline is still future |
| next deadline | soonest future anchor across those concerts' live rounds |

When payment due > 0, one narrative line names the concert with the soonest
pending payment deadline (the concept's "…the closest thing here to losing a
ticket you have already won"). "Go to my board" links to `/`. The page is a
plain GET — visiting it early or repeatedly just shows current tallies.

### The DM backstop (already exists — verified, not rebuilt)

`build_reminder_message` (`bot/messages.py:188-196`) already attaches
`I applied` / `Not applying` buttons to CLOSES-anchor reminder DMs when no
outcome is recorded, `Won` / `Lost` to RESULTS-anchor DMs while the outcome
is unset or `APPLIED`, and `Paid` to PAYMENT-anchor DMs after `WON` — all
funnelling into the same `record_round_outcome`. So anything skipped on
screen 2 is asked about when its result lands, **provided the user has a
reminder rule anchored there** — which the tag-subscription preset
auto-apply normally guarantees. This branch changes none of it; the spec
records the dependency so the footer copy on screen 2 stays honest.

## Branch-4 API this spec assumes

Treat the model as given: subscription is an override (no row = tag-derived
default; a row = explicit opt-in or explicit prune), per-leg opt-out is a
separate row, a prune sticks across unfollow/re-follow, Preferences shows
"N pruned". On top of that this spec assumes, and the plan must verify
against the merged branch-4 code before writing anything:

1. A tracked-set function (today's `tracked_concert_ids`, whose body branch
   4 replaces wholesale per its own docstring) that already folds overrides
   in — screen 1 and the tallies call it, never re-deriving.
2. A service function to write a concert-level opt-out, and one to clear an
   override back to the tag-derived default — including whatever
   reminder-queue resync a prune implies (invariant 2 is branch 4's job at
   that boundary).
3. The Preferences "Run first-time setup again" link, pointing at
   `GET /setup`.

If any of these is missing or shaped differently, that is a plan-level stop:
flag it, do not improvise a schema change or a direct table write here.

## Out of scope

- Any schema change or migration. This branch consumes branch 4's
  `ConcertSubscription` and the existing `RoundOutcome`. If implementation
  appears to need a column, the dependency ordering is wrong — stop and
  flag it.
- Per-leg pruning in the flow. Screen 1 prunes whole concerts; the per-leg
  opt-out lives on the concert page (branch 4's "Not going to this day").
- Upgrade rounds (branch 5) beyond the documented predicate hook.
- Changing the wizard's five steps, `auth.py`'s first-login redirect,
  `onboarding_step`'s meaning, or the skip-all behaviour.
- Any new DM machinery — the backstop already exists.
- The Preferences entry-point link itself (branch 4 adds it).

## Constraints

- `onboarding_step` stays tamper-safe: never read a step (or any flow
  position) from user input. The capture flow has no step state at all.
- `RoundOutcome` is written only through `record_round_outcome` (no second
  write path); `ConcertSubscription` only through branch 4's service
  functions.
- Business logic in `db/service.py`; routes assemble context and delegate.
- Invariant 7: `| tojson` never `| safe` for anything user-controlled in
  inline scripts; no user-controlled text in inline `on*` handlers; not
  `data-name` (collides with `base.html`'s `filterChips()`).
- Times dual, JST first, via `fmt_dual`. Sentence case throughout.
- Every page needs a logged-in GET render test — all three screens.
- Baseline: 638 passing + 1 known-failing local test
  (`test_crud.py::test_test_dm_when_bot_disabled`, pre-existing, CI green,
  out of scope).

## Testing

- All three GETs: 401 logged out; 200 render logged in, including the
  screen-1 empty state and a zero-question screen 2.
- Screen 1: a tile per tracked upcoming concert, eyebrow naming the
  matching subscribed tag; a past-only concert excluded; a pruned concert
  renders unchecked on re-run; Continue prunes unchecked, unprunes
  re-checked, ignores ids outside the recomputed tracked set; both writes
  observed through branch 4's rows, not raw table asserts against a
  hand-built row.
- Screen 2 eligibility: open round asked; closed-awaiting-result asked;
  closed-with-past-result NOT asked; unopened round NOT asked; round with
  any existing outcome NOT asked; pruned concert's rounds NOT asked.
- Screen 2 finish: checked → `RoundOutcome` `APPLIED` exists (and the round
  disappears from a re-rendered screen 2); unchecked → no row; a forged id
  for a decided round → no row; an id the user already has an outcome on →
  outcome unchanged (`record_round_outcome`'s own rule, asserted through
  this route).
- Screen 3: tallies correct for a seeded scenario covering all four
  numbers; payment narrative line present iff payment due > 0.
- Handoff: advancing from wizard step 4 redirects to `/setup`; earlier
  advances still redirect to `/welcome`; `skip-all` still lands on `/`;
  `GET /welcome` when done still redirects to `/`.
- Re-run: after a full pass, `GET /setup` renders again with prior choices
  reflected and a second Finish writes nothing new.

## Verification

Beyond the suite, drive it: fresh login → wizard → land on `/setup`, prune
one concert, mark one application, see the reveal count both, land on Home
with the board matching. Then re-enter `/setup` and confirm the pruned tile
is off and the answered round is gone from screen 2.
