# Agent read API (`/api/v1`)

**Status:** design, approved by the owner 2026-08-08.
**Scope ruling (owner, 2026-08-08):** READS ONLY. Agent writes are explicitly
deferred to their own design — see "What this deliberately is not", below.
**Related:** WISHLIST #1 (the first live completion run is uncalibrated) is the
gate on whether the AI pipeline this serves is worth extending at all; this
build is useful either way, because it removes copy-paste from the agent loop
whether or not the in-app LLM passes survive.

## The problem

Every hop between the app and an agent is copy-paste, in both directions. The
owner copies the discovery-lead block out of a DM or `/admin/discoveries` into
the agent; the agent emits YAML; the owner pastes it back at
`/concerts/import`. Four distinct frictions come out of that, and the owner
confirmed all four (2026-08-08):

1. **The agent cannot see the catalogue.** It does not know which concerts,
   tags or venues already exist, so it re-proposes duplicates and invents tag
   names that match nothing.
2. **Feeding it leads is manual.** The lead queue is only reachable as text a
   human copies.
3. **Iterating on drafts is manual.** The agent cannot read its own committed
   draft, nor the evidence/rejection result the completion pass produced, so
   the owner relays both.
4. **The owner is in every step of the loop.**

Read surfaces DO already exist — `GET /admin/export.zip`,
`GET /concerts/{event_id}/export.yaml`, and the paste block on
`/admin/discoveries` — but all are browser downloads behind a session cookie,
so an agent cannot call them.

## What this deliberately is not

**It does not write.** Friction 4 is the one that is partly BY DESIGN: the
triage pipeline creates no concert and dismisses no lead, drafts land as
`PendingDraft` rows for a human to read, and `import_commit` remains the only
write path into `concerts`. The owner's approval is not friction that leaked
in — it is the safety property the whole AI build was constructed around.

Deferring writes is also the cheaper ORDER. Reads make the writes question
decidable: once the agent can see the catalogue, leads and drafts, the quality
of its proposals becomes observable, and THAT is what says what it is safe to
let it commit unread. Designing autonomy first means guessing. This is the same
sequencing argument that parked the AI calibration — decide with evidence, and
note that deferring costs nothing because the current behaviour is already the
conservative one.

**It does not expose delivery data.** `delivery_log` and anything naming DM
recipients stay out. `/admin/deliveries` is deliberately the ONLY surface that
names who received what (CLAUDE.md, invariant 4: a name in Discord history is a
record `POST /me/delete` cannot reach). A read API is not the place to widen
that. Scope is catalogue + leads + drafts.

## Architecture

### Module

One new router, `src/app/web/routes/api.py`, registered in `web/app.py` like
the others. Its own module for the reason `discoveries.py` and
`fetch_domains.py` are: a router registers whole, and this is an unrelated
concern.

No path-ordering hazard. The `imports`-before-`concerts` rule exists because a
literal segment competes with a template on the SAME prefix; `/api/v1` is its
own prefix and collides with nothing.

English-only and NOT wrapped in `_()`, like `/admin/deliveries` and
`/admin/discoveries`. Its consumer is a program.

### Authentication

A new nullable, unique `User.api_token_hash` (`String(64)`), mirroring
`calendar_token_hash` exactly — `secrets.token_urlsafe(32)`, only the SHA-256
stored, the raw value returned once at mint and never recoverable afterwards.
Recovery is "mint a new one", which invalidates the old.

This is not a new idea being introduced; **invariant 5 already specifies this
shape** for "any future personal-secret-link feature (the calendar feed is the
first)". Following it keeps one pattern rather than two.

Minted at `POST /me/api-token` in Preferences, beside the calendar feed, and
shown exactly once.

**Transport is a header: `Authorization: Bearer <token>`.** The calendar feed
puts its token in the URL path only because calendar clients cannot send
headers; an agent can, and a header keeps the credential out of server logs,
browser history and `Referer`.

**The token acts as the user who minted it.** A dependency resolves it to the
same `SessionUser` the cookie path produces, so `is_editor` / `is_admin` work
identically and there is no second permission model to drift from the first.
An admin's token reads leads and drafts; an editor's token does not.

### Read-only by construction

The router declares only `GET` routes, and a test asserts that no route
registered under `/api/v1` has any other method. That is what makes "read-only"
a property of the code rather than a promise in a docstring — the same shape as
the existing template and CSS sweep tests.

## Endpoints

All are `GET`. All list endpoints are paged (see below).

### Meta

`GET /api/v1/whoami` → `{discord_id, username, is_editor, is_admin}`.
The first call anyone makes when auth misbehaves; it exists to make a bad token
diagnosable in one request.

### Catalogue — any valid token

The catalogue is already public at `/discover`, so no tier is required beyond
holding a valid token.

`GET /api/v1/concerts?q=&tag=&since=&until=&limit=&offset=`
Compact rows: `event_id`, `title`, `title_en`, leg dates, venue tag handles,
attached tag handles, round count, and `next_anchor_at` (see below).
This is the endpoint that answers "do I already have this?".

Parameters, spelled out because each has a wrong reading:

- `q` reuses `concert_search_text` (`web/routes/discover.py`) VERBATIM — title
  plus its en/zh variants, plus every attached tag's name in all three locales,
  lowercased. Reusing the function rather than restating the field list is the
  point: a search that behaves differently from Discover's would be a second
  definition to drift. If it has to move to the db layer to be importable here,
  move it; do not copy it.
- `tag` filters by tag HANDLE (`Tag.slug`), not id and not name, and repeats
  for AND-ing. Invariant 3: a tag is identified by its slug, never its name,
  because names are not unique and never will be.
- `since` / `until` filter on a concert's LEG DATES (`ConcertDay.starts_at_utc`)
  — a concert matches when any live leg falls in the range. They do not filter
  on round windows or on `created_at`.

**`next_anchor_at` is CATALOGUE-LEVEL, not per-viewer.** It is the earliest
future moment among the concert's live rounds. Do NOT implement it with
`concert_next_moment` or `_needs_you`: those are per-user, consulting outcomes
and leg opt-outs, and would make an admin's token and an editor's token report
different facts about the same concert. `null` means the ladder holds no future
anchor at all — which is exactly the "quiet ladder" signal WISHLIST #2 is
about, and a useful thing for an agent to see even though that surface is not
being built here.

`GET /api/v1/concerts/{event_id}`
The same metadata plus **`draft_yaml`** — the existing `concert_export_yaml`
output verbatim. The agent reads it and can hand it straight back to
`POST /concerts/import/draft`.

**Reusing the draft vocabulary rather than inventing a concert JSON schema is
deliberate.** That format already round-trips (`concert_to_yaml` out,
`parse_draft` back, pinned by a test), the `add-concert` skill already writes
it, and CLAUDE.md records that splitting a format's serializer from its parser
is exactly how the catalogue round-trip hole opened. A second concert schema
would be a second thing to keep in sync.

`GET /api/v1/tags?kind=&limit=&offset=`
`current_tag_exports()` as JSON: handle, `name`/`name_en`/`name_zh`, kind,
parent handle, `voiced_by` handle, member handles, region, city,
`eventernote_url`. This is what stops the agent inventing tag names that match
nothing — it is the vocabulary, served.

### Leads — admin

`GET /api/v1/leads?limit=&offset=`
`open_leads()` as JSON: id, `source`, `source_event_id`, `title`, `event_date`,
`date_is_deadline`, `venue`, the tag it was first seen via, `announced_at`,
plus the same-date-same-venue collision hint `/admin/discoveries` computes.

`date_is_deadline` must be carried: the imas feed's DTSTART is an application
deadline, and an agent treating it as a performance date would file the wrong
thing. It is the same reason the page renders such rows as `申込締切 {date}`.

### Drafts — scoped to the token's own user

`GET /api/v1/drafts?limit=&offset=`
`pending_drafts(user_id)`: id, title, created/committed/discarded state,
whether rounds are present, whether `completion_yaml` exists.

`GET /api/v1/drafts/{id}`
Full `draft_text` **and** `completion_yaml`. This is the iteration loop: the
agent reads its own draft and the evidence/rejection result together, with the
owner relaying neither.

Another user's draft answers **404, not 403** — invariant 5's existing rule for
ownership checks on presets and subscriptions.

## Paging

Every list endpoint takes `limit` (default 200, **maximum 500**) and `offset`
(default 0), and returns an envelope:

```json
{ "items": [...], "total": 47, "limit": 200, "offset": 0 }
```

`total` costs one extra `COUNT(*)` per call — free at this scale — and is what
lets a caller know when to stop instead of paging until it receives a short
page.

**Requirement, not a nicety: every paged query must have a TOTALLY ORDERED
sort, with `id` as the final tiebreaker.** Offset paging over a non-unique sort
key is broken even when nothing is being inserted, because SQLite may order
ties differently between the two queries — a row then repeats on page 2 while
another vanishes. `ORDER BY event_date DESC, id DESC`, never
`ORDER BY event_date DESC` alone. `open_leads` already satisfies this; the
other three list queries must be checked and fixed where they do not.

The owner asked for offset paging explicitly (2026-08-08) after the trade was
laid out: at current volumes (dozens of concerts, tens of leads, ~100 tags) a
capped `limit` alone would have sufficed, and `offset` is insurance against
growth rather than a present need.

## Times

**The JSON envelope is UTC; the embedded `draft_yaml` is JST.**

This is not sloppiness, and it is written down because invariant 1 exists to
prevent exactly this class of confusion. The DB stores aware UTC and the API
reports it as ISO-8601 with `Z`. The draft format is the AUTHORING vocabulary,
whose timestamps are JST because that is how the editor forms take input, and
it must stay byte-compatible with `parse_draft`.

Two representations in one response is a real footgun, so it is mitigated by
naming rather than hidden: the field is `draft_yaml` — a document, not parsed
data — and every endpoint's documentation states the rule. Plain date fields
(`event_date`, leg dates) are dates, not instants, and carry no zone, matching
`fmt_day_month`'s reasoning that a performance date is a fact about the world.

## Errors

Every `/api/v1/*` response is JSON, including failures:

| status | meaning |
|---|---|
| 401 | no token, malformed header, or unknown token |
| 403 | valid token whose user lacks the tier (e.g. non-admin on `/leads`) |
| 404 | unknown `event_id`, unknown draft id, or another user's draft |
| 422 | bad query parameter (`limit` over 500 or below 1, negative `offset`, unparseable `since`/`until`) |

`limit` over the cap is a 422 rather than a silent clamp: an agent that asked
for 5000 and received 500 without being told would page wrongly, believing it
had the whole set.

This needs an explicit carve-out rather than inheritance: `web/app.py`'s error
handlers return HTML for anything that looks like a navigation, and an agent's
request looks exactly like one. `tests/test_error_pages.py` already pins that
the tag-quick 409s keep their JSON body, so this extends an existing precedent
instead of introducing a mechanism.

401 must not leak whether a token exists — an unknown token and a malformed
header answer identically.

## Migration

One column, `users.api_token_hash`, nullable and unique. Standard batch
migration; it adds a column and touches no existing constraint, so it needs
neither the `naming_convention` reflection workaround nor a legacy-DDL fixture
(those bind migrations that call `drop_constraint`). Remember the two
post-autogenerate edits CLAUDE.md requires: replace
`app.db.models.UTCDateTime()` with `sa.DateTime()` and drop the
`import app.db.models` line — not applicable to a `String(64)` column here, but
check the generated file regardless.

Deploy is the ordinary ritual: `git pull && uv sync && alembic upgrade head &&
systemctl restart`. No reversed order is needed — that rule binds column DROPS.

## Testing

Following this codebase's conventions:

- **One authenticated GET test per endpoint.** CLAUDE.md requires a render test
  per page because a missing one shipped a 500 once; the same reasoning applies
  per endpoint.
- **An auth matrix per endpoint**: no token → 401; malformed header → 401;
  unknown token → 401; valid non-admin token → 403 on `/leads`; valid admin →
  200. The tier boundary is the single most likely thing to be got wrong.
- **A read-only sweep**: no route under `/api/v1` may declare a method other
  than GET.
- **Paging correctness**: seed enough rows to span two pages, then assert the
  UNION of the pages equals the whole set with no repeats and no omissions.
  That assertion is what catches a missing `id` tiebreaker; merely checking
  that `limit=N` returns N rows does not.
- **Draft scoping**: another user's draft answers 404, not 403, and does not
  appear in the list.
- **Token lifecycle**: mint stores only the hash, the raw value is never
  persisted, and a second mint invalidates the first.
- **Errors stay JSON**, including 401/403/404, and specifically for a request
  carrying browser-like `Accept` headers.
- Tests use `tests/conftest.py`'s shared `db` / `session` fixtures; no new
  fixture.

## Follow-ups, recorded not scheduled

- **Agent writes.** Its own design, gated on evidence from this one.
- **An MCP server.** Approach B from the 2026-08-08 discussion, and explicitly
  NOT foreclosed: an MCP server that calls this API is a small later addition
  if the HTTP ergonomics inside Claude Code prove annoying. It was rejected as
  the FIRST build because it is this plus a wrapper, deployable separately, and
  usable only from an MCP client.
- **The `add-concert` / `triage-leads` skills** should learn to use these
  endpoints. Out of scope here; the API ships first and the skills follow, so
  the paste path keeps working throughout.
