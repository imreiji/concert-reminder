"""Secret tokens at rest.

One hash implementation for every personal-secret-link feature. This module
exists because there were about to be two: `calendar_feed.py` had a private
`_hash_token`, and a second copy for the API token would be one refactor away
from disagreeing -- with a failure mode that is silent rather than loud, since
a mismatched hash just means a token that never matches anything.

Invariant 5's rule, applied here: `secrets.token_urlsafe`, only the SHA-256
stored, the raw value returned once and never recoverable. Recovery is
"generate a new one", which is why every generator overwrites in place.
"""

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def generate_api_token(session: AsyncSession, user_id: int) -> str:
    """(Re)generate the user's agent API token, returning the RAW value once.

    Overwriting invalidates any previously-issued token, because only the hash
    is stored and the old raw value stops matching. Fetched with session.get
    rather than ensure_user for the same reason generate_calendar_token is:
    callers are behind require_user, so the row already exists, and ensure_user
    would overwrite the username with a placeholder.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"no such user: {user_id}")
    token = secrets.token_urlsafe(32)
    user.api_token_hash = hash_token(token)
    await session.flush()
    return token


async def get_user_by_api_token(session: AsyncSession, token: str) -> User | None:
    """None for an unknown token. The caller must answer 401 identically for
    this and for a malformed header, so a probe cannot learn whether a given
    token exists."""
    if not token:
        return None
    res = await session.execute(
        select(User).where(User.api_token_hash == hash_token(token))
    )
    return res.scalar_one_or_none()
