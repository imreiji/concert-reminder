"""Web application: sessions, auth, concert CRUD."""

from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db.models import Concert, ConcertDay, ConcertTag, Tag, User
from app.db.session import get_session
from app.domain.timezones import fmt_dual, utc_to_jst
from app.scheduler import heartbeat
from app.web import auth
from app.web.routes import concerts as concert_routes
from app.web.routes import imports as import_routes
from app.web.routes import preferences as pref_routes
from app.web.routes import tags as tag_routes

_here = Path(__file__).parent
templates = Jinja2Templates(directory=_here / "templates")
templates.env.globals["dual"] = fmt_dual        # {{ dual(dt, tz) }}
templates.env.globals["jst"] = utc_to_jst       # {{ jst(dt).strftime(...) }}

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
    tag id in it together, reusing the existing ?tag= ANY-of query param."""
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
        links.append({"name": region_name, "count": len(rtag_ids), "active": active, "href": href})
    return links


def create_app() -> FastAPI:
    app = FastAPI(title="dekimasen.app", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.base_url.startswith("https"),
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
    tag_routes.templates = templates
    app.include_router(tag_routes.router)
    pref_routes.templates = templates
    app.include_router(pref_routes.router)

    @app.get("/healthz")
    async def healthz() -> dict:
        scheduler_ok, last_tick = heartbeat.status()
        return {
            "ok": scheduler_ok,  # overall health follows the scheduler on purpose
            "bot_enabled": settings.bot_enabled,
            "scheduler_ok": scheduler_ok,
            "scheduler_last_tick": last_tick,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        user: auth.SessionUser | None = Depends(auth.current_user),
        session: AsyncSession = Depends(get_session),
        sort: str = "event",
        tag: list[int] = Query(default=[]),
    ):
        concerts, tz, tz_auto, tags = [], settings.default_timezone, True, []
        if user:
            from sqlalchemy import func as sa_func

            stmt = select(Concert).options(selectinload(Concert.days))
            if tag:
                # ANY-of semantics: a concert matches if it carries any selected tag
                stmt = stmt.join(ConcertTag).where(ConcertTag.tag_id.in_(tag)).distinct()
            if sort == "added":
                stmt = stmt.order_by(Concert.created_at.desc())
            else:  # "event": earliest concert day first; undated concerts last
                first_day = sa_func.min(ConcertDay.starts_at_utc)
                stmt = (
                    stmt.outerjoin(ConcertDay)
                    .group_by(Concert.id)
                    .order_by(first_day.is_(None), first_day)
                )
            stmt = stmt.options(selectinload(Concert.tags))
            concerts = list((await session.execute(stmt)).scalars())
            tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
            db_user = await session.get(User, user.id)
            if db_user:
                tz, tz_auto = db_user.timezone, db_user.tz_auto
        import json as _json

        from app.db.service import tag_picker_context
        from app.domain.types import ConcertKind as _CK
        from app.domain.types import TagKind as _TK

        picker = await tag_picker_context(session) if user else {
            "by_kind": grouped_tags(tags), "groups_json": {}, "tag_names_json": {},
        }
        region_links = region_sidebar_links(picker["by_kind"].get("venue", []), tag, sort)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "user": user,
                "concerts": concerts,
                "all_tags": tags,
                "by_kind": picker["by_kind"],
                "region_links": region_links,
                "groups_json": _json.dumps(picker["groups_json"]),
                "tag_names_json": _json.dumps(picker["tag_names_json"]),
                "selected_tags": set(tag),
                "sort": sort,
                "tz": tz,
                "tz_auto": tz_auto,
                "TagKind": _TK,
                "concert_kinds": list(_CK),
                "bot_enabled": settings.bot_enabled,
            },
        )

    return app
