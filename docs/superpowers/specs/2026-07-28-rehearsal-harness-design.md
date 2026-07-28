# Rehearsal harness: walk the whole user flow, including every Discord DM

Date: 2026-07-28. Status: **designed, not implemented.** Revised the same day
it was written — the first draft targeted production, and that was the wrong
call. See "Why local, not production".

**Sub-project A of three.** The owner asked for a way to walk the entire user
flow including every kind of Discord reminder DM. The same session produced
**B** (`2026-07-28-delivery-feed-design.md`) and **C** (admin broadcast, not
yet specced), and the owner sequenced B and C first, on the reasoning that an
undetected bad delivery on production costs more than a missing test harness.

The dependency runs the other way round, though, and it is worth stating
plainly: **C is a mass-DM route, and this harness is how C gets rehearsed
without DMing real people.** Building C before this means its first real
exercise is on live users.

## Why local, not production

The original ask was "test the flow on prod", and the first draft of this spec
took that literally. It designed a `Concert.rehearsal` column, three global
query filters and rehearsal-only tags — an apparatus whose entire purpose was
to make a fake concert harmless inside a shared production catalogue.

Almost none of it is necessary, because the premise was wrong. There is no rule
that production is the only environment; there is only a **gap** in the three
tiers that already exist:

| Tier | What it proves | Status |
| --- | --- | --- |
| Test suite | logic, planner, suppression, gating, every page renders | 1480 tests, exists |
| Web-only dev mode | the real app in a real browser, real DB, real htmx | exists (empty `DISCORD_TOKEN`) |
| **Real Discord DMs** | embeds, buttons, the 60s tick, delivery | **the gap** |

Tier 3 was missing only because there is one Discord bot. A **second Discord
application** closes it for free. Run the harness locally against a dev bot and
the safety problem evaporates: no shared catalogue to pollute, no other user who
can see the database, nothing to hide from `/discover` because nobody else is
looking.

So the `rehearsal` column, the `include_rehearsal` parameter, the query filters
and the rehearsal-only tag convention are all **dropped**. One config flag
replaces them.

### What still genuinely needs production

A local run cannot prove the *deployment* works — real accumulated DB rows, the
real interpreter, Caddy, Cloudflare, systemd. That residue is small, and it is
covered by a short manual smoke checklist at the foot of this spec rather than
by a harness. `POST /me/test-dm` already answers the single most important
production-only question: can prod DM you at all.

## Environment setup (manual, one-time)

Owner-side work, all free, roughly fifteen minutes:

1. **A second Discord application** — Developer Portal → New Application →
   Bot → Reset Token. This is the dev bot; it never shares prod's token.
2. **A private test server**, with the dev bot invited via OAuth2 URL Generator
   using the `bot` scope.
3. **The server id** for `DEV_GUILD_ID` — enable Developer Mode in Discord's
   settings, right-click the server, Copy Server ID. With it set, slash
   commands sync in seconds instead of up to an hour.
4. **An OAuth2 redirect URI of `http://localhost:8000/auth/callback`** on that
   application. This is the step that bites: without it, signing in locally
   fails with a Discord-side error the app never sees and cannot explain. Give
   the dev application its own `DISCORD_CLIENT_ID`/`SECRET` rather than reusing
   prod's, so local OAuth cannot touch production's app config.
5. **A second Discord account**, distinct from the dev bot, for the new-user
   flow, logged in from an incognito window.

Local `.env`:

```
DISCORD_TOKEN=<dev bot token>
DEV_GUILD_ID=<test server id>
DISCORD_CLIENT_ID=<dev app id>
DISCORD_SECRET=<dev app secret>
DATABASE_URL=sqlite+aiosqlite:///./dev.db
ADMIN_WHITELIST=<your discord id>
REHEARSAL_ENABLED=true
```

`session_secret`'s strength validator only fires when `base_url` is https, so a
local run over http needs no change there.

## Safety model: one config flag

The harness has real write power — it seeds concerts and rewrites
`fire_at_utc`. Shipping that code means the route exists in the repository, and
therefore on the server, and a "pull every reminder forward" button reachable
on production is exactly the accident worth designing out.

So: **a new `rehearsal_enabled: bool = False` setting, and `web/app.py`
registers the harness router only when it is true.** Production never sets it,
so on production the route does not exist at all — no auth surface, no
accidental button, nothing to get wrong. `require_admin` stays on the routes as
a second layer against a misconfigured deploy.

That replaces the whole prod-safety apparatus of the first draft: one boolean in
config instead of a schema column, three query filters and a tag convention. It
is also the shape `bot_enabled` already uses — a config property that switches
a subsystem off — so it is not a new idea here.

Two rules survive the rewrite unchanged, because they are about the harness
being well-built rather than about production:

1. The pull-forward action resolves its queue rows **through the rehearsal
   concert**, never by queue id from a form field. A `queue_id` parameter is the
   version of this feature that fires an arbitrary reminder early.
2. Teardown deletes the `Concert` row and lets existing cascades take the days,
   rounds, queue rows, outcomes and audits. It never deletes users, presets or
   subscriptions.

## The local database

Default: **a fresh `dev.db`**, migrated with `alembic upgrade head` and seeded
entirely by the harness. Nothing else is needed for the walk below.

Do **not** copy the production database wholesale to a laptop. Today it is
effectively the owner's own data, but the habit is the problem: `users`,
`web_sessions`, `round_outcomes`, `concert_subscriptions`, `reminder_rules` and
— once B ships — `reminder_deliveries` are all personal data, and a copy on a
laptop sits outside every deletion path the app promises.

If production realism is ever wanted, the clean version is a
**catalogue-only** copy — `concerts`, `concert_days`, `rounds`, `tags`,
memberships — which contains no personal data by construction. That is exactly
what WISHLIST #1 (the admin catalogue export, "never any user data") produces.
Worth recording on that entry: it doubles as the local-dev seeding path, which
raises its value beyond the backup/rebuild case it was filed for.

## Time: pull the queue forward

A PAYMENT anchor is normally weeks out, so the harness seeds the concert with
**realistic anchors and real reminder rules** — `sync_rule` and the pure planner
genuinely compute the fire times — then rewrites the unsent queue rows'
`fire_at_utc` into the past. The real tick picks them up within a minute and
sends real DMs. Everything downstream of planning is untouched and real; only
the waiting is removed.

Rejected: an injectable clock, because the scheduler tick calls with the real
clock, so the one component most worth proving would be the one not honouring
the fake. Rejected: compressed anchors with real waiting, because it cannot
exercise a realistic offset like "3 days before" without the anchor genuinely
being three days out.

## Control surface: `/admin/rehearsal`

An admin-only web page, htmx fragments, in the design system, registered only
under `rehearsal_enabled`. Chosen over Discord slash commands because it can
show current state — which queue rows exist, what fires next — which is most of
the debugging value.

**English-only, not wrapped in `_()`,** following the `/me/test-dm` precedent
(`HTMLResponse("Test DM sent!")` is unwrapped). A page that only ever renders on
a developer machine should not cost ~30 msgids in three languages, which
`test_i18n_catalogues.py` would otherwise enforce.

## Coverage target

**5 anchors** (OPENS, CLOSES, RESULTS, PAYMENT, EVENT_START), **3 outbox
notices** (`new_event`, `leg_cancelled`, `ops_alert`), **11 persistent buttons**
(`apply`, `remove`, `deadlines`, `snooze`, `reinstate`, `applied`, `notapplied`,
`won`, `lost`, `paid`, `remindlater`).

`ops_alert` is **shape-catalogue only**. Tripping it for real means backdating
`backup_marker_path` or faking low disk, and `domain/health.should_alert`
requires two consecutive agreeing observations while writing real
`OpsCheckState` rows — so even locally it is three ticks of setup to see one
embed the catalogue renders instantly.

## The canonical scenario

One fixed shape, idempotent reset — "Start" tears down any previous rehearsal
and reseeds. Chosen over a menu of targeted scenarios (several times the code,
and it does not answer "does the whole flow hang together") and over pointing
the harness at an existing concert (it can only exercise states that already
happen to exist).

**Two legs** (Day 1, Day 2), both near-future, each with a venue tag. Ordinary
tags now — there is no fan-out risk in a local database, so the first draft's
rehearsal-only tag convention is dropped. One franchise-or-artist tag is
followed by the operator's account and by the second test account, which is what
makes the `new_event` DM testable at all.

**Three rounds**, each earning its place:

- **R1 · `LOTTERY_ROUND`, `applies_to` = both legs**, all four anchors set.
  Yields the whole ladder from one round, and because it spans two legs,
  recording WON exercises the `RoundOutcomeDay` materialization (implicit rows
  become explicit on the first per-day write, invariant 2).
- **R2 · `FCFS_SALE`, `applies_to` = Day 1**, opens + closes. Proves
  suppression: once R1 is WON on Day 1, `_apply_outcome_suppression`'s "secured
  elsewhere" pass should silently delete R2's reminders. A round that *stops*
  arriving is the hardest thing to notice, so it is watched deliberately.
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

Step 3 must precede 4 and 4 must precede 6: PAYMENT only offers Paid from WON,
so pressing Lost at step 4 ends the ladder.

Step 8 must call `notify_newly_cancelled_legs` **before** `sync_concert`, which
deletes the queue rows that function inspects.

**The page names the buttons it expects** on the row it just pulled. Without
that the harness is a trigger; with it, an oracle — it distinguishes "no button
rendered" from "wrong button rendered".

## The shape catalogue

A second action, independent of the scenario: send each DM shape directly
through the existing builders in `bot/messages.py`, one at a time from a picker
rather than all eight at once, so a specific embed can be re-checked after a
copy change without eight DMs arriving.

This is the half that stays useful after every i18n change, since it exercises
`build_new_event_message`, `build_leg_cancelled_message` and
`build_reminder_message` under a chosen locale without needing any of the state
the pipeline half constructs.

## The new-user flow

The signed-out landing → OAuth → `/welcome` → `/setup` half is walked by logging
in as the **second Discord account** at `http://localhost:8000` from an incognito
window, not by a reset button.

It is the only approach that exercises the new-account branches at all:
`auth.py`'s `is_new_user` detection, seeding `users.language` from the `lang`
cookie *at creation only*, the `/welcome` step counter, and the handoff into
`/setup`. A reset button cannot — the row already exists, so those branches
never run. `/setup` is already re-runnable from Preferences, so a reset's only
unique value would be re-walking `/welcome`, and a fresh `dev.db` gives that for
nothing.

The second account also follows the seeded tag, so a DM can be watched landing
on an account that is not the operator's.

## Production smoke checklist

What the local harness cannot prove, kept deliberately short and manual — no
code, no route, nothing shipped:

1. `POST /me/test-dm` from Preferences → a DM arrives. Proves the prod token,
   the gateway connection and DM permissions.
2. `/healthz` returns `"ok":true` → the scheduler is ticking.
3. One real concert page and `/discover` render → prod's accumulated rows do not
   break a template, the failure class this project has shipped before.
4. `journalctl -u concert-reminder -f` shows no exceptions across one tick.

## Testing

- The harness router is **not registered** when `rehearsal_enabled` is false —
  asserted directly, since that flag is the entire safety model.
- Seeding is idempotent: "Start" twice leaves one rehearsal concert.
- Pull-forward touches only queue rows belonging to the rehearsal concert, and
  only unsent ones.
- Teardown leaves users, presets and subscriptions intact.
- `/admin/rehearsal` renders for an admin (the every-page logged-in GET rule)
  and 403s for a signed-in non-admin.
- The seeded scenario produces one queue row per expected anchor — that shape is
  a claim about the planner and should fail loudly if the planner changes
  underneath it.
- Discord is never imported in service-layer tests; the shape catalogue is
  exercised through the builders with a fake bot.

## Open question

Should the shape catalogue let you pick the locale it renders in? It would turn
the eight-shape walk into the fastest ja/zh copy review the project has, which
is worth something given how much recent work was i18n. Leaning yes, since
`set_locale` makes it nearly free, but it is additive and can land second.

## Deviations from this spec

**Step 8 cancels the whole show, not just Day 2** (Task 3, 2026-07-28). The
walk above says "Cancel Day 2 → `leg_cancelled`". Measured against the real
code, that queues nothing: `notify_newly_cancelled_legs` is CONCERT-scoped by
design — it stays silent for any user who still holds a live reminder anywhere
on the concert — and after Day 2 goes down, Day 1's EVENT_START plus R1's four
anchors are all still standing, so the operator has fallback rows and gets no
notice. Cancelling Day 1 instead fails the same way. Only when the concert has
no live leg left (`all_legs_cancelled`) does every round count as lost and the
notice fire.

So `cancel_rehearsal_leg` cancels every remaining LIVE leg in one press. The
alternative — a per-leg button the operator presses twice, the first press
doing nothing visible — would demonstrate the `leg_cancelled` DM by not
sending it. The name is kept because Task 4's `/admin/rehearsal/cancel-leg`
route depends on it. `tests/test_rehearsal.py` pins the underlying rule
directly (`test_cancelling_takes_every_live_leg_because_the_notice_is_concert_scoped`)
so a later tidy-up cannot quietly restore the per-leg version.
