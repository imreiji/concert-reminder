"""Web application: sessions, auth, concert CRUD."""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db.models import Concert, User
from app.db.session import get_session
from app.domain.timezones import fmt_dual, utc_to_jst
from app.scheduler import heartbeat
from app.web import auth
from app.web.routes import concerts as concert_routes

_here = Path(__file__).parent
templates = Jinja2Templates(directory=_here / "templates")
templates.env.globals["dual"] = fmt_dual        # {{ dual(dt, tz) }}
templates.env.globals["jst"] = utc_to_jst       # {{ jst(dt).strftime(...) }}

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
        https_only=settings.base_url.startswith("https"),
        max_age=60 * 60 * 24 * 30,
    )
    app.mount("/static", StaticFiles(directory=_here / "static"), name="static")
    app.include_router(auth.router)

    concert_routes.templates = templates
    app.include_router(concert_routes.router)

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
    ):
        concerts, tz = [], settings.default_timezone
        if user:
            res = await session.execute(select(Concert).order_by(Concert.created_at.desc()))
            concerts = list(res.scalars())
            db_user = await session.get(User, user.id)
            if db_user:
                tz = db_user.timezone
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "user": user,
                "concerts": concerts,
                "timezone": tz,
                "timezones": COMMON_TIMEZONES,
                "bot_enabled": settings.bot_enabled,
            },
        )

    return app
