# Codebase Review — 2026-07-17

Scope: README.md, docs/deploy.md (docs/ contains no CSVs), scripts/seed_demo.py,
and the application code under src/app/ (this repo has no `pod/` or `tools/`
directory — reviewed the actual root-level layout instead). Findings are
ordered by urgency.

## 1. `scripts/seed_demo.py` is broken — it crashes on import

```
$ uv run python scripts/seed_demo.py
ImportError: cannot import name 'Window' from 'app.db.models'
```

The script still imports `Window`/`WindowKind` from `app.db.models` /
`app.domain.types`. Those were renamed to `Round`/`RoundKind` in the
"Windows to Rounds" restructuring (commit `e13447d`, migration
`20b27b1046f4_windows_to_rounds.py`) and no longer exist anywhere in `src/`.
`grep -rn Window src/` turns up nothing except this file and an unrelated
docstring reference in `yaml_export.py`.

This is the only place in the repo that still uses the old names, which is
why `ruff check .` and `pytest -q` (the two CI gates) both stay green —
neither one executes the script, and Ruff doesn't verify that an imported
name actually exists in the target module. The breakage is invisible until
someone actually runs the documented command (README.md and CLAUDE.md both
advertise `uv run python scripts/seed_demo.py` as the way to get demo data).

Beyond the rename, the script also predates `event_id`: it constructs
`Concert(title=..., franchise=..., venue=..., notes=..., created_by=...)`
with no `event_id`, which is now a required, unique, non-null column — so a
straight find-and-replace of `Window`→`Round` isn't enough, the `Concert(...)`
call needs an `event_id=` too (e.g. via the same `generate_event_id` helper
`routes/imports.py` uses).

**Recommendation:** fix the script (rename + add `event_id`), and add a cheap
regression guard — either a smoke test that imports `scripts.seed_demo` and
calls `main()` against the test DB, or a `uv run python scripts/seed_demo.py
--clean` step in CI — so a future rename doesn't silently break it again.

## 2. README.md has the same staleness problem CLAUDE.md had

This session already found and fixed CLAUDE.md describing the app as it
stood at "Phase 12" while several features had shipped since (event_id +
edit page, venue regions, `.ics` export, YAML export, the ramen.events
importer — see PR #9). README.md has the identical gap and one more:

- Its Roadmap section also stops at Phase 12, so none of the above appear.
- Its "Rules that prevent the classic bugs" section says: *"only IDs in
  `EDITOR_WHITELIST` can create/edit concerts"* — this is no longer
  accurate. Per `CLAUDE.md` and `src/app/config.py`/`web/auth.py`, editor
  rights now come from `EDITOR_WHITELIST` **or** the DB-backed
  `users.is_editor` flag (toggleable live by admins), and there's a whole
  admin tier (`ADMIN_WHITELIST`) this section doesn't mention at all. A
  reader relying only on README would get the access model wrong.

**Recommendation:** update README's roadmap and access-control paragraph to
match CLAUDE.md's (already-current) description of the three-tier auth
model. Since this is the second time a "shipped features not reflected in
docs" gap has shown up in one review, it may be worth a lightweight habit —
e.g. a PR checklist line, or a CI step that just diffs the Roadmap's last
phase against recent merge commits — rather than relying on manual updates.

## 3. SSRF guard in the ramen.events importer has two soft spots

`src/app/web/routes/imports.py`:

- `_check_host()` validates that the **submitted** URL's scheme is `https`
  and host is exactly `ramen.events` — good, allowlist not blocklist, as the
  comment says. But `fetch_ramen_html()` then calls
  `httpx.AsyncClient(follow_redirects=True)`. Redirect hops are never
  re-checked against the allowlist, so if `ramen.events` ever redirects
  somewhere else (compromised host, or an innocuous open-redirect endpoint
  on that domain), the fetch will follow it wherever it goes — including to
  a cloud metadata address or another internal host. Since the whole point
  of `_check_host` is to be an SSRF guard, this defeats it in the redirect
  case.
  **Recommendation:** either set `follow_redirects=False` (ramen.events
  posts are permalinks, a redirect isn't expected), or re-validate the host
  after each hop with `httpx`'s event hooks / manual redirect handling.

- The `MAX_RESPONSE_BYTES` check happens *after* `client.get()` returns —
  i.e. after the full body has already been downloaded and buffered into
  `resp.content`. The cap doesn't bound memory or bandwidth use during the
  fetch; it only stops the parser from running on an oversized page. For a
  URL fetcher this is a minor DoS-hardening gap.
  **Recommendation:** stream the response and abort once the running byte
  count exceeds the limit, rather than checking `len(resp.content)` after
  the fact.

Neither is exploitable today just by an ordinary user (the importer is
editor-only, `require_editor`), so this is defense-in-depth rather than an
active vulnerability — but worth tightening given the module's whole job is
being the SSRF boundary.

## 4. `web/routes/concerts.py` has outgrown its name

At 943 lines it's by a wide margin the largest module in the app (the next
largest, `service.py`, is 653). Skimming its route list shows it's carrying
several concerns beyond "concert CRUD":

- concert create/edit/delete + the event_id validation helpers (its stated job)
- `.ics` export for a single round (`GET /rounds/{round_id}/ics`)
- YAML export for a whole concert (`GET /concerts/{event_id}/export.yaml`)
- reminder-rule add/delete (`POST /concerts/{event_id}/rules`, `POST /rules/{rule_id}/delete`)
- user timezone preference endpoints (`POST /me/timezone`, `/me/timezone/auto`, `/me/timezone/reset`)

The last two groups don't obviously belong here: reminder rules feel closer
to `db/service.py`'s reminder-rule functions, and `/me/timezone*` duplicates
the concern `web/routes/preferences.py` already exists for. This isn't
urgent — nothing here is broken — but it's the kind of file that keeps
growing because "the concert routes are already in there," and splitting it
now (mechanical route moves, same `router` prefix conventions) is a lot
cheaper than splitting it after another few features land on top.

## 5. Minor / worth a one-line note rather than a fix

- **No CSRF token.** State-changing routes are all POST forms and rely on
  `SameSite=Lax` cookies (`web/app.py`'s `SessionMiddleware`) for CSRF
  protection, with no separate token. That's a reasonable, common tradeoff
  for an app this size, but it's implicit — worth a one-line note in
  CLAUDE.md so a future contributor doesn't either assume it's an oversight
  or bolt on a token system that isn't needed.
- **Global slash-command sync on every startup.** `bot/client.py`'s
  `setup_hook` calls `self.tree.sync()` unscoped. Global syncs can take up
  to an hour to propagate and share Discord's global rate limit; for a bot
  that's only ever installed in one or two servers, a guild-scoped sync
  (`copy_global_to` + `sync(guild=...)`) would make local iteration faster.
  Pure dev-experience, no user-facing impact once commands have propagated
  once.

## Not flagged

`docs/deploy.md` was read in full and matches the current deploy shape
(Lightsail + Caddy + Cloudflare Origin cert + S3 backups + `/healthz`
scheduler-aware monitoring) — no drift found there. The reminder-queue
idempotency design, timezone boundary (`UTCDateTime`), and group-tag
expansion semantics all check out against their stated invariants in
CLAUDE.md and the code that implements them.
