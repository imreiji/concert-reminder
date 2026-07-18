# Free-Text Search Matches Tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The index page's free-text search matches tag names (franchise/group/artist/venue) and a free-text venue fallback, not just concert title/title_en.

**Architecture:** One centralized `concert_search_text(c)` helper in `web/app.py` becomes the single source of truth for "what text counts," used by the server-side `matches_query` fallback and by both the tile grid's and the "Coming up soon" deadline list's `data-search` attributes — guaranteeing all three stay in sync by construction, not by three independently-maintained implementations happening to agree.

**Tech Stack:** FastAPI + Jinja2, SQLAlchemy 2.0 async.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` must both be clean before every commit.
- All four tag kinds count toward search: franchise, group, artist, venue.
- `Concert.venue` (free-text) counts as a fallback ONLY when no VENUE-kind tag is attached to the concert — mirrors the tile macro's own existing display fallback (`{% elif cv or c.venue %}`) exactly. It must NOT be searched when a VENUE tag exists, even if the free text differs from the tag name.
- `ConcertDay.venue` (per-day venue) is explicitly out of scope — only the top-level `Concert.venue` field.
- No new database query: `Concert.tags` is already eager-loaded via `selectinload(Concert.tags)` in the index route.
- Matching stays substring, case-insensitive — the same convention the existing title search already uses. No fuzzy matching.
- The "Coming up soon" deadline list's separate, pre-existing lack of server-side initial-hide (unlike tiles, which get one via `visible_concert_ids`) is explicitly NOT being fixed by this plan.
- Spec reference: `docs/superpowers/specs/2026-07-18-search-matches-tags-design.md`.

---

## Task 1: Centralized search-text helper across all three call sites

**Files:**
- Modify: `src/app/web/app.py`
- Modify: `src/app/web/templates/index.html`
- Test: `tests/test_tags.py`

**Interfaces:**
- Produces: `def concert_search_text(c: Concert) -> str` (module-level function in `web/app.py`).
- Produces: `concert_search_by_event_id: dict[str, str]` context variable passed to `index.html`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags.py`, after `test_index_search_box_prefills_from_query_param` (right before `test_index_sorts_by_earliest_event_day`):

```python
def test_index_search_matches_artist_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Kozue Otomune", "kind": "artist"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "artist_tags": [1],
    })
    client.post("/concerts", data={"title": "Other Show", "event_id": "other-show"})

    filtered = client.get("/?q=kozue").text
    some_tile = filtered[filtered.rindex('<a class="tile"', 0, filtered.index("Some Show")):]
    other_tile = filtered[filtered.rindex('<a class="tile"', 0, filtered.index("Other Show")):]
    assert 'style="display:none"' not in some_tile.split("</a>", 1)[0]
    assert 'style="display:none"' in other_tile.split("</a>", 1)[0]


def test_index_search_matches_group_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "group_tags": [1],
    })
    filtered = client.get("/?q=liella").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


def test_index_search_matches_franchise_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "franchise_tags": [1],
    })
    filtered = client.get("/?q=gakumas").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


def test_index_search_matches_venue_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Yokohama Arena", "kind": "venue"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "venue_tags": [1],
    })
    filtered = client.get("/?q=yokohama").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


async def test_index_search_falls_back_to_free_text_venue_when_no_venue_tag(client):
    """Concert.venue is a legacy top-level field the current creation form
    doesn't expose (only per-day ConcertDay.venue is settable through the
    UI) -- set it directly at the DB layer, matching how other tests reach
    fields the form doesn't cover."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Some Show", "event_id": "some-show"})
    async with client.db() as s:
        from app.db.models import Concert as ConcertModel

        concert = (await s.execute(
            select(ConcertModel).where(ConcertModel.event_id == "some-show")
        )).scalar_one()
        concert.venue = "Nippon Budokan"
        await s.commit()

    filtered = client.get("/?q=budokan").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


async def test_index_search_ignores_free_text_venue_when_venue_tag_exists(client):
    """The free-text-venue fallback only applies when NO VENUE tag is
    attached -- if a VENUE tag exists, stale/mismatched free-text venue
    text must not spuriously match."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Yokohama Arena", "kind": "venue"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "venue_tags": [1],
    })
    async with client.db() as s:
        from app.db.models import Concert as ConcertModel

        concert = (await s.execute(
            select(ConcertModel).where(ConcertModel.event_id == "some-show")
        )).scalar_one()
        concert.venue = "Stale Old Name"
        await s.commit()

    filtered = client.get("/?q=stale").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' in tile.split("</a>", 1)[0]
```

Also **update** the existing test `test_index_deadline_list_carries_tag_and_search_attributes` (currently asserting `data-search="tagged deadline show"`) — once the deadline list reads from the shared `concert_search_by_event_id` dict, the concert's attached "Test Artist" tag name is folded in too. Find:

```python
    r = client.get("/").text
    li_start = r.index("<li", r.index("deadline-list"))
    li_end = r.index("</li>", li_start)
    li_html = r[li_start:li_end]
    assert f'data-tags="{tag_id}"' in li_html
    assert 'data-search="tagged deadline show"' in li_html
```

Replace with:

```python
    r = client.get("/").text
    li_start = r.index("<li", r.index("deadline-list"))
    li_end = r.index("</li>", li_start)
    li_html = r[li_start:li_end]
    assert f'data-tags="{tag_id}"' in li_html
    assert 'data-search="tagged deadline show test artist"' in li_html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tags.py -k "search_matches or search_falls_back or search_ignores_free_text or deadline_list_carries" -v`
Expected: the 6 new tests FAIL (search doesn't match tags yet); `test_index_deadline_list_carries_tag_and_search_attributes` FAILs too (its updated assertion doesn't match today's title-only output).

- [ ] **Step 3: Add `concert_search_text` and update `matches_query`**

In `src/app/web/app.py`, add `TagKind` to the top-level imports. Find:

```python
from app.db.models import Concert, ConcertDay, Tag, User
from app.db.service import LABEL_BY_ANCHOR
from app.db.session import get_session
from app.domain.timezones import fmt_dual, utc_to_jst
```

Replace with:

```python
from app.db.models import Concert, ConcertDay, Tag, User
from app.db.service import LABEL_BY_ANCHOR
from app.db.session import get_session
from app.domain.timezones import fmt_dual, utc_to_jst
from app.domain.types import TagKind
```

Add `concert_search_text` right after `has_open_round` (before `def create_app`):

```python
def concert_search_text(c: Concert) -> str:
    """Lowercased blob everything free-text search matches: title,
    title_en, every attached tag's name (all four kinds count --
    franchise/group/artist/venue), and the concert's free-text venue as a
    fallback ONLY when no VENUE tag is attached (mirrors the tile macro's
    own venue display fallback in index.html exactly)."""
    parts = [c.title]
    if c.title_en:
        parts.append(c.title_en)
    parts.extend(t.name for t in c.tags)
    if not any(t.kind is TagKind.VENUE for t in c.tags) and c.venue:
        parts.append(c.venue)
    return " ".join(parts).lower()
```

Inside the `index` route, find:

```python
        def matches_query(c: Concert) -> bool:
            if not query:
                return True
            haystack = f"{c.title} {c.title_en or ''}".lower()
            return query in haystack
```

Replace with:

```python
        def matches_query(c: Concert) -> bool:
            if not query:
                return True
            return query in concert_search_text(c)
```

Find:

```python
            concert_tags_by_event_id = {c.event_id: {t.id for t in c.tags} for c in concerts}
```

Replace with:

```python
            concert_tags_by_event_id = {c.event_id: {t.id for t in c.tags} for c in concerts}
            concert_search_by_event_id = {c.event_id: concert_search_text(c) for c in concerts}
```

Find the line initializing the anonymous-user defaults:

```python
        deadlines, concert_tags_by_event_id = [], {}
```

Replace with:

```python
        deadlines, concert_tags_by_event_id, concert_search_by_event_id = [], {}, {}
```

Find the context dict's `"concert_tags_by_event_id": concert_tags_by_event_id,` line and add the new dict right after it:

```python
                "concert_tags_by_event_id": concert_tags_by_event_id,
                "concert_search_by_event_id": concert_search_by_event_id,
```

- [ ] **Step 4: Update `index.html`'s two `data-search` call sites**

Find the tile macro's line:

```html
       data-search="{{ (c.title ~ ' ' ~ (c.title_en or '')) | lower }}"
```

Replace with:

```html
       data-search="{{ concert_search_by_event_id.get(c.event_id, '') }}"
```

Find the deadline list's line:

```html
      <li data-tags="{{ dtags | join(',') }}" data-search="{{ d.concert_title | lower }}">
```

Replace with:

```html
      <li data-tags="{{ dtags | join(',') }}" data-search="{{ concert_search_by_event_id.get(d.event_id, '') }}">
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tags.py -k "search_matches or search_falls_back or search_ignores_free_text or deadline_list_carries" -v`
Expected: `7 passed`

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q` — expect all passing (305). Confirm the pre-existing `test_index_search_filters_by_title`, `test_index_search_is_case_insensitive_and_matches_title_en`, `test_index_search_combines_with_tag_filter_as_and`, and `test_index_filters_by_tag` all still pass unchanged (none of their seeded concerts carry tags/venue text that this change would affect).
Run: `uv run ruff check .` — expect `All checks passed!`

```bash
git add src/app/web/app.py src/app/web/templates/index.html tests/test_tags.py
git commit -m "Make free-text search match tag names and free-text venue"
```

---

## Final step: update CLAUDE.md and WISHLIST.md

**CLAUDE.md:**

- Bump the test count in the intro sentence to 305, and add "free-text search matching tag names (franchise/group/artist/venue) and a free-text-venue fallback" to the shipped-features list.
- Update the UI-conventions bullet describing the index page's search box. Find:

  ```
  - The index page's tag filter and its free-text search box (matches title +
    title_en, case-insensitive) combine as AND, not OR — both narrow the same
  ```

  Replace `"matches title + title_en, case-insensitive"` with `"matches title, title_en, every attached tag's name, and a free-text-venue fallback when no VENUE tag exists, all case-insensitive"`.

**WISHLIST.md:** per CLAUDE.md's "Feature wishlist" maintenance convention (move the shipped entry, then do a full revision pass over what's left):

- Move the "Free-text search matches artists, groups, and venues" entry from `## Proposed` to `## Shipped`, with today's date and a one-line note on what shipped (the centralized `concert_search_text` helper, all four tag kinds, the free-text-venue fallback, and the deadline list's matching `data-search`).
- Re-rank and reconsider the remaining 3 entries (per-round lottery outcome tracking, daily digest mode, first-run guided setup). None of them are obviously invalidated or newly enabled by this ship — confirm the order still makes sense rather than leaving it unexamined.
