"""Web application.

Phase 1: a status page and /healthz.
Phase 4 adds Discord OAuth (web/auth.py); Phase 5 adds concert CRUD routes.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings

_here = Path(__file__).parent
templates = Jinja2Templates(directory=_here / "templates")


def create_app() -> FastAPI:
    app = FastAPI(title="concert-reminder", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=_here / "static"), name="static")

    @app.get("/healthz")
    async def healthz() -> dict:
        """Uptime-monitor endpoint. Point UptimeRobot (or similar) here."""
        return {"ok": True, "bot_enabled": settings.bot_enabled}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "bot_enabled": settings.bot_enabled,
                "editor_count": len(settings.editor_ids),
            },
        )

    return app
