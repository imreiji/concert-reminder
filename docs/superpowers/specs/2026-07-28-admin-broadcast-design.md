# Admin broadcast: a targeted, recallable message to real users' DMs

Date: 2026-07-28. Status: **designed, not implemented.** Branch
`admin-broadcast`, off `main` (`2d05078`). Designed with the owner; every
decision below was his call or was approved section by section.

**Sub-project C of three**, and the last. **B**
(`2026-07-28-delivery-feed-design.md`) shipped in PR #106 and is deployed; it
is what makes this addressable — "the recipients of that batch" is a set only
because `delivery_log` exists. **A** (`2026-07-28-rehearsal-harness-design.md`)
is specced and unbuilt.

## The honest framing

This is the most dangerous route in the application. Everything else here
either reads, or writes rows only the owner sees; this one puts text into other
people's Discord DMs, at a scale the sender chooses, with no recall once it is
on the wire.

It was proposed, questioned, and the owner reaffirmed it with a reason that
answers the objection: **detection without remedy is half a tool.** B tells you
that forty people received a wrong DM; without this, the only thing you can do
about it is nothing. The incident class he named — "messages have been sent to
the wrong users" — is not complete until there is a way to say so to the people
it happened to.

So the design question is not *whether* the route exists. It is what makes it
survivable, and that is the four rails in §3.

## Scope

In: three recipient modes, a compose → preview → send flow, a guaranteed undo
window, a permanent audit record, and the `/admin/deliveries` handoff B
promised.

Out: recipient modes that are *derived* rather than resolved (see §2). Rich
embeds. Scheduled/recurring broadcasts. Any notion of a reply.

## 0. A correction to B, which this spec carries

`UNREPORTED_NOTE_KINDS` currently reads
`frozenset({"delivery_digest", "admin_broadcast"})`. The second entry was added
defensively while planning B, before this feature existed, and **it is wrong.**

The feedback loop it guards against is specific: the digest reports on
deliveries, so logging the digest's own delivery makes the next digest report
it, forever, once a minute. A broadcast has no such property — it terminates
after one hop:

```
broadcast delivered -> logged -> one digest line -> digest delivered -> NOT logged -> stop
```

And logging broadcasts is not merely harmless, it is the point: **whether the
remedy reached the people you sent it to** — including which of them are
`FORBIDDEN` — is the question you send it asking. Task 1 removes
`"admin_broadcast"` from the set, keeping `"delivery_digest"`.

## 1. Data model

**One new table, `broadcasts`** — the permanent audit record:

| Column | Notes |
| --- | --- |
| `id` | PK |
| `created_by` | FK `users.discord_id` **SET NULL** — deleting an admin's account keeps the record of what they did |
| `created_at_utc` | `UTCDateTime` |
| `mode` | `Enum(BroadcastMode)`: BATCH / ALL / EXPLICIT |
| `mode_param` | Text: the batch timestamp, the raw id list, or NULL for ALL |
| `body` | Text, as typed |
| `recipient_count` | int — what was actually queued, not what was previewed |
| `send_after_utc` | `UTCDateTime` — when the hold expires |
| `cancelled_at_utc` | `UTCDateTime`, nullable |

**Never pruned.** It deliberately outlives `delivery_log`'s 30-day window,
because it records an *admin action against other people's DMs*, not a
delivery. "Did we already tell them?" must stay answerable indefinitely.

**Two nullable columns on `Notification`:**

- `send_after_utc` (`UTCDateTime`, nullable) — `due_notifications` gains
  `(send_after_utc IS NULL OR send_after_utc <= now)`.
- `broadcast_id` (FK `broadcasts.id`, **SET NULL**, nullable) — what Cancel
  deletes by. Without it, cancelling would have to guess by kind and timestamp.

**Both nullable, and NULL means exactly today's behaviour.** `new_event`,
`leg_cancelled`, `ops_alert` and `delivery_digest` are untouched by
construction — which is the single most important property of this change,
since it modifies the drain query every notice in the app passes through.

`HOLD_SECONDS = 120`. Long enough to reread what you sent and see the mistake;
short enough that a real remedy is not uselessly delayed.

## 2. Recipient modes

Three, all **resolved** sets:

- **BATCH** — `SELECT DISTINCT user_id FROM delivery_log WHERE batch_at_utc = ?`.
  The remedy case, and the reason B's log exists.
- **ALL** — every `users` row.
- **EXPLICIT** — parsed Discord ids, validated against `users`. Unknown ids are
  **reported in the preview, never silently dropped**: quietly discarding a
  mistyped id is how you conclude you messaged someone you did not.

**Two modes were considered and rejected**: everyone tracking a concert, and
followers of a tag. Both are *derived* — the set can change between the preview
you approved and the send that executes, so the count you confirmed was a lie.
Every surviving mode is resolved, so that class of bug does not exist here at
all. (A tag-followers mode is also the one most likely to be a mass-send while
feeling targeted, since a popular franchise tag may be most of the userbase.)

**Send re-resolves from mode + param; it does not trust a snapshot posted back
in the form.** Tampering is not the threat — only admins reach the route, and
EXPLICIT already accepts arbitrary ids, so a forged field grants nothing new.
Drift is. Re-resolving means `recipient_count` records what was queued. If it
differs from the previewed count, the status page shows both.

## 3. The four rails

The owner asked for all four.

1. **Preview before anything is queued.** Compose → preview showing the exact
   recipient count, the resolved mode, any unmatched ids, and the body as
   recipients will see it. Nothing is written to the outbox until confirmed
   from that screen.
2. **Typed confirmation above 10 recipients.** You type the count to proceed.
   Keyed on SIZE, not mode, so a 400-person explicit list is gated exactly like
   ALL. Same shape as the existing heavy confirmations on account deletion and
   on unfollowing a won ticket.
3. **A guaranteed undo window.** The only rail that helps *after* you press
   send, which is when mistakes are discovered. See §1 and §5.
4. **A permanent audit record.** §1.

**Plus one the audit table makes nearly free:** the compose page warns if an
identical body was sent within the last hour — the stale-tab resubmit, and the
"did I already send this?" question, answered by a query that already exists.

## 4. Flow

Five routes, all `require_admin`, all **English-only and not wrapped in
`_()`**, following the `/me/test-dm` and `/admin/deliveries` precedent.

| Route | Does |
| --- | --- |
| `GET /admin/broadcast` | compose form + audit list |
| `POST /admin/broadcast/preview` | resolves recipients, renders preview, **writes nothing** |
| `POST /admin/broadcast/send` | writes the `broadcasts` row + N held notifications → 303 |
| `GET /admin/broadcast/{id}` | status: held with countdown + Cancel, sent, or cancelled |
| `POST /admin/broadcast/{id}/cancel` | deletes unsent rows, stamps `cancelled_at_utc` → 303 |

`/admin/deliveries` batch detail gains a **"Message these recipients"** action
prefilling BATCH mode with that timestamp — the handoff B's spec promised.

No CSRF token, consistent with the app's existing `SameSite=Lax` decision.
This does not warrant reopening it.

**Everything goes through the notifications outbox — invariant 4.** There is no
direct send anywhere in this feature, and the `POST /me/test-dm` carve-out is
not extended.

## 5. The message

**Plain text, no embed.** It routes through `_notification_context`'s existing
`concert_id=None` path exactly as `ops_alert` does, so **no send code changes at
all**. A URL pasted into plain text still renders as a link in Discord, so
nothing is lost.

**Typed body, localized frame.** The admin writes one message in one language.
Each recipient receives it under a title resolved in *their* language via
`gettext_in(user.language)` — the explicit-locale escape hatch `NoticeContext`
already uses for text composed before a recipient is known:

- en: `From dekimasen.app`
- ja: `dekimasen.app より`
- zh: `来自 dekimasen.app`

The brand is never translated, exactly as the language names EN/中文/日本語 are
not. Two msgids total.

Rejected: requiring three bodies under the all-three-or-none rule. It is the
most consistent option and the only one where a Japanese reader gets a Japanese
sentence — but an incident remedy is written under time pressure, and a rule
that blocks sending until three translations exist is a rule that will be
fought. The localized frame means a Japanese reader still knows instantly what
the DM is and that it is not spam, which is most of the value for two msgids.

## 6. Hazards

**Throughput.** `due_notifications` drains 100/tick ordered by `created_at`, so
an ALL broadcast occupies the queue at ~6000/hour. **Reminders are unaffected**
— they drain from `reminder_queue`, a separate table — so no deadline is ever
delayed by a broadcast. What queues behind it is other *notifications*:
`new_event`, `leg_cancelled`, `ops_alert`. At the current scale that is
seconds. If it ever matters, the fix is capping broadcast rows to a share of
each tick's 100; noted rather than built.

**The cancel race is real and is reported honestly.** A tick can drain rows
between the click and the delete. Cancel removes only unsent rows, so the status
page reads *"cancelled — 12 of 40 had already been delivered"* rather than
claiming a clean stop. Cancelling after full delivery says that too. A rail that
lies about what it undid is worse than no rail.

**A `FORBIDDEN` recipient is signal, not an error.** The outbox marks it sent
and sets `dm_blocked_since`; with §0's correction, `delivery_log` records it. So
"did the remedy land?" is answerable per recipient on `/admin/deliveries`.

**`bot_enabled` false** queues rows that never send, exactly like every other
notification. The compose page says so rather than pretending.

## 7. Testing

- **Existing notifications with `send_after_utc = NULL` still drain
  immediately.** The most important regression here, since this touches the
  drain query every notice in the app goes through.
- Held rows are not drained before `send_after_utc`, and are after.
- Preview writes nothing — no `broadcasts` row, no `Notification`.
- Send queues exactly N held rows plus one `broadcasts` row with a matching
  `recipient_count`.
- Cancel deletes unsent rows only; already-delivered ones are counted and
  reported, not silently ignored.
- Cancel after full delivery reports zero cancellable rather than erroring.
- Unknown EXPLICIT ids appear in the preview.
- Typed confirmation is required above 10 recipients and not below.
- Broadcast deliveries **are** logged and reach the digest, with a two-tick test
  proving it terminates — the same shape B used for the digest's own delivery.
- The `broadcasts` row survives deletion of the admin who sent it.
- `require_admin` on all five routes: 403 signed-in non-admin, 303 signed-out.
- Discord is never imported in service tests; delivery is exercised with a fake
  bot.

## 8. Obligations

1. **`/privacy`** — no new data category about *users* is stored (the body and
   the recipient count are admin-authored), so no change is required. Confirm
   this during implementation rather than assuming it.
2. **`CLAUDE.md`** — invariant 4 gains a line: a broadcast is queued held via
   `send_after_utc`, and cancelling deletes unsent rows only; nothing may send
   admin-authored text outside the outbox.
3. **`WISHLIST.md`** — move C's Proposed entry to Shipped, then the full
   revision pass. Note there that this closes the arc B opened, and that A
   remains the only unbuilt piece.

## 9. Open question

Should `HOLD_SECONDS` be configurable rather than a constant? A 2-minute hold
on a genuine outage announcement is 2 minutes of users not knowing. Leaning no
— a constant is one fewer thing to get wrong at 3am, and 120s is small against
any incident that warrants a broadcast — but it is worth a sentence of the
owner's opinion before it is built.

## Deviations from this spec

(To be filled during implementation.)
