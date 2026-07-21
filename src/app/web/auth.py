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
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User, WebSession
from app.db.service import ensure_user
from app.db.session import get_session
from app.domain.urls import safe_next
from app.i18n import LANG_COOKIE_MAX_AGE
from app.i18n import SUPPORTED as SUPPORTED_LANGUAGES

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
    # Resolved once in current_user() (env whitelist OR admin OR DB flag) —
    # unlike is_admin this needs a DB read, so it can't be a cheap property.
    is_editor: bool = False
    # Also resolved in current_user() from the same already-loaded User row
    # (dm_blocked_since is not None) -- zero extra query cost. Drives the
    # sitewide "DMs blocked" banner in base.html.
    dm_blocked: bool = False

    @property
    def is_admin(self) -> bool:
        return settings.is_admin(self.id)

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
async def login(request: Request, next_: str = Query("", alias="next")) -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    # The return path rides in OUR signed session cookie, exactly like
    # oauth_state -- it is never handed to Discord as a query param, so it
    # cannot come back attacker-controlled. Always assign (not just on a
    # hit), or a stale destination from an abandoned login outlives it.
    request.session["oauth_next"] = safe_next(next_) or ""
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
    # Pop unconditionally: a failed callback must not leave a destination
    # behind for whatever login attempt comes next.
    destination = safe_next(request.session.pop("oauth_next", None))
    if not code or not state or state != expected:
        raise HTTPException(status_code=400, detail="OAuth state mismatch — try logging in again")

    token = await exchange_code(code)
    me = await fetch_identity(token)

    user_id = int(me["id"])
    username = me.get("global_name") or me["username"]
    is_new_user = await db.get(User, user_id) is None
    await ensure_user(db, user_id, username)
    db_user = await db.get(User, user_id)
    lang_cookie = request.cookies.get("lang")
    if is_new_user and lang_cookie in SUPPORTED_LANGUAGES:
        # Seed at creation ONLY: the column can't distinguish "default en"
        # from "chose en", so only the moment before the row exists is safe.
        db_user.language = lang_cookie
    sid = await create_web_session(db, user_id)
    await db.commit()

    request.session["sid"] = sid
    request.session["user"] = {"id": user_id, "username": username, "avatar": me.get("avatar")}
    log.info("login: %s (%s)", username, user_id)
    # A brand-new account goes through the wizard no matter what asked for the
    # login: someone who has not picked a single tag yet is not served by
    # landing on the concert page that happened to bounce them here.
    response = RedirectResponse("/welcome" if is_new_user else (destination or "/"))
    # Set the cookie from the (possibly just-seeded, possibly pre-existing)
    # column so this browser now matches the account.
    response.set_cookie(
        "lang", db_user.language, max_age=LANG_COOKIE_MAX_AGE, samesite="lax", path="/",
    )
    return response


async def revoke_session(request: Request, db: AsyncSession) -> None:
    """Drop the cookie's session pointer and mark its DB row revoked so a
    replayed cookie is dead. Shared by logout and account deletion; the caller
    commits. Safe when the row is already gone -- delete_user's cascade removes
    the WebSession, and validate_web_session then simply returns None."""
    sid = request.session.pop("sid", None)
    request.session.pop("user", None)
    if sid:
        row = await validate_web_session(db, sid)
        if row is not None:
            row.revoked_at = datetime.now(UTC)  # server-side kill: replays are dead


@router.get("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_session)) -> RedirectResponse:
    await revoke_session(request, db)
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
    user_id = data["id"]
    db_user = await db.get(User, user_id)
    is_editor = (
        settings.is_editor(user_id)
        or settings.is_admin(user_id)
        or (db_user.is_editor if db_user else False)
    )
    return SessionUser(
        id=user_id, username=data["username"], avatar=data.get("avatar"), is_editor=is_editor,
        dm_blocked=bool(db_user and db_user.dm_blocked_since is not None),
    )


class LoginRequired(Exception):
    """A signed-out visitor reached a signed-in-only route.

    Deliberately NOT an HTTPException: a bare 401 is a dead end in a browser
    (no auth challenge this app can answer, just an error page). `web/app.py`
    registers a handler that sends the visitor to Home instead, which signed
    out IS the landing page, sign-in CTA and all.
    """


async def require_user(user: SessionUser | None = Depends(current_user)) -> SessionUser:
    if user is None:
        raise LoginRequired
    return user


async def require_editor(user: SessionUser = Depends(require_user)) -> SessionUser:
    if not user.is_editor:
        raise HTTPException(status_code=403, detail="Editor access required")
    return user


async def require_admin(user: SessionUser = Depends(require_user)) -> SessionUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
