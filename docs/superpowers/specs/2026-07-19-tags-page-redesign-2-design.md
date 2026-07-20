# Tags page redesign 2

Date: 2026-07-19

Branch 3 of the UI/UX refactor. Branch 1 (Home / Discover split) and branch 2 (concert page +
editor) shipped. Distinct from `2026-07-18-tags-page-redesign-design.md`, which built the current
page (search, hierarchy, dialogs, rename, retroactive-apply); this branch restyles that page to
the refactor's visual system and fixes what shipping branches 1–2 exposed.

## Problem

### A chip carries no idea of what it costs to change it

`tags.html` renders every tag as a bare name chip. A franchise on 64 concerts looks identical to
an artist on zero — and since following a tag auto-subscribes you to every future concert
carrying it, renaming or deleting the wrong one is not a small mistake. Nothing on the page says
which tags are even in use.

### Venues ignore the thing that makes them useful

Discover filters venues **by region** (`region_sidebar_links`, `routes/discover.py:82`), and a
venue with no region silently falls into the "Other" bucket and out of area filtering. But the
Tags page shows venues as one flat chip list: you cannot see which venues are missing a region,
and the new-tag form's region field carries no hint of why it matters.

### The new-tag form shows every field to every kind

The current `<details>` form (`tags.html:9-33`) shows name, kind, parent, location URL, and
region at once, hiding only the parent select for non-groups. A franchise needs a name and
nothing else; a venue needs a region; showing all fields and letting the editor guess which
apply is how you get venues with no region.

### Duplicate names are a blunt 409

`POST /tags` rejects any same-name tag with a bare 409 (`routes/tags.py:86`), naming neither the
existing tag's kind nor what it touches. The editor learns nothing and cannot proceed even when a
second same-name tag is genuinely wanted.

### Performer chips can't link out

Branch 2 deliberately left the concert page's performer chips as plain spans: linking them to
eventernote needs an `eventernote_url` on ARTIST/GROUP tags, deferred to this branch. VENUE tags
already carry the equivalent (`location_url`, `models.py:218`).

## Approach

Port the **Tags** view of the interactive concept at
`https://claude.ai/code/artifact/ea939428-b99e-43e7-8664-fa276431baba` — including its new-tag
dialog ("+ New tag") and per-chip edit dialog. Port the structure; do not redesign it.

One schema change: `eventernote_url` on tags, mirroring `location_url`. Everything else runs on
existing data and existing routes.

## Scope — page structure

**Header.** `Tags` plus a summary note — *"**N concerts** across X franchises, Y groups,
Z performers, W venues"* — and an editor-only `+ New tag` button (right-aligned) opening the
new-tag dialog. The button replaces the current inline `<details>` form. Below the header, one
line of context: *"Following a tag subscribes you to every future concert carrying it, so counts
here are what a rename or delete actually touches."*

**Search** stays exactly as shipped: an input calling `filterChips(this, '.tags-page')`, chips
carrying `data-name="{{ t.name | lower }}"`. That is `data-name`'s one sanctioned job on this
page — the filter hook — and why nothing else may use it (see Constraints).

**Section 1 — "Franchises and groups"** (*"click any tag to edit"*). One family block per
franchise: the franchise chip on its own head row, then one indented row per child group —
group chip left, that group's member chips right (concept classes `.fam` / `.famhead` /
`.grow2` / `.memb`). A group with no members shows *"no members yet"* dimmed. Groups with no
parent franchise render as a trailing family in this same section, headed **"No franchise"**
(replaces today's separate "Other groups" section).

**Section 2 — "Venues"** (*"grouped by the region used in the Discover sidebar"*). One row per
region — region name as an eyebrow label, that region's venue chips beside it — sorted
alphabetically with a **"No region"** bucket last. Deliberate label difference: Discover's
fallback bucket is called "Other" (`discover.py:95`) because there it is a filter; here it is
called "No region" because it is a to-do list — every chip in it is a venue an editor should
open and fix. Discover is not touched.

**Section 3 — "Performers with no group"** (*"solo artists, or members not yet attached"*).
Replaces the "Solo artists" section; same population — ARTIST tags that are no group's member.

## Scope — chips

Every franchise, group, venue, and solo-performer chip carries its **concert count** as a small
trailing number (concept `.tchip` with `.n2`). Kind tints via `k-franchise` / `k-group` /
`k-venue` classes. A tag on **zero concerts** additionally gets `.unused`: dashed border, faded
— visibly present, visibly dead weight.

Member chips inside a group row show the name only, no count — the row must stay scannable at
nine members, and the count lives in that member's own dialog.

Counts come from one new service function (see Data), not N+1 queries in the template.

## Scope — per-tag edit dialog

One server-rendered `<dialog>` per tag, editor-only, exactly as the current page does it — the
concept's single JS-populated dialog is a prototype convenience, not the mechanism. Opening
stays `showModal()` from the chip; forms keep posting to the existing routes.

**Head:** tag name, a quiet kind pill (`franchise` / `group` / `performer` / `venue`), close ×.

**Usage strip** (concept `.usage`), before any form field:

| stat | shown for | meaning |
|---|---|---|
| Concerts | all kinds | count of `ConcertTag` rows |
| Followers | all kinds | count of `TagSubscription` rows |
| Members | groups only | count of `TagMember` rows |
| Upcoming | all kinds | concerts carrying the tag with ≥1 non-cancelled day not yet past — the same "active" reading as `active_concerts_missing_member` (`service.py:1770`) |

**Form fields by kind** (posting to `POST /tags/{id}/edit`):

- all kinds: Name
- group, performer: eventernote URL (the new column)
- venue: Location link, Region — Region as a text input with a `<datalist>` of every region
  already in use, so spelling converges without hard-coding a region vocabulary

**Groups additionally get:**

- the member list as chips with a remove ×, and the add-member control — existing routes
  (`POST /tags/{id}/members`, `POST /tags/{gid}/members/{mid}/delete`) unchanged, including the
  add-member redirect into the retroactive-apply confirmation when concerts are eligible.
- the **"Apply to existing concerts"** box (concept `.upgradebox`), stating the invariant in
  editor language: *"Adding a member does not touch concerts that already carry this group —
  expansion is materialised at the time you attach it. Use this to push a new member onto active
  events."* Below the text, one link **per member with eligible concerts** — *"Apply {member} to
  N upcoming concerts"* — going to the existing
  `GET /tags/{gid}/members/{mid}/retroactive-apply` confirmation page. Members with nothing
  eligible show no link; a group with no eligible pairs shows the explanation only. This is
  invariant 3 held exactly: expansion stays materialised-at-attach; pushing a member onto
  existing concerts remains an explicit, editor-confirmed action through the already-shipped
  confirmation flow — never automatic, never a new write path.

**Footer:** Save, Cancel, and a right-separated Delete tag. Delete keeps the confirm via
`data-tag-name` + `confirmDeleteTag` (`base.html:73`) — the name reaches JS through `dataset`,
never interpolated into the `onsubmit` attribute.

## Scope — new-tag dialog

One dialog, editor-only, posting to the existing `POST /tags`.

**Kind picker** — a pill row: Franchise / Group / Performer / Venue (UI says "Performer"; the
submitted value stays `artist`). Under it, a one-line hint that changes with the kind:

- franchise: *"A franchise is the top level. Groups hang off it."*
- group: *"A group contains performers. Attaching it to a concert adds its members."*
- performer: *"A single performer. Can belong to a group, or stand alone."*
- venue: *"A place. Discover filters venues by region, not individually."*

**Fields are conditional on kind:**

| kind | fields beyond Name |
|---|---|
| franchise | none — an inline note: *"A franchise is just a parent for groups — nothing else to set."* |
| group | Parent franchise select (with "— none —") · eventernote URL (optional) |
| performer | eventernote URL (optional) |
| venue | Region (input + datalist, as in the edit dialog) · Map or venue URL (optional) |

Region carries its consequence inline: *"Discover filters venues by region. Without one, this
venue lands in 'No region' here and in Discover's 'Other' bucket, outside region filtering."*

Hidden fields are `disabled` while hidden so a kind switch cannot submit a stale value (today's
form submits `location_url`/`region` for every kind). With JS unavailable, all fields render
visible and the form still submits — same degradation posture as the rest of the app.

## Scope — duplicate detection: warn, don't block

**Client:** the dialog's inline script receives a list of
`{name, kind, concerts, followers}` for every existing tag — passed as a raw Python object
rendered with `| tojson`, never `| safe`, never pre-serialized (invariant 7: `tojson` must do
the serializing itself to escape `<`/`>`/`&` inside the `<script>` block). As the editor types,
a case-insensitive same-kind name match shows a warning box (concept `.dupe`):

> **Already exists.** A {kind} tag with this name is on {N} concerts with {M} followers.
> Adding a second one splits them — there is no merge yet.

The Create button stays enabled. Different-kind matches do not warn — "Aqours" the venue next to
"Aqours" the group is odd but not a split.

**Server:** `POST /tags` **drops** its blanket 409 (`routes/tags.py:86-87`). A warning whose
Create button leads to a 409 is a block wearing a warning's clothes. Duplicate names have no DB
uniqueness constraint — the 409 was purely app-level — and `find_tag_by_name`'s only other
caller is the rename path. **Rename keeps its 409**: `POST /tags/{id}/edit` colliding with an
existing name is near-certainly a typo, and the edit dialog offers no warning affordance;
"generate the duplicate deliberately at creation" remains possible via the new-tag dialog.
Existing test `test_duplicate_tag_names_rejected_case_insensitively` (`tests/test_tags.py:154`)
inverts to assert the create now succeeds.

## Scope — the schema change: `eventernote_url`

`Tag.eventernote_url: Mapped[str | None] = mapped_column(String(500))` — commented as
ARTIST/GROUP-specific and harmless if unset on other kinds, exactly mirroring `location_url`'s
comment (`models.py:215-218`).

**Migration** — this repo's SQLite rules apply in full:

- `uv run alembic revision --autogenerate -m "tag eventernote url"`, then **hand-review**: the
  metadata NAMING_CONVENTION stays untouched; if the revision emits
  `app.db.models.UTCDateTime()` anywhere, replace it with `sa.DateTime()` and delete the
  `import app.db.models` line (this revision should contain neither, but the review step is not
  optional); the file stays ASCII-only; the change runs in batch (table-rebuild) mode as a
  `batch_alter_table` `add_column`, the same shape as
  `96e348a6310c_venue_tag_region_and_location_url.py`.
- `uv run alembic upgrade head` after review.

**Boundary:** `eventernote_url` is editor-supplied, so it passes through `form_url`
(`web/forms.py`) in both `create_tag` and `edit_tag` — a `javascript:` value is a 422, same as
`location_url` today (invariant 7).

**Consumer:** the concert page's performers panel (branch 2's `concert_detail.html`) renders a
performer or group chip as an `<a>` to its `eventernote_url` when set, and as a plain span when
not — a chip without a URL is not a dead link. Stored values are scheme-validated at entry;
Jinja autoescaping covers the attribute.

## Out of scope

- **Tag merging** — owner said no; the duplicate warning says so in as many words.
- **Discover** — its sidebar, its "Other" bucket label, its facets: untouched.
- **Following toggle / `ConcertSubscription`** — branch 4.
- **Upgrade rounds** — branch 5.
- Preferences, onboarding, bot commands, and how concert tiles display tags.
- Any change to group-expansion semantics (invariant 3) — this branch only re-surfaces the
  existing explicit retroactive-apply flow.

## Constraints

- Every editor-supplied URL (`eventernote_url`, `location_url`) through `form_url` at the route
  boundary; the bot layer, if it ever renders these, uses `clean_url` via `safe_button_url`,
  never `form_url`.
- Tag names and counts reaching the dialog's inline `<script>` go through `| tojson` on a raw
  Python object — never `| safe`, never `json.dumps` first.
- No user-controlled text interpolated into inline `on*` handlers; names cross into JS via
  `data-` attributes read through `dataset`. `data-tag-name` for the delete confirm — **not**
  `data-name`, which `filterChips()` in `base.html` claims for live filtering.
- Business logic in `db/service.py`; the route assembles context; `src/app/domain/` stays pure.
- Sentence case everywhere; chips remain the universal element; dialogs stay native `<dialog>`
  white cards (header + × / body; backdrop-click and Esc close).
- Baseline: **638 passed, 1 failed**. The failure is
  `tests/test_crud.py::test_test_dm_when_bot_disabled` — pre-existing, local-only (repo-root
  `.env` sets a real `DISCORD_TOKEN`), CI green. **Out of scope — do not touch it.**
- Every page keeps at least one logged-in GET render test; the migration follows the SQLite
  rules quoted above.

## Testing

- Chips render their concert count; a zero-concert tag carries `unused`; member chips inside a
  group row carry no count.
- Venues group by region with "No region" last; a region-less venue appears in it.
- Parentless groups render under "No franchise"; ungrouped artists under "Performers with no
  group".
- The usage strip's four counts are each computed correctly (concerts, followers, members,
  upcoming), with "upcoming" excluding a concert whose only future day is cancelled.
- The apply-to-existing link appears only for a member with eligible concerts, names the right
  N, and targets the existing retroactive-apply confirmation route; the invariant text renders.
- New-tag dialog: per-kind fields present/absent as specified; hidden fields disabled; region
  note renders; the datalist lists existing regions.
- Duplicate warning: the `| tojson` payload contains name/kind/concerts/followers and is not
  double-encoded (a name containing `</script>` stays inert); create of a same-name tag now
  succeeds (inverting `test_duplicate_tag_names_rejected_case_insensitively`); rename to a
  colliding name still 409s.
- `eventernote_url` round-trips through create and edit; a `javascript:` value 422s on both.
- Concert page: a performer chip with `eventernote_url` renders as a link, one without renders
  as a span; the group chip likewise.
- Logged-in GET render tests for `/tags` (editor and non-editor — the non-editor sees no
  dialogs, no + New tag) still pass.

## Verification

Drive it (`uv run python -m app.main`, blank `DISCORD_TOKEN`): open `/tags`, confirm counts on
chips and a dashed unused tag; open a venue with no region from the "No region" bucket, set its
region, watch it move buckets; open a group, add a member, land on the retroactive-apply
confirmation, apply, and confirm the member appears on the affected concert; create a tag whose
name matches an existing group and confirm the warning names the counts but Create still works;
give a performer an eventernote URL and confirm their chip on a concert page became a link.
