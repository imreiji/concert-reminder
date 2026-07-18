# Tags Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Tags page (`tags.html`/`routes/tags.py`) with a search box, a franchise→group→member hierarchy (mirroring `preferences.html`'s subscription-box layout), dialog-based editing for every tag kind (replacing today's inline `<details>` forms), a tag-rename capability, and a retroactive "add this artist to active events already tagged this group" confirmation flow.

**Architecture:** One new pure-ish service function (`active_concerts_missing_member`) computes the retroactive-apply candidate set by reusing the existing `ConcertDay.cancelled` flag (PR #21) instead of any new date-status concept. `POST /tags/{tag_id}/edit` gains an optional `name` field for renaming (reusing the existing `find_tag_by_name` uniqueness check). `POST /tags/{tag_id}/members` conditionally redirects to a new confirmation page instead of straight back to `/tags` when there's something to offer. The whole tag directory template is rewritten around one small `tag_chip`/`tag_dialog` macro pair — every tag chip, of every kind, opens the same dialog shape (rename + kind-specific extra fields + delete), reusing the `dialog.picker`/`.picker-head`/`.picker-body` CSS classes the concert-creation tag picker already established — no new CSS needed for the dialogs themselves.

**Tech Stack:** FastAPI + Jinja2 (server-rendered, no client framework), SQLAlchemy 2.0 async, vanilla JS (the existing shared `filterChips` helper in `base.html`, plus native `<dialog>`).

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` must both be clean before every commit.
- Sentence case in all new user-facing copy.
- Tag chips are the universal element in this app's UI language; "+ Add x" buttons share the exact chip silhouette. Pickers are native `<dialog>` white cards: header (title + ×), search, chip list; no footer; backdrop-click and Esc close (backdrop-click is already handled globally in `base.html`, do not re-implement it per-dialog).
- Group tag expansion semantics are non-negotiable and this plan does not change them: attaching a GROUP tag to a concert only expands its members at that moment; membership edits never rewrite existing concerts. The retroactive-apply feature in this plan is a distinct, always-explicit, editor-confirmed action — never automatic, never triggered by anything other than a human clicking "Apply to all" on the confirmation page.
- Every new page-rendering code path needs at least one logged-in GET render test.
- Spec reference: `docs/superpowers/specs/2026-07-18-tags-page-redesign-design.md`. Read it if anything below is unclear.

---

## Task 1: `active_concerts_missing_member` service function

**Files:**
- Modify: `src/app/db/service.py` (add new function, after `group_members`)
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: `async def active_concerts_missing_member(session: AsyncSession, group_id: int, member_id: int, now: datetime | None = None) -> list[Concert]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_service.py`, right after the `# ── Concert edit history` section's tests (before `# ── set_editor / list_editors`):

```python
# ── Retroactive artist-to-active-events (Tags page) ──────────────────────


async def seed_group_and_concerts(s) -> tuple[Tag, Tag, Tag]:
    """A group with one already-attached artist, and three concerts all
    tagged with the group: one with a live future leg (active, missing the
    new member), one with only a cancelled leg (not active), one already
    carrying the new member (not eligible -- already covered)."""
    await ensure_user(s, 42, "reiji")
    group = Tag(name="Liella", kind=TagKind.GROUP, created_by=42)
    existing_member = Tag(name="Kaho", kind=TagKind.ARTIST, created_by=42)
    new_member = Tag(name="Sumire", kind=TagKind.ARTIST, created_by=42)
    s.add_all([group, existing_member, new_member])
    await s.flush()

    active = Concert(title="Active Show", event_id="active-show", created_by=42)
    cancelled_only = Concert(title="Cancelled Show", event_id="cancelled-show", created_by=42)
    already_covered = Concert(title="Covered Show", event_id="covered-show", created_by=42)
    s.add_all([active, cancelled_only, already_covered])
    await s.flush()

    s.add_all([
        ConcertDay(concert_id=active.id, label="Day 1", starts_at_utc=dt(9, 1, 18)),
        ConcertDay(
            concert_id=cancelled_only.id, label="Day 1", starts_at_utc=dt(9, 1, 18), cancelled=True
        ),
        ConcertDay(concert_id=already_covered.id, label="Day 1", starts_at_utc=dt(9, 1, 18)),
    ])
    await s.flush()

    s.add_all([
        ConcertTag(concert_id=active.id, tag_id=group.id),
        ConcertTag(concert_id=cancelled_only.id, tag_id=group.id),
        ConcertTag(concert_id=already_covered.id, tag_id=group.id),
        ConcertTag(concert_id=already_covered.id, tag_id=new_member.id),
    ])
    await s.flush()
    return group, existing_member, new_member


async def test_active_concerts_missing_member_excludes_cancelled_and_covered(session):
    group, _, new_member = await seed_group_and_concerts(session)
    result = await active_concerts_missing_member(session, group.id, new_member.id, NOW)
    assert [c.title for c in result] == ["Active Show"]


async def test_active_concerts_missing_member_excludes_past_dated(session):
    """Same seed as above, but queried with a `now` after the "Active
    Show"'s only leg (2026-09-01) -- it should no longer count as active,
    leaving nothing eligible at all (the other two concerts are already
    excluded for their own reasons regardless of `now`)."""
    group, _, new_member = await seed_group_and_concerts(session)
    result = await active_concerts_missing_member(session, group.id, new_member.id, dt(10, 1))
    assert result == []


async def test_active_concerts_missing_member_empty_for_ungrouped_concert(session):
    group, _, new_member = await seed_group_and_concerts(session)
    unrelated = Concert(title="No Group", event_id="no-group", created_by=42)
    session.add(unrelated)
    await session.flush()
    session.add(ConcertDay(concert_id=unrelated.id, label="Day 1", starts_at_utc=dt(9, 1, 18)))
    await session.flush()
    result = await active_concerts_missing_member(session, group.id, new_member.id, NOW)
    assert "No Group" not in [c.title for c in result]
```

Update the existing import blocks at the top of `tests/test_service.py`:
- `from app.db.models import (...)` (currently `Base, Concert, ConcertDay, ReminderQueue, ReminderRule, Round, User`) gains `ConcertTag` and `Tag`.
- `from app.db.service import (...)` gains `active_concerts_missing_member`.
- `from app.domain.types import Anchor, ConcertKind, RoundKind` gains `TagKind`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_service.py -k "active_concerts_missing_member" -v`
Expected: FAIL with `ImportError`/`NameError` (the function doesn't exist yet).

- [ ] **Step 3: Implement the function**

In `src/app/db/service.py`, add this right after `group_members` (search for `async def group_members` — it's a few lines below `find_tag_by_name`):

```python
async def active_concerts_missing_member(
    session: AsyncSession, group_id: int, member_id: int, now: datetime | None = None
) -> list[Concert]:
    """Concerts tagged with `group_id` that don't already carry `member_id`
    and have at least one live (non-cancelled) leg whose date hasn't
    passed -- the set the Tags page's retroactive-apply confirmation
    offers to bulk-attach an artist to. "Active" reuses the same
    live-leg-date-range logic concert_date_range()/concert_past already use
    on the concert detail page (routes/concerts.py), reimplemented directly
    here rather than imported from web/routes/ -- this module sits below
    routes in this project's dependency direction, so importing the other
    way would invert it for a few lines of straightforward logic."""
    now = now or _now()
    res = await session.execute(
        select(Concert)
        .join(ConcertTag, ConcertTag.concert_id == Concert.id)
        .where(ConcertTag.tag_id == group_id)
    )
    candidates = list(res.scalars())
    already_tagged = set((await session.execute(
        select(ConcertTag.concert_id).where(ConcertTag.tag_id == member_id)
    )).scalars())

    out = []
    for c in candidates:
        if c.id in already_tagged:
            continue
        await session.refresh(c, ["days"])
        live_starts = [d.starts_at_utc for d in c.days if not d.cancelled]
        if not live_starts or max(live_starts) < now:
            continue
        out.append(c)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_service.py -k "active_concerts_missing_member" -v`
Expected: `3 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/db/service.py tests/test_service.py
git commit -m "Add active_concerts_missing_member for the Tags page's retroactive-apply flow"
```

---

## Task 2: Tag rename capability

**Files:**
- Modify: `src/app/web/routes/tags.py` (`edit_tag`)
- Test: `tests/test_tags.py`

**Interfaces:**
- Consumes: `find_tag_by_name` (existing, `db/service.py`).
- Produces: `POST /tags/{tag_id}/edit` now also accepts an optional `name` form field.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags.py`, after the existing `test_groups_cannot_contain_groups` test:

```python
def test_rename_tag_round_trips(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    r = client.post("/tags/1/edit", data={"name": "Hasunosora Idols"})
    assert r.status_code == 303

    async def check():
        async with client.db() as s:
            tag = await s.get(Tag, 1)
            assert tag.name == "Hasunosora Idols"

    import asyncio
    asyncio.get_event_loop().run_until_complete(check())


def test_rename_tag_rejects_case_insensitive_duplicate(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    r = client.post("/tags/2/edit", data={"name": "hasunosora"})
    assert r.status_code == 409


def test_rename_tag_to_its_own_current_name_is_a_noop(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    r = client.post("/tags/1/edit", data={"name": "Hasunosora"})
    assert r.status_code == 303


def test_edit_tag_without_name_field_leaves_name_unchanged(client):
    """Backward compatibility: the venue-only edit form that existed before
    this feature never sends `name` at all."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "K Arena", "kind": "venue"})
    r = client.post("/tags/1/edit", data={"region": "Kanto"})
    assert r.status_code == 303

    async def check():
        async with client.db() as s:
            tag = await s.get(Tag, 1)
            assert tag.name == "K Arena"
            assert tag.region == "Kanto"

    import asyncio
    asyncio.get_event_loop().run_until_complete(check())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tags.py -k "rename or edit_tag_without_name" -v`
Expected: FAIL — `test_rename_tag_round_trips`/`test_rename_tag_rejects_case_insensitive_duplicate` fail because `name` currently has no effect; `test_rename_tag_to_its_own_current_name_is_a_noop` and `test_edit_tag_without_name_field_leaves_name_unchanged` may already pass by coincidence (name is currently ignored entirely) — run all four anyway to establish the baseline.

- [ ] **Step 3: Implement rename in `edit_tag`**

In `src/app/web/routes/tags.py`, replace the current `edit_tag` (lines 85-101):

```python
@router.post("/tags/{tag_id}/edit")
async def edit_tag(
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    location_url: str = Form(""),
    region: str = Form(""),
):
    """Venue-only in practice today (the only fields worth correcting after
    creation so far) but not kind-restricted -- harmless to set on others."""
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404)
    tag.location_url = location_url.strip() or None
    tag.region = region.strip() or None
    await session.commit()
    return RedirectResponse("/tags", status_code=303)
```

with:

```python
@router.post("/tags/{tag_id}/edit")
async def edit_tag(
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    name: str = Form(""),
    location_url: str = Form(""),
    region: str = Form(""),
):
    """Rename (any kind) plus venue-only location_url/region -- not
    kind-restricted on the latter two, harmless to set on others.
    `name` is optional so callers that never send it (there were none
    before this feature; kept optional in case any external client still
    doesn't) leave the tag's name untouched."""
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404)
    name = name.strip()
    if name and name.lower() != tag.name.lower():
        existing = await find_tag_by_name(session, name)
        if existing is not None and existing.id != tag.id:
            raise HTTPException(status_code=409, detail=f"tag {name!r} already exists")
        tag.name = name
    tag.location_url = location_url.strip() or None
    tag.region = region.strip() or None
    await session.commit()
    return RedirectResponse("/tags", status_code=303)
```

Add `find_tag_by_name` to the existing `from app.db.service import (...)` block at the top of the file (it currently imports `ensure_user, find_tag_by_name, group_members` — check first, `find_tag_by_name` may already be imported since `create_tag` uses it; if so, no import change needed).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tags.py -k "rename or edit_tag_without_name" -v`
Expected: `4 passed`

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/routes/tags.py tests/test_tags.py
git commit -m "Add tag rename capability to POST /tags/{tag_id}/edit"
```

---

## Task 3: Retroactive-apply confirmation flow

**Files:**
- Modify: `src/app/web/routes/tags.py` (`add_member`, plus two new routes)
- Create: `src/app/web/templates/retroactive_apply.html`
- Test: `tests/test_tags.py`

**Interfaces:**
- Consumes: `active_concerts_missing_member` (Task 1), `attach_tag`/`handle_newly_tagged` (existing, `db/service.py`).
- Produces: `GET /tags/{group_id}/members/{member_id}/retroactive-apply`, `POST /tags/{group_id}/members/{member_id}/retroactive-apply`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags.py`, after the rename tests from Task 2:

```python
def create_active_concert_with_group(client, event_id, group_tag_id):
    """A concert with one live future leg, tagged with the given group --
    the shape active_concerts_missing_member requires to count a concert
    as an eligible retroactive-apply target."""
    return client.post(
        "/concerts",
        data={
            "title": event_id, "event_id": event_id, "group_tags": [group_tag_id],
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["false"],
        },
    )


def test_add_member_redirects_straight_to_tags_when_nothing_eligible(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    r = client.post("/tags/1/members", data={"member_tag_id": 2})
    assert r.status_code == 303
    assert r.headers["location"] == "/tags"


def test_add_member_redirects_to_confirmation_when_something_eligible(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    create_active_concert_with_group(client, "liella-live", 1)
    r = client.post("/tags/1/members", data={"member_tag_id": 2})
    assert r.status_code == 303
    assert r.headers["location"] == "/tags/1/members/2/retroactive-apply"


def test_confirmation_page_lists_eligible_concert_titles(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    create_active_concert_with_group(client, "liella-live", 1)
    client.post("/tags/1/members", data={"member_tag_id": 2})
    r = client.get("/tags/1/members/2/retroactive-apply")
    assert r.status_code == 200
    assert "liella-live" in r.text
    assert "Sumire" in r.text


def test_confirmation_page_handles_nothing_eligible_gracefully(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    r = client.get("/tags/1/members/2/retroactive-apply")
    assert r.status_code == 200
    assert "Nothing to apply" in r.text


async def test_apply_to_all_attaches_tag_and_notifies_subscriber(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    create_active_concert_with_group(client, "liella-live", 1)

    login_as(client, VIEWER_ID, "viewer")
    client.post("/subscriptions", data={"tag_id": 2, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")

    client.post("/tags/1/members", data={"member_tag_id": 2})
    r = client.post("/tags/1/members/2/retroactive-apply")
    assert r.status_code == 303
    assert r.headers["location"] == "/tags"

    async with client.db() as s:
        from app.db.models import ConcertTag, Notification

        concert_tags = (await s.execute(select(ConcertTag))).scalars().all()
        assert any(ct.tag_id == 2 for ct in concert_tags)  # Sumire attached
        notes = (await s.execute(select(Notification))).scalars().all()
        assert any(n.user_id == VIEWER_ID for n in notes)


def test_confirmation_page_requires_editor(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})

    login_as(client, VIEWER_ID, "viewer")
    assert client.get("/tags/1/members/2/retroactive-apply").status_code == 403
    assert client.post("/tags/1/members/2/retroactive-apply").status_code == 403
```

The existing `client` fixture in `test_tags.py` already constructs `TestClient(app, follow_redirects=False)` (line 124) — `r.headers["location"]` is already inspectable in every test above with no fixture change needed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tags.py -k "add_member_redirects or confirmation_page or apply_to_all" -v`
Expected: FAIL — the new routes (`/tags/{group_id}/members/{member_id}/retroactive-apply`) don't exist yet (404), and `add_member` always redirects to `/tags` today.

- [ ] **Step 3: Modify `add_member` and add the two new routes**

In `src/app/web/routes/tags.py`, add `active_concerts_missing_member`, `attach_tag`, `handle_newly_tagged` to the existing `from app.db.service import (...)` import block, and add `Concert` to the existing `from app.db.models import Tag, TagMember` line (making it `from app.db.models import Concert, Tag, TagMember`). Add `Request` to the existing `from fastapi import APIRouter, Depends, Form, HTTPException, Request` import if not already present (it is — `tag_directory` already takes a `request: Request` parameter).

Replace `add_member` (currently lines 118-137):

```python
@router.post("/tags/{tag_id}/members")
async def add_member(
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    member_tag_id: int = Form(...),
):
    group = await session.get(Tag, tag_id)
    member = await session.get(Tag, member_tag_id)
    if group is None or member is None:
        raise HTTPException(status_code=404)
    if group.kind is not TagKind.GROUP:
        raise HTTPException(status_code=422, detail="members can only be added to group tags")
    if member.kind is TagKind.GROUP:
        raise HTTPException(status_code=422, detail="groups cannot contain groups")
    existing = await session.get(TagMember, (tag_id, member_tag_id))
    if existing is None:
        session.add(TagMember(group_tag_id=tag_id, member_tag_id=member_tag_id))
        await session.commit()
    return RedirectResponse("/tags", status_code=303)
```

with:

```python
@router.post("/tags/{tag_id}/members")
async def add_member(
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    member_tag_id: int = Form(...),
):
    group = await session.get(Tag, tag_id)
    member = await session.get(Tag, member_tag_id)
    if group is None or member is None:
        raise HTTPException(status_code=404)
    if group.kind is not TagKind.GROUP:
        raise HTTPException(status_code=422, detail="members can only be added to group tags")
    if member.kind is TagKind.GROUP:
        raise HTTPException(status_code=422, detail="groups cannot contain groups")
    existing = await session.get(TagMember, (tag_id, member_tag_id))
    if existing is None:
        session.add(TagMember(group_tag_id=tag_id, member_tag_id=member_tag_id))
        await session.commit()
        eligible = await active_concerts_missing_member(session, tag_id, member_tag_id)
        if eligible:
            return RedirectResponse(
                f"/tags/{tag_id}/members/{member_tag_id}/retroactive-apply", status_code=303
            )
    return RedirectResponse("/tags", status_code=303)


@router.get("/tags/{group_id}/members/{member_id}/retroactive-apply", response_class=HTMLResponse)
async def retroactive_apply_form(
    request: Request,
    group_id: int,
    member_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """The one-time confirmation offered right after adding a member to a
    group: bulk-attach that artist to every currently-active concert that
    already has the group tag but not this member individually. Always an
    explicit, editor-confirmed action -- never automatic (see the Group Tag
    Expansion invariant in CLAUDE.md)."""
    group = await session.get(Tag, group_id)
    member = await session.get(Tag, member_id)
    if group is None or member is None:
        raise HTTPException(status_code=404)
    concerts = await active_concerts_missing_member(session, group_id, member_id)
    return templates.TemplateResponse(
        request,
        "retroactive_apply.html",
        {"user": user, "group": group, "member": member, "concerts": concerts},
    )


@router.post("/tags/{group_id}/members/{member_id}/retroactive-apply")
async def retroactive_apply(
    group_id: int,
    member_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    member = await session.get(Tag, member_id)
    if member is None:
        raise HTTPException(status_code=404)
    concerts = await active_concerts_missing_member(session, group_id, member_id)
    for concert in concerts:
        newly = await attach_tag(session, concert.id, member)
        await handle_newly_tagged(session, concert, newly)
    await session.commit()
    return RedirectResponse("/tags", status_code=303)
```

- [ ] **Step 4: Create the confirmation template**

Create `src/app/web/templates/retroactive_apply.html`:

```html
{% extends "base.html" %}
{% block title %}confirm — dekimasen.app{% endblock %}
{% block content %}
<h1>Add {{ member.name }} to active events?</h1>
{% if concerts %}
<p class="dim">{{ member.name }} was just added to {{ group.name }}. These {{ concerts | length }}
  active event(s) already have {{ group.name }} attached but not {{ member.name }} individually:</p>
<ul class="rows">
  {% for c in concerts %}<li><a href="/concerts/{{ c.event_id }}">{{ c.title }}</a></li>{% endfor %}
</ul>
<form class="inline" method="post" action="/tags/{{ group.id }}/members/{{ member.id }}/retroactive-apply">
  <button>Apply to all</button>
  <a class="btn quiet" href="/tags">Skip</a>
</form>
{% else %}
<p class="dim">Nothing to apply — every active event with {{ group.name }} already has {{ member.name }}.</p>
<p><a class="btn quiet" href="/tags">Back to tags</a></p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tags.py -k "add_member_redirects or confirmation_page or apply_to_all" -v`
Expected: `6 passed`

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing.
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/routes/tags.py src/app/web/templates/retroactive_apply.html tests/test_tags.py
git commit -m "Add the retroactive-apply-to-active-events confirmation flow"
```

---

## Task 4: Tags page layout redesign — search, hierarchy, unified dialogs

**Files:**
- Modify: `src/app/web/routes/tags.py` (`tag_directory`)
- Modify: `src/app/web/templates/tags.html` (full rewrite)
- Test: `tests/test_tags.py`

**Interfaces:**
- Consumes: `group_members` (existing), the `filterChips` JS helper (existing, `base.html`), the `dialog.picker`/`.picker-head`/`.picker-body` CSS classes (existing, `style.css`) — no new CSS is added by this task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags.py`:

```python
def test_tags_page_renders_hierarchy_and_search_box(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/tags", data={"name": "Liella", "kind": "group", "parent_id": 1})
    client.post("/tags", data={"name": "Kaho", "kind": "artist"})
    client.post("/tags/2/members", data={"member_tag_id": 3})
    client.post("/tags", data={"name": "K Arena", "kind": "venue"})

    r = client.get("/tags")
    assert r.status_code == 200
    assert 'placeholder="Search tags…"' in r.text
    assert "Hasunosora" in r.text and "Liella" in r.text and "Kaho" in r.text and "K Arena" in r.text
    # every tag gets its own dialog
    assert 'id="tag-dialog-1"' in r.text  # Hasunosora
    assert 'id="tag-dialog-2"' in r.text  # Liella
    assert 'dialog.picker' not in r.text  # sanity: that's a CSS selector, not markup
    assert 'class="picker"' in r.text


def test_tags_page_solo_artist_bucket(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Solo Artist", "kind": "artist"})
    r = client.get("/tags")
    assert r.status_code == 200
    assert "Solo artists" in r.text
    assert "Solo Artist" in r.text


def test_tags_page_viewer_sees_no_edit_dialogs(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    login_as(client, VIEWER_ID, "viewer")
    r = client.get("/tags")
    assert r.status_code == 200
    assert 'id="tag-dialog-1"' not in r.text


def test_new_tag_form_includes_parent_visibility_script(client):
    """The kind/parent select-hiding is JS-only, client-side behavior --
    not server-testable via HTTP. This just confirms the toggle script and
    its target elements are actually present in the rendered page."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/tags")
    assert "new-tag-kind" in r.text and "new-tag-parent" in r.text
    assert "syncParentVisibility" in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tags.py -k "tags_page or new_tag_form" -v`
Expected: FAIL — the current template has no search box, no per-tag dialogs, no solo-artist bucket, no visibility-toggle script.

- [ ] **Step 3: Update `tag_directory` to compute the hierarchy**

In `src/app/web/routes/tags.py`, replace `tag_directory` (currently lines 39-53):

```python
@router.get("/tags", response_class=HTMLResponse)
async def tag_directory(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    tags = await all_tags(session)
    members = {t.id: await group_members(session, t.id) for t in tags if t.kind is TagKind.GROUP}
    return templates.TemplateResponse(
        request,
        "tags.html",
        {"user": user, "tags": tags, "members": members, "kinds": list(TagKind),
         "artist_tags": [t for t in tags if t.kind is TagKind.ARTIST],
         "franchise_tags": [t for t in tags if t.kind is TagKind.FRANCHISE]},
    )
```

with:

```python
@router.get("/tags", response_class=HTMLResponse)
async def tag_directory(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    tags = await all_tags(session)
    groups = [t for t in tags if t.kind is TagKind.GROUP]
    members = {t.id: await group_members(session, t.id) for t in groups}
    grouped_artist_ids = {m.id for ms in members.values() for m in ms}
    return templates.TemplateResponse(
        request,
        "tags.html",
        {
            "user": user, "members": members, "kinds": list(TagKind),
            "franchise_tags": [t for t in tags if t.kind is TagKind.FRANCHISE],
            "franchises": [t for t in tags if t.kind is TagKind.FRANCHISE],
            "groups": groups,
            "solo_artists": [
                t for t in tags if t.kind is TagKind.ARTIST and t.id not in grouped_artist_ids
            ],
            "artist_tags": [t for t in tags if t.kind is TagKind.ARTIST],
            "venues": [t for t in tags if t.kind is TagKind.VENUE],
        },
    )
```

(`franchise_tags` is kept alongside the new `franchises` name because the "+ New tag" form's franchise-parent `<select>` already uses `franchise_tags` — both names point at the same list; no template change needed for that part.)

- [ ] **Step 4: Rewrite the template**

Replace the entire contents of `src/app/web/templates/tags.html`:

```html
{% extends "base.html" %}
{% block title %}tags — dekimasen.app{% endblock %}
{% block content %}
<h1>Tags</h1>
<p class="dim">Franchises, artists, venues, and groups. Group tags contain artist tags:
attaching a group to a concert adds all its members, which editors can then prune.</p>

{% if user.is_editor %}
<details class="panel">
  <summary>+ New tag</summary>
  <form class="inline" method="post" action="/tags" id="new-tag-form">
    <input name="name" required maxlength="100" placeholder="Kozue Otomune">
    <select name="kind" id="new-tag-kind">{% for k in kinds %}<option value="{{ k.value }}">{{ k.value | capitalize }}</option>{% endfor %}</select>
    <select name="parent_id" id="new-tag-parent" title="franchise (for group tags)">
      <option value="0">— franchise (groups only) —</option>
      {% for f in franchise_tags %}<option value="{{ f.id }}">{{ f.name }}</option>{% endfor %}
    </select>
    <input name="location_url" type="url" placeholder="Location link (venues, optional)">
    <input name="region" maxlength="100" placeholder="Region (venues, optional)">
    <button>Create</button>
  </form>
</details>
<script>
  (function () {
    var kindSel = document.getElementById("new-tag-kind");
    var parentSel = document.getElementById("new-tag-parent");
    function syncParentVisibility() {
      parentSel.style.display = kindSel.value === "group" ? "" : "none";
    }
    kindSel.addEventListener("change", syncParentVisibility);
    syncParentVisibility();
  })();
</script>
{% endif %}

<input class="tag-search" type="search" placeholder="Search tags…" oninput="filterChips(this, '.tags-page')">

{% macro tag_dialog(t) %}
<dialog id="tag-dialog-{{ t.id }}" class="picker">
  <div class="picker-head">
    <h3>{{ t.name }}</h3>
    <button type="button" class="x" onclick="document.getElementById('tag-dialog-{{ t.id }}').close()">×</button>
  </div>
  <div class="picker-body">
    <form method="post" action="/tags/{{ t.id }}/edit" class="stack">
      <label>Name <input name="name" required maxlength="100" value="{{ t.name }}"></label>
      {% if t.kind.value == "venue" %}
      <label>Location link <input name="location_url" type="url" value="{{ t.location_url or '' }}"></label>
      <label>Region <input name="region" maxlength="100" value="{{ t.region or '' }}"></label>
      {% endif %}
      <button>Save</button>
    </form>
    {% if t.kind.value == "group" %}
    <div>
      <strong>Members</strong>
      <div class="taglist">
        {% for m in members.get(t.id, []) %}
        <span class="chip">{{ m.name }}
          <form class="chip-x" method="post" action="/tags/{{ t.id }}/members/{{ m.id }}/delete">
            <button class="x">×</button>
          </form>
        </span>
        {% else %}<em class="dim">none yet</em>
        {% endfor %}
      </div>
      {% if artist_tags %}
      <form class="inline" method="post" action="/tags/{{ t.id }}/members">
        <select name="member_tag_id">
          {% for a in artist_tags %}<option value="{{ a.id }}">{{ a.name }}</option>{% endfor %}
        </select>
        <button>Add member</button>
      </form>
      {% endif %}
    </div>
    {% endif %}
    <form method="post" action="/tags/{{ t.id }}/delete"
          onsubmit="return confirm('Delete tag {{ t.name }} everywhere?')">
      <button class="danger">Delete tag</button>
    </form>
  </div>
</dialog>
{% endmacro %}

{% macro tag_chip(t) %}
<button type="button" class="chip kind-{{ t.kind.value }}" data-name="{{ t.name | lower }}"
        {% if user.is_editor %}onclick="document.getElementById('tag-dialog-{{ t.id }}').showModal()"{% endif %}>{{ t.name }}</button>
{% if user.is_editor %}{{ tag_dialog(t) }}{% endif %}
{% endmacro %}

<div class="tags-page">
  {% if franchises %}
  <details open class="tag-section">
    <summary>Franchises</summary>
    {% for f in franchises %}
    <details open class="tag-section">
      <summary>{{ tag_chip(f) }}</summary>
      {% for g in groups if g.parent_id == f.id %}
      <div class="group-row">
        {{ tag_chip(g) }}
        <span class="member-chips">{% for m in members.get(g.id, []) %}{{ tag_chip(m) }}{% endfor %}</span>
      </div>
      {% endfor %}
    </details>
    {% endfor %}
  </details>
  {% endif %}

  {% set orphan_groups = groups | selectattr("parent_id", "none") | list %}
  {% if orphan_groups %}
  <details open class="tag-section">
    <summary>Other groups</summary>
    {% for g in orphan_groups %}
    <div class="group-row">{{ tag_chip(g) }}
      <span class="member-chips">{% for m in members.get(g.id, []) %}{{ tag_chip(m) }}{% endfor %}</span></div>
    {% endfor %}
  </details>
  {% endif %}

  {% if solo_artists %}
  <details open class="tag-section">
    <summary>Solo artists</summary>
    <div class="taglist">{% for a in solo_artists %}{{ tag_chip(a) }}{% endfor %}</div>
  </details>
  {% endif %}

  {% if venues %}
  <details open class="tag-section">
    <summary>Venues</summary>
    <div class="taglist">{% for v in venues %}{{ tag_chip(v) }}{% endfor %}</div>
  </details>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tags.py -k "tags_page or new_tag_form" -v`
Expected: `4 passed`

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing. (Confirmed while writing this plan: no existing test anywhere in `tests/` asserts on the old markup this task replaces — `grep -rn "edit-round|Members:|no region set" tests/` returns nothing — so no other test file needs updating for this rewrite.)
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/routes/tags.py src/app/web/templates/tags.html tests/test_tags.py
git commit -m "Redesign the Tags page: search, hierarchy, unified per-tag dialogs"
```

---

## Final step: update CLAUDE.md

Add a short update after this lands, matching the pattern every prior
feature in this project's history has followed: bump the test count, add
"a redesigned Tags page (search, hierarchy, dialog-based editing, rename,
retroactive artist-to-active-events apply)" to the shipped-features
sentence in the intro. No new invariant needed — this feature doesn't
introduce a new non-negotiable rule, it reuses `ConcertDay.cancelled` and
the existing Group Tag Expansion invariant exactly as documented. Fold this
into Task 4's commit or add one small final commit for it alone.
