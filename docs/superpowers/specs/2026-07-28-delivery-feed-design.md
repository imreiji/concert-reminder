# Delivery feed: a durable delivery log and a per-tick admin digest

Date: 2026-07-28. Status: **designed, not implemented**. Branch
`delivery-feed`, off `main` (`19c5d81`). Designed with the owner in one
brainstorming session; every decision below was his call or was approved
section by section.

**Sub-project B of three.** The owner asked for two things in one session:
a way to rehearse the whole user flow on production including every Discord
DM (**A**, the rehearsal harness), and an admin feed of what the scheduler
actually delivered so a production incident is caught fast (**B**, this
spec), which in turn led to a targeted admin broadcast so an incident can be
*remedied* (**C**). They are independent subsystems sharing only the admin
page shell and the notifications outbox, so they get three specs. The owner
sequenced **B and C before A**, on the reasoning that an undetected bad
delivery on production costs more than a missing test harness.

A's approved design is recorded separately in
`2026-07-28-rehearsal-harness-design.md` so it is not lost while parked.
C depends on this spec's log and will be designed on top of it.

## Why

The scheduler delivers DMs on a 60-second tick and, once delivered, keeps
almost no record of it. `reminder_queue.sent_at_utc` is the only trace, and
it is not durable: `sync_rule` deletes rows it no longer plans, and deleting
a round cascades its queue rows away. So the evidence of what was sent
disappears exactly when a bad concert edit is the thing being investigated.

The owner's incident class is specific and worth quoting: not only "a
reminder failed" but **"messages have been sent to the wrong users"**. That
shapes two decisions below — logging both drains rather than reminders
alone, and grouping the digest by recipient *count*, which is what makes a
wrong fan-out visible at a glance.

## Scope

In: a durable delivery log, a per-tick digest DM to admins, an admin page to
read the log, a `/privacy` disclosure, a retention prune.

Out: the rehearsal harness (A). The broadcast (C) — this spec stops at
handing C a resolved recipient set. Nothing about the reminder planner,
`sync_rule`, or the queue changes; this feature only *observes*.

## A. The log: `reminder_deliveries`

One row per attempted delivery, written by the scheduler after the delivery
bookkeeping commit.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | int PK | |
| `batch_at_utc` | `UTCDateTime`, indexed | the tick's `now`; the batch identity |
| `user_id` | FK `users.discord_id` **CASCADE** | |
| `outcome` | `Enum(DeliveryOutcome)` | SUCCESS / FORBIDDEN / TRANSIENT_FAILURE — see the move below |
| `source` | `Enum(DeliverySource)` | REMINDER or NOTIFICATION — see §B |
| `anchor` | `Enum(Anchor)`, nullable | null for notification rows |
| `note_kind` | String, nullable | the `Notification.kind` for notification rows |
| `concert_title` | String, nullable | **denormalized** |
| `leg_label` | String, nullable | **denormalized** |
| `round_label` | String, nullable | **denormalized** |
| `concert_id` | FK `concerts.id` **SET NULL**, nullable | convenience pointer |
| `round_id` | FK `rounds.id` **SET NULL**, nullable | convenience pointer |
| `day_id` | FK `concert_days.id` **SET NULL**, nullable | convenience pointer |
| `sent_at_utc` | `UTCDateTime` | |

### `DeliveryOutcome` must move first

`DeliveryOutcome` is currently defined in `scheduler/loop.py` (a plain `Enum`
with values `"success"` / `"forbidden"` / `"transient_failure"`). A
`db/models.py` column typed on it there would make `db/` import from
`scheduler/`, inverting the layering — `scheduler/` calls `db/service.py`, not
the reverse, and `db/service.py` already imports `app.ops` function-locally
to avoid precisely this.

So the first task is a pure move: `DeliveryOutcome` goes to
`domain/types.py` as a `StrEnum`, alongside every other enum in the app, and
`scheduler/loop.py` imports it from there. The member values are already the
right strings, so the move preserves behaviour exactly and there is no
existing persisted data to migrate — the enum has never been stored.

`DeliverySource` is new and goes in `domain/types.py` from the start. Both
columns take the house `Enum(E, values_callable=lambda e: [m.value for m in
e])` treatment so `.value` strings are what land in SQLite, and the migration
gets the usual hand-edit (`sa.DateTime()` for `UTCDateTime`, drop the
`import app.db.models` line).

Three further choices carry weight:

**`user_id` CASCADE is non-negotiable.** This table holds personal data —
which events a named person was reminded about. `delete_user` is a single
`session.delete` relying on cascades (invariant 5), so a non-CASCADE FK here
would make `POST /me/delete` quietly stop being true. The DB fixture rule
applies with force: without the `PRAGMA foreign_keys=ON` connect listener the
cascade silently does not fire and an erasure test passes while leaking.

**The labels are denormalized text, and the FKs are SET NULL.** The point of
the log is to survive the catalogue changing under it. Deleting a concert
must not erase the record that nine people were DMed about it — that is the
investigation. The FKs exist only so the admin view can link through when the
row still exists, following the `Concert.created_by` SET NULL precedent
(erasure keeps the shared record, anonymizes the pointer).

**No `delivery_batches` table.** `batch_at_utc` is the batch key and is
human-readable in the digest ("batch 14:23 UTC"). Aggregates — sent, users,
failed — compute on read from this one table, so there are no stored counts
to drift out of agreement with the rows they summarize.

**Retention: 30 days**, pruned inside the scheduler's existing
`HEALTH_EVERY_N_TICKS` block rather than a new cron job. 30 to match
`deploy/backup.sh`'s S3 lifecycle, so the system has one retention number
rather than two.

## B. The digest

### Both drains, not just reminders

The first draft of this design logged reminders only, because reminders were
what the owner asked about and because a log that included notification
deliveries risks reporting its own digest and DMing forever.

That draft missed the stated incident. The most likely way this app sends
messages to the wrong users is not a reminder at all — it is
`handle_newly_tagged` fanning a `new_event` notice across a tag's followers,
which is a `Notification`, and which the 2026-07-28 cleanup batch has already
had to correct once (the venue rollup announcing a dead concert). A
reminders-only feed would be blind to it.

So both drains are logged, distinguished by `source`, and the feedback loop
is closed by an explicit exclusion set instead:

```
UNREPORTED_NOTE_KINDS = {"delivery_digest", "admin_broadcast"}
```

A notification of an excluded kind is delivered normally and simply not
logged, so a digest can never report its own delivery, and C's broadcast
cannot start a cascade either. The exclusion is by `kind`, not by recipient,
so it holds however many admins exist.

### Placement in `tick()`

After the existing delivery-bookkeeping commit, in its own `try/except` with
its own commit. This is the shape and the reason `evaluate_and_alert` already
uses: the DMs are on the wire, so a failure while recording them must never
roll back `sent_at_utc` and cause a double-send. Ordering within the tick:

1. increment `_tick_count`
2. drain `due_reminders` → send → mark sent / record dm outcome
3. drain `due_notifications` → send
4. **commit delivery bookkeeping** (unchanged)
5. **NEW:** write `reminder_deliveries` rows; if any, queue the digest —
   own try/except, own commit
6. every 5th tick: `evaluate_and_alert`, plus the retention prune — own
   try/except, own commit (unchanged but for the prune)

### Queueing

One `Notification(kind="delivery_digest", concert_id=None)` per
`settings.admin_ids`, exactly as `evaluate_and_alert` does. `concert_id=None`
falls through `scheduler.loop._notification_context` to the plain-text path,
so **the send code needs no changes at all**.

Suppressed when `not settings.bot_enabled`, for `evaluate_and_alert`'s reason:
without it every local dev run accumulates junk notifications. A tick that
delivered nothing queues nothing, so a quiet app stays quiet.

### Body

Composed at queue time, before any recipient is known. Failures first,
grouped by (concert, leg, round, anchor) or (concert, note kind), with a
recipient count per group:

```
⚠ 2 failed / 12 sent · 9 users · batch 14:23 UTC

FAILED
  transient · CLOSES · Snow Miku 2027 / Day 2 / 一次先行
  forbidden · RESULTS · Snow Miku 2027 / Day 1 / 二次先行

SENT
  CLOSES  ×5  Snow Miku 2027 / Day 1 / 一次先行
  RESULTS ×3  Snow Miku 2027 / Day 1 / 二次先行
  new_event ×4  Wonder Live Vol.3
  +2 more groups
```

Grouped rather than per-recipient for three reasons: it keeps the DM
impersonal (names live in the app, §C), it fits Discord's 2000-character
ceiling that a 100-reminder tick would otherwise blow, and **the count is the
anomaly detector** — a group reading `×40` when the app has three users is
the tell, and a per-recipient list would bury it.

Group list capped at 10, remainder as `+N more groups`. Failures are never
truncated away: they are listed before the sent groups and, if the cap would
cut them, the sent list shrinks instead.

**English-only, not wrapped in `_()`.** Partly the `/me/test-dm` precedent
(`HTMLResponse("Test DM sent!")` is unwrapped), partly forced: the body is
composed before a recipient is known, so translating it would mean
`gettext_in` per admin for operational copy only admins read. Keeping it out
of the catalogues also keeps `test_i18n_catalogues.py` honest rather than
padded with strings no user sees.

## C. The admin view: `/admin/deliveries`

`require_admin` (a signed-in non-admin gets 403, invariant 5), English-only,
three screens:

1. **Recent failures** — every non-SUCCESS row in the retention window,
   newest first, independent of batch. The digest says something broke in the
   last minute; this says whether it has been breaking all week. This is the
   screen to open first during an incident.
2. **Batch list** — newest first, capped rather than paginated (the window is
   30 days and a tick only appears if it delivered): `batch_at_utc`, sent,
   users, failed.
3. **Batch detail** — the same groups the digest showed, each expanding to its
   **actual recipients**.

Screen 3 is the deliberate answer to the owner's two coupled answers —
"counts only" in the DM, but "a way to identify which users" for a targeted
remedy. Identity is revealed only here: behind admin auth, inside the app's
own deletion story, on a 30-day window. The alternative, naming users in the
DM, would build a permanent record of who follows which artists in a place
`POST /me/delete` cannot reach.

Batch detail and each group carry a **"Message these recipients"** action —
the handoff to C. It passes a resolved recipient set rather than a query for
C to re-derive, so the people messaged are provably the people who received
the bad DM.

## Error handling

- The digest and log writes never affect delivery bookkeeping (own commit,
  after §B step 4).
- A failure to build or queue the digest is logged and swallowed; a dead
  scheduler loop is the one unacceptable outcome, matching `reminder_loop`'s
  existing posture.
- The prune is in the every-5th-tick block's try/except; a failed prune
  retries in five minutes and, worst case, the table grows.
- Log-row writing must not raise on a missing label: every denormalized field
  is nullable and populated best-effort from what `DueReminder` already
  carries.

## Testing

- Log rows written for both sources, with the right `outcome` per
  `DeliveryOutcome` branch.
- **A `delivery_digest` notification's own delivery produces no log row** —
  the feedback-loop guard, asserted directly rather than inferred.
- `delete_user` removes that user's delivery rows (with the FK pragma
  registered).
- Deleting a concert leaves its delivery rows intact with `concert_id` NULL
  and `concert_title` still populated — the durability claim.
- No digest queued on an empty tick; none queued when `bot_enabled` is false.
- Digest body: failures ordered first, group cap applied, failures never
  truncated, count per group correct.
- Prune deletes beyond 30 days and spares rows inside it.
- `/admin/deliveries` renders for an admin (the every-page logged-in GET rule)
  and 403s for a signed-in non-admin.
- Retention prune and digest are exercised through the service layer with a
  fake bot, never by importing discord in service tests.

## Obligations this creates

1. **`/privacy` needs a line.** The app now stores, per user, which events
   they were reminded about, on a 30-day window. That is more sensitive than
   anything the page currently describes, and the page already exists.
2. **`WISHLIST.md`** gets the two deferred siblings appended: A (rehearsal
   harness, spec written, parked) and C (admin broadcast, designed next), with
   dates and the sequencing reason.
3. **`CLAUDE.md`** gains the feedback-loop rule next to invariant 4 — a new
   notification kind that reports on deliveries must be added to
   `UNREPORTED_NOTE_KINDS` or it will report itself every 60 seconds.

## Deviations from this spec

(To be filled during implementation.)
