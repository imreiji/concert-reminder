# Running locally against your own Discord bot

One-time setup, roughly fifteen minutes, all free. What it buys is the one
thing nothing else in this project proves.

| Tier | What it proves | Status |
| --- | --- | --- |
| Test suite | logic, planner, suppression, gating, every page renders | 1586 tests, exists |
| Web-only dev mode | the real app in a real browser, real DB, real htmx | exists (empty `DISCORD_TOKEN`) |
| **Real Discord DMs** | embeds, buttons, the 60s tick, delivery | **what this guide sets up** |

Tier 3 was missing only because there was one Discord bot and it was
production's. A second Discord application closes the gap, and once the app is
running against it, `/admin/rehearsal` (the rehearsal harness) walks the whole
user flow -- every anchor, every DM shape, every button -- in minutes instead
of weeks of real waiting.

Nothing here touches production. The harness routes are registered only when
`REHEARSAL_ENABLED` is true, which the server never sets, so on production they
do not exist at all.

Full design reasoning: `docs/superpowers/specs/2026-07-28-rehearsal-harness-design.md`.

---

## 1. A second Discord application

Discord Developer Portal -> **New Application** -> name it something you will
recognise in a hurry (`dekimasen-dev`) -> **Bot** -> **Reset Token** -> copy the
token somewhere for step 7. It is shown once.

This is a different application from production's, and it must stay that way:
the two never share a token, a client secret, or a redirect list.

Leave all three **privileged gateway intents OFF**. `bot/client.py` runs on
`discord.Intents.default()` -- slash commands and DMs need nothing privileged --
so turning them on only widens what a local token can do.

## 2. A private test server, and inviting the bot

In Discord: **Add a Server** -> **Create My Own** -> skip the questions. Nobody
else needs to be in it.

Then Developer Portal -> **OAuth2** -> **URL Generator**:

- scopes: **`bot`** and **`applications.commands`**
- bot permissions: none needed. The bot only sends DMs and answers slash
  commands; it never posts in a channel.

Open the generated URL, pick your test server, authorise.

`applications.commands` is easy to miss because `bot` alone produces a working
invite. Without it the bot joins and the slash commands never appear, which
looks like a sync bug rather than a missing scope.

**Every account you want DMed must be a member of that server.** A bot can only
open a DM with someone it shares a guild with. If it cannot, the send raises
`discord.Forbidden` and the scheduler *drops* that queue row (invariant 2) --
the reminder does not retry and nothing on the page says so. Both your account
and the second test account from step 6 belong in the test server.

## 3. The server id, for `DEV_GUILD_ID`

Discord -> Settings -> Advanced -> **Developer Mode** on. Then right-click the
server -> **Copy Server ID**.

With `DEV_GUILD_ID` set, slash commands sync to that one guild in seconds. Left
empty (production's setting) the sync is global and can take up to an hour,
which turns "did my command change work" into a coffee break.

## 4. The redirect URI -- this is the step that bites

Developer Portal -> **OAuth2** -> **Redirects** -> Add
`http://localhost:8000/auth/callback` -> **Save Changes**.

Miss it and signing in locally fails *at Discord*, before any request reaches
the app: Discord refuses the authorize URL with "invalid OAuth2 redirect_uri"
and never sends the browser back. So there is nothing in the app's log, nothing
in the browser's network tab pointing at the app, and no error page the app
could write -- it is the one failure in this setup that the software cannot
explain to you, which is why it gets its own section.

It must match **exactly** -- scheme, host, port, path, no trailing slash -- and
it must agree with `BASE_URL` in your `.env`, because `web/auth.py` builds the
value it sends as `f"{settings.base_url}/auth/callback"`. Change the port in
one place and you have to change it in both.

## 5. The dev application's own client id and secret

Same OAuth2 page: copy **Client ID**, and **Reset Client Secret** for the
secret. From the *dev* application -- not production's.

Two reasons, and neither is theoretical. Reusing production's credentials means
adding a `localhost` redirect to the live application's config, i.e. editing
production settings to do local work. And a client secret that has been on a
laptop, in a `.env`, in a terminal history is a secret that can mint sessions on
the live site if it ever leaks.

## 6. A second Discord account

For the new-user flow (see below), which cannot be walked from an account that
already exists in the database. Any spare account works; invite it to the test
server too (step 2's DM rule), and drive it from an **incognito window** so the
two sessions do not fight over the same cookie.

## 7. The local `.env`

At the repo root, next to `.env.example`. Never committed.

```
DISCORD_TOKEN=<dev bot token from step 1>
DEV_GUILD_ID=<test server id from step 3>
DISCORD_CLIENT_ID=<dev app client id>
DISCORD_CLIENT_SECRET=<dev app client secret>

BASE_URL=http://localhost:8000
DATABASE_URL=sqlite+aiosqlite:///./dev.db
ADMIN_WHITELIST=<your discord user id>
REHEARSAL_ENABLED=true
DEFAULT_TIMEZONE=America/Moncton
```

Four notes on that block:

- **`REHEARSAL_ENABLED=true` is the entire safety model, inverted.**
  `web/app.py` registers the harness router only under this flag. Production
  leaves it unset, so `/admin/rehearsal` is absent from the route table there
  rather than merely protected -- a "pull every reminder forward" button
  guarded only by a permission check is one misconfiguration away from firing
  real reminders early.
- **`ADMIN_WHITELIST` is what unlocks the harness.** `require_admin` sits on
  every route as a second layer. Admins pass editor checks too, so you do not
  also need `EDITOR_WHITELIST` to create concerts locally. It wants your
  Discord *user* id: Developer Mode on, right-click yourself, Copy User ID.
- **`dev.db`, not `app.db`.** A separate file keeps whatever is in your
  existing local `app.db` out of reach of a harness whose job is to seed and
  delete concerts.
- **`SESSION_SECRET` can stay unset.** Its strength validator only fires when
  `BASE_URL` is https, precisely so a fresh clone runs over http without
  ceremony.

## 8. First run

```
uv run alembic upgrade head
uv run python -m app.main
```

(Two lines, not `&&` -- PowerShell 5.1 does not chain that way.)

The first command creates `dev.db` at head. The second starts all three
subsystems on one loop: the bot, the web app on `http://localhost:8000`, and
the 60-second scheduler tick. Watch for `synced N slash command(s) to dev guild
...` in the log -- that line is step 3 working.

Then open `http://localhost:8000`, sign in with Discord (step 4 working), and
go to `/admin/rehearsal`.

---

## 9. The rehearsal harness

**Start** seeds one canonical concert -- `event_id` `rehearsal`, two legs, three
rounds -- attaches an ARTIST tag you follow (which fires the `new_event` fan-out,
step 1 below), subscribes you to the concert, writes one reminder rule per anchor
at zero offset, and lets the real `sync_concert` plan them. That order matters:
the tag is attached before any rule exists, because `handle_newly_tagged` skips a
user who already has rules on the concert. Eight queue rows come out, and the
page's state table lists them in the order they will fire:

| # | Anchor | Round or leg |
| --- | --- | --- |
| 1 | opens | 1st lottery (R1, both legs, all four anchors) |
| 2 | opens | General sale (R2, Day 1 only) |
| 3 | closes | 1st lottery |
| 4 | closes | General sale |
| 5 | results | 1st lottery |
| 6 | payment | 1st lottery |
| 7 | event_start | Day 1 |
| 8 | event_start | Day 2 |

The upgrade round (R3) contributes no rows at all yet: it is per-user gated and
you hold no ticket. It appears the moment you win one.

**Next reminder** rewrites the soonest unsent row's `fire_at_utc` into the past.
Within a minute the real tick delivers it as a real DM. That is the only thing
faked, and it fakes the *wait*, not the work -- the planner computed the row,
and suppression, gating, the send path and the buttons all run exactly as they
do in production. Pressing Next resolves the row through the rehearsal concert;
no queue id crosses the boundary, so no other concert's reminder is reachable.

**Cancel the show** cancels every remaining live leg at once and queues the
`leg_cancelled` notice. It cancels all of them because that notice is
concert-scoped by design: it stays silent for anyone who still holds a live
reminder somewhere on the concert, so killing one leg of two demonstrates the
DM by not sending it.

**End** deletes the concert; cascades take the days, rounds, queue rows and
outcomes. Your user, presets and subscriptions are untouched.

### The prescribed walk

Order is load-bearing. Payment only offers Paid from Won, so step 3 must come
before 4 and 4 before 6 -- press Lost at step 4 and the ladder ends there.

| # | Action | What arrives | Buttons |
| --- | --- | --- | --- |
| 1 | Start | `new_event` embed, fanned out by the tag attach | apply / remove / deadlines |
| 2 | Next | R1 opens | snooze |
| 3 | Next | R1 closes -> press **I applied** | applied / notapplied / remindlater |
| 4 | Next | R1 results -> press **Won -- Day 1** | wonall / wonday x2 / lostall / snooze |
| 5 | *(observe the table)* | R3's two rows appear; R2's remaining rows vanish | -- |
| 6 | Next | R1 payment -> press **Paid** | paid / snooze |
| 7 | Next | Day 1 starts | snooze |
| 8 | Cancel the show | `leg_cancelled` -> press **Turn my reminders back on** | reinstate |
| 9 | End | concert deleted, cascades take the rest | -- |

Three things about that table are worth spelling out, because each of them was
wrong in an earlier draft of it and a walk that lies about what a correct DM
looks like teaches you to "fix" working code.

**Step 1 really does DM you, and the ordering is why.** The `new_event` notice
is fanned out by `handle_newly_tagged` to the followers of a newly attached
tag, and `handle_newly_tagged` skips any user who ALREADY has rules on the
concert. So the seed attaches its ARTIST tag (リハーサル・アーティスト, which you
are subscribed to with notify on) *before* writing its five rules. Attach it
afterwards and Start queues nothing at all — silently.

That is worth watching rather than clicking past: this is the widest
notification path in the app, the one that DMs every follower of a tag, and
the likeliest way it could ever message the wrong people. It is also the only
step here that exercises delivery rather than rendering — the shape catalogue
can draw the same embed, but only this proves it reaches anyone.

**Step 4 is the per-leg split, not Won/Lost.** R1 deliberately covers both
legs, so `build_reminder_message` renders `wonall`, one `wonday` per covered
leg, and `lostall` -- the flat Won/Lost pair is what a *single*-leg round
renders. And every reminder DM ends with a trailing button: `remindlater` on a
closes row, `snooze` on every other. An expectation that stopped at the capture
buttons would call a correct DM wrong.

**Rows 2 and 4 of the queue table arrive between the numbered steps.** The
General sale is a separate round with its own opens and closes reminders and
its own outcome, so pressing Next repeatedly gives you R1 opens, *General sale
opens*, R1 closes, *General sale closes*, R1 results. That is not a defect; R2
exists precisely so that step 5 has something to go quiet. If you want to
*watch* the suppression rather than take it on trust, stop pulling before the
General sale's closes row and check the state table before and after step 4:
recording a win on Day 1 secures the leg R2 covers, so its unsent rows are
deleted, silently, the way they would be in production. A round that stops
arriving is the hardest thing in this app to notice by hand.

The page names, for the next row only, the buttons a correct DM should carry.
That column is restated in `domain/rehearsal.py` from the message builder, not
read from it -- an oracle that derived its expectation from the code under test
would agree with that code however wrong it became. If what arrives differs
from what the page predicted, the harness has caught something.

### The shape catalogue

The other half of the page, and it needs none of the walk: pick one of the
eight DM shapes and one of three languages, press Send, and it is rendered
through the real builders and delivered now.

The eight are the five reminder anchors, the two notice embeds (`new_event`,
`leg_cancelled`) and the plain-text ops alert. It still needs the seeded
concert -- press Start first -- because every shape is composed from it.

Two deliberate quirks. The payment shape is built as if you had won, so it
shows its Paid button whatever the walk has reached; that is what keeps this
half independent of the pipeline half. And the ops alert ignores the language
picker, because `evaluate_and_alert` composes it as a bare f-string with no
gettext anywhere -- it is in the catalogue for its *layout* (plain text, no
embed, no buttons), the one thing the other seven cannot show.

This is the half that stays useful after every copy or translation change:
eight embeds in three languages is a minute's work, and it is the fastest ja/zh
copy review the project has.

## 10. The new-user flow

Walked from the **second Discord account**, in an incognito window at
`http://localhost:8000`: the signed-out landing page -> sign in -> `/welcome` ->
`/setup`.

It has to be a genuinely new account, and there is no reset button by design. The
branches worth exercising only run once per account: `auth.py`'s new-user
detection, seeding `users.language` from the `lang` cookie *at account creation
only*, the `/welcome` step counter, and the handoff into `/setup`. A reset
button cannot reach any of them, because the row already exists. `/setup` is
re-runnable from Preferences anyway, so a reset's only unique value would be
re-walking `/welcome` -- and deleting `dev.db` and re-migrating gives you that
for nothing.

That account is also how you watch a DM land somewhere that is not your own
inbox: have it follow a tag you then attach to a concert.

## 11. Do NOT copy the production database

Migrate a fresh `dev.db` and let the harness seed it. That is enough for
everything above.

Do not copy production's `app.db` to a laptop. Today it is effectively the
owner's own data, but the habit is the problem: `users`, `web_sessions`,
`round_outcomes`, `concert_subscriptions`, `reminder_rules` and `delivery_log`
are all personal data, and a copy on a laptop sits outside every deletion path
this app promises -- `POST /me/delete` cannot reach it, and neither can the
30-day retention the privacy policy commits to.

If production realism is ever genuinely wanted, the clean version is a
**catalogue-only** copy -- concerts, days, rounds, tags, memberships -- which
contains no personal data by construction rather than by a filter somebody has
to get right. That is exactly what the admin catalogue export on WISHLIST.md
produces, and local dev seeding is the second use it was filed for.

## 12. What a local run cannot prove

A local walk proves the code. It cannot prove the *deployment*: production's
accumulated rows, its interpreter, Caddy, Cloudflare, systemd. That residue is
small and stays a manual checklist rather than a harness -- four things, after
a deploy you care about:

1. `POST /me/test-dm` from Preferences -> a DM arrives. Proves the production
   token, the gateway connection and DM permissions in one press.
2. `/healthz` returns `"ok":true` -> the scheduler is ticking.
3. One real concert page and `/discover` render -> production's accumulated
   rows do not break a template, which is a failure class this project has
   shipped before.
4. `journalctl -u concert-reminder -f` shows no exceptions across one tick.
