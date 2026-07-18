"""First-run guided setup: a five-step wizard offered once at first login
(see auth.py's callback -- a brand-new row redirects here instead of /).
Each step reuses an existing action's route verbatim; this file only
sequences them.

  GET  /welcome              current step's screen (redirects to / once done)
  POST /welcome/advance      move to the next step
  POST /welcome/skip-all     jump straight to done
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tag, TagSubscription
from app.db.service import ensure_user, group_members
from app.db.session import get_session
from app.domain.types import Anchor, TagKind
from app.web.auth import SessionUser, require_user
from app.web.routes.preferences import my_presets

router = APIRouter()

templates = None  # set by web.app at startup

TOTAL_STEPS = 5


@router.get("/welcome", response_class=HTMLResponse)
async def welcome(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    if db_user.onboarding_step >= TOTAL_STEPS:
        return RedirectResponse("/", status_code=303)

    step = db_user.onboarding_step
    context = {"user": user, "step": step}

    if step == 0:
        subs = list((await session.execute(
            select(TagSubscription, Tag)
            .join(Tag, TagSubscription.tag_id == Tag.id)
            .where(TagSubscription.user_id == user.id)
        )).all())
        sub_by_tag = {tag.id: sub for sub, tag in subs}
        tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
        franchises = [t for t in tags if t.kind is TagKind.FRANCHISE]
        groups = [t for t in tags if t.kind is TagKind.GROUP]
        venues = [t for t in tags if t.kind is TagKind.VENUE]
        members = {g.id: await group_members(session, g.id) for g in groups}
        grouped_artist_ids = {m.id for ms in members.values() for m in ms}
        solo_artists = [
            t for t in tags if t.kind is TagKind.ARTIST and t.id not in grouped_artist_ids
        ]
        context.update({
            "franchises": franchises, "groups": groups, "members": members,
            "solo_artists": solo_artists, "venues": venues, "sub_by_tag": sub_by_tag,
        })
    elif step == 1:
        presets = await my_presets(session, user.id)
        context.update({"has_preset": bool(presets), "anchors": list(Anchor)})

    return templates.TemplateResponse(request, "welcome.html", context)


@router.post("/welcome/advance")
async def advance(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    db_user.onboarding_step = min(db_user.onboarding_step + 1, TOTAL_STEPS)
    await session.commit()
    return RedirectResponse("/welcome", status_code=303)


@router.post("/welcome/skip-all")
async def skip_all(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    db_user.onboarding_step = TOTAL_STEPS
    await session.commit()
    return RedirectResponse("/", status_code=303)
