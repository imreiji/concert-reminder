# Outcome correction — design

**Date:** 2026-08-11
**Branch:** `outcome-correction`
**Sketch:** `docs/superpowers/demo/dekimasen-outcome-correction-sketch.html`

## The problem

A user who records the wrong lottery result cannot take it back.

`capture_gates` (`db/core.py`) opens the result buttons only from `APPLIED`:

```python
can_report_result = can_capture and outcome is LotteryOutcome.APPLIED and ...
```

So once a round reads WON or LOST, `_capture_actions.html` falls through to its
last two branches — **"Paid"** if won, **"Nothing to do"** if not — and there is
no way back. Per leg it is the same: a leg with a `RoundOutcomeDay` row leaves
`unresolved_day_ids`, so it drops out of `capture_days` and its card renders no
forms at all.

The starting states are worse. `record_round_outcome` refuses NOT_APPLIED and
APPLIED whenever any row already exists, so a mis-pressed **"Not applying"**
pins the round to `NOT_APPLIED` permanently — and the planner then drops that
round's RESULTS and PAYMENT anchors. One slip silently ends the campaign. This
half is already filed (WISHLIST, 2026-08-04, the entry about an irreversible
APPLIED press); this design closes it as a side effect rather than as a second
feature.

**The model is not the obstacle.** `record_round_outcome` accepts WON and LOST
from any state by design, and `record_round_day_result` overwrites an existing
row. What is missing is a way back to *nothing recorded*, and a surface that
offers it. Nothing in the codebase deletes a `RoundOutcome` today except
`POST /me/delete`, which erases the whole account.

One accidental path exists and is not a feature: the flat `WonButton` /
`LostButton` in a results DM are persistent and unguarded, so pressing the other
one months later flips a settled round. Only single-leg rounds get them, only if
the DM was composed while the round was unanswered, and it is undiscoverable.
The newer progressive buttons already refuse the same press, commenting *"the
site owns corrections"* — a premise this design makes true.

## What we are building

One idea, expressed on two surfaces: **un-answer**. There is no new answer
button anywhere. A correction returns the round (or one leg of it) to the state
it was in before anyone pressed anything, and the existing capture buttons —
which already encode which answer is offerable when — take over from there.

Decisions taken with the owner during brainstorming:

| Question | Decision |
| --- | --- |
| Scope | Clear back to unrecorded, not a transition table |
| Web placement | Concert page round rows only |
| Granularity | Per leg where the leg has its own answer; whole round otherwise |
| Treatment | Quiet text affordance, not a bordered `.act` button |
| Discord | An explicit backtrack button **and** a guard on the stale flat pair |
| Verification | Drive real DMs through the dev bot via `/admin/rehearsal` |

## A · Service — one new writer

`clear_round_outcome(session, user_id, round_id, day_id=None, now=None)` in
`db/core.py`, beside the writers it mirrors, and exported through
`db/service.py` (`tests/test_service_facade.py` fails if the facade disagrees).

It is the **only** deletion path for `RoundOutcome` / `RoundOutcomeDay`, for the
reason invariant 2 gives about their writers: a second one desyncs the queue.
Both modes end by calling `reinstate_user_rules(session, user_id,
round_.concert_id, now)` — the same resync every sibling runs, owned by the
writer so no call site can forget it.

A round id naming no round returns silently, exactly as
`record_round_outcome` and `record_round_day_result` do.

### A1 · Whole round (`day_id is None`)

Delete this user's `RoundOutcome` row for the round and every
`RoundOutcomeDay` row for it. No re-derivation: the round returns to "no row",
which is the common case the entire model is already built around, so every
downstream reader — board column, Coming up, the `.ics` feed, Discover's
standing pill, `covered_round_ids` — is correct with **no change to any of
them**.

### A2 · Per leg (`day_id` given)

Validate the day is covered by the round (`_covered_day_ids`); a forged, stale,
or other-concert id writes nothing at all — one class of input, one answer, as
`record_round_day_result` already rules. Then delete that one
`RoundOutcomeDay` row and re-derive the round from what survives.

**Per-leg clearing is offered only when the leg has its own row**
(`RoundRow.leg_result is not None`). This one rule carries the whole design:

- A leg showing an *inherited* pill is showing the **round's** answer, not its
  own, so the honest correction there is a whole-round clear. The UI can state
  that, and the reader sees exactly what they pressed.
- It guarantees day rows already exist whenever a per-leg clear runs, so the
  no-rows-means-all convention is already off. `_materialize_implicit_won_rows`
  is therefore **never needed on this path**, and its LOST-side twin — which
  does not exist and which a naive design would have had to invent — is not
  needed either.

**Re-derivation**, read off the surviving `RoundOutcomeDay` rows:

| Surviving state | Round becomes |
| --- | --- |
| any WON row left | unchanged — WON stays WON, **PAID stays PAID** |
| no WON row, some covered leg now unresolved | `APPLIED` |
| no WON row, nothing unresolved | `LOST` |

`APPLIED` is the honest answer to the middle case rather than a convenience: a
reader who had a per-leg result was in the draw, and it is exactly the state the
won/lost buttons re-open from. Clearing the last remaining leg lands there too —
after which the leg has no row, so the row now offers a whole-round clear and
two presses reach "nothing recorded". That is a consequence of the rule, not an
exception to it.

PAID is preserved deliberately. Demoting it would re-arm a payment reminder for
a ticket already paid for — the same trap `record_round_day_result` guards.

## B · Web route

`POST /rounds/{round_id}/outcome/clear` in `web/routes/outcomes.py`, with an
optional `day_id` form field.

The module holds no business logic and this route adds none: resolve the caller,
`ensure_user` (the FK guard its siblings need), hand off, commit, re-render. It
reuses `_outcome_response` **verbatim** — the same `HX-Current-URL` surface
split, the same out-of-band fragments, the same 303 for the JS-less path, the
same `open_round_id` fold reopening. Nothing about that helper needs to learn
this route exists.

Validation mirrors the siblings exactly: a missing round is 404 (the service
returns silently, so the route must ask), a `day_id` naming no leg of this
round's concert is a committed no-op.

A new `cleared` key joins `TOAST_MSGS` in `base.html`. The existing keys are
`LotteryOutcome` values; this one is not, which is fine — the map is a lookup
and an unmapped key already yields no toast rather than a wrong one.

## C · Web template

`_capture_actions.html` gains a `correctable=False` parameter. Only
`_round_rows.html` passes `True`, so **Home's rendered markup stays
byte-identical** — which matters beyond tidiness: Home drops LOST and
NOT_APPLIED rounds from Coming up entirely (the planner suppresses their
anchors), so a correction offered there would be unreachable for exactly the
rounds that need it.

Where it renders, by the branch `_capture_actions.html` is already in:

| Branch | Renders |
| --- | --- |
| `row.outcome is none` | nothing — there is no answer to un-answer |
| per-day branch | after this leg's own questions |
| APPLIED (`can_report_result`) | after "I won" / "I lost" |
| WON | after "Paid" |
| terminal (see below) | **replacing** "Nothing to do" |

Those three "nothing" cases are the whole list. **The correction follows the
capture buttons: wherever a card lets you record, it must let you un-record**
(owner ruling, 2026-08-11). There is no per-leg suppression — do not re-derive
one.

A `leg_off` parameter briefly withheld the correction on a leg that was
cancelled or opted out, on the reasoning that it does not belong under a night
that is not happening. The reasoning was fine and the rule was still wrong,
because it was **stricter than the capture rule beside it** — and that
mismatch, not the correction, was the surprise. `capture_gates` takes its
`cancelled` input from `all_legs_cancelled`, which is *concert*-level, so a
round whose `applies_to` names only dead legs on an otherwise live concert
keeps both gates open and renders under that dead leg alone. Measured: it went
from one clear form on the page to zero, with no live sibling to correct from
and no reader-reversible un-cancel, while the same card still offered "I won" /
"I lost". Writable but not un-writable. An opted-out leg makes the same point
from the other side: invariant 8 says an opt-out forfeits the reminder and
never the record, so the record is still theirs to correct.

The per-day branch renders the correction **whether or not this leg still has
questions of its own above it**, which was also a ruling. It used to require
the leg to be answered already, and that was a dead end: a two-leg round marked
APPLIED offers the clear in the terminal branch right up until its results
moment, then falls here and the button vanishes — at exactly the moment
somebody goes looking for it, since "results are out" is when you discover you
told the app the wrong thing. The round holds an outcome even when neither leg
has answered, so there is something to take back; it is simply the round rather
than the leg, which the rule below already covers.

**What a clear POSTS is not a property of the branch** (owner ruling,
2026-08-11; this table originally said it was, and was wrong). The rule:

> A clear posts `day_id` exactly when this card's leg has its **own** answer —
> a `RoundOutcomeDay` row of its own. Otherwise it posts none and clears the
> whole round.

The branch a row falls into is an accident of how many legs are still
unresolved. A fully resolved multi-leg round — won Saturday, lost Sunday —
has nothing unresolved, so `capture_days` is empty and it **never reaches the
per-day branch at all**; it lands in WON. Both legs plainly have their own
answers, so both get their own clear, and Sunday's press must not throw
Saturday's ticket away. `_capture_actions.html` therefore resolves the
condition once at the top of the macro (`clear_day`) rather than per branch.

"This leg has its own answer" is `row.leg_result is not None` **and**
`row.has_day_results`, not the first alone. With zero day rows on the round,
`_leg_result_for`'s no-rows-means-all convention *derives* a `leg_result` for
every covered leg from the round's own outcome — the inherited pill. That is
the round's answer wearing the leg's pill, and §A2's "no materialisation step"
holds only because the surfaces never post a `day_id` for it.

The confirmation (§D) is scoped the same way: a per-leg clear asks about **this
leg** (a LOST leg on a WON round forfeits nothing; a WON leg asks even when the
round is only WON because of it), a whole-round clear about the round.

The final branch is reached whenever there is a recorded outcome and no
offerable answer: PAID, LOST, NOT_APPLIED — **and also APPLIED while the
results are still ahead**, which the original wording missed. That last one is
the same mis-press with more at stake (`record_round_outcome` refuses to
overwrite a starting state), so it gets the correction too, unconfirmed: an
APPLIED round forfeits nothing when cleared. The affordance replaces "Nothing
to do" rather than sitting beside it; the pill on the left already says where
the reader stands. Settled rows get quieter, not busier. This was caught by
building the sketch, not by reading the template.

Two branches deliberately render nothing: `row.covered` (the standing comes from
another round; there is nothing recorded here) and `not row.can_capture`, which
`capture_gates` also shuts for a **cancelled** concert — the show is not
happening and correcting the record changes nothing about that.

**Treatment.** A quiet `.reopen` text button: no border, no wash, `--dim` with a
hairline underline, `--accent` on hover. A correction is not a capture action
and must not compete with "Paid" for the eye; on a settled row it is the only
thing in the cell and reads as a footnote, which is right, since almost nobody
needs it. A hover-only reveal was considered and rejected — touch has no hover,
and the phone is where the mis-press happens.

## D · Confirmation

Only when the press would drop a **secured** record: the round is WON or PAID
(whole-round clear), or the leg's own result is WON (per-leg clear). Everything
else — LOST, NOT_APPLIED, APPLIED — clears on one press, because nothing is
forfeited and a confirmation would be theatre.

Reuses the `<dialog class="prune">` shape from `_following_toggle.html`: `.dh`
header, a `<p>` naming the loss, a `.da` row of `.btn quiet` + `.btn danger`.
One dialog per page, filled from the pressed button's `data-clear-*` attributes
read via `dataset` — the same pattern `data-prune-title` already uses, and never
an inline `on*` handler, since the round label is user-controlled text
(invariant 7). Backdrop-close comes only from `base.html`'s global drag-safe
handler; a local `e.target === dlg` handler is forbidden by a sweep test.

The copy must name three things and must **not** borrow the opt-out's wording.
`_following_toggle.html` promises "Stopping following does not remove that
mark", because an opt-out forfeits the reminder and never the record. This
feature is the first thing in the app that genuinely removes the record, and the
confirmation has to say so.

## E · Discord

Corrections become discoverable in the DM instead of accidental.

### E1 · An explicit backtrack button

Every reply to an outcome press carries **"Change my answer"** — a persistent
`DynamicItem` on `dk:clear:(?P<rid>\d+)`. That covers both press paths: the flat
buttons' `_handle_outcome_click` reply, and `_progress_reply`'s terminal states,
which return `view=None` today.

Pressing it re-derives state first and never trusts the message it was pressed
on, exactly as `_progressive_click` already does — a DM outlives the state it
was built for:

- round currently WON or PAID → reply with a confirm view ("Clear it" / "Keep
  it") naming the loss, since a DM has no dialog;
- otherwise → clear immediately and reply with what is true now.

It calls the same `clear_round_outcome`. The bot adds no write path of its own.

**The DM backtrack clears the whole round**, day rows included, and its
confirmation names how many legs that is when the round covers more than one. A
DM reply is a single moment about a single press; per-leg surgery needs to see
all the legs at once, which is the page — one tap away on the "Open on
dekimasen.app" button every reminder already carries.

`domain/rehearsal.py:expected_buttons` is **untouched**: it describes the
buttons a *reminder* carries, and the backtrack lives on the *reply* to a press.

### E2 · Guard the flat pair

`WonButton` / `LostButton` get the guard `_apply_press` already applies to the
all-legs shortcuts: a press that would overwrite a WON or PAID round writes
nothing. Instead of a silent no-op the reply says the round is already marked,
and carries the backtrack button — so the guard is a signpost rather than a dead
end, and corrections live in exactly one vocabulary.

## F · Testing

Service (`clear_round_outcome`): each re-derivation branch including PAID
survival; a forged/other-concert `day_id` writes nothing; a missing round
returns silently; the queue is actually re-planned (assert on `reminder_queue`,
not on the return value); clearing a round that another round was `covered` by
restores that round's capture gates.

Route: 404 on a missing round; the concert-page fragment shape; the JS-less 303;
the toast header; and that the write commits before the re-render, matching its
siblings.

Template: a settled row renders the clear affordance and **not** "Nothing to
do"; Home's rows render neither; a covered row and a cancelled concert's row
render neither.

Discord: the flat guard writes nothing against WON/PAID; the backtrack clears;
the confirm step is required for a secured round and skipped otherwise.

i18n: every new string in both `.po` catalogues
(`tests/test_i18n_catalogues.py` fails on anything untranslated), with existing
msgids byte-identical.

Per standing guidance, each test names the mutation it would survive; a test
that only re-asserts the code's own shape is a proxy assertion and does not
count.

### F1 · Manual verification (owner-requested)

Run locally with the dev bot token (a throwaway bot with only the owner on it)
and drive `/admin/rehearsal` — the flag-gated harness that seeds a concert and
steps its reminders — to send real DMs and press the real buttons: record a
result, press "Change my answer", confirm the secured path, then re-record.
Automated tests never touch the Discord gateway, so this is the only thing that
proves the button wiring end to end.

## What this does not do

- No correction on Home, in the catch-up dialog, or on `/setup`.
- No way back to a *specific* prior state; the only motion is to unrecorded.
- No provenance: nothing records that a correction happened. The audit log
  covers editor actions on the catalogue, not a user's own record of their own
  lottery.
- A cleared LOST keeps its auto-armed next-round rule. `_auto_arm_next_round`
  created a real `ReminderRule` on the next round's OPENS; clearing the loss
  does not retract "tell me when the next one opens", which stays useful and is
  idempotent if the loss is re-recorded.
