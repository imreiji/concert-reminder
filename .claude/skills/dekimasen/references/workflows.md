# Workflows — the rituals that keep the repo coherent

Three recurring processes. Each step exists because skipping it has caused
(or visibly nearly caused) a real problem. The migration ritual lives in
`migrations.md` and the i18n string-change ritual in `i18n.md`; this file
holds the rest.

## Shipping a feature (the full ritual)

1. **Read `WISHLIST.md` first** if the feature came from a roadmap/UX
   discussion — it may already have an entry with agreed impact/effort
   framing and prior reasoning.
2. **Spec, then plan, then code** for anything substantial: write a
   date-prefixed design spec in `docs/superpowers/specs/` and an
   implementation plan in `docs/superpowers/plans/`
   (`YYYY-MM-DD-feature-name.md`), commit them before the implementation.
   Recent features (cancelled legs, Tags redesign, index reorg) all followed
   this; match their shape. Small fixes don't need this apparatus — use
   judgment, and when unsure ask the owner whether it's "substantial".
3. **Check the invariants** (`invariants.md`) for every area the feature
   touches, and the matching area references (`architecture.md`,
   `ui-conventions.md`, `i18n.md`, `migrations.md`).
4. **UI changes reconcile against the concept demos** in
   `docs/superpowers/demo/` — `dekimasen-demo.html` (core views),
   `dekimasen-onboarding-demo.html` (signed-out/new-user flows),
   `dekimasen-mobile-demo.html` + `dekimasen-mobile-live.html` (phone).
   The demos are the design source of truth. If the shipped design
   deliberately diverges, update the demo so it stays the reference.
5. **New user-visible strings** trigger the i18n ritual (`i18n.md`) —
   both catalogues, no fuzzy entries, `test_i18n_catalogues.py` green.
6. **Schema changes** trigger the migration ritual (`migrations.md`).
7. **Tests**: every new page gets at least one logged-in GET render test;
   domain logic is tested hardest; see `testing.md` for the conventions.
8. **Gates before any commit**: `uv run pytest -q` and
   `uv run ruff check .` must both pass — the same two gates CI runs.
9. **After the feature ships, update `WISHLIST.md`**: move the shipped
   entry to its Shipped section with the date (or log it there even if it
   was never Proposed), then do a full revision pass over the remaining
   entries — re-rank by impact and reconsider each one, since a shipped
   feature can raise, lower, or obsolete others. Append new ideas raised
   during the work with date and context; move rejected ideas to Rejected
   with the reason instead of deleting them.

## Wishlist maintenance (outside of shipping)

Read `WISHLIST.md` before any feature-planning or roadmap discussion.
Every idea raised in discussion gets appended with its date and context,
even if it's immediately rejected (then it goes to Rejected with the
reason). The list is ordered by user impact, highest first, with impact +
effort noted per entry so re-ranking has a basis.

## Deploy

Server: Lightsail Ubuntu 24.04, app at `~/app`, systemd unit
`concert-reminder`, Caddy with a Cloudflare Origin cert.

Update ritual, in order:

```
cd ~/app && git pull && uv sync && uv run alembic upgrade head && sudo systemctl restart concert-reminder
```

Caddyfile changes additionally need:
`sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`

Logs: `journalctl -u concert-reminder -f`. Health: `/healthz` (UptimeRobot
keyword-monitors `"ok":true`; goes false after 3 missed scheduler ticks).
Nightly S3 backups via `deploy/backup.sh` (cron, 30-day lifecycle).
Full runbook: `docs/deploy.md`. Never commit `.env`; secrets live only on
the server and in the owner's local copy.

Note: these are commands run ON the Ubuntu server (`&&` is fine there).
Commands given to the owner to run on his LOCAL machine are PowerShell 5.1
— no `&&` chaining; use `;` or separate lines.
