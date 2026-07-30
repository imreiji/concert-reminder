# dekimasen.app

> できませんでした, never again.

Discord bot + web UI that tracks the deadline chain around Japanese concerts —
lottery rounds (先行抽選), eligibility item sales (シリアル対象商品), general sale,
per-day stream tickets (配信チケット) — and DMs you N days before/after each one.

One Python process runs three things on one asyncio event loop:
the Discord bot, the FastAPI web UI, and a 60-second scheduler tick.
State is a single SQLite file. Deployment is one $5 VPS + Caddy.

## Architecture in 30 seconds

```
┌─────────────────────────── one process (python -m app.main) ───┐
│  discord.py bot ──┐                                            │
│  FastAPI web UI ──┼── shared asyncio loop ── SQLite (app.db)   │
│  scheduler tick ──┘                                            │
└────────────────────────────────────────────────────────────────┘
         ▲ Caddy (HTTPS) fronts the web UI in production
```

Layout:

```
src/app/
  config.py      env-driven settings; admin/editor whitelists live here
  domain/        pure logic, no I/O — timezone + reminder math, ramen.events
                 HTML parsing, .ics/YAML export formatting
  db/            SQLAlchemy models, async session, and service.py — all
                 business logic that touches the DB lives here
  bot/           discord.py client + cogs, embed builders, persistent buttons
  scheduler/     the 60s loop that drains reminder_queue + notifications
  web/           FastAPI app: Discord OAuth, htmx CRUD UI, calendar feed
  i18n.py        gettext plumbing (en/ja/zh); top-level because the bot needs
                 it too, and it does file I/O at startup
  ops.py         the I/O half of the health checks (domain/health.py is pure)
tests/           pytest; domain logic is tested hardest
deploy/          setup.sh, systemd unit, Caddyfile, backup.sh
docs/            deploy runbook, the local dev-bot guide, per-feature design
                 specs and plans, and the concept demos that are the design
                 source of truth for the UI
```

## Rules that prevent the classic bugs

1. **Timezones:** DB stores aware UTC only. Forms accept JST (that's how
   Japanese ticketing announces). Display shows user-local + JST. All
   conversions go through `app/domain/timezones.py` — nowhere else.
2. **Access:** anyone with Discord can log in and manage their own reminders.
   Creating/editing concerts needs editor rights: either a Discord ID in the
   `EDITOR_WHITELIST` env var (permanent bootstrap/break-glass set) or the
   DB-backed `users.is_editor` flag, which admins (`ADMIN_WHITELIST`) can
   grant or revoke live from the preferences page or `/promote-editor` /
   `/demote-editor`. Admins automatically have editor rights too.
3. **Reminders are idempotent:** the queue has a UNIQUE constraint, editing a
   date reschedules instead of duplicating, and the scheduler only marks a row
   sent after the DM succeeds.

## Local development

```bash
uv sync                        # install everything (creates .venv)
cp .env.example .env           # fill in tokens; leave DISCORD_TOKEN empty
uv run python -m app.main      #   for web-only dev mode
# open http://localhost:8000
uv run pytest                  # tests
uv run ruff check .            # lint
```

The Python version is pinned in `.python-version`, which `uv` honours in dev,
in CI and on the server, so all three run the same interpreter.

To run the bot locally: create an app at https://discord.com/developers,
put the token in `.env`, invite the bot to a test server with the
`bot` + `applications.commands` scopes, then `/ping` it. Set
`DEV_GUILD_ID` to that test server's ID for slash commands to sync in
seconds instead of up to an hour (global sync, used when it's unset).
Full walkthrough, including the redirect URI that bites: `docs/local-dev-bot.md`.

To exercise the DM side without waiting for real deadlines, set
`REHEARSAL_ENABLED=true` and visit `/admin/rehearsal`: it seeds one canonical
concert, pulls its reminders forward so the real 60s tick delivers them now,
and sends any DM shape in any language on demand. Leave the flag off in
production — the routes are then not registered at all, which is the safety
model, not the `require_admin` on each one.

## Deployment (short version — full runbook: docs/deploy.md)

1. Ubuntu 24.04 box (Lightsail $5 / EC2 t4g.micro) with a static IP.
2. Cloudflare zone for dekimasen.app (nameservers moved from Porkbun), A record
   proxied to the box, SSL mode Full (strict), Origin cert installed for Caddy.
3. `deploy/setup.sh` on the box; edit `.env`; re-run.
4. Add `https://dekimasen.app/auth/callback` to the Discord OAuth redirects.
5. Point an uptime monitor at `https://dekimasen.app/healthz`.

Updating: `git pull && uv sync && uv run alembic upgrade head && sudo systemctl restart concert-reminder`

Logs: `journalctl -u concert-reminder -f`
Backups: `deploy/backup.sh` via cron (nightly SQLite → S3).

## Roadmap

- [x] Phase 1 — skeleton: config, dual-process entrypoint, /ping, status page, CI
- [x] Phase 2 — schema (concerts / days / windows / rules / queue) + reminder math
- [x] Phase 3 — scheduler sends real DMs; /upcoming, /remindme, /myreminders
- [x] Phase 4 — Discord OAuth login
- [x] Phase 5 — concert CRUD web UI (htmx) + dekimasen.app branding
- [x] Phase 6 — deployed: Cloudflare -> Caddy (origin cert) -> Lightsail
- [x] Phase 7 — S3 backups, scheduler heartbeat in /healthz, uptime monitoring
- [x] Phase 8 — DB-backed revocable sessions; browser timezone auto-detect + local times in UI
- [x] Phase 9 — tags (franchise/artist/venue/group), filtering & sorting, group auto-expansion
- [x] Phase 10 — reminder presets, one-click apply, tag subscriptions with notify-and-apply
- [x] Phase 11 — UI overhaul: sidebar filters, tiles, tag-driven creation, sentence presets, hierarchical subscriptions, favicon
- [x] Phase 12 — interactive DM embeds: state-aware buttons, default preset, snooze, deadline list

Shipped since Phase 12 (no phase numbers assigned, tracked as feature PRs instead):
- [x] Three-tier access control: Admin, Editor, User
- [x] Import a concert draft from a ramen.events URL
- [x] Windows restructured into Rounds: apply/results/payment bundled into one row
- [x] Concert kind, day/leg round grouping, and a YAML export
- [x] A dedicated, richer `/concerts/new` creation page
- [x] Venue tags with region + link, round table, `.ics` export, live countdown, past-marking
- [x] `event_id` URLs and a dedicated Edit Concert page
- [x] Personal calendar-feed subscription (a secret `.ics` URL calendar apps poll)
- [x] Free-text concert search on the index page (combines with tag filters as AND)
- [x] `/mydeadlines` — your upcoming deadlines as a Discord slash command
- [x] Per-concert edit history
- [x] Per-leg cancelled status: legs stay visible, their reminders drop, one-click reinstate
- [x] Tags page redesign — search, hierarchy, dialog-based editing, rename, retroactive apply
- [x] Index page reorg — open-and-upcoming bucketing + a global chronological deadline list
- [x] Multi-language support — English/Mandarin/Japanese, gettext catalogues, per-user locale, translated UGC
- [x] Mobile retrofit — bottom tab bar, editor FAB, bottom-sheet dialogs, Discover filter sheet, all in one `@media` block
- [x] Signed-out redirect home — a real landing page instead of a bare 401, returning you to the page you asked for after login
- [x] Venue-to-tags — a leg's venue is a VENUE tag (city/address live on the tag); a concert's venues derive from its legs; create a venue without leaving the editor; the legacy free-text venue columns are gone
- [x] Trilingual concert pages — leg and round labels render in the viewer's language; a self-populating round-label phrase library (typed triples become one-click suggestions); every translatable field is filled in all three languages or none; the import preview's per-leg venue picker
- [x] Agent-driven concert import — paste a YAML draft and the import preview arrives prefilled (titles, legs, rounds, tag/venue matches); the add-concert skill that authors drafts is downloadable from the import page
- [x] i18n calibration — a native-level review pass applied across both catalogues (307 ja / 344 zh strings corrected), a ja agent-proofread round, and all 132 reviewed English-source fixes applied at the source layer (mapping preserved in `docs/i18n-english-source-fixes-2026-07-24.csv`)
- [x] Editor coherence — create/edit/import leg-and-round cards share two partials; destructive actions in a kebab menu (the × beside Cancelled is gone); ja/EN/中文 labels on their own row; reminder sentence builders read grammatically in all three languages via locale-ordered slot patterns
- [x] Tablet band (701–1040px) — one bounded `@media` section like the phone retrofit: compact one-row header, swipeable campaign board, Discover's filter sheet as an inline panel; the scattered mid-band breakpoints are gone and a guard test keeps them gone
- [x] Inline tag creation — unmatched draft tags in the import preview become per-name create chips opening a kind-aware popup (kind pre-selected, parent franchise for groups); the created tag joins the picker on the spot
- [x] UX pass — 20 changes in five batches: board column-head colours, Discover active-filter chips with live section counts, the "Next for you" strip moved into the concert header, a numbered create-form spine, the Tags chips⇄table view and follow bell, an htmx progress bar, and the two-shape callout grammar (`.edgecard` for ongoing state, `.banner` for needs-attention)
- [x] Per-leg outcome truth — a real lottery resolves per performance, so a round covering Sat+Sun can come back won on one and lost on the other; a round whose legs you already hold stops asking
- [x] "Coming up" de-crowded — one block per concert (a header, the row that actually wants you, the rest behind a fold) instead of one row per anchor, so the row budget counts concerts
- [x] Board ladder capped — a card shows the rung that explains its column plus the next actionable one, with the remainder as a count line; uniform card height is what makes four columns scan as a board
- [x] Settled rounds fold per leg on the concert page — a secured leg keeps its receipt visible while everything resolved goes behind one fold (still in the DOM, so its capture form works)
- [x] Performer chips cluster by group on the concert page — one block per attached group, then an unlabelled trailer for performers in no attached group
- [x] A concert whose every leg is cancelled stops asking you to act — it leaves *Open now*, offers no capture, and a tag attached to it announces nothing and applies no preset
- [x] Cleanup batch — `event_id` slugs prefer `title_en`; the unfollow dialog stops overstating what it removes; nothing is announced about a dead concert; expanded folds survive an htmx swap; importer review debt
- [x] Delivery feed — every DM is recorded in `delivery_log` and readable at `/admin/deliveries`, plus a per-tick digest DM that reports counts rather than names (a name in Discord history is a record account deletion cannot reach)
- [x] Targeted admin broadcast — `/admin/broadcast` puts admin-authored text in users' DMs through the same outbox, held 120s so it can be cancelled before it sends
- [x] Local rehearsal harness — `/admin/rehearsal`, registered only when `REHEARSAL_ENABLED` so production has no such routes: seed a canonical concert, pull its reminders forward, send any DM shape in any language on demand
- [x] Real 403/404/422/500 pages — a browser navigation gets styled HTML with a way back, an XHR keeps the JSON body it was already parsing; the admin pages are indexed in Preferences instead of needing their URLs known
- [x] Correctness sweep — create and import no longer DM followers a "new event" for a concert whose only leg arrived cancelled, and a generated `event_id` can no longer take a reserved word and mint an unreachable page
