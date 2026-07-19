"""Tag management (editors).

Endpoints:
  GET  /tags                            tag directory (anyone signed in)
  POST /tags                            create tag                (editor)
  POST /tags/{id}/edit                  update location_url/region(editor)
  POST /tags/{id}/delete                delete tag everywhere     (editor)
  POST /tags/{id}/members               add member to a group     (editor)
  POST /tags/{gid}/members/{mid}/delete remove member from group  (editor)

Attaching/detaching tags on a concert now happens through the rich concert
edit page (web/routes/concerts.py's GET/POST /concerts/{event_id}/edit),
not through per-tag htmx endpoints here.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tag, TagMember
from app.db.service import (
    active_concerts_missing_member,
    attach_tag,
    ensure_user,
    find_tag_by_name,
    group_members,
    handle_newly_tagged,
    resolve_group_member,
)
from app.db.session import get_session
from app.domain.types import TagKind
from app.web.auth import SessionUser, require_editor, require_user
from app.web.forms import form_url

router = APIRouter()

templates = None  # set by web.app at startup


async def all_tags(session: AsyncSession) -> list[Tag]:
    return list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())


# ── Tag directory / management ───────────────────────────────────────────


@router.get("/tags", response_class=HTMLResponse)
async def tag_directory(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    tags = await all_tags(session)
    groups = [t for t in tags if t.kind is TagKind.GROUP]
    members = {t.id: await group_members(session, t.id) for t in groups}
    grouped_artist_ids = {m.id for ms in members.values() for m in ms}
    return templates.TemplateResponse(
        request,
        "tags.html",
        {
            "user": user, "members": members, "kinds": list(TagKind), "all_tags": tags,
            "franchise_tags": [t for t in tags if t.kind is TagKind.FRANCHISE],
            "franchises": [t for t in tags if t.kind is TagKind.FRANCHISE],
            "groups": groups,
            "solo_artists": [
                t for t in tags if t.kind is TagKind.ARTIST and t.id not in grouped_artist_ids
            ],
            "artist_tags": [t for t in tags if t.kind is TagKind.ARTIST],
            "venues": [t for t in tags if t.kind is TagKind.VENUE],
        },
    )


@router.post("/tags")
async def create_tag(
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., min_length=1, max_length=100),
    kind: TagKind = Form(TagKind.ARTIST),
    parent_id: int = Form(0),
    location_url: str = Form(""),
    region: str = Form(""),
):
    name = name.strip()
    if await find_tag_by_name(session, name) is not None:
        raise HTTPException(status_code=409, detail=f"tag {name!r} already exists")
    parent = None
    if parent_id:
        parent = await session.get(Tag, parent_id)
        if parent is None or parent.kind is not TagKind.FRANCHISE:
            raise HTTPException(status_code=422, detail="parent must be a franchise tag")
        if kind is not TagKind.GROUP:
            raise HTTPException(status_code=422, detail="only group tags take a franchise parent")
    await ensure_user(session, user.id, user.username)
    session.add(Tag(
        name=name, kind=kind, created_by=user.id, parent_id=parent.id if parent else None,
        location_url=form_url(location_url), region=region.strip() or None,
    ))
    await session.commit()
    return RedirectResponse("/tags", status_code=303)


@router.post("/tags/{tag_id}/edit")
async def edit_tag(
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    # max_length matches create_tag's: a rename must not be able to produce a
    # name the creation form would have rejected.
    name: str = Form("", max_length=100),
    location_url: str = Form(""),
    region: str = Form(""),
):
    """Rename (any kind) plus venue-only location_url/region -- not
    kind-restricted on the latter two, harmless to set on others.
    `name` is optional so callers that never send it (there were none
    before this feature; kept optional in case any external client still
    doesn't) leave the tag's name untouched."""
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404)
    name = name.strip()
    if name and name.lower() != tag.name.lower():
        existing = await find_tag_by_name(session, name)
        if existing is not None and existing.id != tag.id:
            raise HTTPException(status_code=409, detail=f"tag {name!r} already exists")
        tag.name = name
    tag.location_url = form_url(location_url)
    tag.region = region.strip() or None
    await session.commit()
    return RedirectResponse("/tags", status_code=303)


@router.post("/tags/{tag_id}/delete")
async def delete_tag(
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404)
    await session.delete(tag)  # cascades: concert_tags + tag_members rows
    await session.commit()
    return RedirectResponse("/tags", status_code=303)


@router.post("/tags/{tag_id}/members")
async def add_member(
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    member_tag_id: int = Form(...),
):
    group = await session.get(Tag, tag_id)
    member = await session.get(Tag, member_tag_id)
    if group is None or member is None:
        raise HTTPException(status_code=404)
    if group.kind is not TagKind.GROUP:
        raise HTTPException(status_code=422, detail="members can only be added to group tags")
    if member.kind is TagKind.GROUP:
        raise HTTPException(status_code=422, detail="groups cannot contain groups")
    existing = await session.get(TagMember, (tag_id, member_tag_id))
    if existing is None:
        session.add(TagMember(group_tag_id=tag_id, member_tag_id=member_tag_id))
        await session.commit()
        eligible = await active_concerts_missing_member(session, tag_id, member_tag_id)
        if eligible:
            return RedirectResponse(
                f"/tags/{tag_id}/members/{member_tag_id}/retroactive-apply", status_code=303
            )
    return RedirectResponse("/tags", status_code=303)


@router.get("/tags/{group_id}/members/{member_id}/retroactive-apply", response_class=HTMLResponse)
async def retroactive_apply_form(
    request: Request,
    group_id: int,
    member_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """The one-time confirmation offered right after adding a member to a
    group: bulk-attach that artist to every currently-active concert that
    already has the group tag but not this member individually. Always an
    explicit, editor-confirmed action -- never automatic (see the Group Tag
    Expansion invariant in CLAUDE.md)."""
    pair = await resolve_group_member(session, group_id, member_id)
    if pair is None:
        raise HTTPException(status_code=404)
    group, member = pair
    concerts = await active_concerts_missing_member(session, group_id, member_id)
    return templates.TemplateResponse(
        request,
        "retroactive_apply.html",
        {"user": user, "group": group, "member": member, "concerts": concerts},
    )


@router.post("/tags/{group_id}/members/{member_id}/retroactive-apply")
async def retroactive_apply(
    group_id: int,
    member_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    # Same relationship check as the GET: the confirmation page is only a
    # promise about which pair it displays, not enforcement of the one it
    # submits.
    pair = await resolve_group_member(session, group_id, member_id)
    if pair is None:
        raise HTTPException(status_code=404)
    _, member = pair
    concerts = await active_concerts_missing_member(session, group_id, member_id)
    for concert in concerts:
        newly = await attach_tag(session, concert.id, member)
        await handle_newly_tagged(session, concert, newly)
    await session.commit()
    return RedirectResponse("/tags", status_code=303)


@router.post("/tags/{group_id}/members/{member_id}/delete")
async def remove_member(
    group_id: int,
    member_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(TagMember, (group_id, member_id))
    if row is not None:
        await session.delete(row)
        await session.commit()
    return RedirectResponse("/tags", status_code=303)
