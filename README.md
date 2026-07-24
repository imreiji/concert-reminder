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
tests/           pytest; domain logic is tested hardest
deploy/          setup.sh, systemd unit, Caddyfile, backup.sh
docs/            deploy runbook + per-feature design specs and plans
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

To run the bot locally: create an app at https://discord.com/developers,
put the token in `.env`, invite the bot to a test server with the
`bot` + `applications.commands` scopes, then `/ping` it. Set
`DEV_GUILD_ID` to that test server's ID for slash commands to sync in
seconds instead of up to an hour (global sync, used when it's unset).

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
