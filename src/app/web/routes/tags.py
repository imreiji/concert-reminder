"""Tag management (editors).

Endpoints:
  GET  /tags                            tag directory (anyone signed in)
  POST /tags                            create tag                (editor)
  POST /tags/venue/quick                create a VENUE tag, JSON  (editor)
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

from app.db.models import Tag, TagMember, TagSubscription
from app.db.service import (
    active_concerts_missing_member,
    assign_tag_slug,
    attach_tag,
    ensure_user,
    find_tags_by_name_and_kind,
    group_members,
    handle_newly_tagged,
    resolve_group_member,
    tag_directory_context,
    tag_variant_gaps,
)
from app.db.session import get_session
from app.domain.types import TagKind
from app.web.auth import SessionUser, require_editor, require_user
from app.web.forms import form_url, require_variants

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
    ctx = await tag_directory_context(session)
    # The viewer's own subscriptions, for the table view's follow bell (E2) --
    # the same tag_id->sub map Preferences builds inline (preferences.py).
    subs = list((await session.execute(
        select(TagSubscription).where(TagSubscription.user_id == user.id)
    )).scalars())
    sub_by_tag = {sub.tag_id: sub for sub in subs}
    groups = [t for t in tags if t.kind is TagKind.GROUP]
    members = {t.id: await group_members(session, t.id) for t in groups}
    grouped_artist_ids = {m.id for ms in members.values() for m in ms}
    counts = ctx["counts"]
    # Raw Python payload for the new-tag dialog's duplicate warning; the
    # template embeds it via `| tojson` (never json.dumps first, never | safe)
    # so it escapes cleanly into the inline <script>.
    tag_dupe_data = [
        {
            "name": t.name,
            "kind": t.kind.value,
            "concerts": counts[t.id].concerts,
            "followers": counts[t.id].followers,
        }
        for t in tags
    ]
    return templates.TemplateResponse(
        request,
        "tags.html",
        {
            "user": user, "nav_page": "tags", "members": members, "kinds": list(TagKind),
            "all_tags": tags,
            "sub_by_tag": sub_by_tag,
            "franchise_tags": [t for t in tags if t.kind is TagKind.FRANCHISE],
            "franchises": [t for t in tags if t.kind is TagKind.FRANCHISE],
            "groups": groups,
            "solo_artists": [
                t for t in tags if t.kind is TagKind.ARTIST and t.id not in grouped_artist_ids
            ],
            "artist_tags": [t for t in tags if t.kind is TagKind.ARTIST],
            "venues": [t for t in tags if t.kind is TagKind.VENUE],
            "tag_dupe_data": tag_dupe_data,
            # Per-tag "what's missing" notice for the edit dialogs, keyed by
            # id -- one page renders every tag's dialog, so this has to be a
            # lookup rather than a single value. Informational only; edit_tag
            # deliberately does not enforce the rule.
            "tag_gaps": {t.id: tag_variant_gaps(t) for t in tags},
            **ctx,
        },
    )


@router.post("/tags")
async def create_tag(
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., min_length=1, max_length=100),
    name_en: str = Form(""),
    name_zh: str = Form(""),
    kind: TagKind = Form(TagKind.ARTIST),
    parent_id: int = Form(0),
    location_url: str = Form(""),
    region: str = Form(""),
    eventernote_url: str = Form(""),
):
    name = name.strip()
    # A tag name cannot render at all without a value, so it is mandatory in
    # all three -- checked here, at the create boundary only: edit_tag below
    # stays open so the pre-i18n tags already in the DB remain editable.
    # (This form has no city inputs; the VENUE city rule lives in
    # quick_create_venue, the only route that collects one on create.)
    require_variants("Tag name", name, name_en, name_zh, mandatory=True)
    # NO duplicate-name check, in any scope. Two performers may genuinely share
    # a name and a venue may share one with a group (owner ruling 2026-07-29),
    # so a name cannot be a uniqueness rule -- `slug` is the identity, and
    # assign_tag_slug below guarantees THAT is unique.
    #
    # This page is the deliberate place to create a tag, and it already warns
    # before submit (#new-tag-dupe in tags.html, fed by tag_dupe_data), naming
    # how many events and followers the existing one has. That warning has
    # shipped for a while telling the truth -- "creating another one will keep
    # them separate" -- while this route 409'd and refused. Removing the block is
    # what makes it honest.
    #
    # The two quick-create routes below deliberately KEEP their 409: there the
    # editor is being offered an existing match mid-import, which is a different
    # question from deliberately making a second tag.
    parent = None
    if parent_id:
        parent = await session.get(Tag, parent_id)
        if parent is None or parent.kind is not TagKind.FRANCHISE:
            raise HTTPException(status_code=422, detail="parent must be a franchise tag")
        if kind is not TagKind.GROUP:
            raise HTTPException(status_code=422, detail="only group tags take a franchise parent")
    await ensure_user(session, user.id, user.username)
    tag = Tag(
        name=name, name_en=name_en.strip() or None, name_zh=name_zh.strip() or None,
        kind=kind, created_by=user.id, parent_id=parent.id if parent else None,
        location_url=form_url(location_url), region=region.strip() or None,
        eventernote_url=form_url(eventernote_url),
    )
    session.add(tag)
    await assign_tag_slug(session, tag)
    await session.commit()
    return RedirectResponse("/tags", status_code=303)


@router.post("/tags/venue/quick")
async def quick_create_venue(
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., max_length=100),
    name_en: str = Form(""),
    name_zh: str = Form(""),
    city: str = Form(""),
    city_en: str = Form(""),
    city_zh: str = Form(""),
    region: str = Form(""),
    address: str = Form(""),
    location_url: str = Form(""),
) -> dict:
    """Create a VENUE tag without leaving the concert editor. Returns JSON so
    the caller can select the new tag into the leg it was creating it for.

    Deliberately NOT a second write path in any meaningful sense: it builds the
    same Tag row `create_tag` above does, through the same `form_url` boundary.
    It CREATES ONLY -- it never attaches anything to a concert. The new venue
    reaches the concert's VENUE rollup the normal way, by being selected into a
    leg and picked up by `sync_concert_venue_tags` when the editor finally saves.

    **The duplicate-name 409 stays here, and no longer matches `create_tag`.**
    Since 2026-07-29 names are not unique and the Tags page allows a duplicate
    outright; this route does not, because it is asking a different question.
    Here the editor is mid-edit with a name they typed in passing, and an
    existing venue of that name is overwhelmingly the one they meant -- so the
    409 exists to hand it to them ("that venue already exists", one click to
    select it), not to enforce a rule. A deliberate second venue of the same
    name is made on the Tags page, which is where deliberate things happen.

    Every other failure here (blank name, an unsafe location_url via `form_url`,
    a name over `max_length`) stays 422 -- the dialog's JS relies on 409 being
    distinguishable from the rest to pick the right error copy (see
    `_venue_create_dialog.html`), and `tests/test_error_pages.py` pins that a
    409 from these dialogs keeps its JSON body rather than becoming an HTML
    error page.

    Route path note: `/tags/venue/quick` cannot be swallowed by
    `/tags/{tag_id}/...` -- those all carry a different literal third segment.
    """
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="a venue needs a name")
    # The other half of create_tag's rule, plus the city: a venue is the one
    # tag that carries one, and it is all-or-nothing rather than mandatory --
    # a venue with no city recorded at all is a normal, complete row.
    require_variants("Venue name", name, name_en, name_zh, mandatory=True)
    require_variants("Venue city", city, city_en, city_zh)
    if await find_tags_by_name_and_kind(session, name, TagKind.VENUE):
        raise HTTPException(status_code=409, detail=f"a venue named {name!r} already exists")
    await ensure_user(session, user.id, user.username)
    tag = Tag(
        name=name,
        name_en=name_en.strip() or None,
        name_zh=name_zh.strip() or None,
        kind=TagKind.VENUE,
        city=city.strip() or None,
        city_en=city_en.strip() or None,
        city_zh=city_zh.strip() or None,
        region=region.strip() or None,
        address=address.strip() or None,
        location_url=form_url(location_url),
        created_by=user.id,
    )
    session.add(tag)
    await assign_tag_slug(session, tag)
    await session.commit()
    return {"id": tag.id, "name": tag.name}


# The three kinds this endpoint creates; VENUE keeps its own richer
# quick_create_venue route (it collects city/region/address a franchise or
# artist never has), so it is deliberately NOT in this set.
_QUICK_KINDS = (TagKind.FRANCHISE, TagKind.GROUP, TagKind.ARTIST)


@router.post("/tags/quick")
async def quick_create_tag(
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    name: str = Form(..., max_length=100),
    name_en: str = Form(""),
    name_zh: str = Form(""),
    kind: str = Form(...),
    parent_id: int = Form(0),
) -> dict:
    """Create a franchise/group/artist tag without leaving the import preview.
    Returns JSON so the caller can drop the new tag straight into the tag
    picker's selection (see `_tag_create_dialog.html`).

    Sibling of `quick_create_venue` above, and it keeps its kind-scoped 409 for
    the same reason: mid-import, an existing tag of the name you just typed is
    almost certainly the one you meant, so the 409 hands it to you rather than
    enforcing a rule. It no longer matches `create_tag`, which since 2026-07-29
    allows a duplicate name outright -- deliberate divergence, not drift. Unlike
    `create_tag`, the English/中文 name variants are OPTIONAL here (no
    `require_variants`): a
    tag is not held to the concert all-three-or-none rule, and an editor
    quick-creating a scraped Japanese name mid-import should not be blocked for
    lacking a translation. Editing the tag later can fill them in.

    Creating a GROUP here creates an EMPTY group -- no member expansion happens
    (invariant 3: expansion is an attach-time act; a memberless group expands
    to nothing when attached). No notification fires: `handle_newly_tagged` is
    about concert attachment, and this route attaches nothing.

    The 409 body carries the existing tag's id and name so the dialog can offer
    a one-click "select the existing one" instead of a dead end.
    """
    try:
        tag_kind = TagKind(kind)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown tag kind {kind!r}") from exc
    if tag_kind not in _QUICK_KINDS:
        raise HTTPException(
            status_code=422,
            detail="quick-create handles franchise, group and artist tags only",
        )
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="a tag needs a name")
    # Oldest match: with names non-unique there can be several, and the one the
    # editor most likely meant is the one that has been around.
    matches = await find_tags_by_name_and_kind(session, name, tag_kind)
    if matches:
        existing = matches[0]
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"a {tag_kind.value} tag named {name!r} already exists",
                "id": existing.id,
                "name": existing.name,
            },
        )
    parent = None
    if parent_id:
        if tag_kind is not TagKind.GROUP:
            raise HTTPException(status_code=422, detail="only group tags take a franchise parent")
        parent = await session.get(Tag, parent_id)
        if parent is None or parent.kind is not TagKind.FRANCHISE:
            raise HTTPException(status_code=422, detail="parent must be a franchise tag")
    await ensure_user(session, user.id, user.username)
    tag = Tag(
        name=name,
        name_en=name_en.strip() or None,
        name_zh=name_zh.strip() or None,
        kind=tag_kind,
        parent_id=parent.id if parent else None,
        created_by=user.id,
    )
    session.add(tag)
    await assign_tag_slug(session, tag)
    await session.commit()
    return {
        "id": tag.id, "name": tag.name, "slug": tag.slug,
        "kind": tag.kind.value, "parent_id": tag.parent_id,
    }


@router.post("/tags/{tag_id}/edit")
async def edit_tag(
    tag_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    # max_length matches create_tag's: a rename must not be able to produce a
    # name the creation form would have rejected.
    name: str = Form("", max_length=100),
    name_en: str = Form(""),
    name_zh: str = Form(""),
    location_url: str = Form(""),
    region: str = Form(""),
    eventernote_url: str = Form(""),
):
    """Rename (any kind) plus venue-only location_url/region and the
    artist/group eventernote_url -- not kind-restricted on those, harmless
    to set on others. `name` is optional so callers that never send it
    (there were none before this feature; kept optional in case any external
    client still doesn't) leave the tag's name untouched."""
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404)
    name = name.strip()
    if name:
        # NO collision check. Names are not unique (owner ruling 2026-07-29), and
        # blocking here while `create_tag` allows a duplicate would be incoherent:
        # you could type your way to two Yuki Satos but never edit your way there.
        # The tag's `slug` is untouched by a rename -- it is the identity, and
        # rewriting it would break anything already holding it, exactly as
        # invariant 6 says of a concert's event_id.
        tag.name = name
    # Name variants carry no uniqueness constraint -- two tags may share an
    # English/Chinese rendering, so no collision check here (unlike `name`).
    tag.name_en = name_en.strip() or None
    tag.name_zh = name_zh.strip() or None
    tag.location_url = form_url(location_url)
    tag.region = region.strip() or None
    tag.eventernote_url = form_url(eventernote_url)
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
