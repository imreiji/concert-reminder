"""Web application: sessions, auth, concert CRUD."""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db.models import User
from app.db.service import LABEL_BY_ANCHOR, LABEL_BY_ROUND_KIND
from app.db.session import get_session
from app.domain.timezones import fmt_dual, fmt_dual_lines, utc_to_jst
from app.ops import run_checks
from app.scheduler import heartbeat
from app.web import auth
from app.web.routes import calendar as calendar_routes
from app.web.routes import concerts as concert_routes
from app.web.routes import discover as discover_routes
from app.web.routes import imports as import_routes
from app.web.routes import outcomes as outcome_routes
from app.web.routes import preferences as pref_routes
from app.web.routes import privacy as privacy_routes
from app.web.routes import reminders as reminder_routes
from app.web.routes import setup as setup_routes
from app.web.routes import subscriptions as subscription_routes
from app.web.routes import tags as tag_routes
from app.web.routes import terms as terms_routes
from app.web.routes import welcome as welcome_routes

_here = Path(__file__).parent
templates = Jinja2Templates(directory=_here / "templates")
templates.env.globals["dual"] = fmt_dual        # {{ dual(dt, tz) }}
templates.env.globals["dual_lines"] = fmt_dual_lines  # dual_lines(dt, tz) -> (date, time)
templates.env.globals["jst"] = utc_to_jst       # {{ jst(dt).strftime(...) }}
templates.env.globals["deadline_label"] = lambda anchor: LABEL_BY_ANCHOR[anchor]
templates.env.globals["round_kind_label"] = lambda kind: LABEL_BY_ROUND_KIND[kind]

COMMON_TIMEZONES = [
    "America/Moncton", "America/Halifax", "America/Toronto", "America/Vancouver",
    "Asia/Tokyo", "Asia/Hong_Kong", "Asia/Singapore", "Australia/Sydney",
    "Europe/London", "Europe/Paris", "UTC",
]


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
    # /concerts/{event_id}/subscription and .../legs/{day_id}/opt-out are all
    # deeper than the /concerts/{event_id} catch-all, so registration order
    # against concert_routes does not matter here (unlike imports/concerts).
    subscription_routes.templates = templates
    app.include_router(subscription_routes.router)
    tag_routes.templates = templates
    app.include_router(tag_routes.router)
    # /discover is a literal, unique path -- order-independent.
    discover_routes.templates = templates
    app.include_router(discover_routes.router)
    # /rounds/{id}/outcome is a literal, unique prefix -- order-independent.
    outcome_routes.templates = templates
    app.include_router(outcome_routes.router)
    pref_routes.templates = templates
    app.include_router(pref_routes.router)
    welcome_routes.templates = templates
    app.include_router(welcome_routes.router)
    # /setup* are literal, unique paths -- order-independent (the flow the
    # wizard hands off to; also Preferences' "Run first-time setup again").
    setup_routes.templates = templates
    app.include_router(setup_routes.router)
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
    async def home(
        request: Request,
        user: auth.SessionUser | None = Depends(auth.current_user),
        session: AsyncSession = Depends(get_session),
    ):
        """"Where do I stand" -- four blocks: Up next, the campaign board,
        Coming up, and a teaser out to Discover. Signed out it is the hero
        alone, which is what the old index already did.

        A thin shell: every query below is a single service call, and the only
        logic here is picking which row is "up next". Note the deliberate
        ABSENCE of a limit argument on my_deadline_rows -- POST
        /rounds/{id}/outcome re-renders the same fragment and also omits it, so
        both take DEADLINE_ROWS_LIMIT and the htmx swap can never change the
        number of rows on the page."""
        from app.db.service import (
            board_cards,
            discoverable_concert_count,
            my_deadline_rows,
            tracked_concert_ids,
        )

        ctx = {
            "user": user, "tz": settings.default_timezone, "tz_auto": True,
            "nav_page": "home",
        }
        if user:
            db_user = await session.get(User, user.id)
            if db_user:
                ctx["tz"], ctx["tz_auto"] = db_user.timezone, db_user.tz_auto
            # Resolved ONCE and handed to both: the board and the deadline
            # rows are two views of the same tracked set, and each used to
            # re-derive it with its own query on every render.
            tracked = await tracked_concert_ids(session, user.id)
            columns, open_total = await board_cards(session, user.id, concert_ids=tracked)
            rows = await my_deadline_rows(session, user.id, concert_ids=tracked)
            # Column is a StrEnum, but an Enum member does not hash equal to
            # its value -- so a template doing columns["open"] would silently
            # miss. Re-key to plain strings at the boundary.
            ctx |= {
                "columns": {col.value: cards for col, cards in columns.items()},
                "open_total": open_total,
                "rows": rows,
                # The nearest thing that needs the user's attention: a row
                # with a round behind it, whatever anchor that row carries.
                # Falls back to the soonest row of any kind (an event start)
                # so the block is never empty when the list is not. The
                # template heads it "Up next", not "Closes next" -- see
                # home.html for why narrowing this to Anchor.CLOSES would be
                # the wrong repair.
                "up_next": next(
                    (r for r in rows if r.deadline.round_id is not None),
                    rows[0] if rows else None,
                ),
                # What /discover would actually LIST, not every Concert row.
                "catalogue_count": await discoverable_concert_count(session),
            }
        return templates.TemplateResponse(request, "home.html", ctx)

    return app
