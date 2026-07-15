# concert-reminder

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
  config.py      env-driven settings; editor whitelist lives here
  domain/        pure logic, no I/O — timezone math now, reminder math in Phase 2
  db/            SQLAlchemy models + async session (Phase 2)
  bot/           discord.py client + one cog per feature
  scheduler/     the 60s loop that drains reminder_queue (Phase 3)
  web/           FastAPI app, Discord OAuth (Phase 4), CRUD UI (Phase 5)
tests/           pytest; domain logic is tested hardest
deploy/          setup.sh, systemd unit, Caddyfile, backup.sh
```

## Rules that prevent the classic bugs

1. **Timezones:** DB stores aware UTC only. Forms accept JST (that's how
   Japanese ticketing announces). Display shows user-local + JST. All
   conversions go through `app/domain/timezones.py` — nowhere else.
2. **Access:** anyone with Discord can log in and manage their own reminders;
   only IDs in `EDITOR_WHITELIST` can create/edit concerts.
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
`bot` + `applications.commands` scopes, then `/ping` it.

## Deployment (short version — full walkthrough in docs/)

1. Ubuntu 24.04 box (Lightsail $5 / EC2 t4g.micro) with a static IP.
2. Porkbun DNS: `A` record → that IP.
3. `deploy/setup.sh` on the box; edit `.env`; re-run.
4. Update the Discord OAuth redirect to `https://your.domain/auth/callback`.
5. Point an uptime monitor at `https://your.domain/healthz`.

Updating: `git pull && uv sync && uv run alembic upgrade head && sudo systemctl restart concert-reminder`

Logs: `journalctl -u concert-reminder -f`
Backups: `deploy/backup.sh` via cron (nightly SQLite → S3).

## Roadmap

- [x] Phase 1 — skeleton: config, dual-process entrypoint, /ping, status page, CI
- [x] Phase 2 — schema (concerts / days / windows / rules / queue) + reminder math
- [x] Phase 3 — scheduler sends real DMs; /upcoming, /remindme, /myreminders
- [ ] Phase 4 — Discord OAuth login
- [ ] Phase 5 — concert CRUD web UI (htmx)
- [ ] Phase 6 — deploy to AWS + Porkbun domain
- [ ] Phase 7 — backups, monitoring, polish
