"""Web application: sessions, auth, concert CRUD."""

from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Match

from app import i18n
from app.config import settings
from app.db.models import User
from app.db.service import LABEL_BY_ANCHOR, LABEL_BY_ROUND_KIND
from app.db.session import get_session
from app.domain.timezones import fmt_day_month, fmt_dual, fmt_dual_lines, utc_to_jst
from app.domain.types import TagKind
from app.domain.urls import safe_next
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
from app.web.static_assets import static_url

_here = Path(__file__).parent
templates = Jinja2Templates(directory=_here / "templates")
templates.env.add_extension("jinja2.ext.i18n")
# newstyle=True gives {% trans %} and {{ _("...") }} that read the request's
# ContextVar on every call -- one shared env, per-request locale.
templates.env.install_gettext_callables(
    gettext=i18n.gettext, ngettext=i18n.ngettext, newstyle=True
)
# Locale-aware wrappers: template-side signatures unchanged -- the active
# locale is picked up here, NOT passed by templates.
templates.env.globals["dual"] = lambda dt, tz: fmt_dual(dt, tz, i18n.get_locale())
templates.env.globals["dual_lines"] = lambda dt, tz: fmt_dual_lines(dt, tz, i18n.get_locale())
templates.env.globals["jst"] = utc_to_jst       # {{ jst(dt).strftime(...) }}
templates.env.globals["day_month"] = lambda dt: fmt_day_month(dt, i18n.get_locale())
templates.env.globals["deadline_label"] = lambda anchor: i18n.gettext(LABEL_BY_ANCHOR[anchor])
templates.env.globals["round_kind_label"] = lambda kind: i18n.gettext(LABEL_BY_ROUND_KIND[kind])
templates.env.globals["current_locale"] = i18n.get_locale  # {{ current_locale() }}
# UGC parallel-column display: {{ loc(concert, "title") }} / {{ loc(tag, "name") }}
# renders the viewer-locale variant, falling back to the original. Display
# ONLY -- form values, data-* filter keys and URLs keep the original field.
templates.env.globals["loc"] = lambda obj, field: i18n.loc_field(obj, field, i18n.get_locale())
# Filter form for `| map("loc_name")` over a tag list (the "F · G" eyebrow joins).
templates.env.filters["loc_name"] = lambda tag: i18n.loc_field(tag, "name", i18n.get_locale())
# Cache-busting: {{ static_url("style.css") }} -> /static/style.css?v=<hash>.
# Content hash, memoized per file at first use -- see web/static_assets.py.
templates.env.globals["static_url"] = static_url

def home_with_next(target: str | None) -> str:
    """Home, carrying the page the visitor actually asked for (if any)."""
    return f"/?next={quote(target, safe='')}" if target else "/"


def login_url(request: Request) -> str:
    """The sign-in href for the CURRENT page, preserving any `next`.

    A template global rather than per-route context so every CTA agrees --
    header, tab bar and both landing-page buttons. Miss one and that button
    silently drops the destination the others keep.
    """
    target = safe_next(request.query_params.get("next"))
    return f"/auth/login?next={quote(target, safe='')}" if target else "/auth/login"


templates.env.globals["login_url"] = login_url

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

    @app.middleware("http")
    async def set_request_locale(request: Request, call_next):
        # Cookie is a per-browser cache of users.language (synced at login and
        # by POST /language); header detection covers first-ever visits. The
        # ContextVar set here is visible downstream (Starlette runs call_next
        # in a child task, which copies this context).
        lang = request.cookies.get("lang")
        if lang not in i18n.SUPPORTED:
            lang = i18n.negotiate(request.headers.get("accept-language", ""))
        i18n.set_locale(lang)
        return await call_next(request)

    @app.exception_handler(auth.LoginRequired)
    async def login_required(request: Request, exc: auth.LoginRequired) -> Response:
        # Signed out, Home IS the landing page (hero + sign-in CTA), so send
        # them there rather than dead-ending on a 401 error page. 303 and not
        # 307: a signed-out POST must not be replayed against /.
        if request.headers.get("hx-request"):
            # An htmx XHR would FOLLOW a 303 and swap the whole landing page
            # into whatever fragment target the request had. HX-Redirect makes
            # the browser navigate instead, which is what a session that
            # expired mid-page actually needs. The destination is the PAGE they
            # were on (HX-Current-URL), never this fragment endpoint -- and only
            # its path survives, so the header's origin can't steer us.
            current = urlsplit(request.headers.get("hx-current-url", ""))
            target = safe_next(current.path + (f"?{current.query}" if current.query else ""))
            return Response(status_code=204, headers={"HX-Redirect": home_with_next(target)})
        # Only a GET is worth returning to. A signed-out POST's body is gone by
        # now, so replaying its URL after login would render a form that looks
        # like it submitted and didn't.
        target = None
        if request.method == "GET":
            query = request.url.query
            target = safe_next(request.url.path + (f"?{query}" if query else ""))
        return RedirectResponse(home_with_next(target), status_code=303)

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

    @app.post("/language")
    async def set_language(
        request: Request,
        user: auth.SessionUser | None = Depends(auth.current_user),
        session: AsyncSession = Depends(get_session),
        language: str = Form(...),
        next_url: str = Form("/", alias="next"),
    ):
        """The header switcher's single write path: cookie always; the DB
        column too when signed in (Discord DMs read the column). Public on
        purpose -- anonymous visitors switch the landing/discover/legal pages."""
        if language not in i18n.SUPPORTED:
            raise HTTPException(status_code=422, detail=f"unsupported language: {language}")
        # Local-path redirect guard: same shape as the auth callback's, but
        # sitewide, since the switcher lives on every page. Beyond locality,
        # `next` must be GET-routable: a page rendered from a POST-only route
        # (the import preview) would otherwise 303 the browser into a 405.
        # Such pages should also set `lang_next_url` for a better landing.
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        else:
            probe = {"type": "http", "method": "GET", "path": next_url}
            if all(r.matches(probe)[0] is not Match.FULL for r in app.routes):
                next_url = "/"
        if user:
            from app.db.service import ensure_user

            db_user = await ensure_user(session, user.id, user.username)
            db_user.language = language
            await session.commit()
        response = RedirectResponse(next_url, status_code=303)
        response.set_cookie(
            "lang", language, max_age=i18n.LANG_COOKIE_MAX_AGE, samesite="lax", path="/",
        )
        return response

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
            catalogue_tag_counts,
            discover_peek,
            discover_statuses,
            discoverable_concert_count,
            discoverable_open_round_count,
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
                # The teaser's "N with a round still open" clause.
                "open_round_count": await discoverable_open_round_count(session),
            }
            # The peek grid: a taste of the catalogue below the teaser,
            # excluding what's already on the board above it. Same card shape
            # /discover renders, via the same discover_statuses pill.
            peek_concerts = await discover_peek(session, tracked, limit=4)
            ctx["peek_concerts"] = peek_concerts
            ctx["peek_statuses"] = await discover_statuses(session, peek_concerts, user.id)
        else:
            # The landing page: the catalogue stat line plus a taste of
            # Discover, reusing the exact helpers /discover itself calls --
            # no separate landing-only query path (see spec's "Signed-out
            # home" section).
            ctx["catalogue_count"] = await discoverable_concert_count(session)
            ctx["open_round_count"] = await discoverable_open_round_count(session)
            tag_counts = await catalogue_tag_counts(session)
            ctx["franchise_count"] = tag_counts.get(TagKind.FRANCHISE, 0)
            ctx["performer_count"] = tag_counts.get(TagKind.ARTIST, 0)
            # No tracked concerts to exclude -- everyone is a stranger signed out.
            landing_concerts = await discover_peek(session, set(), limit=3)
            ctx["landing_concerts"] = landing_concerts
            ctx["landing_statuses"] = await discover_statuses(session, landing_concerts, None)
        return templates.TemplateResponse(request, "home.html", ctx)

    return app
