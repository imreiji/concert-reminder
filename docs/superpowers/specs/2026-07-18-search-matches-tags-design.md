# Free-text search matches tags design

## Context

Feature #1 (current ranking) from `WISHLIST.md` (raised 2026-07-18 UX
review, impact: medium-high, effort: small). The index page's free-text
search (both the client-side `data-search` attribute on every tile and
the server-side fallback in `web/app.py`'s `matches_query`) currently
matches only `Concert.title`/`title_en` — a user searching "when's the
next ○○ show" by group/artist/venue/franchise name finds nothing, even
though every concert already carries this information as tags.

## Non-goals

- **The "Coming up soon" deadline list's separate SSR-initial-visibility
  gap.** Unlike concert tiles (which get an initial `style="display:none"`
  computed server-side via `visible_concert_ids`, so there's no flash of
  wrong tiles before JS runs), the deadline list's `<li>` rows currently
  have no such mechanism — they always render un-hidden on first paint
  regardless of any active tag filter or query, relying entirely on
  client-side JS to hide them. This is a pre-existing, unrelated
  SSR-completeness gap, not something this feature touches — it's about
  *what render-time visibility state exists*, not *what text search
  matches*, and fixing it would require restructuring how the deadline
  list computes visibility, not just what its `data-search` blob contains.
- **Per-day venue text.** `ConcertDay.venue` (a leg/day can have its own
  venue, since tours change cities) is NOT searched — only the top-level
  `Concert.venue` fallback, matching the tile's own existing display logic
  exactly (`{% elif cv or c.venue %}`, `templates/index.html`). Multi-leg
  tours complex enough to have per-day venues are also the concerts most
  likely to already carry curated VENUE tags.
- **Fuzzy/typo-tolerant matching.** Substring, case-insensitive — same
  convention the existing title search already uses. No new matching
  algorithm.

## Design

**One centralized helper, three call sites.** Rather than duplicating
"what text counts" logic separately in Python (the server-side fallback)
and in Jinja (the client-side `data-search` attribute), a single function
in `web/app.py` becomes the one source of truth:

```python
def concert_search_text(c: Concert) -> str:
    """Lowercased blob of everything free-text search matches: title,
    title_en, every attached tag's name (all four kinds count --
    franchise/group/artist/venue), and the concert's free-text venue as a
    fallback ONLY when no VENUE tag is attached (mirrors the tile's own
    venue display fallback exactly)."""
    parts = [c.title, c.title_en or ""]
    parts.extend(t.name for t in c.tags)
    if not any(t.kind is TagKind.VENUE for t in c.tags) and c.venue:
        parts.append(c.venue)
    return " ".join(parts).lower()
```

No new query: `Concert.tags` is already eager-loaded via
`selectinload(Concert.tags)` in the index route.

1. **`matches_query(c)`** (the server-side fallback used both for the
   initial SSR-computed `visible_concert_ids` and for JS-disabled clients
   submitting the real `<form>`) calls `concert_search_text(c)` instead of
   its current title-only haystack.
2. **Tile macro's `data-search` attribute** (`templates/index.html`) reads
   from a new `concert_search_by_event_id: dict[str, str]` context
   variable — computed once per request from the already-loaded
   `concerts` list — instead of inlining its own `(c.title ~ ' ' ~
   (c.title_en or '')) | lower` Jinja expression.
3. **The "Coming up soon" deadline list's `<li data-search="...">`** reads
   from the *same* `concert_search_by_event_id` dict, keyed by
   `d.event_id` (the same lookup pattern `concert_tags_by_event_id`
   already establishes for `data-tags`). This guarantees the tile grids
   and the deadline list search identically by construction, not by two
   independently-maintained implementations happening to agree.

`concert_tags_by_event_id` (tag IDs, driving `data-tags` for the existing
tag-filter mechanism) is untouched — this is a separate new dict for the
search-text concern, not a replacement.

The client-side JS filtering logic (`applyVisibility` in `index.html`,
already reading `.dataset.search` via substring `.includes()`) needs no
changes at all — it doesn't care what's inside the string, only that it's
there.

## Testing

- **Service/route-level** (`tests/test_tags.py`, which already owns the
  index page's HTTP-level tests): search matches a concert by its
  artist/group/franchise/venue tag name; search falls back to matching
  free-text `Concert.venue` when no VENUE tag is attached; search does
  *not* spuriously match free-text venue text when a VENUE tag IS
  attached with a different name (locks in the "only as a fallback"
  behavior deliberately, not accidentally-always-both); the "Coming up
  soon" deadline list's rows carry the same matching `data-search` text
  for a concert matched by tag name.
- **Server-side fallback**: a GET request with `?q=<tag name>` (no JS
  involved) returns the concert in `visible_concert_ids`, confirming
  `matches_query` uses the same logic as the client-side attribute.
