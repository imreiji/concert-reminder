"""Web application.

Phase 4: Discord OAuth login + signed sessions.
Phase 5 adds concert CRUD routes on top of require_editor.
"""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.web.auth import SessionUser, current_user
from app.web.auth import router as auth_router

_here = Path(__file__).parent
templates = Jinja2Templates(directory=_here / "templates")


def create_app() -> FastAPI:
    app = FastAPI(title="concert-reminder", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.base_url.startswith("https"),
        max_age=60 * 60 * 24 * 30,  # 30-day sessions
    )
    app.mount("/static", StaticFiles(directory=_here / "static"), name="static")
    app.include_router(auth_router)

    @app.get("/healthz")
    async def healthz() -> dict:
        """Uptime-monitor endpoint. Point UptimeRobot (or similar) here."""
        return {"ok": True, "bot_enabled": settings.bot_enabled}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, user: SessionUser | None = Depends(current_user)):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "user": user,
                "bot_enabled": settings.bot_enabled,
                "editor_count": len(settings.editor_ids),
            },
        )

    return app
