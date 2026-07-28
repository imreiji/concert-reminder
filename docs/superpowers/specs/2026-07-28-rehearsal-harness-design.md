# Rehearsal harness: exercise the whole user flow on production

Date: 2026-07-28. Status: **designed, PARKED — not implemented and not
planned.** Designed with the owner in the same session as
`2026-07-28-delivery-feed-design.md`; the safety model and the canonical
scenario were both approved section by section before it was parked.

**Sub-project A of three.** The owner asked for a way to walk the entire user
flow on production, including every kind of Discord reminder DM. That session
also produced **B** (the delivery feed) and **C** (the admin broadcast), and
the owner sequenced **B and C first**, on the reasoning that an undetected bad
delivery on production costs more than a missing test harness. This spec
exists so the approved design is not lost in the meantime.

Nothing here blocks B or C. Worth noting the dependency runs the other way:
this harness is how C — a mass-DM route — could be rehearsed without DMing
real people, which is an argument for building it before C ships rather than
after.

## What it has to cover

Both halves, per the owner's answer:

- **A pipeline harness** — real rows, real `reminder_queue`, the real 60s tick
  sending real DMs. Catches planner, suppression and gating bugs.
- **A shape catalogue** — one action that synthesizes every DM shape and sends
  it, for fast copy/layout/translation checks after an i18n change.

Coverage target: **5 anchors** (OPENS, CLOSES, RESULTS, PAYMENT, EVENT_START),
**3 outbox notices** (`new_event`, `leg_cancelled`, `ops_alert`), **11
persistent buttons** (`apply`, `remove`, `deadlines`, `snooze`, `reinstate`,
`applied`, `notapplied`, `won`, `lost`, `paid`, `remindlater`).

`ops_alert` is **shape-catalogue only**. Tripping it for real means backdating
`backup_marker_path` or faking low disk, and `domain/health.should_alert`
requires two consecutive agreeing observations while writing real
`OpsCheckState` rows — so a pipeline test would corrupt live ops-alert state
to see one embed.

## Safety model (approved)

Production's catalogue is shared, so a rehearsal concert has two leak
channels of unequal severity: appearing on public `/discover` is cosmetic,
while `handle_newly_tagged` DMing every follower of an attached tag is a real
intrusion with no un-send. The owner's requirement was that the design be
safe with real users even though today he is effectively the only one.

**One new column: `Concert.rehearsal`, boolean, default false.** That is the
entire schema change, and it is honoured in only three places because
everything else is already scoped by tracking:

| Surface | Kind | Treatment |
| --- | --- | --- |
| `discoverable_concert_criterion` | global (tiles + Home teaser count) | `AND NOT rehearsal`, unconditional |
| `upcoming_rounds` | global (bot `/upcoming`) | `AND NOT rehearsal`, unconditional |
| `upcoming_deadlines` | **both** | new `include_rehearsal: bool = False`; only `my_upcoming_deadlines` passes `True` |
| board, Coming up, `/setup`, calendar feed, `/mydeadlines`, concert page | per-user | **untouched** |

The last row is the load-bearing claim. `tracked_concert_ids` is
`(tag_matched − opted_out) ∪ subscribed` (invariant 8), so if the rehearsal
concert carries only rehearsal tags that nobody else follows, no other user
can reach it through any per-user surface — with no changes to those surfaces
at all.

`upcoming_deadlines` needs the parameter rather than a blanket filter because
it has two callers of different kinds: `/discover`'s public list (global) and
Home's "Coming up" via `my_upcoming_deadlines` (per-user, narrowed to tracked
concerts). An unconditional filter would hide the rehearsal rows from the
owner's own Home page, which is where the capture buttons are.

The concert stays directly reachable at `/concerts/{event_id}`, exactly as a
fully-cancelled concert does — deliberate, since that is how it is inspected.

**Two hard rules for the harness itself:**

1. The pull-forward action resolves its queue rows **through the rehearsal
   concert**, never by queue id from a form field. A `queue_id` parameter is
   the version of this feature that fires a stranger's reminders early.
2. Teardown deletes the `Concert` row and lets existing cascades take the
   days, rounds, queue rows, outcomes and audits. It never deletes users,
   presets or subscriptions.

## Time: pull the queue forward (approved)

A PAYMENT anchor is normally weeks out, so the harness builds the rehearsal
concert with **realistic anchors and real reminder rules** — `sync_rule` and
the pure planner genuinely compute the fire times — and then rewrites the
unsent rehearsal queue rows' `fire_at_utc` into the past. The real tick picks
them up within a minute and sends real DMs. Everything downstream of planning
is untouched and real; only the waiting is removed.

Rejected: an injectable clock, because the scheduler tick calls with the real
clock, so the one component most worth proving would be the one component not
honouring the fake. Rejected: compressed anchors with real waiting, because it
cannot exercise a realistic offset like "3 days before" without the anchor
genuinely being three days out.

## Control surface: `/admin/rehearsal` (approved)

An admin-only web page, `require_admin`, htmx fragments, in the design
system. Chosen over Discord slash commands because it can show current
rehearsal state — which queue rows exist, what fires next — which is most of
the debugging value.

**English-only, not wrapped in `_()`,** following the `/me/test-dm`
precedent; otherwise a page only admins see costs ~30 msgids in three
languages, which `test_i18n_catalogues.py` enforces.

It shares an admin page shell with B's `/admin/deliveries`.

## The canonical scenario (approved)

One fixed shape, idempotent reset — "Start" tears down any previous rehearsal
and reseeds. Chosen over a menu of targeted scenarios (several times the code,
and it does not answer "does the whole flow hang together") and over pointing
the harness at an existing concert (unsafe on production by construction).

**Two legs**, both near-future, each carrying a rehearsal **VENUE** tag — a
real venue tag is subscribable, so the rollup would fan out to its followers;
a rehearsal one exercises `sync_concert_venue_tags` → `handle_newly_tagged`
for real instead.

**One rehearsal FRANCHISE or ARTIST tag**, followed only by the owner's two
accounts. This is a revision of the initial "attach no subscribable tags"
idea, for the better: the `new_event` DM cannot be tested without a tag
somebody follows, so a tag followed by exactly two known recipients tests the
real fan-out safely rather than skipping it.

**Three rounds**, each earning its place:

- **R1 · `LOTTERY_ROUND`, `applies_to` = both legs**, all four anchors set.
  Yields the whole ladder from one round, and because it spans two legs,
  recording WON exercises the `RoundOutcomeDay` materialization (implicit rows
  become explicit on the first per-day write, invariant 2).
- **R2 · `FCFS_SALE`, `applies_to` = Day 1**, opens + closes. Proves
  suppression: once R1 is WON on Day 1, `_apply_outcome_suppression`'s
  "secured elsewhere" pass should silently delete R2's reminders. A round that
  *stops* arriving is the hardest thing to notice, so it is deliberately
  watched.
- **R3 · `UPGRADE`, `qualifiers` = [R1]**, opens + closes. Invisible and
  `upgrade_locked` before the viewer holds a ticket, live after WON — the
  eligibility gate proven end to end.

**EVENT_START** comes from a concert-scoped rule, one reminder per leg.

### The prescribed walk

Button gating makes order load-bearing.

| # | Action | Expected DM | Buttons |
| --- | --- | --- | --- |
| 1 | Start | `new_event` + preset auto-applied | apply / remove / deadlines |
| 2 | Next | R1 OPENS | snooze |
| 3 | Next | R1 CLOSES → press **Applied** | applied / notapplied / remindlater |
| 4 | Next | R1 RESULTS → press **Won** | won / lost, then per-leg split |
| 5 | *(observe)* | R3 becomes eligible; R2 goes quiet | — |
| 6 | Next | R1 PAYMENT → press **Paid** | paid |
| 7 | Next | Day 1 EVENT_START | snooze |
| 8 | Cancel Day 2 | `leg_cancelled` → press **Reinstate** | reinstate |
| 9 | End | concert deleted, cascades take the rest | — |

Step 3 must precede 4 and 4 must precede 6: PAYMENT only offers Paid from
WON, so pressing Lost at step 4 ends the ladder.

Step 8 must call `notify_newly_cancelled_legs` **before** `sync_concert`,
which deletes the queue rows that function inspects.

**The page names the buttons it expects** on the row it just pulled. Without
that the harness is a trigger; with it, it is an oracle — it distinguishes "no
button rendered" from "wrong button rendered".

## New-user flow: a standing second Discord account (approved)

The signed-out landing → OAuth → `/welcome` → `/setup` half is tested by
logging in as a **second Discord account** from an incognito window, not by a
reset button.

It is the only option that exercises the new-account branches at all:
`auth.py`'s `is_new_user` detection, seeding `users.language` from the `lang`
cookie *at creation only*, the `/welcome` step counter, and the handoff into
`/setup`. A reset button cannot — the row already exists, so those branches
never run — and it would destroy real production data every time. `/setup` is
already re-runnable from Preferences, so a reset's only unique value would be
re-walking `/welcome`.

The second account also follows the rehearsal tag, so a DM can be watched
landing on an account that is not the operator's.

## Open questions

1. Should `Tag.rehearsal` exist too, hiding the rehearsal tags from `/tags`?
   Leaning no — two or three obviously-named rows on an editor-facing page is
   cosmetic, and it would widen the schema change from one column to two.
2. Should the shape catalogue send all eight shapes at once, or one at a time
   from a picker? Leaning one at a time, so a specific embed can be re-checked
   after a copy change without eight DMs.

Both are cheap to settle when this is unparked; neither changes the safety
model or the scenario.
