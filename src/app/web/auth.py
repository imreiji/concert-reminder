"""Discord OAuth2 login + server-side sessions.

OAuth flow unchanged from Phase 4 (authorization code, `state` CSRF check,
minimum `identify` scope, token used once and discarded).

Sessions are now DB-BACKED (Phase 8), fixing cookie replay:
  * login mints a random 256-bit token; the cookie stores the token,
    the DB stores only its SHA-256 hash + expiry.
  * every request validates the token against the DB: unknown, expired,
    or revoked -> anonymous.
  * logout REVOKES server-side, so a stolen/replayed cookie dies with it.
  * each login mints a fresh token (rotation); expired rows are swept
    opportunistically at login.

Display data (username/avatar) stays in the signed cookie for zero-query
rendering; AUTHORITY always comes from the DB row.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import WebSession
from app.db.service import ensure_user
from app.db.session import get_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")

DISCORD_AUTHORIZE = "https://discord.com/oauth2/authorize"
DISCORD_API = "https://discord.com/api/v10"
SESSION_DAYS = 30


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


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Discord API calls (module-level so tests can monkeypatch) ────────────


async def exchange_code(code: str) -> str:
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


# ── Server-side session helpers ──────────────────────────────────────────


async def create_web_session(db: AsyncSession, user_id: int) -> str:
    """Mint a session row, sweep expired ones, return the raw token."""
    now = datetime.now(UTC)
    await db.execute(delete(WebSession).where(WebSession.expires_at < now))
    token = secrets.token_urlsafe(32)
    db.add(WebSession(
        token_hash=_hash(token), user_id=user_id,
        expires_at=now + timedelta(days=SESSION_DAYS),
    ))
    await db.flush()
    return token


async def validate_web_session(db: AsyncSession, token: str) -> WebSession | None:
    res = await db.execute(select(WebSession).where(WebSession.token_hash == _hash(token)))
    row = res.scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at < datetime.now(UTC):
        return None
    return row


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = urlencode({
        "client_id": settings.discord_client_id,
        "redirect_uri": f"{settings.base_url}/auth/callback",
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "none",
    })
    return RedirectResponse(f"{DISCORD_AUTHORIZE}?{params}")


@router.get("/callback")
async def callback(
    request: Request,
    code: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    expected = request.session.pop("oauth_state", None)
    if not code or not state or state != expected:
        raise HTTPException(status_code=400, detail="OAuth state mismatch — try logging in again")

    token = await exchange_code(code)
    me = await fetch_identity(token)

    user_id = int(me["id"])
    username = me.get("global_name") or me["username"]
    await ensure_user(db, user_id, username)
    sid = await create_web_session(db, user_id)
    await db.commit()

    request.session["sid"] = sid
    request.session["user"] = {"id": user_id, "username": username, "avatar": me.get("avatar")}
    log.info("login: %s (%s)", username, user_id)
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_session)) -> RedirectResponse:
    sid = request.session.pop("sid", None)
    request.session.pop("user", None)
    if sid:
        row = await validate_web_session(db, sid)
        if row is not None:
            row.revoked_at = datetime.now(UTC)  # server-side kill: replays are dead
            await db.commit()
    return RedirectResponse("/")


# ── Dependencies ─────────────────────────────────────────────────────────


async def current_user(
    request: Request, db: AsyncSession = Depends(get_session)
) -> SessionUser | None:
    data = request.session.get("user")
    sid = request.session.get("sid")
    if not data or not sid:
        return None
    row = await validate_web_session(db, sid)
    if row is None or row.user_id != data["id"]:
        request.session.pop("sid", None)  # stale/revoked cookie: clean it up
        request.session.pop("user", None)
        return None
    return SessionUser(id=data["id"], username=data["username"], avatar=data.get("avatar"))


async def require_user(user: SessionUser | None = Depends(current_user)) -> SessionUser:
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    return user


async def require_editor(user: SessionUser = Depends(require_user)) -> SessionUser:
    if not user.is_editor:
        raise HTTPException(status_code=403, detail="Editor access required")
    return user
