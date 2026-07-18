# Tags page redesign design

## Context

The Tags page (`tags.html`/`routes/tags.py`) is the one page in this app that
manages franchise/group/artist/venue tags, and the owner flagged it as
"clumsy" — with the tag list (artists especially) expected to grow far
faster than concerts/rounds ever will. This spec was the *original* ask in
a longer brainstorm that also surfaced two other things, both handled as
separate work before this one:

- The retroactive-apply feature described in this spec (Section 4) needed a
  real "is this concert still active" signal instead of ad-hoc date math.
  That became its own spec/plan/implementation: the `ConcertDay.cancelled`
  flag, shipped as PR #21 (`docs/superpowers/specs/2026-07-17-cancelled-leg-status-design.md`).
  This spec assumes that flag exists and builds directly on it.
- A bigger index-page reorganization (open-and-upcoming tiles + a
  chronological deadline list) was also raised and deferred to its own
  future spec — unrelated to tags, not part of this work.

## Current state (read fresh before writing this spec)

- `tags.html` renders one flat `<ul class="rows">` per kind (Franchises,
  Groups, Artists, Venues), always fully expanded, no search/filter box —
  the one tag-heavy page in the app that doesn't have one (`index.html` and
  `preferences.html` both already do, via a shared `filterChips(input,
  scope)` helper defined once in `base.html`).
- Editing is inline: a venue's region/link editor is an inline `<details
  class="edit-round">` form per row; a group's member list renders inline
  with its own inline "add member" `<select>`+button row.
- `routes/tags.py` has no rename endpoint — `POST /tags/{tag_id}/edit` only
  ever accepts `location_url`/`region`. The only way to fix a typo today is
  delete-and-recreate, which detaches the tag from every concert it's on
  (cascade deletes `concert_tags`/`tag_members` rows).
- The "add member to group" flow (`POST /tags/{tag_id}/members`) only ever
  adds a `TagMember` row; it never touches any already-tagged concert
  (correct today — group membership edits are explicitly documented as
  never rewriting existing concerts, per `CLAUDE.md`'s Group Tag Expansion
  invariant). This spec adds an *explicit, opt-in* action alongside it —
  never automatic — so that invariant is not being changed, just
  supplemented with a manual bulk-attach a human has to confirm.

## Non-goals (explicitly out of scope)

- Tag **merging** (owner said no).
- Group-tag expansion semantics changing in any way — attaching a GROUP tag
  to a concert still only expands at that moment; this spec's retroactive
  action is a distinct, always-explicit, editor-confirmed action, not a
  change to that rule.
- The index-page reorganization (separate future spec).
- Anything about how concerts *display* tags (index tiles, detail page
  chip rendering) — this spec is entirely about the `/tags` management
  page and its two backing capabilities (rename, retroactive-apply).

## Layout: search + hierarchy

`tags.html` gains a `<input type="search" oninput="filterChips(this,
'.tags-page')">` at the top (wrapping the whole tag area in a `.tags-page`
container so the existing `filterChips` helper's scope selector works
unchanged — no new JS).

Franchises/Groups/Artists reorganize from three flat per-kind lists into
one nested tree, copying `preferences.html`'s existing subscription-box
structure exactly (same macro shape, same nesting, same "Other groups"/
"Solo artists" bucket logic — that template already solves this problem
for the *filtering* UI; this reuses the same solved shape for the
*management* UI):

```
Franchises
  <franchise name>
    <group belonging to this franchise>
      <member chips>
    ...
  (groups with no franchise parent, under "Other groups")
  (artist tags not in any group, under "Solo artists")
Venues
  <venue chips, flat, as today>
```

Each top-level bucket (Franchises, Other groups, Solo artists, Venues)
stays a `<details open>` — expanded by default, collapsible per-section.
Search filtering works regardless of collapsed state (matches the existing
`filterChips` behavior already used elsewhere, which just toggles
`display:none` and doesn't care about `<details>` state).

## Editing: dialogs instead of inline forms

Two of today's inline `<details>`-based edit forms move into `<dialog>`
elements, using the exact visual chrome the concert-creation tag picker
(`_tag_picker_script.html`) already establishes: `.picker-head` (title +
`×` close button), `.picker-body`, no footer, closes on backdrop click or
Esc (already-shared JS in `base.html` handles the backdrop-click part
globally).

- **Venue region/link**: click a venue chip → `<dialog id="venue-edit-{id}">`
  with the `location_url`/`region` fields + Save, posting to the existing
  `POST /tags/{tag_id}/edit` route unchanged.
- **Group membership**: click a group chip → `<dialog id="group-members-{id}">`
  showing current members (each removable via the existing `POST
  /tags/{gid}/members/{mid}/delete` route) plus a search-filtered add-member
  list (reusing the exact `filterChips`-driven picker-body pattern from
  `_tag_picker_script.html`) posting to the existing `POST
  /tags/{tag_id}/members` route. This is also where the retroactive-apply
  confirmation (below) gets triggered from.

Tag creation stays the existing single-row inline form (`<details
class="panel"><summary>+ New tag</summary>`) — it's a one-off action, not
a per-row edit, so it doesn't belong in a dialog. One small addition: the
franchise-parent `<select>` is hidden via a few lines of vanilla JS unless
"Group" is the selected kind, since it's dead noise for every other kind
today.

Tag deletion is untouched (`×` button + `confirm()`) — already minimal,
no dialog warranted for a single destructive click.

## New capability: rename

`POST /tags/{tag_id}/edit` gains an optional `name` field. When present:

```python
name = name.strip()
if name and name.lower() != tag.name.lower():
    existing = await find_tag_by_name(session, name)
    if existing is not None and existing.id != tag.id:
        raise HTTPException(status_code=409, detail=f"tag {name!r} already exists")
    tag.name = name
```

Reuses `find_tag_by_name` (already used by `create_tag`'s own uniqueness
check) with an id-exclusion, mirroring `validate_event_id`'s
`exclude_concert_id` parameter in `concerts.py` — same shape, same
project, already-proven pattern.

**Accepted trade-off, not a bug to fix here:** `find_venue_tag()` in
`concerts.py` matches a `ConcertDay.venue` free-text string against
VENUE-tag names, case-insensitively, purely for display (it's a soft
link — `ConcertDay.venue` is never a foreign key). Renaming a venue tag
means any concert whose saved venue text matched the *old* name stops
matching after the rename. This is a pre-existing property of that
free-text-matching design (a day's venue text has always been one typo
away from not matching), not a new problem rename introduces — no
cascade-rename or migration is planned for it.

## Retroactive "add to active events"

**"Active" concert, defined concretely:** the concert has the group tag
attached, AND `concert_date_range(concert.days)` is not `None` (i.e., it
has at least one non-cancelled leg — that function already excludes
cancelled legs as of PR #21), AND that range's later date hasn't passed
(`date_range[1] >= now`) — the exact same computation the concert detail
page already performs for its own `concert_past` flag, just reused as a
query filter here instead of a display flag.

**Flow:**

1. `POST /tags/{tag_id}/members` (unchanged route, unchanged behavior —
   still just adds the `TagMember` row) redirects, as it does today, to
   `/tags` — UNLESS there is at least one "active" concert (per the
   definition above) that already has the group tag attached but not this
   member, in which case it redirects instead to a small confirmation page:
   `GET /tags/{group_id}/members/{member_id}/retroactive-apply`.
2. That page reads: *"{Artist name} was added to {Group name}. Also add
   them to these {N} active events already tagged {Group name}?"* — with
   the concrete concert titles listed (not just a bare count), an "Apply to
   all" button, and a "Skip" link straight back to `/tags`.
3. "Apply to all" POSTs `POST /tags/{group_id}/members/{member_id}/retroactive-apply`,
   which re-derives the same "active concerts missing this member" set
   (never trusts a client-submitted list) and, for each, calls the
   already-existing `attach_tag(session, concert.id, member_tag)` then
   `handle_newly_tagged(session, concert, [member_tag])` — the exact two
   functions every other tag-attachment path in this app already uses, so
   tag-subscribers get notified through the same existing pipeline, no new
   notification logic. `expand` is omitted (default `True`) since it only
   ever matters for GROUP-kind tags and `member_tag` is an ARTIST — matches
   how every other individual-artist attachment in this codebase already
   calls `attach_tag` (e.g. `create_concert_row`'s artist loop). Redirects
   to `/tags` when done.

**No new persisted state.** Skipping doesn't record a "don't ask again" —
it just means the interstitial won't reappear until a *different* member
gets added to that group later (at which point it's a fresh, genuinely new
decision anyway). If the same member's addition needs re-offering, the
editor can re-trigger it by removing and re-adding the member (an already-
existing, if roundabout, path — not worth special-casing further).

**Common case is untouched:** when there's nothing eligible to offer (no
"active" concerts have this group, or they all already have this member),
the add-member flow behaves exactly as it does today — straight back to
`/tags`, no interstitial, no behavior change for the overwhelmingly common
case of adding a member to a group with no concerts yet or fully historical
ones.

## Testing

- **Service-layer:** a query/helper for "active concerts with group X
  attached, missing member Y" — seeded with a mix of cancelled-leg-only,
  past-dated, and genuinely-active concerts, confirming only the genuinely
  active + missing-member ones are returned.
- **HTTP-level:**
  - Rename round-trips (`PATCH`-via-POST semantics on the existing edit
    route) and rejects a case-insensitive duplicate name, mirroring the
    existing `test_duplicate_tag_names_rejected_case_insensitively`
    pattern for creation.
  - Adding a member to a group with zero eligible active concerts redirects
    straight to `/tags` (no interstitial) — the common-case regression
    guard.
  - Adding a member to a group with ≥1 eligible active concert redirects to
    the confirmation page, which lists the correct concert title(s).
  - "Apply to all" attaches the tag to exactly the eligible concerts (not
    already-covered ones, not past/cancelled ones) and triggers a
    notification for a subscriber, verified against real DB rows.
  - A logged-in GET render test for the redesigned `/tags` page (this
    project's standing rule: every page needs at least one).

## Open questions for the implementation plan (not blocking this spec)

- Exact dialog IDs/markup structure for the venue and group-member dialogs
  — cosmetic, follows `_tag_picker_script.html`'s established pattern
  directly, no new decisions needed at spec level.
- Whether the franchise-parent-dropdown-hides-unless-Group JS belongs in a
  new small script block in `tags.html` or an addition to an existing
  shared script — minor organizational call, decide during implementation.
