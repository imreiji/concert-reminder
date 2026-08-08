"""The agent read API: `/api/v1`, GET only.

Its own module because a router registers whole, and this is an unrelated
concern from the pages beside it -- the same reason `discoveries.py` and
`fetch_domains.py` are their own files.

English-only and NOT wrapped in `_()`, like /admin/deliveries: the consumer is
a program.

READ-ONLY BY CONSTRUCTION. Only `@router.get` appears here, and
`tests/test_api_auth.py::test_every_api_route_is_read_only` sweeps the routing
table for anything else. `import_commit` remains the only write path into
`concerts`; nothing in this module writes at all.

The token acts AS its minting user: `api_user` returns the same `SessionUser`
the cookie path builds, so `is_editor`/`is_admin` mean exactly what they mean
everywhere else and there is no second permission model to drift from the
first.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.service import get_user_by_api_token
from app.db.session import get_session
from app.web.auth import SessionUser

API_PREFIX = "/api/v1"

router = APIRouter(prefix=API_PREFIX)


async def api_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> SessionUser:
    """Resolve `Authorization: Bearer <token>` to a SessionUser, or 401.

    Every failure answers the SAME 401 body -- absent header, wrong scheme,
    unparseable, unknown token. Distinguishing them would let a prober learn
    which tokens exist, and the caller can do nothing different about any of
    them anyway.
    """
    unauthorized = HTTPException(status_code=401, detail="invalid or missing API token")
    if not authorization:
        raise unauthorized
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        raise unauthorized

    user = await get_user_by_api_token(session, raw.strip())
    if user is None:
        raise unauthorized

    # Same resolution current_user() does, from the same inputs: env whitelist,
    # admin whitelist, or the DB flag.
    is_editor = (
        settings.is_editor(user.discord_id)
        or settings.is_admin(user.discord_id)
        or user.is_editor
    )
    return SessionUser(
        id=user.discord_id,
        username=user.username,
        avatar=None,
        is_editor=is_editor,
        dm_blocked=user.dm_blocked_since is not None,
    )


async def api_admin(user: SessionUser = Depends(api_user)) -> SessionUser:
    """403, not 404: the caller authenticated fine and simply lacks the tier.
    Same split web/auth.py draws -- signed out is 401, signed in and
    unauthorized is 403."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


@router.get("/whoami")
async def whoami(user: SessionUser = Depends(api_user)) -> dict:
    """The first call to make when auth misbehaves: it turns 'my token does not
    work' into one request that says which account it resolved to."""
    return {
        "discord_id": user.id,
        "username": user.username,
        "is_editor": user.is_editor,
        "is_admin": user.is_admin,
    }
