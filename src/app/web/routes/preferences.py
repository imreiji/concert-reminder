"""User preferences: reminder presets and tag subscriptions.

  GET  /preferences                          the page
  POST /presets                              create preset
  POST /presets/{id}/delete
  POST /presets/{id}/items                   add an item to a preset
  POST /presets/{id}/items/{item_id}/delete
  POST /subscriptions                        subscribe to a tag (+preset, notify)
  POST /subscriptions/{id}/delete
  POST /concerts/{cid}/presets/{pid}/apply   one-click apply (rules fragment swap)

Everything here is per-user: routes verify ownership and 404 on other
people's presets/subscriptions rather than admitting they exist.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PresetItem, ReminderPreset, Tag, TagSubscription, User
from app.db.service import apply_preset, ensure_user, group_members, set_default_preset
from app.db.session import get_session
from app.domain.types import Anchor
from app.web.auth import SessionUser, require_user

router = APIRouter()

templates = None  # set by web.app at startup


async def my_presets(session: AsyncSession, user_id: int) -> list[ReminderPreset]:
    res = await session.execute(
        select(ReminderPreset)
        .where(ReminderPreset.user_id == user_id)
        .order_by(ReminderPreset.created_at)
    )
    presets = list(res.scalars())
    for p in presets:
        await session.refresh(p, ["items"])
    return presets


async def owned_preset(
    session: AsyncSession, user_id: int, preset_id: int
) -> ReminderPreset:
    preset = await session.get(ReminderPreset, preset_id)
    if preset is None or preset.user_id != user_id:
        raise HTTPException(status_code=404)
    return preset


# ── The page ─────────────────────────────────────────────────────────────


def all_timezones() -> list[str]:
    """Full IANA zone list, region-grouped in the template."""
    import zoneinfo

    return sorted(z for z in zoneinfo.available_timezones() if "/" in z or z == "UTC")


COMMON_TIMEZONES = [
    "America/Moncton", "America/Halifax", "America/Toronto", "America/Vancouver",
    "Asia/Tokyo", "Asia/Hong_Kong", "Asia/Singapore", "Australia/Sydney",
    "Europe/London", "Europe/Paris", "UTC",
]


@router.get("/preferences", response_class=HTMLResponse)
async def preferences(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    from app.domain.types import TagKind

    presets = await my_presets(session, user.id)
    subs = list((await session.execute(
        select(TagSubscription, Tag)
        .join(Tag, TagSubscription.tag_id == Tag.id)
        .where(TagSubscription.user_id == user.id)
        .order_by(Tag.name)
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
    db_user = await session.get(User, user.id)
    tz = db_user.timezone if db_user else "America/Moncton"
    tz_auto = db_user.tz_auto if db_user else True
    return templates.TemplateResponse(
        request,
        "preferences.html",
        {"user": user, "presets": presets, "subs": subs, "sub_by_tag": sub_by_tag,
         "franchises": franchises, "groups": groups, "members": members,
         "solo_artists": solo_artists, "venues": venues,
         "tz": tz, "tz_auto": tz_auto,
         "common_timezones": COMMON_TIMEZONES, "all_timezones": all_timezones(),
         "anchors": list(Anchor)},
    )


# ── Presets ──────────────────────────────────────────────────────────────


@router.post("/presets")
async def create_preset(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., min_length=1, max_length=100),
    anchor: Anchor = Form(Anchor.CLOSES),
    days: int = Form(3, ge=0, le=60),
    hours: int = Form(0, ge=0, le=23),
    direction: str = Form("before"),
):
    """Create a preset WITH its first item — no empty-preset limbo."""
    await ensure_user(session, user.id, user.username)
    preset = ReminderPreset(user_id=user.id, name=name.strip())
    session.add(preset)
    await session.flush()
    sign = 1 if direction == "after" else -1
    session.add(PresetItem(
        preset_id=preset.id, anchor=anchor,
        offset_days=sign * days, offset_hours=sign * hours,
    ))
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/presets/{preset_id}/rename")
async def rename_preset(
    preset_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., min_length=1, max_length=100),
):
    preset = await owned_preset(session, user.id, preset_id)
    preset.name = name.strip()
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/presets/{preset_id}/default")
async def make_default(
    preset_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """The default preset is what the DM 'Set my reminders' button applies."""
    await owned_preset(session, user.id, preset_id)
    await set_default_preset(session, user.id, preset_id)
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/presets/{preset_id}/delete")
async def delete_preset(
    preset_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    preset = await owned_preset(session, user.id, preset_id)
    await session.delete(preset)  # subscriptions keep working: preset_id SET NULL
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/presets/{preset_id}/items")
async def add_item(
    preset_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    anchor: Anchor = Form(...),
    days: int = Form(..., ge=0, le=60),
    hours: int = Form(0, ge=0, le=23),
    direction: str = Form("before"),
):
    await owned_preset(session, user.id, preset_id)
    sign = 1 if direction == "after" else -1
    session.add(PresetItem(
        preset_id=preset_id, anchor=anchor,
        offset_days=sign * days, offset_hours=sign * hours,
    ))
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/presets/{preset_id}/items/{item_id}/edit")
async def edit_item(
    preset_id: int,
    item_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    anchor: Anchor = Form(...),
    days: int = Form(..., ge=0, le=60),
    hours: int = Form(0, ge=0, le=23),
    direction: str = Form("before"),
):
    """Adjust an existing item in place — every field, no delete-and-rebuild."""
    await owned_preset(session, user.id, preset_id)
    item = await session.get(PresetItem, item_id)
    if item is None or item.preset_id != preset_id:
        raise HTTPException(status_code=404)
    sign = 1 if direction == "after" else -1
    item.anchor = anchor
    item.offset_days = sign * days
    item.offset_hours = sign * hours
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/presets/{preset_id}/items/{item_id}/delete")
async def delete_item(
    preset_id: int,
    item_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await owned_preset(session, user.id, preset_id)
    item = await session.get(PresetItem, item_id)
    if item is not None and item.preset_id == preset_id:
        await session.delete(item)
        await session.commit()
    return RedirectResponse("/preferences", status_code=303)


# ── Subscriptions ────────────────────────────────────────────────────────


@router.post("/subscriptions")
async def subscribe(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    tag_id: int = Form(...),
    preset_id: int = Form(0),
    notify: bool = Form(False),
):
    if await session.get(Tag, tag_id) is None:
        raise HTTPException(status_code=404, detail="tag not found")
    if preset_id:
        await owned_preset(session, user.id, preset_id)
    existing = await session.execute(
        select(TagSubscription).where(
            TagSubscription.user_id == user.id, TagSubscription.tag_id == tag_id
        )
    )
    sub = existing.scalar_one_or_none()
    await ensure_user(session, user.id, user.username)
    if sub is None:
        session.add(TagSubscription(
            user_id=user.id, tag_id=tag_id,
            preset_id=preset_id or None, notify=notify,
        ))
    else:  # re-submitting updates the existing subscription
        sub.preset_id = preset_id or None
        sub.notify = notify
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/subscriptions/{sub_id}/delete")
async def unsubscribe(
    sub_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(TagSubscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status_code=404)
    await session.delete(sub)
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


# ── One-click apply on a concert ─────────────────────────────────────────


@router.post("/concerts/{concert_id}/presets/{preset_id}/apply", response_class=HTMLResponse)
async def apply_preset_to_concert(
    request: Request,
    concert_id: int,
    preset_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    from app.db.models import Concert
    from app.web.routes.concerts import render_fragment

    concert = await session.get(Concert, concert_id)
    if concert is None:
        raise HTTPException(status_code=404)
    preset = await owned_preset(session, user.id, preset_id)
    await apply_preset(session, user.id, concert_id, preset)
    await session.commit()
    return await render_fragment(request, "_rules.html", concert, user, session)
