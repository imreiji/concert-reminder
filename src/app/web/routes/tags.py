"""Tag management (editors) + attaching tags to concerts.

Endpoints:
  GET  /tags                            tag directory (anyone signed in)
  POST /tags                            create tag                (editor)
  POST /tags/{id}/delete                delete tag everywhere     (editor)
  POST /tags/{id}/members               add member to a group     (editor)
  POST /tags/{gid}/members/{mid}/delete remove member from group  (editor)
  POST /concerts/{id}/tags              attach (find-or-create; groups expand)
  POST /concerts/{id}/tags/{tid}/delete detach from this concert  (editor)
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tag, TagMember
from app.db.service import (
    attach_tag,
    detach_tag,
    ensure_user,
    find_tag_by_name,
    group_members,
    handle_newly_tagged,
)
from app.db.session import get_session
from app.domain.types import TagKind
from app.web.auth import SessionUser, require_editor, require_user

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
    members = {t.id: await group_members(session, t.id) for t in tags if t.kind is TagKind.GROUP}
    return templates.TemplateResponse(
        request,
        "tags.html",
        {"user": user, "tags": tags, "members": members, "kinds": list(TagKind),
         "artist_tags": [t for t in tags if t.kind is TagKind.ARTIST],
         "franchise_tags": [t for t in tags if t.kind is TagKind.FRANCHISE]},
    )


@router.post("/tags")
async def create_tag(
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., min_length=1, max_length=100),
    kind: TagKind = Form(TagKind.ARTIST),
    parent_id: int = Form(0),
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
    session.add(Tag(name=name, kind=kind, created_by=user.id,
                    parent_id=parent.id if parent else None))
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


# ── Tags on a concert (htmx fragment) ────────────────────────────────────


async def render_tags_fragment(
    request: Request, concert_id: int, user: SessionUser, session: AsyncSession
) -> HTMLResponse:
    from app.db.models import Concert

    concert = await session.get(Concert, concert_id)
    await session.refresh(concert, ["tags"])
    return templates.TemplateResponse(
        request,
        "_tags.html",
        {"concert": concert, "user": user, "tag_kinds": list(TagKind),
         "all_tags": await all_tags(session)},
    )


@router.post("/concerts/{concert_id}/tags", response_class=HTMLResponse)
async def attach_concert_tag(
    request: Request,
    concert_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., min_length=1, max_length=100),
    kind: TagKind = Form(TagKind.ARTIST),
):
    from app.db.models import Concert

    concert = await session.get(Concert, concert_id)
    if concert is None:
        raise HTTPException(status_code=404)
    tag = await find_tag_by_name(session, name)
    if tag is None:  # find-or-create; `kind` applies only on create
        await ensure_user(session, user.id, user.username)
        tag = Tag(name=name.strip(), kind=kind, created_by=user.id)
        session.add(tag)
        await session.flush()
    added = await attach_tag(session, concert_id, tag)  # groups expand here
    await handle_newly_tagged(session, concert, added)  # notify-and-apply
    await session.commit()
    return await render_tags_fragment(request, concert_id, user, session)


@router.post("/concerts/{concert_id}/tags/{tag_id}/delete", response_class=HTMLResponse)
async def detach_concert_tag(
    request: Request,
    concert_id: int,
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    await detach_tag(session, concert_id, tag_id)
    await session.commit()
    return await render_tags_fragment(request, concert_id, user, session)
