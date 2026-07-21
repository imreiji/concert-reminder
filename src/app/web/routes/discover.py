"""The public catalogue.

  GET /discover

"What's on", as opposed to `/`'s "where do I stand". This is the old index
page, given its own URL and its own template, plus the three things that make
it a discovery page rather than a moved one: a round-status facet, a
next-deadline sort, and one status pill per card.

Deliberately NO require_user -- `current_user`, so an anonymous visitor
resolves to None and still browses the whole catalogue. /privacy, /terms and
/healthz are the existing precedents; this is the only *content* page among
them, which is why the signed-out render is the test that matters most.

Because the pill merges the viewer's own standing over the event state, the
page is per-user and can never be anonymously cached.

A thin shell: everything but the assembly of the template context lives in
`db/service.py` (the pill and facet in `discover_statuses`) or in the pure
helpers below, which take plain objects and do no I/O.
"""

from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import Concert, ConcertDay, Tag, User
from app.db.service import (
    discover_statuses,
    discoverable_concert_count,
    discoverable_concert_criterion,
    discoverable_open_round_count,
    discoverable_tag_counts,
    upcoming_deadlines,
)
from app.db.session import get_session
from app.domain.types import ConcertKind, TagKind
from app.web.auth import SessionUser, current_user

router = APIRouter()

templates = None  # set by web.app at startup

# The round-status facet, in the order it renders. Keys match
# DiscoverStatus.status, which is what each tile carries as data-status.
STATUS_FACETS = [
    ("open", "Open now"),
    ("soon", "Opening soon"),
    ("none", "Not tracking"),
]

# Sort keys the page offers. "next" is handled in Python rather than SQL --
# the soonest deadline spans four nullable timestamp columns across a
# variable number of non-cancelled rounds, and discover_statuses has already
# computed it for the pill by the time we sort.
SORTS = [("event", "Event date"), ("next", "Next deadline"), ("added", "Recently added")]

DEADLINE_LIST_LIMIT = 10


def grouped_tags(tags: list[Tag]) -> dict[str, list[Tag]]:
    """Tags bucketed by kind for the sidebar. The franchise -> group -> member
    hierarchy itself is handled in the template."""
    by_kind: dict[str, list[Tag]] = {}
    for t in tags:
        by_kind.setdefault(t.kind.value, []).append(t)
    return by_kind


def filter_query(sort: str, tag_ids, status: str) -> str:
    """The page's whole filter state as a query string, so every link on it
    preserves the filters the visitor already set instead of dropping them."""
    parts = [f"sort={sort}"]
    parts += [f"tag={i}" for i in tag_ids]
    if status:
        parts.append(f"status={status}")
    return "&".join(parts)


def region_sidebar_links(
    venue_tags: list[Tag], selected: list[int], sort: str, status: str = ""
) -> list[dict]:
    """Sidebar filter data for VENUE tags grouped by region ("Other" bucket
    for unset) instead of one link per venue -- filtering by exact venue
    was called out as not useful. Toggling a region (de)selects every venue
    tag id in it together.

    `ids` is what the client-side filter actually uses (toggles all of a
    region's tag ids together, no server round trip); `href` stays as a
    plain-nav fallback for when JS is unavailable."""
    by_region: dict[str, list[Tag]] = {}
    for t in venue_tags:
        by_region.setdefault(t.region or "Other", []).append(t)
    links = []
    for region_name in sorted(by_region, key=lambda r: (r == "Other", r)):
        rtag_ids = [t.id for t in by_region[region_name]]
        active = any(i in selected for i in rtag_ids)
        others = [i for i in selected if i not in rtag_ids]
        href_ids = others if active else others + rtag_ids
        links.append({
            "name": region_name, "count": len(rtag_ids), "active": active,
            "href": "/discover?" + filter_query(sort, href_ids, status), "ids": rtag_ids,
        })
    return links


def concert_search_text(c: Concert) -> str:
    """Lowercased blob everything free-text search matches: title (plus its
    en/zh variants), every attached tag's name (all four kinds count --
    franchise/group/artist/venue -- plus each tag's en/zh variants), and the
    concert's free-text venue (all variants) as a fallback ONLY when no VENUE
    tag is attached (mirrors the tile macro's own venue display fallback in
    discover.html exactly). Localizing the haystack rather than the query lets
    a search in any language match a concert filled in any other."""
    parts = [c.title, c.title_en, c.title_zh]
    for t in c.tags:
        parts += [t.name, t.name_en, t.name_zh]
    if not any(t.kind is TagKind.VENUE for t in c.tags):
        parts += [c.venue, c.venue_en, c.venue_zh]
    return " ".join(p for p in parts if p).lower()


@router.get("/discover", response_class=HTMLResponse)
async def discover(
    request: Request,
    user: SessionUser | None = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    sort: str = "event",
    tag: list[int] = Query(default=[]),
    q: str = "",
    status: str = "",
):
    now = datetime.now(UTC)
    stmt = select(Concert).options(
        selectinload(Concert.days), selectinload(Concert.rounds), selectinload(Concert.tags)
    )
    # What this page shows lives in ONE place (service.discoverable_concert_criterion),
    # because Home's "N concerts in the catalogue" teaser has to count exactly
    # what this link leads to -- it used to count every Concert row instead.
    stmt = stmt.where(discoverable_concert_criterion())
    # No server-side tag filtering: every concert renders into the DOM tagged
    # with its tag ids, and JS toggles tile visibility -- tag filtering was the
    # slowest part of this page when it round-tripped the server on every click.
    if sort == "added":
        stmt = stmt.order_by(Concert.created_at.desc())
    else:  # "event" and "next": earliest LIVE day first, undated concerts last
        first_day = func.min(ConcertDay.starts_at_utc)
        stmt = (
            stmt.outerjoin(
                ConcertDay,
                (ConcertDay.concert_id == Concert.id) & (ConcertDay.cancelled.is_(False)),
            )
            .group_by(Concert.id)
            .order_by(first_day.is_(None), first_day)
        )
    concerts = list((await session.execute(stmt)).scalars())

    # ONE outcome query for the whole page, signed in; none at all signed out.
    statuses = await discover_statuses(session, concerts, user.id if user else None, now)

    if sort == "next":
        # Applied over the SQL order above, which therefore acts as the
        # tiebreak for concerts sharing a deadline (or having none).
        concerts.sort(key=lambda c: (statuses[c.id].next_deadline is None,
                                     statuses[c.id].next_deadline or now))

    tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
    by_kind = grouped_tags(tags)
    selected_tags = set(tag)
    query = q.strip().lower()
    facet = status if status in {key for key, _ in STATUS_FACETS} else ""

    # Initial visibility, computed server-side so there's no flash of wrongly
    # shown tiles before JS runs on first load -- and so tag, search and facet
    # all still work with JS off. Every subsequent change is client-side.
    visible_concert_ids = {
        c.id for c in concerts
        if (not selected_tags or ({t.id for t in c.tags} & selected_tags))
        and (not query or query in concert_search_text(c))
        and (not facet or statuses[c.id].status == facet)
    }

    tz, tz_auto = settings.default_timezone, True
    if user:
        db_user = await session.get(User, user.id)
        if db_user:
            tz, tz_auto = db_user.timezone, db_user.tz_auto

    # Sidebar chip counts (demo `.chip .n`) and the round-status facet's own
    # counts. The latter is a tally of `statuses` -- already built above for
    # the tile pills -- so it costs nothing extra to compute.
    tag_counts = await discoverable_tag_counts(session)
    status_counts = Counter(st.status for st in statuses.values())
    # Same two counts Home's teaser links out with (invariant: one definition
    # in discoverable_concert_criterion) -- the summary line under the
    # heading here, per the demo's "142 concerts · 38 with a round still
    # open" `.note`.
    catalogue_count = await discoverable_concert_count(session)
    open_round_count = await discoverable_open_round_count(session, now)

    return templates.TemplateResponse(
        request,
        "discover.html",
        {
            "user": user,
            "concerts": concerts,
            "statuses": statuses,
            "open_concert_ids": {
                cid for cid, st in statuses.items() if st.status == "open"
            },
            "catalogue_count": catalogue_count,
            "open_round_count": open_round_count,
            "tag_counts": tag_counts,
            "status_counts": status_counts,
            "by_kind": by_kind,
            "region_links": region_sidebar_links(by_kind.get("venue", []), tag, sort, facet),
            "selected_tags": selected_tags,
            "visible_concert_ids": visible_concert_ids,
            "deadlines": await upcoming_deadlines(session, now, limit=DEADLINE_LIST_LIMIT),
            "concert_tags_by_event_id": {
                c.event_id: {t.id for t in c.tags} for c in concerts
            },
            "concert_search_by_event_id": {
                c.event_id: concert_search_text(c) for c in concerts
            },
            "query": q,
            "sort": sort,
            "sorts": SORTS,
            "status": facet,
            "status_facets": STATUS_FACETS,
            "filter_query": filter_query,
            "tz": tz,
            "tz_auto": tz_auto,
            "TagKind": TagKind,
            "concert_kinds": list(ConcertKind),
            "bot_enabled": settings.bot_enabled,
            "nav_page": "discover",
        },
    )
