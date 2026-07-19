"""Web application: sessions, auth, concert CRUD."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db.models import Concert, ConcertDay, Tag, User
from app.db.service import LABEL_BY_ANCHOR, LABEL_BY_ROUND_KIND
from app.db.session import get_session
from app.domain.timezones import fmt_dual, utc_to_jst
from app.domain.types import TagKind
from app.ops import run_checks
from app.scheduler import heartbeat
from app.web import auth
from app.web.routes import calendar as calendar_routes
from app.web.routes import concerts as concert_routes
from app.web.routes import imports as import_routes
from app.web.routes import preferences as pref_routes
from app.web.routes import privacy as privacy_routes
from app.web.routes import reminders as reminder_routes
from app.web.routes import tags as tag_routes
from app.web.routes import terms as terms_routes
from app.web.routes import welcome as welcome_routes

_here = Path(__file__).parent
templates = Jinja2Templates(directory=_here / "templates")
templates.env.globals["dual"] = fmt_dual        # {{ dual(dt, tz) }}
templates.env.globals["jst"] = utc_to_jst       # {{ jst(dt).strftime(...) }}
templates.env.globals["deadline_label"] = lambda anchor: LABEL_BY_ANCHOR[anchor]
templates.env.globals["round_kind_label"] = lambda kind: LABEL_BY_ROUND_KIND[kind]

COMMON_TIMEZONES = [
    "America/Moncton", "America/Halifax", "America/Toronto", "America/Vancouver",
    "Asia/Tokyo", "Asia/Hong_Kong", "Asia/Singapore", "Australia/Sydney",
    "Europe/London", "Europe/Paris", "UTC",
]


def grouped_tags(tags):
    """franchise -> its groups (parent_id) -> members handled in templates;
    plus buckets for orphan groups / artists / venues."""
    by_kind = {}
    for t in tags:
        by_kind.setdefault(t.kind.value, []).append(t)
    return by_kind


def region_sidebar_links(venue_tags: list[Tag], selected: list[int], sort: str) -> list[dict]:
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
        href = f"/?sort={sort}" + "".join(f"&tag={i}" for i in href_ids)
        links.append({
            "name": region_name, "count": len(rtag_ids), "active": active,
            "href": href, "ids": rtag_ids,
        })
    return links


def has_open_round(concert: Concert, now: datetime) -> bool:
    """A concert is "open" if any of its non-cancelled rounds currently has
    an active application window: closes_at_utc set and in the future, AND
    opens_at_utc either unset or already passed. A round with only
    results/payment timestamps set is never "open" on its own."""
    from app.db.service import is_round_cancelled

    cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
    for r in concert.rounds:
        if is_round_cancelled(r, cancelled_day_ids):
            continue
        if r.closes_at_utc and r.closes_at_utc > now and (
            r.opens_at_utc is None or r.opens_at_utc <= now
        ):
            return True
    return False


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


def create_app() -> FastAPI:
    app = FastAPI(title="dekimasen.app", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.base_url.lower().startswith("https"),
        max_age=60 * 60 * 24 * 30,
    )
    app.mount("/static", StaticFiles(directory=_here / "static"), name="static")
    app.include_router(auth.router)

    # import_routes MUST be registered before concert_routes: GET /concerts/import
    # would otherwise be swallowed by GET /concerts/{event_id} -- FastAPI matches
    # the path template first, not the literal segment, so it doesn't fall
    # through to try the next route. (concerts.py additionally rejects "import"
    # and "new" as event_id values so they can never collide the other way.)
    import_routes.templates = templates
    app.include_router(import_routes.router)
    concert_routes.templates = templates
    app.include_router(concert_routes.router)
    # no templates: renders via concerts.render_rules_fragment
    app.include_router(reminder_routes.router)
    tag_routes.templates = templates
    app.include_router(tag_routes.router)
    pref_routes.templates = templates
    app.include_router(pref_routes.router)
    welcome_routes.templates = templates
    app.include_router(welcome_routes.router)
    # no templates: pure .ics responses
    app.include_router(calendar_routes.router)
    # /privacy and /terms are literal, unique paths -- order-independent,
    # unlike the imports/concerts pair above.
    privacy_routes.templates = templates
    app.include_router(privacy_routes.router)
    terms_routes.templates = templates
    app.include_router(terms_routes.router)

    @app.get("/healthz")
    async def healthz(session: AsyncSession = Depends(get_session)) -> dict:
        scheduler_ok, last_tick = heartbeat.status()
        # run_checks never raises: every check is wrapped, so a broken check
        # degrades to ok=false rather than 500ing the endpoint the uptime
        # monitor is watching.
        results = await run_checks(session)
        return {
            # `ok` deliberately still follows the scheduler ALONE. UptimeRobot
            # keyword-matches '"ok":true'; folding degraded checks in here would
            # silently redefine an existing external alert. The detail lives in
            # `checks`, and the scheduler DMs on state change.
            "ok": scheduler_ok,
            "bot_enabled": settings.bot_enabled,
            "scheduler_ok": scheduler_ok,
            "scheduler_last_tick": last_tick,
            "checks": {r.name: {"ok": r.ok, "detail": r.detail} for r in results},
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        user: auth.SessionUser | None = Depends(auth.current_user),
        session: AsyncSession = Depends(get_session),
        sort: str = "event",
        tag: list[int] = Query(default=[]),
        q: str = "",
    ):
        concerts, tz, tz_auto, tags = [], settings.default_timezone, True, []
        open_concert_ids: set[int] = set()
        deadlines, concert_tags_by_event_id, concert_search_by_event_id = [], {}, {}
        if user:
            from sqlalchemy import func as sa_func

            from app.db.service import upcoming_deadlines

            stmt = select(Concert).options(selectinload(Concert.days), selectinload(Concert.rounds))
            # Hide a concert whose every existing leg is cancelled -- it has
            # no valid dates left, same treatment as a concert with zero
            # legs would get if it also had no live rounds, except this is a
            # deliberate exclusion rather than just sorting last. Still
            # reachable directly via /concerts/{event_id}. A concert with NO
            # days at all (e.g. a fresh draft) keeps today's existing
            # behavior of showing up, sorted last -- untouched by this.
            # .correlate(Concert) is required here: the "event" sort branch
            # below also outerjoins ConcertDay onto this same statement, so
            # without an explicit correlate() SQLAlchemy's auto-correlation
            # sees ConcertDay in both places and "correlates it away" from
            # these subqueries too, leaving them with zero FROM clauses.
            has_any_day = exists().where(ConcertDay.concert_id == Concert.id).correlate(Concert)
            has_live_day = (
                exists()
                .where(ConcertDay.concert_id == Concert.id, ConcertDay.cancelled.is_(False))
                .correlate(Concert)
            )
            stmt = stmt.where(~has_any_day | has_live_day)
            # No server-side tag filtering: every concert renders into the DOM
            # tagged with its tag ids, and JS toggles tile visibility -- tag
            # filtering was the slowest part of this page when it round-tripped
            # the server on every click.
            if sort == "added":
                stmt = stmt.order_by(Concert.created_at.desc())
            else:  # "event": earliest LIVE concert day first; undated concerts last
                first_day = sa_func.min(ConcertDay.starts_at_utc)
                stmt = (
                    stmt.outerjoin(
                        ConcertDay,
                        (ConcertDay.concert_id == Concert.id) & (ConcertDay.cancelled.is_(False)),
                    )
                    .group_by(Concert.id)
                    .order_by(first_day.is_(None), first_day)
                )
            stmt = stmt.options(selectinload(Concert.tags))
            concerts = list((await session.execute(stmt)).scalars())
            now = datetime.now(UTC)
            open_concert_ids = {c.id for c in concerts if has_open_round(c, now)}
            deadlines = await upcoming_deadlines(session, now, limit=10) if user else []
            concert_tags_by_event_id = {c.event_id: {t.id for t in c.tags} for c in concerts}
            concert_search_by_event_id = {c.event_id: concert_search_text(c) for c in concerts}
            tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
            db_user = await session.get(User, user.id)
            if db_user:
                tz, tz_auto = db_user.timezone, db_user.tz_auto
        from app.db.service import tag_picker_context
        from app.domain.types import ConcertKind as _CK
        from app.domain.types import TagKind as _TK

        # index.html only ever reads by_kind (it has no tag picker), so the
        # anonymous fallback supplies just that.
        picker = await tag_picker_context(session) if user else {"by_kind": grouped_tags(tags)}
        region_links = region_sidebar_links(picker["by_kind"].get("venue", []), tag, sort)
        selected_tags = set(tag)
        query = q.strip().lower()

        def matches_query(c: Concert) -> bool:
            if not query:
                return True
            return query in concert_search_text(c)

        # Initial visibility, computed server-side so there's no flash of
        # wrongly-shown tiles before JS runs on first load; every subsequent
        # filter change (tag AND search, combined) is handled entirely
        # client-side (see index.html).
        visible_concert_ids = {
            c.id for c in concerts
            if (not selected_tags or ({t.id for t in c.tags} & selected_tags)) and matches_query(c)
        }
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "user": user,
                "concerts": concerts,
                "all_tags": tags,
                "by_kind": picker["by_kind"],
                "region_links": region_links,
                "selected_tags": selected_tags,
                "visible_concert_ids": visible_concert_ids,
                "open_concert_ids": open_concert_ids,
                "deadlines": deadlines,
                "concert_tags_by_event_id": concert_tags_by_event_id,
                "concert_search_by_event_id": concert_search_by_event_id,
                "query": q,
                "sort": sort,
                "tz": tz,
                "tz_auto": tz_auto,
                "TagKind": _TK,
                "concert_kinds": list(_CK),
                "bot_enabled": settings.bot_enabled,
            },
        )

    return app
