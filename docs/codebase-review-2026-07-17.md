# Codebase Review — 2026-07-17

Scope: README.md, docs/deploy.md (docs/ contains no CSVs), scripts/seed_demo.py,
and the application code under src/app/ (this repo has no `pod/` or `tools/`
directory — reviewed the actual root-level layout instead). Findings are
ordered by urgency.

## 1. `scripts/seed_demo.py` was broken — removed

```
$ uv run python scripts/seed_demo.py
ImportError: cannot import name 'Window' from 'app.db.models'
```

The script still imported `Window`/`WindowKind` from `app.db.models` /
`app.domain.types`. Those were renamed to `Round`/`RoundKind` in the
"Windows to Rounds" restructuring (commit `e13447d`, migration
`20b27b1046f4_windows_to_rounds.py`) and no longer existed anywhere else in
`src/`. It also predated `event_id`: it constructed `Concert(...)` with no
`event_id`, now a required, unique, non-null column — so a straight
find-and-replace wouldn't have been enough to fix it either.

Neither CI gate (`ruff check .`, `pytest -q`) caught this, since neither one
executes the script and Ruff doesn't verify that an imported name actually
exists in the target module. **Resolved:** the script (and the now-empty
`scripts/` directory) was deleted rather than repaired, along with the
"Demo data" line in CLAUDE.md — it had drifted twice already (once on the
Window→Round rename, once on `event_id`) and the app has no other use for
it, so removal is cheaper long-term than maintaining a demo-data path
nothing else in the codebase exercises.

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

## 3. SSRF guard in the ramen.events importer had two soft spots — hardened

`src/app/web/routes/imports.py`:

- `_check_host()` validated the **submitted** URL's scheme/host, but
  `fetch_ramen_html()` called `httpx.AsyncClient(follow_redirects=True)`
  with no re-check on the redirect hops — a `ramen.events` redirect
  (compromised host, or an innocuous open-redirect endpoint there) would
  have been followed wherever it pointed, including an internal address,
  defeating the point of `_check_host` as an SSRF guard.
  **Resolved:** `fetch_ramen_html` now registers `_check_redirect_host` as
  an httpx response event hook, which re-runs `_check_host` against the
  `Location` header on every hop (capped at `MAX_REDIRECTS = 5`), so a
  redirect off the allowlisted host raises the same 400 the initial check
  would have.
- The `MAX_RESPONSE_BYTES` check ran *after* `client.get()` returned, i.e.
  after the full body was already downloaded and buffered into
  `resp.content` — the cap didn't bound memory/bandwidth during the fetch
  itself.
  **Resolved:** the fetch now uses `client.stream()` and reads the body in
  chunks via `aiter_bytes()`, aborting as soon as the running byte count
  exceeds `MAX_RESPONSE_BYTES` instead of after the fact.

Neither was exploitable today just by an ordinary user (the importer is
editor-only, `require_editor`), so this was defense-in-depth rather than an
active vulnerability. Covered by three new tests in `tests/test_imports.py`
that call `fetch_ramen_html` directly against an `httpx.MockTransport`: a
same-host redirect still resolves, a redirect off `ramen.events` raises
`HTTPException(400)`, and an oversized response raises
`HTTPException(502)` (with `MAX_RESPONSE_BYTES` monkeypatched down for the
test). All 199 tests pass, `ruff check .` is clean.

## 4. `web/routes/concerts.py` had outgrown its name — split

At 943 lines it was by a wide margin the largest module in the app (the
next largest, `service.py`, is 653), carrying several concerns beyond
"concert CRUD": create/edit/delete + `event_id` validation (its stated
job), `.ics`/YAML export, reminder-rule add/delete, and user-timezone
preference endpoints. The last two didn't obviously belong: reminder rules
are conceptually closer to `db/service.py`'s reminder-rule functions, and
`/me/timezone*` duplicated the concern `web/routes/preferences.py` already
exists for.

**Resolved:**
- `add_rule`/`delete_rule` moved to a new `web/routes/reminders.py`
  (mirroring the naming of `bot/cogs/reminders.py`). It imports
  `get_concert`, `get_concert_by_event_id`, and `render_rules_fragment`
  from `concerts.py` — those three helpers stay there since the
  concert-detail page (and `preferences.apply_preset_to_concert`, which was
  already doing this same lazy import) still need them.
- `/me/timezone`, `/me/timezone/auto`, `/me/timezone/reset` moved into
  `web/routes/preferences.py`, next to the rest of the per-user preference
  routes they were duplicating the concern of.
- `web/app.py` registers the new `reminders` router; no route paths
  changed, so this was a pure internal reorganization. `.ics`/YAML export
  stayed in `concerts.py` — they're concert/round-scoped exports, not a
  clearly separate concern the way rules/timezone were.

`concerts.py` is now 852 lines; all 199 tests pass unchanged (the routes
moved, not their behavior), confirming the split didn't alter any
request/response contract.

## 5. Minor — both addressed

- **No CSRF token.** State-changing routes are all POST forms and rely on
  `SameSite=Lax` cookies (`web/app.py`'s `SessionMiddleware`) for CSRF
  protection, with no separate token. That's a reasonable, common tradeoff
  for an app this size, but it was implicit.
  **Resolved:** added a one-line note to CLAUDE.md's Auth invariant so a
  future contributor doesn't assume it's an oversight or bolt on a token
  system that isn't needed. No code change.
- **Global slash-command sync on every startup.** `bot/client.py`'s
  `setup_hook` called `self.tree.sync()` unscoped. Global syncs can take up
  to an hour to propagate; for local dev against a single test server, a
  guild-scoped sync is near-instant.
  **Resolved:** added an optional `DEV_GUILD_ID` setting
  (`config.py`/`.env.example`); when set, `setup_hook` does
  `copy_global_to(guild=...)` + `sync(guild=...)` instead of the global
  sync, documented in README's "run the bot locally" section. Leaving it
  unset (the production default) keeps the old global-sync behavior
  unchanged, so this only affects opted-in local dev.

## Not flagged

`docs/deploy.md` was read in full and matches the current deploy shape
(Lightsail + Caddy + Cloudflare Origin cert + S3 backups + `/healthz`
scheduler-aware monitoring) — no drift found there. The reminder-queue
idempotency design, timezone boundary (`UTCDateTime`), and group-tag
expansion semantics all check out against their stated invariants in
CLAUDE.md and the code that implements them.
