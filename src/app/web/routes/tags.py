"""Tag management (editors).

Endpoints:
  GET  /tags                            tag directory (anyone signed in)
  POST /tags                            create tag                (editor)
  POST /tags/venue/quick                create a VENUE tag, JSON  (editor)
  POST /tags/{id}/edit                  name/handle/venue fields, and a
                                        character's seiyuu           (editor)
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
    attach_tag,
    create_tag_row,
    ensure_user,
    find_tags_by_name_and_kind,
    handle_newly_tagged,
    members_by_group,
    resolve_group_member,
    tag_directory_context,
    tag_variant_gaps,
)
from app.db.session import get_session
from app.domain.slugs import slug_core
from app.domain.types import ALLOWED_PARENT_KINDS, EVENTERNOTE_KINDS, TagKind
from app.web.auth import SessionUser, require_editor, require_user
from app.web.forms import form_url, require_variants

router = APIRouter()

templates = None  # set by web.app at startup


async def all_tags(session: AsyncSession) -> list[Tag]:
    return list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())


async def resolve_seiyuu(
    session: AsyncSession, kind: TagKind, voiced_by_tag_id: int | None
) -> int | None:
    """The ARTIST id a CHARACTER is voiced by, checked at the write boundary.

    A falsy value (absent, or the dialog's "— none —" 0) means NO seiyuu; the
    two are told apart by the CALLER, not here: `create_tag` has nothing to
    preserve, while `edit_tag` must keep its omitted-leaves-alone rule.

    Two refusals, both 422 and both mirroring the catalogue importer's
    warn-and-skip (`apply_tag_import`), because an editor surface must not be
    more permissive than the file format:

    * a non-CHARACTER carrying one -- `parent_id` is where "the broader thing
      I belong to" goes, and nothing reads `voiced_by_tag_id` off any other
      kind, so accepting it would store a value that renders nowhere;
    * a target that is not an ARTIST. `attach_tag` materialises whatever this
      names onto a concert, so a VENUE here would render as a performer and
      DM its followers. Refusing a non-ARTIST also refuses SELF-voicing for
      free -- a character pointed at herself vanishes from the Performing
      panel, since `performer_clusters` counts her as her own paired seiyuu
      and filters her out.
    """
    if not voiced_by_tag_id:
        return None
    if kind is not TagKind.CHARACTER:
        raise HTTPException(
            status_code=422, detail=f"a {kind.value} tag cannot have a seiyuu"
        )
    voice = await session.get(Tag, voiced_by_tag_id)
    if voice is None:
        raise HTTPException(status_code=422, detail="seiyuu tag not found")
    if voice.kind is not TagKind.ARTIST:
        raise HTTPException(
            status_code=422,
            detail=f"only an artist can voice a character, not a {voice.kind.value}",
        )
    return voice.id


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
    by_id = {t.id: t for t in tags}
    groups = [t for t in tags if t.kind is TagKind.GROUP]
    members = await members_by_group(session, [t.id for t in groups])
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
            # Each character paired with the ARTIST who voices her, resolved
            # here off the already-loaded tag list rather than in the template
            # (Tag.voiced_by is not a loaded relationship, and a lazy load
            # during async rendering is a MissingGreenlet 500). A character
            # whose seiyuu is unset -- or whose seiyuu tag was deleted, since
            # the FK is ON DELETE SET NULL -- pairs with None and says so.
            "characters": [
                (t, by_id.get(t.voiced_by_tag_id))
                for t in tags
                if t.kind is TagKind.CHARACTER
            ],
            "venues": [t for t in tags if t.kind is TagKind.VENUE],
            # The kinds whose dialogs render the eventernote field, as plain
            # strings so the template can compare them to `t.kind.value` and
            # build the create dialog's `k-<kind>` class list. THE SAME TABLE
            # `edit_tag` gates its write on -- see EVENTERNOTE_KINDS for why a
            # second copy here would be silent and destructive.
            "eventernote_kinds": sorted(k.value for k in EVENTERNOTE_KINDS),
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
    # CHARACTER-only. `None` (the field absent) and 0 ("— none —") both mean
    # unvoiced here -- unlike edit_tag, a create has no stored value to keep.
    voiced_by_tag_id: int | None = Form(None),
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
    # create_tag_row below mints one, and that IS unique.
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
        if parent is None:
            raise HTTPException(status_code=422, detail="parent tag not found")
        # Widened 2026-08-01, and SHARED with the catalogue importer since --
        # two copies of this table drifted apart once (the importer stayed
        # franchise-only and so could not express a subunit), which is why it
        # lives in domain/types.py now.
        if parent.kind not in ALLOWED_PARENT_KINDS.get(kind, ()):
            raise HTTPException(
                status_code=422,
                detail=f"a {kind.value} tag cannot have a {parent.kind.value} parent",
            )
    voiced_by = await resolve_seiyuu(session, kind, voiced_by_tag_id)
    await ensure_user(session, user.id, user.username)
    # slug omitted -> minted. The catalogue importer is the one caller that
    # passes an explicit handle, because its handles come from a file.
    await create_tag_row(
        session,
        name=name, name_en=name_en.strip() or None, name_zh=name_zh.strip() or None,
        kind=kind, created_by=user.id, parent_id=parent.id if parent else None,
        voiced_by_tag_id=voiced_by,
        location_url=form_url(location_url), region=region.strip() or None,
        eventernote_url=form_url(eventernote_url),
    )
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
    tag = await create_tag_row(
        session,
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
    await session.commit()
    return {"id": tag.id, "name": tag.name}


# The kinds this endpoint creates; VENUE keeps its own richer
# quick_create_venue route (it collects city/region/address a franchise or
# artist never has), so it is deliberately NOT in this set.
# CHARACTER joined 2026-08-01: an im@s event credits 如月千早, and until then a
# name the editor had to re-kind as a character mid-import had no one-click
# create at all. It arrives WITHOUT a seiyuu -- `voiced_by_tag_id` is set on
# the Tags page, where the artist list to choose from is on screen.
_QUICK_KINDS = (TagKind.FRANCHISE, TagKind.GROUP, TagKind.ARTIST, TagKind.CHARACTER)


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
    """Create a franchise/group/character/artist tag without leaving the
    import preview.
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
        # The SAME table create_tag and the catalogue importer read. This route
        # kept a franchise-only rule of its own ("only group tags take a
        # franchise parent") after the editor widened -- harmless in that it
        # can only create, so it can neither close a parent loop nor reach a
        # forbidden state, but ALLOWED_PARENT_KINDS exists precisely so the
        # write paths cannot drift, and domain/types.py already claimed they
        # all read it.
        parent = await session.get(Tag, parent_id)
        if parent is None:
            raise HTTPException(status_code=422, detail="parent tag not found")
        if parent.kind not in ALLOWED_PARENT_KINDS.get(tag_kind, ()):
            raise HTTPException(
                status_code=422,
                detail=f"a {tag_kind.value} tag cannot have a {parent.kind.value} parent",
            )
    await ensure_user(session, user.id, user.username)
    tag = await create_tag_row(
        session,
        name=name,
        name_en=name_en.strip() or None,
        name_zh=name_zh.strip() or None,
        kind=tag_kind,
        parent_id=parent.id if parent else None,
        created_by=user.id,
    )
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
    slug: str = Form(""),
    location_url: str = Form(""),
    region: str = Form(""),
    eventernote_url: str = Form(""),
    voiced_by_tag_id: int | None = Form(None),
):
    """Rename (any kind), edit the handle, plus venue-only location_url/region,
    the artist/group/character eventernote_url and a character's seiyuu -- not
    kind-restricted on the first two, harmless to set on others.

    `name` and `slug` are both optional, and an omitted one leaves the stored
    value ALONE rather than blanking it: every caller of this form predates the
    handle, and none of them may wipe a tag's identity by not knowing about it.
    `voiced_by_tag_id` follows the SAME rule, and is why it is `int | None`
    rather than `int` defaulting to 0: absent means "leave her seiyuu alone"
    (the field only renders on a character's dialog, so every other tag's
    submit omits it), while an explicit 0 -- the select's "— none —" -- clears
    it. A recast is this one value re-pointed; there is no history model.
    `eventernote_url` was moved onto the same rule (2026-08-01): it is rendered
    only for the kinds with an actor page, so a franchise or venue submit
    omitted it and the old `Form("")` default wrote None over whatever the
    catalogue import had put there. Silent for franchises since it shipped;
    load-bearing the moment characters gained the field, since a character's
    own page is the entire "discovery is nearly free" payoff.

    NO parent editing here, deliberately. `parent_id` has never been editable
    on this form (a group's franchise is set at creation), widening `kind`'s
    parent rules did not change that, and inventing the surface would be the
    one thing on this route able to close a `parent_id` loop -- which is why
    `would_create_tag_cycle` is wired into the catalogue importer and not
    here. Adding parent editing later means calling that guard from this
    function, next to the ALLOWED_PARENT_KINDS check `create_tag` runs.
    """
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
    if slug.strip():
        # NORMALISED, not validated: uppercase and spaces are what a person
        # types, and bouncing them for punctuation they cannot see the rule for
        # is hostile. Same helper that mints one, so a typed handle and a
        # generated one can never disagree about shape.
        normalised = slug_core(slug)
        if not normalised:
            # Nothing survives (an all-CJK handle), so there is nothing to store
            # and nothing to correct on their behalf. The ONE case worth a 422.
            raise HTTPException(
                status_code=422,
                detail="a handle needs at least one letter or digit (a-z, 0-9)",
            )
        if normalised != tag.slug:
            # The handle IS the identity, so this is the only uniqueness check
            # left in this module. Excluding itself, or resubmitting the edit
            # form unchanged would 409 against the tag being edited.
            clash = await session.execute(
                select(Tag.id).where(Tag.slug == normalised, Tag.id != tag.id)
            )
            if clash.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=409, detail=f"handle {normalised!r} is already taken"
                )
            tag.slug = normalised
    # Name variants carry no uniqueness constraint -- two tags may share an
    # English/Chinese rendering, so no collision check here (unlike `name`).
    tag.name_en = name_en.strip() or None
    tag.name_zh = name_zh.strip() or None
    tag.location_url = form_url(location_url)
    tag.region = region.strip() or None
    if tag.kind in EVENTERNOTE_KINDS:
        # GATED ON KIND, and it has to be: this is the one field whose "leave it
        # alone" cannot be expressed by an absent value. `str | None = Form(None)`
        # does not work -- FastAPI folds an empty form value into the default,
        # so "" and omitted arrive identically -- which is why `slug` and
        # `voiced_by_tag_id` get their sentinels from their own types instead.
        # Writing it unconditionally is how renaming a CHARACTER silently wiped
        # the discovery link the catalogue import had set on her.
        #
        # The kinds here are exactly the kinds whose dialog RENDERS the field,
        # which is why both read one table. Inside those kinds an empty box
        # still clears the value: that is an editor emptying something they can
        # see.
        tag.eventernote_url = form_url(eventernote_url)
    if voiced_by_tag_id is not None:
        tag.voiced_by_tag_id = await resolve_seiyuu(session, tag.kind, voiced_by_tag_id)
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
