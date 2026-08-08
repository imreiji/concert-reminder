# Agent read API (`/api/v1`)

A read-only, bearer-token-authenticated JSON API over the catalogue, the
discovery lead queue and your own pending-import drafts. It exists so an
agent can see what the app already knows — concerts, tags, venues, open
leads, its own draft/completion pairs — without the owner copy-pasting any
of it by hand.

**It has no write endpoints, and that is by construction, not convention.**
Every route in `src/app/web/routes/api.py` is a `GET`, and a test
(`tests/test_api_auth.py::test_every_api_route_is_read_only`) sweeps the
routing table to fail if anything else ever gets registered under `/api/v1`.
Creating a concert still goes exclusively through
`POST /concerts/import/draft` → `/concerts/import/pending/{id}/commit`, the
same as it does for a human pasting YAML.

For the full design rationale (why reads shipped before writes, why
`delivery_log` is excluded, why paging is offset-based) see
`docs/superpowers/specs/2026-08-08-agent-read-api-design.md`. This page is
the how-to.

## Minting a token

1. Sign in and go to **Preferences**.
2. In the **API access** section, click **Generate API token**.
3. The page redirects back to a clean `/preferences` and shows the raw token
   **exactly once**, in a box with a copy button.

Only its SHA-256 hash is stored — the raw value is never persisted anywhere
retrievable, so if you navigate away (or reload) without copying it, it is
gone for good. **Recovery is "mint a new one"**, via the same button (now
labelled "Generate a new token"). Minting again immediately invalidates the
previous token; there is no way to have two live tokens for one account at
once.

The token is deliberately never put in a URL — unlike the personal calendar
feed, which has to be (calendar clients poll a URL with no cookies). Putting
this one in a URL would leak it into Caddy/Cloudflare access logs and browser
history for no reason, since an agent can send a header.

## Authenticating requests

Send the raw token as a bearer token:

```
Authorization: Bearer <token>
```

Example:

```bash
curl -s https://dekimasen.app/api/v1/whoami \
  -H "Authorization: Bearer $DEKIMASEN_API_TOKEN"
```

```json
{
  "discord_id": 4242,
  "username": "reiji",
  "is_editor": true,
  "is_admin": true
}
```

`GET /api/v1/whoami` is the first call to make when something isn't working —
it turns "my token doesn't seem to work" into one request that says exactly
which account (if any) it resolved to.

**The token acts as the account that minted it.** Every endpoint applies the
same `is_editor`/`is_admin` logic the website itself uses for that account —
there is no separate permission model for the API. An admin's token can read
`/leads`; an editor-only or plain-user token gets `403` there.

## Endpoints

All paths are prefixed `/api/v1`. All are `GET`. All list endpoints return
the [paging envelope](#paging) described below.

### `GET /api/v1/whoami`

**Tier:** any valid token.

No parameters. See the example above.

### `GET /api/v1/concerts`

**Tier:** any valid token — the catalogue is already public at `/discover`,
so holding any token is enough.

The question this answers is "do I already have this concert?" — check it
before drafting a new one.

| param | meaning |
|---|---|
| `q` | free-text search: title (ja/en/zh) plus every attached tag's name in all three locales, case-insensitive. Same matcher `/discover`'s search box uses. |
| `tag` | filter by tag **handle** (`Tag.slug`), never by name — names are not unique. Repeatable; repeats AND together. |
| `since`, `until` | plain dates (`YYYY-MM-DD`, no timezone). A concert matches when **at least one** of its live (non-cancelled) legs falls within `[since, until]` — a tour with a March leg and a December leg does not match a `since=2026-09-01&until=2026-09-30` query just because one leg clears each bound separately. |
| `limit`, `offset` | see [paging](#paging). |

A cancelled-out concert (every leg cancelled) never appears in this list —
it fails the same "discoverable" check `/discover` applies. It is still
reachable at its own `GET /api/v1/concerts/{event_id}`.

Example — `curl -s .../api/v1/concerts?q=sunshine -H "Authorization: Bearer $TOKEN"`:

```json
{
  "items": [
    {
      "event_id": "ll-sunshine-2026",
      "title": "ラブライブ！サンシャイン!! ライブ",
      "title_en": "Love Live! Sunshine!! Live",
      "leg_dates": ["2026-09-01"],
      "tag_handles": ["lovelive", "nakano-sun-plaza"],
      "venue_handles": ["nakano-sun-plaza"],
      "round_count": 1,
      "next_anchor_at": "2026-08-20T00:00:00+00:00"
    }
  ],
  "total": 1,
  "limit": 200,
  "offset": 0
}
```

`leg_dates` and `venue_handles` cover only **live** (non-cancelled) legs.
`tag_handles` is every tag attached to the concert (franchise/artist/venue/
group/character), sorted; `venue_handles` is the subset of those that are
VENUE tags, derived from the legs.

`next_anchor_at` is the earliest **future** moment (open/close/results/
payment-deadline) among the concert's still-live rounds, or `null` if none
remains. **It is catalogue-level, not per-viewer** — it does not consult
your outcomes or leg opt-outs, so two different tokens always see the same
value for the same concert. Don't read it as "your next deadline"; that's a
different, per-user question this API doesn't answer.

### `GET /api/v1/concerts/{event_id}`

**Tier:** any valid token.

Same fields as a list row, plus `draft_yaml`: the concert's full export in
the same YAML vocabulary `add-concert` writes and
`POST /concerts/import/draft` reads back — trilingual titles, per-leg venue
handles, all four round anchors, tag handles. Round-trip it directly.

**`draft_yaml`'s timestamps are JST, not UTC** — see [Times](#times) below,
this is the one field in the whole API where that's true.

Unlike the list endpoint, this one has no "is it discoverable" filter, so a
concert whose every leg is cancelled is still reachable here (its
`next_anchor_at` will be `null` and `leg_dates` empty).

Example — `curl -s .../api/v1/concerts/ll-sunshine-2026 -H "Authorization: Bearer $TOKEN"`:

```json
{
  "event_id": "ll-sunshine-2026",
  "title": "ラブライブ！サンシャイン!! ライブ",
  "title_en": "Love Live! Sunshine!! Live",
  "leg_dates": ["2026-09-01"],
  "tag_handles": ["lovelive", "nakano-sun-plaza"],
  "venue_handles": ["nakano-sun-plaza"],
  "round_count": 1,
  "next_anchor_at": "2026-08-20T00:00:00+00:00",
  "draft_yaml": "event_id: ll-sunshine-2026\ntitle: ラブライブ！サンシャイン!! ライブ\ntitle_en: Love Live! Sunshine!! Live\ntitle_zh: null\nkind: null\norganizer: Sunrise\ncategories: null\nseries:\n  franchises:\n  - ラブライブ！\n  groups: []\n  characters: []\n  artists: []\nseries_handles:\n  franchises:\n  - lovelive\n  groups: []\n  characters: []\n  artists: []\nvenues:\n- 中野サンプラザ\nperformers: []\neventernote_url: null\nofficial_url: https://example.com\nsource_url: null\nperformances:\n- label: Day 1\n  label_en: null\n  label_zh: null\n  city: Tokyo\n  venue: 中野サンプラザ\n  venue_address: null\n  venue_handle: nakano-sun-plaza\n  doors_jst: null\n  starts_at_jst: 2026-09-01 21:00\nrounds:\n- label: 1次先行\n  label_en: null\n  label_zh: null\n  kind: lottery_round\n  applies_to:\n  - Day 1\n  apply_opens_jst: 2026-08-01 09:00\n  apply_closes_jst: 2026-08-20 09:00\n  results_jst: 2026-08-25 09:00\n  payment_deadline_jst: null\n  url: null\n  notes: null\n  requires: null\nnotes: null\nnotes_en: null\nnotes_zh: null\n"
}
```

An unknown `event_id` answers `404`.

### `GET /api/v1/tags`

**Tier:** any valid token.

The tag vocabulary — every franchise, artist, venue, group and character tag
— so an agent stops proposing tag names that match nothing. Built from the
same `current_tag_exports()` the `export.zip` catalogue backup and the
tags-import differ both use.

| param | meaning |
|---|---|
| `kind` | filter by exact kind: one of `franchise`, `artist`, `venue`, `group`, `character`. |
| `limit`, `offset` | see [paging](#paging). |

Example — `curl -s .../api/v1/tags?kind=venue -H "Authorization: Bearer $TOKEN"`:

```json
{
  "items": [
    {
      "handle": "nakano-sun-plaza",
      "name": "中野サンプラザ",
      "name_en": "Nakano Sun Plaza",
      "name_zh": null,
      "kind": "venue",
      "parent": null,
      "voiced_by": null,
      "members": [],
      "region": "kanto",
      "city": "Tokyo",
      "city_en": "Tokyo",
      "city_zh": null,
      "address": null,
      "location_url": null,
      "eventernote_url": null
    }
  ],
  "total": 1,
  "limit": 200,
  "offset": 0
}
```

`handle` is the tag's `slug` — the only unique identifier a tag has (invariant
3: names are never unique). Use it, not `name`, everywhere a tag is
referenced — including the `tag` filter on `/concerts` and the
`series_handles`/`venue_handle` fields of a `draft_yaml`. `parent` and
`voiced_by` are themselves handles of other tags (or `null`); `members` is a
GROUP's member handles.

### `GET /api/v1/leads` — admin only

**Tier:** admin. A non-admin token (even an editor's) gets `403`.

The open Eventernote/calendar discovery queue — the same set
`/admin/discoveries` shows, and the same one `open_leads()` computes: not
dismissed, not yet bound to a concert.

| param | meaning |
|---|---|
| `limit`, `offset` | see [paging](#paging). |

Example — `curl -s .../api/v1/leads -H "Authorization: Bearer $ADMIN_TOKEN"`:

```json
{
  "items": [
    {
      "id": 1,
      "source": "eventernote",
      "source_event_id": "12345",
      "title": "Love Live! Sunshine!! Live",
      "event_date": "2026-10-12",
      "date_is_deadline": false,
      "venue": "Zepp Haneda",
      "first_seen_via_tag_id": 2,
      "first_seen_at": "2026-08-01T00:00:00+00:00",
      "announced_at": "2026-08-01T00:00:00+00:00"
    }
  ],
  "total": 1,
  "limit": 200,
  "offset": 0
}
```

**`event_date` is a plain date and `date_is_deadline` says what it means.**
Most sources are performance dates, but a lead sourced from the imas feed
carries an *application deadline* in that same field
(`date_is_deadline: true`) — treat it as a 申込締切, not a show date, or
you'll file the wrong thing. `source` is `"eventernote"` or a calendar feed
key; `source_event_id` is that source's own id (namespaced as
`"<feed key>:<UID>"` for calendar leads, bare for Eventernote).
`first_seen_via_tag_id` is a raw internal tag **id** (not a handle) — the
tag whose `eventernote_url`/calendar feed first surfaced this lead. Note
that `GET /api/v1/tags` rows do not carry their internal id (only their
handle), so there is currently no way to resolve this id back to a handle
through this API alone; treat it as an opaque reference for now.

**This endpoint does NOT carry the same-date-same-venue collision hint**
that `/admin/discoveries` shows on each row (e.g. "might be the same show as
an existing leg"). That hint is a second query per row and was left out
deliberately rather than half-built; don't assume it's here just because the
web page has it.

### `GET /api/v1/drafts` — your own drafts only

**Tier:** any valid token, scoped to the token's own user.

Your own open `PendingDraft` rows — never another user's, whether that user
is an admin or not.

| param | meaning |
|---|---|
| `limit`, `offset` | see [paging](#paging). |

Example — `curl -s .../api/v1/drafts -H "Authorization: Bearer $TOKEN"`:

```json
{
  "items": [
    {
      "id": 1,
      "title": "Sample Draft",
      "created_at": "2026-08-08T09:55:47.655253+00:00",
      "has_rounds": false,
      "has_completion": false
    }
  ],
  "total": 1,
  "limit": 200,
  "offset": 0
}
```

**`has_rounds` comes from actually parsing the draft's YAML** (through the
same `parse_draft` the import route uses), not from a text search — and it
is `false`, not an error, for a draft whose text no longer parses at all.
It only becomes `true` once the draft's `rounds:` list is non-empty, so a
freshly-authored skeleton (no `rounds:` key yet) reads `false` exactly like
one with `rounds: []`. `has_completion` is just whether
`completion_yaml` is non-empty for that row.

### `GET /api/v1/drafts/{id}`

**Tier:** any valid token, scoped to the token's own user.

The full text of one of your own drafts, plus its completion evidence (if
the AI completion pass has run on it) — this is the pairing an agent needs
to iterate on a draft without the owner relaying either half by hand.

Example — `curl -s .../api/v1/drafts/1 -H "Authorization: Bearer $TOKEN"`:

```json
{
  "id": 1,
  "title": "Sample Draft",
  "created_at": "2026-08-08T09:55:47.655253+00:00",
  "committed_at": null,
  "discarded_at": null,
  "draft_text": "title: Sample Draft\nrounds: []\n",
  "completion_yaml": ""
}
```

`completion_yaml` is `""` until the completion pass has run on this draft;
once it has, it holds the evidence/rejection YAML the pass produced —
separate from `draft_text` because it is documentation *about* the draft, not
part of it.

**Another user's draft ID answers `404`, not `403`** — same as everywhere
else in this app that checks ownership (invariant 5). A `403` would confirm
the row exists at all; `404` says nothing either way. It also never appears
in your own `/api/v1/drafts` list.

## Paging

Every list endpoint (`/concerts`, `/tags`, `/leads`, `/drafts`) returns the
same envelope:

```json
{ "items": [ ... ], "total": 47, "limit": 200, "offset": 0 }
```

- `limit` — default **200**, maximum **500**.
- `offset` — default **0**.
- `total` — the count *before* `limit`/`offset` are applied, so you know
  when to stop paging instead of paging until you get a short page.

**Asking for `limit` above 500 is a `422`, not a silent clamp to 500.** A
client that asked for 5000 and silently got 500 back with no signal would
wrongly conclude it had read everything; asking again with a smaller `limit`
is the only path.

Every list is totally ordered (with a unique column as the final tiebreaker,
never a bare "closest date" sort), so repeated paging through a stable
dataset returns each row exactly once — no duplicates, no gaps — even across
ties.

## Times

**The JSON envelope is UTC. The embedded `draft_yaml` string is JST. These
are not the same rule and mixing them up will silently misplace a deadline
by up to nine hours.**

- Every timestamp field in the JSON itself (`next_anchor_at`,
  `first_seen_at`, `announced_at`, `created_at`, `committed_at`,
  `discarded_at`, ...) is an aware UTC instant, ISO-8601 with a `+00:00`/`Z`
  offset — read straight out of the database, which stores UTC only.
- The `draft_yaml` field on `GET /api/v1/concerts/{event_id}` is different:
  it is the **authoring** format (the same YAML the edit forms and the
  `add-concert` skill produce), and every timestamp inside it —
  `starts_at_jst`, `apply_opens_jst`, `apply_closes_jst`, `results_jst`,
  `payment_deadline_jst` — is **JST, with no UTC offset in the string at
  all**. That's on purpose: it has to stay byte-compatible with what
  `POST /concerts/import/draft` parses back in, and that parser expects JST
  wall-clock times because that's what the editor forms take as input.
- Plain **dates** (`leg_dates`, a lead's `event_date`) carry no zone at all,
  because they're facts about the world (a show happens on a given day), not
  instants to act by — same reasoning the website's own date rendering
  follows.

If you're writing code against this API: never parse `draft_yaml`'s
timestamps as UTC, and never treat a value from inside `draft_yaml` as
directly comparable to a UTC field sitting next to it in the same response.

## Errors

Every response under `/api/v1/*` is JSON, including failures — this holds
even for a request that sends browser-like `Accept: text/html` headers,
which everywhere else in this app gets a styled HTML error page instead.

| status | meaning |
|---|---|
| 401 | no `Authorization` header, a non-`Bearer` scheme, or a token that doesn't match any account. All three answer with the *same* body — an unknown token and a malformed header are indistinguishable, so a prober can't learn whether a given token exists. |
| 403 | the token is valid but its account lacks the tier the endpoint needs (e.g. a non-admin token on `/leads`). |
| 404 | unknown `event_id`, unknown draft id, or a draft id that belongs to a different user. |
| 422 | a bad query parameter — `limit` outside 1-500, a negative `offset`, or an unparseable `since`/`until`/date. |

Error bodies are `{"detail": "..."}`, e.g.:

```json
{ "detail": "invalid or missing API token" }
```
```json
{ "detail": "limit must be between 1 and 500" }
```
```json
{ "detail": "no such concert" }
```
```json
{ "detail": "admin only" }
```

## What this API deliberately does not do

- **It never writes anything.** Creating or editing a concert still goes
  exclusively through `POST /concerts/import/draft` (paste YAML — the same
  `draft_yaml` this API hands you round-trips there) followed by
  `/concerts/import/pending/{id}/commit`. There is no `POST`/`PUT`/`PATCH`/
  `DELETE` anywhere under `/api/v1`.
- **It never exposes who received a DM.** `delivery_log` and anything else
  naming a notification's recipients is out of scope entirely —
  `/admin/deliveries` stays the only surface that names recipients.
