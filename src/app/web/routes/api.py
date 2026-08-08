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

from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.service import (
    api_concert_detail,
    api_concert_rows,
    api_draft_detail,
    api_draft_rows,
    api_lead_rows,
    api_tag_rows,
    concert_export_yaml,
    get_user_by_api_token,
)
from app.db.session import get_session
from app.web.auth import SessionUser
from app.web.paging import PageParams, page_envelope, page_params

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


@router.get("/concerts")
async def list_concerts(
    q: str = "",
    tag: list[str] = Query(default=[]),
    since: date | None = None,
    until: date | None = None,
    page: PageParams = Depends(page_params),
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The catalogue, for answering "do I already have this?".

    Any valid token: /discover is already public, so no tier is required.
    `tag` filters by HANDLE (Tag.slug), never by name -- invariant 3, names are
    not unique. `since`/`until` filter on LEG DATES.
    """
    rows, total = await api_concert_rows(
        session, q=q, tag_handles=tag, since=since, until=until,
        limit=page.limit, offset=page.offset,
    )
    return page_envelope(rows, total, page)


@router.get("/tags")
async def list_tags(
    kind: str | None = None,
    page: PageParams = Depends(page_params),
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The vocabulary, served -- so an agent stops inventing tag names that
    match nothing. Any valid token, same as /concerts."""
    rows, total = await api_tag_rows(
        session, kind=kind, limit=page.limit, offset=page.offset
    )
    return page_envelope(rows, total, page)


@router.get("/leads")
async def list_leads(
    page: PageParams = Depends(page_params),
    user: SessionUser = Depends(api_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The open discovery queue. ADMIN only -- same audience as
    /admin/discoveries, which is where these are triaged."""
    rows, total = await api_lead_rows(session, limit=page.limit, offset=page.offset)
    return page_envelope(rows, total, page)


@router.get("/drafts")
async def list_drafts(
    page: PageParams = Depends(page_params),
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The token holder's OWN open pending-import drafts -- never another
    user's. Two editors (or agents) triaging their own batches at once is the
    expected case, not an exotic one; see `api_draft_rows`."""
    rows, total = await api_draft_rows(session, user.id, limit=page.limit, offset=page.offset)
    return page_envelope(rows, total, page)


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: int,
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """One draft's full text plus its completion evidence -- the pairing that
    closes the agent's iteration loop (see `api_draft_detail`). Another
    user's draft answers 404, never 403 -- invariant 5's ownership rule."""
    row = await api_draft_detail(session, draft_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    return row


@router.get("/concerts/{event_id}")
async def get_concert(
    event_id: str,
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """One concert, plus its draft YAML.

    The two halves are composed HERE rather than inside `api_concert_detail`:
    `concert_export_yaml` lives in `db/tags.py`, and `db/core.py` must never
    import a sibling feature module (see the docstring there).
    """
    found = await api_concert_detail(session, event_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such concert")
    row, concert = found
    row["draft_yaml"] = await concert_export_yaml(session, concert)
    return row
