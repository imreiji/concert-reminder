"""Web application: sessions, auth, concert CRUD."""

import logging
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Match

from app import i18n
from app.config import settings
from app.db.models import User
from app.db.service import LABEL_BY_ANCHOR, LABEL_BY_ROUND_KIND
from app.db.session import get_session
from app.domain.board import visible_rungs
from app.domain.sentence import split_slots
from app.domain.timezones import fmt_day_month, fmt_dual, fmt_dual_lines, utc_to_jst
from app.domain.types import TagKind
from app.domain.urls import safe_next
from app.ops import run_checks
from app.scheduler import heartbeat
from app.web import auth
from app.web.routes import admin as admin_routes
from app.web.routes import api as api_routes
from app.web.routes import calendar as calendar_routes
from app.web.routes import concerts as concert_routes
from app.web.routes import discover as discover_routes
from app.web.routes import discoveries as discoveries_routes
from app.web.routes import fetch_domains as fetch_domain_routes
from app.web.routes import imports as import_routes
from app.web.routes import outcomes as outcome_routes
from app.web.routes import preferences as pref_routes
from app.web.routes import privacy as privacy_routes
from app.web.routes import quiet_ladders as quiet_ladder_routes
from app.web.routes import rehearsal as rehearsal_routes
from app.web.routes import reminders as reminder_routes
from app.web.routes import setup as setup_routes
from app.web.routes import subscriptions as subscription_routes
from app.web.routes import tags as tag_routes
from app.web.routes import terms as terms_routes
from app.web.routes import welcome as welcome_routes
from app.web.static_assets import static_url

_here = Path(__file__).parent
log = logging.getLogger(__name__)

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
# The board card's ladder cap. Pure (domain.board), registered here so the
# rule has exactly one definition and _board.html can call it inline.
templates.env.globals["visible_rungs"] = visible_rungs


def sentence_slots(pattern: str, slots: dict[str, Markup]) -> Markup:
    """Render a translated reminder-rule pattern with the caller's selects.

    The reminder-rule builders (welcome's `remrow`, preferences' `sentence_fields`)
    pass an already-TRANSLATED pattern (`_("Remind me {offset} {direction}
    {anchor}.")`) plus a dict mapping each `{name}` to the server-built `<select>`
    Markup. This joins the pattern's parts in order: text runs are escaped
    (invariant 7 -- a translator-controlled string must not inject markup), and
    each slot renders ONLY the trusted, server-built select. An unknown
    placeholder raises (loud in the catalogue tests) via `split_slots`.
    """
    out: list = []
    for kind, value in split_slots(pattern, slots.keys()):
        out.append(slots[value] if kind == "slot" else value)
    return Markup("").join(out)


templates.env.globals["sentence_slots"] = sentence_slots


def fold_count_label(kind: str, n: int) -> str:
    """One state chip in a leg's fold summary ("2 lost", "1 upcoming").

    Each kind is its OWN msgid rather than a sentence assembled from pieces:
    composing "3 rounds - 2 lost, 1 skipped" out of fragments is a word-order
    trap in ja/zh, and this project's rule is that translators own word order.
    Chips turn the ordering into a layout question instead.

    The English singular and plural are identical -- none of these words
    inflects for number -- but the entries stay ngettext ones so a locale whose
    plural rules DO differ has somewhere to say so; ja/zh are nplurals=1 and
    collapse to one form. The literals are spelled out at each branch because
    `pybabel extract` only sees literal arguments -- a lookup table keyed by
    kind would extract nothing.

    An unknown kind RAISES rather than falling through to a default, following
    `domain/sentence.py:split_slots`: a silent default here would render a new
    `_FOLD_KINDS` entry as somebody else's chip -- "3 cancelled" quietly
    reading "3 upcoming" -- and nothing would fail. Loud at the seam instead.
    """
    if kind == "lost":
        text = i18n.ngettext("%(count)s lost", "%(count)s lost", n)
    elif kind == "skipped":
        text = i18n.ngettext("%(count)s skipped", "%(count)s skipped", n)
    elif kind == "covered":
        text = i18n.ngettext("%(count)s covered", "%(count)s covered", n)
    elif kind == "upcoming":
        text = i18n.ngettext("%(count)s upcoming", "%(count)s upcoming", n)
    else:
        raise ValueError(f"no fold chip for {kind!r}")
    return text % {"count": n}


templates.env.globals["fold_count_label"] = fold_count_label
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

    def _wants_html(request: Request) -> bool:
        """Is this a full-page navigation, as opposed to an XHR?

        The split that matters, and it is NOT the status code. `fetch()` sends
        `Accept: */*` and htmx sets `HX-Request`; a browser navigation always
        asks for `text/html`. Getting this backwards breaks the tag and venue
        quick-create dialogs, which read `(await resp.json()).detail` off a 409
        to offer "that already exists, select it instead" -- they would fall
        back to a generic failure with no message and nothing would say why.

        The agent API always answers JSON, including its errors. Its requests
        can carry a browser-like Accept header, so without this an agent's
        401 arrives as a styled HTML error page it cannot parse. Checked
        first because it is unconditional -- no header may override it.
        """
        if request.url.path.startswith("/api/"):
            return False
        if request.headers.get("hx-request"):
            return False
        return "text/html" in request.headers.get("accept", "")

    def _error_page(request: Request, status: int, messages: list[str] | None = None) -> Response:
        """Render the friendly page. Copy is per-code because each code has a
        different amount to usefully say."""
        # The signed-cookie display dict, not a DB read: an exception handler
        # has no dependency injection and must not be able to fail again while
        # rendering the failure.
        session_user = request.session.get("user") if "session" in request.scope else None
        name = (session_user or {}).get("username")
        if status == 404:
            heading = i18n.gettext("Page not found")
            body = i18n.gettext("That page doesn't exist. The link may be out of date.")
        elif status == 403:
            heading = i18n.gettext("No access")
            # Naming the account is the whole point: the commonest cause is
            # being signed in on the wrong Discord account, and nothing else
            # here would tell you which one you are.
            body = (
                i18n.gettext(
                    "You're signed in as %(name)s, which doesn't have access to that page."
                )
                % {"name": name}
                if name
                else i18n.gettext("That page needs an account with more access than yours.")
            )
        elif status == 422:
            heading = i18n.gettext("That didn't go through")
            body = i18n.gettext("Nothing was saved. Here's what needs fixing:")
        else:
            heading = i18n.gettext("Something broke")
            body = i18n.gettext(
                "Something broke on our end, and it's been logged. Nothing you did caused it."
            )
        return templates.TemplateResponse(
            request,
            "error.html",
            {"user": None, "heading": heading, "body": body, "messages": messages or []},
            status_code=status,
        )

    # Keyed on STARLETTE's HTTPException, not FastAPI's. FastAPI's subclasses
    # it, and Starlette resolves handlers by walking the RAISED class's MRO --
    # so a handler registered on the subclass never sees the parent, and an
    # unmatched route (which raises the parent) would keep returning JSON.
    # Registering the parent catches both.
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if not _wants_html(request):
            return await http_exception_handler(request, exc)
        detail = exc.detail
        messages = [str(detail)] if exc.status_code == 422 and detail else None
        return _error_page(request, exc.status_code, messages)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        if not _wants_html(request):
            return await request_validation_exception_handler(request, exc)
        # Pydantic's per-field errors, flattened to something a person can act
        # on: "body: field required" rather than the nested JSON shape.
        messages = [
            f"{'.'.join(str(p) for p in e.get('loc', ())[1:]) or 'form'}: {e.get('msg', '')}"
            for e in exc.errors()
        ]
        return _error_page(request, 422, messages)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> Response:
        """Starlette's default 500 is unstyled plain text with no way back.

        Logs FIRST and with the traceback: a prettier 500 that costs you the
        stack trace is a bad trade. Re-raises for a non-navigation so the
        default machinery (and TestClient's raise_server_exceptions) behaves
        exactly as before.
        """
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        if not _wants_html(request):
            raise exc
        return _error_page(request, 500)

    app.mount("/static", StaticFiles(directory=_here / "static"), name="static")
    app.include_router(auth.router)
    # /api/v1/* is a literal, unique prefix -- order-independent, unlike the
    # imports/concerts pair below.
    app.include_router(api_routes.router)

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
    # /admin/deliveries shares no prefix with a path-template route, so
    # registration order does not matter here (unlike imports/concerts).
    admin_routes.templates = templates
    app.include_router(admin_routes.router)
    # /admin/discoveries, likewise a literal path -- order-independent. Its own
    # router because a router registers whole and admin.py is already three
    # unrelated operational concerns.
    discoveries_routes.templates = templates
    app.include_router(discoveries_routes.router)
    # /admin/fetch-domains, likewise a literal path -- order-independent. Its
    # own router for the reason discoveries.py has one: a router registers
    # whole, and this is a fifth unrelated operational concern.
    fetch_domain_routes.templates = templates
    app.include_router(fetch_domain_routes.router)
    # /admin/quiet-ladders, likewise a literal path plus one {event_id} POST
    # route that shares no prefix with concerts.py's -- no ordering hazard.
    quiet_ladder_routes.templates = templates
    app.include_router(quiet_ladder_routes.router)
    # Gated, not guarded: production leaves rehearsal_enabled false, so these
    # routes are absent from the app entirely rather than merely protected.
    if settings.rehearsal_enabled:
        rehearsal_routes.templates = templates
        app.include_router(rehearsal_routes.router)

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

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots_txt() -> str:
        # Discover's filter chips are real links over a combinatorial ?tag=
        # URL space -- the open crawler trap that took production down on
        # 2026-08-04 (WISHLIST has the incident; deploy.md the dashboard
        # half). "Disallow: /discover?" is a literal prefix match against
        # path-plus-query under both the 1994 grammar and RFC 9309 ('?' is
        # not a metacharacter), so every query-stringed /discover URL is
        # blocked while the bare catalogue page stays crawlable -- no
        # wildcard support required of the crawler.
        return "User-agent: *\nDisallow: /discover?\n"

    @app.get("/healthz")
    async def healthz(
        session: AsyncSession = Depends(get_session),
        user: auth.SessionUser | None = Depends(auth.current_user),
    ) -> dict:
        """Liveness for anyone; diagnostics for an admin.

        The endpoint stays PUBLIC and unauthenticated -- UptimeRobot polls it
        with no cookie and keyword-matches '"ok":true', so that field's shape
        and meaning are an external contract.

        What moved behind admin is `checks`, whose details are infrastructure
        facts about a machine anonymous callers have no business reading: free
        disk in GB, the timestamp of the last backup, and -- on a marker that
        will not parse -- a filesystem path inside the exception string.
        `check_dms` already withheld its number for exactly this reason (it
        logs the count instead); the other three simply never got the same
        treatment, and this makes the surface consistent rather than
        per-check.

        Anonymous callers also skip run_checks ENTIRELY rather than having its
        output filtered out. `ok` is derived from the in-memory heartbeat
        alone, so the checks are not needed to answer them -- and running them
        anyway would do a disk stat, a file read and a COUNT(*) on the users
        table for every unauthenticated request, which is a free amplification
        primitive pointed at the one endpoint that must always answer.
        `Depends(auth.current_user)` (not require_admin) is what keeps the
        route reachable signed out: it returns None instead of raising.
        """
        scheduler_ok, last_tick = heartbeat.status()
        body = {
            # `ok` deliberately still follows the scheduler ALONE. UptimeRobot
            # keyword-matches '"ok":true'; folding degraded checks in here would
            # silently redefine an existing external alert. The detail lives in
            # `checks`, and the scheduler DMs on state change.
            "ok": scheduler_ok,
            "bot_enabled": settings.bot_enabled,
            "scheduler_ok": scheduler_ok,
            "scheduler_last_tick": last_tick,
        }
        if user is not None and user.is_admin:
            # run_checks never raises: every check is wrapped, so a broken check
            # degrades to ok=false rather than 500ing the endpoint the uptime
            # monitor is watching.
            results = await run_checks(session)
            body["checks"] = {r.name: {"ok": r.ok, "detail": r.detail} for r in results}
        return body

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
        ABSENCE of a limit argument on my_deadline_blocks -- POST
        /rounds/{id}/outcome re-renders the same fragment and also omits it, so
        both take DEADLINE_ROWS_LIMIT and the htmx swap can never change how
        many concerts the page shows. VISIBLE_BLOCKS (where the page-level fold
        starts) is passed for the same reason: both paths read the one constant
        rather than a literal, or the swap would silently re-fold the list."""
        from app.db.service import (
            VISIBLE_BLOCKS,
            board_cards,
            catalogue_tag_counts,
            discover_peek,
            discover_statuses,
            discoverable_concert_count,
            discoverable_open_round_count,
            my_deadline_blocks,
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
            blocks = await my_deadline_blocks(session, user.id, concert_ids=tracked)
            # "Up next" names ONE moment, so it still reads a row, not a block.
            # The block layer only groups and folds the rows it was built from,
            # so flattening them back into chronological order hands this pick
            # exactly what the flat list used to -- no second query, and no
            # second definition of what is upcoming.
            upcoming = sorted(
                (r for b in blocks for r in (b.lead, *b.others)),
                key=lambda r: r.deadline.at_utc,
            )
            # Column is a StrEnum, but an Enum member does not hash equal to
            # its value -- so a template doing columns["open"] would silently
            # miss. Re-key to plain strings at the boundary.
            ctx |= {
                "columns": {col.value: cards for col, cards in columns.items()},
                "open_total": open_total,
                "blocks": blocks,
                "visible_blocks": VISIBLE_BLOCKS,
                # The nearest thing that needs the user's attention: a row
                # with a round behind it, whatever anchor that row carries.
                # Falls back to the soonest row of any kind (an event start)
                # so the block is never empty when the list is not. The
                # template heads it "Up next", not "Closes next" -- see
                # home.html for why narrowing this to Anchor.CLOSES would be
                # the wrong repair.
                "up_next": next(
                    (r for r in upcoming if r.deadline.round_id is not None),
                    upcoming[0] if upcoming else None,
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
