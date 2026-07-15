"""Discord OAuth2 login ("Sign in with Discord") and session handling.

Flow (standard authorization-code):
  1. /auth/login    -> redirect to Discord's consent page (scope: identify only)
  2. /auth/callback -> exchange code for a token, fetch the user's identity,
                       upsert their DB row, store {id, username, avatar} in a
                       signed session cookie, redirect home.
  3. /auth/logout   -> clear the session.

Security notes:
  * `state` parameter: random per-attempt value stored in the session and
    verified on callback — blocks login CSRF.
  * We request the minimum scope (`identify`): username + id + avatar.
    No email, no guilds, no token storage — the access token is used once
    to fetch identity and then discarded.
  * The session cookie is signed (SessionMiddleware + SESSION_SECRET) and
    https-only in production.

Authorization model (matches the bot):
  * anyone with Discord can log in -> manage their own reminders
  * only EDITOR_WHITELIST ids pass require_editor -> concert CRUD (Phase 5)
"""

import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.db.service import ensure_user
from app.db.session import SessionMaker

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")

DISCORD_AUTHORIZE = "https://discord.com/oauth2/authorize"
DISCORD_API = "https://discord.com/api/v10"


@dataclass(frozen=True)
class SessionUser:
    id: int
    username: str
    avatar: str | None

    @property
    def is_editor(self) -> bool:
        return settings.is_editor(self.id)

    @property
    def avatar_url(self) -> str:
        if self.avatar:
            return f"https://cdn.discordapp.com/avatars/{self.id}/{self.avatar}.png?size=64"
        return "https://cdn.discordapp.com/embed/avatars/0.png"


# ── Discord API calls (module-level so tests can monkeypatch) ────────────


async def exchange_code(code: str) -> str:
    """Trade the authorization code for an access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{settings.base_url}/auth/callback",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        log.warning("token exchange failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail="Discord token exchange failed")
    return resp.json()["access_token"]


async def fetch_identity(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Discord identity fetch failed")
    return resp.json()


async def persist_user(discord_id: int, username: str) -> None:
    async with SessionMaker() as session:
        await ensure_user(session, discord_id, username)
        await session.commit()


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = urlencode(
        {
            "client_id": settings.discord_client_id,
            "redirect_uri": f"{settings.base_url}/auth/callback",
            "response_type": "code",
            "scope": "identify",
            "state": state,
            "prompt": "none",  # returning users skip the consent screen
        }
    )
    return RedirectResponse(f"{DISCORD_AUTHORIZE}?{params}")


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    expected = request.session.pop("oauth_state", None)
    if not code or not state or state != expected:
        raise HTTPException(status_code=400, detail="OAuth state mismatch — try logging in again")

    token = await exchange_code(code)
    me = await fetch_identity(token)

    user_id = int(me["id"])
    username = me.get("global_name") or me["username"]
    await persist_user(user_id, username)

    request.session["user"] = {"id": user_id, "username": username, "avatar": me.get("avatar")}
    log.info("login: %s (%s)", username, user_id)
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.pop("user", None)
    return RedirectResponse("/")


# ── Dependencies for other routes ────────────────────────────────────────


def current_user(request: Request) -> SessionUser | None:
    data = request.session.get("user")
    if not data:
        return None
    return SessionUser(id=data["id"], username=data["username"], avatar=data.get("avatar"))


def require_user(request: Request) -> SessionUser:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_editor(request: Request) -> SessionUser:
    user = require_user(request)
    if not user.is_editor:
        raise HTTPException(status_code=403, detail="Editor access required")
    return user
