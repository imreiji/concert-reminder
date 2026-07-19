# Concert subscriptions and per-leg opt-out

Date: 2026-07-19

Branch 4 of the UI/UX refactor. It supplies the model that three shipped/queued surfaces are
currently faking or missing: Home's "no way to prune", the concert page's Following toggle, the
per-leg opt-out, and the onboarding capture flow (branch 6). Preferences is redesigned here too,
because its Following section is meaningless without this model.

## Problem

Since branch 1, "tracked" means *matches a tag the user follows*, derived at query time. There is
**no way to prune a single concert off your Home** — following Liella! puts every Liella! concert
on your board whether or not you are going. The demo's Following toggle, the per-leg
"Not going to this day", and the capture flow's pruning pass are all inert placeholders waiting on
a real per-concert, per-user state.

## Approach: an override table, not a materialised record

The obvious design — one row per (user, concert), auto-created when a matching concert appears — is
write amplification (a row per user per matching concert) and needs a **data migration to backfill
every existing user**, or every board goes empty on deploy.

Instead the row is an **override on the tag-derived default**:

| State | Meaning |
|---|---|
| **No row** | Follow the tag-derived default — exactly today's behaviour. |
| **`subscribed` row** | Explicit opt-in. Puts a concert on your board even with no matching tag — this is the one-click "subscribe to this event" button. |
| **`opted_out` row** | Explicit prune. Removes a concert even though a tag matches. |

Rows exist **only when a user acts**. No amplification, **no backfill**, and every existing board
keeps working through the same derivation it uses now. This mirrors invariant 3's spirit — the
default is materialised lazily and explicit user edits are what persist.

**Per-leg opt-out** is the same shape one level down: a leg defaults to following its concert, so
only an opt-out row exists, keyed (user, concert_day). A leg opt-out suppresses that leg's rounds
for that user, exactly as a fully-cancelled leg does globally — reusing the `applies_to` /
`_apply_outcome_suppression` machinery, scoped per user.

## The "tracked" predicate becomes

A concert is tracked for a user when:

```
(a matching tag is followed AND no opted_out row exists)  OR  a subscribed row exists
```

`tracked_concert_ids` (added in branch 1) is the single place this is computed. Every board and
deadline query already routes through it, so this is a one-function change plus the new table.

## Decisions settled with the owner

1. **A prune sticks across unfollow/re-follow of the tag.** The `opted_out` row survives, so
   re-following a tag does not resurrect a concert you deliberately removed — consistent with
   "removed members stay removed" (invariant 3). Because that state is deliberately invisible,
   **Preferences shows a count**: "18 concerts · 2 you pruned", with a way to review and restore.

2. **An outcome does not lock a subscription.** A user who applied, won, or even paid can still opt
   out — people genuinely decide not to go. But because this can forfeit a ticket, opting out of a
   concert where you hold a `WON`/`PAID` outcome requires a **heavy, specific confirmation** naming
   the concrete loss ("You won this ticket. Payment is due Tue 22 Jul. Opting out stops that
   reminder and forfeits the ticket."), not a generic "are you sure". This is the one place the
   confirmation weight scales with the stakes.

3. **Opting out never deletes an outcome or a reminder-queue row's history** — it suppresses future
   informational reminders. The record of what you did stays; you just stop being chased.

## Scope

### Model

- `ConcertSubscription(user_id, concert_id, state)` where `state` is `subscribed | opted_out`,
  unique on (user_id, concert_id). New table — plain `CREATE TABLE`, **no backfill**.
- `LegOptOut(user_id, concert_day_id)` — presence means opted out. New table.
- Both cascade on user delete (join the erasure story from the privacy branch) and on
  concert/day delete.

These are the only schema changes. Follow CLAUDE.md's migration rules: NAMING_CONVENTION, batch
mode, `sa.DateTime()` not `UTCDateTime()`, ASCII config, and the legacy-anonymous-constraint lesson
now recorded in the migrations section.

### Service

- `tracked_concert_ids` gains the override logic above — the single tracked predicate.
- `set_concert_subscription(user_id, concert_id, state | clear)` and
  `set_leg_opt_out(user_id, day_id, bool)` — the only write paths.
- Per-user leg suppression folds into the existing `_apply_outcome_suppression` pass so the reminder
  planner stays ignorant of it, exactly as it already is for cancellation and outcomes (invariant 2).

### Surfaces (make the inert things real)

- **Concert page:** the Following toggle writes a subscription override; per-leg "Not going to this
  day" writes a leg opt-out. Both replace the demo's placeholders.
- **Home:** the follow-up dialog's second press ("Skip this concert entirely") becomes a real
  concert opt-out instead of a link to the concert page.
- **Discover:** a one-click subscribe on a card writes a `subscribed` row.
- **Preferences — Following section (the redesign lands here):** per-tag notify / auto-apply
  toggles, the tracked/pruned counts, and a review-and-restore list for pruned concerts. The rest of
  the Preferences redesign from the demo (Reminders / Time / Delivery / Account with the delete-account
  and setup-rerun buttons / Editors) lands in this branch too, since the left-rail structure is
  built once.

### Reminder correctness

- A concert opt-out suppresses that concert's reminders for that user; a leg opt-out suppresses only
  that leg's rounds.
- **An opt-out must never suppress a round the user has standing in beyond the informational
  reminder** — it stops the chase, it does not silently drop a payment the user might still make
  after reconsidering. The heavy confirmation is what guards the forfeit; the suppression itself is
  reversible by clearing the override.

## Out of scope

- Upgrade rounds (branch 5). The subscription model must not assume they exist.
- The onboarding capture flow (branch 6) consumes this model but is its own branch — expose a clean
  `set_concert_subscription` / `set_leg_opt_out` API it can call, and stop there.
- Materialising per-user rows eagerly. Deliberately rejected above.

## Constraints

- Two new tables; **no backfill migration**. If the plan finds itself needing to populate rows for
  existing users, that is the wrong design — stop and reconsider.
- No second `RoundOutcome` write path (invariant 2); suppression folds into the existing pass.
- `tracked_concert_ids` stays the single tracked predicate — do not add a second definition of
  "tracked".
- Invariant 3 unchanged: subscription overrides are the concert-level analogue of member pruning,
  not a replacement for it.
- Invariant 7: no user text in inline `on*` handlers; `data-name` collides with `filterChips()`.
- The heavy opt-out confirmation must name the specific loss, not be generic.
- Times dual, JST first. Sentence case. Every page a logged-in GET render test. DB fixtures register
  `PRAGMA foreign_keys=ON` — the cascade behaviour here is load-bearing.
- Baseline: 638 passing + 1 known-failing local `test_test_dm_when_bot_disabled` (out of scope).

## Testing

- No row → tracked iff a followed tag matches (unchanged behaviour, pinned).
- `opted_out` row → not tracked even with a matching tag.
- `subscribed` row → tracked even with no matching tag.
- Unfollow then re-follow a tag → an `opted_out` concert stays pruned; Preferences count reflects it.
- A leg opt-out suppresses that leg's rounds and leaves the other leg's rounds alone.
- Opting out of a `WON` concert requires the heavy confirmation path and, once confirmed, stops the
  payment reminder while leaving the `RoundOutcome` intact.
- Deleting a user cascades both new tables (erasure story).
- `tracked_concert_ids` is still computed in exactly one place.

## Verification

Drive it: follow a tag, confirm its concerts populate Home; prune one from the concert page, confirm
it leaves Home; unfollow and re-follow the tag, confirm the pruned one stays gone and Preferences
says "1 pruned"; opt out of a leg on a two-day concert, confirm only that leg's rounds stop
reminding; try to opt out of a concert you have won and confirm the heavy confirmation fires.
