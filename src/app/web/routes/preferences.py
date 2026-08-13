"""User preferences: reminder presets, tag subscriptions, and timezone.

  GET  /preferences                          the page
  GET  /following                            followed tags, chips by franchise
  POST /presets                              create preset
  POST /presets/apply-to-following           fill the default into preset-less follows
  POST /presets/{id}/delete
  POST /presets/{id}/items                   add an item to a preset
  POST /presets/{id}/items/{item_id}/delete
  POST /subscriptions                        subscribe to a tag (+preset, notify)
  POST /subscriptions/unfollow               unfollow BY TAG, idempotent (chips)
  POST /subscriptions/{id}/notify            toggle the per-tag Notify flag
  POST /subscriptions/{id}/settings          preset + notify together (/following)
  POST /subscriptions/{id}/auto-apply        toggle default-preset auto-apply
  POST /subscriptions/{id}/delete            unfollow BY SUBSCRIPTION ID
  POST /concerts/{event_id}/presets/{pid}/apply   one-click apply (rules fragment swap)
  POST /me/timezone                          manual timezone choice
  POST /me/timezone/auto                     browser-detected timezone
  POST /me/timezone/reset                    back to browser auto-detect
  POST /me/test-dm                           send a synchronous diagnostic test DM
  POST /me/api-token                         mint/re-mint the agent read-API token

Everything here is per-user: routes verify ownership and 404 on other
people's presets/subscriptions rather than admitting they exist.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import discord
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import i18n
from app.config import settings
from app.db.models import (
    Concert,
    ConcertTag,
    PresetItem,
    ReminderPreset,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import (
    apply_preset,
    concert_subscription_states,
    delete_user,
    ensure_user,
    followed_tag_counts,
    followed_tag_families,
    generate_api_token,
    get_default_preset,
    list_editors,
    members_by_group,
    record_dm_outcome,
    set_default_preset,
    set_editor,
    tracked_concert_ids,
    upcoming_concert_count,
)
from app.db.session import get_session
from app.domain.timezones import fmt_dual_lines
from app.domain.types import Anchor
from app.web.auth import SessionUser, require_admin, require_user, revoke_session

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


# A CLOSED allowlist of landing pages, not an open-redirect guard (that is
# `domain/urls.py:safe_next`). Anything absent silently becomes "/preferences",
# so a page that posts one of these forms and is NOT listed here bounces its
# reader off itself on every save -- which is exactly what shipped with /tags
# and lived unnoticed until it was measured. Add the path WITH the surface.
_ALLOWED_NEXT = {"/preferences", "/welcome", "/tags", "/following"}


def _safe_next(next_url: str) -> str:
    return next_url if next_url in _ALLOWED_NEXT else "/preferences"


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
    feed_token: str = "",
):
    from app.domain.types import TagKind

    # POP, not get: this is what makes the mint's session flash one-shot. A
    # second render of this same page (back button, refresh, a second tab)
    # must show nothing -- see POST /me/api-token's docstring for why the
    # token isn't carried here as a query parameter the way feed_token is.
    api_token = request.session.pop("api_token", None)
    # Same one-shot discipline, and POP for the same reason: the fill report is
    # about ONE press, so a refresh or a second tab must not repeat it. Numbers
    # only -- the sentence is composed in the template so it lands in the
    # viewer's locale at RENDER time (see apply_default_to_following).
    preset_fill = request.session.pop("preset_fill", None)

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
    members = await members_by_group(session, [g.id for g in groups])
    grouped_artist_ids = {m.id for ms in members.values() for m in ms}
    solo_artists = [
        t for t in tags if t.kind is TagKind.ARTIST and t.id not in grouped_artist_ids
    ]
    db_user = await session.get(User, user.id)
    tz = db_user.timezone if db_user else "America/Moncton"
    tz_auto = db_user.tz_auto if db_user else True
    has_calendar_feed = bool(db_user and db_user.calendar_token_hash)
    has_api_token = bool(db_user and db_user.api_token_hash)
    editors = await list_editors(session) if user.is_admin else []

    # Following section: the tracked count, plus the deliberately-invisible
    # OPTED_OUT overrides surfaced as a review-and-restore list (spec
    # decision 1). concert_subscription_states is Task 2's read surface;
    # tracked_concert_ids is the single definition of "tracked".
    tracked_ids = await tracked_concert_ids(session, user.id)
    tracked_count = len(tracked_ids)
    upcoming_count = await upcoming_concert_count(session, tracked_ids)
    overrides = await concert_subscription_states(session, user.id)
    from app.domain.types import SubscriptionState
    pruned_ids = [cid for cid, st in overrides.items() if st is SubscriptionState.OPTED_OUT]
    pruned_concerts = []
    if pruned_ids:
        pruned_concerts = list((await session.execute(
            select(Concert).where(Concert.id.in_(pruned_ids)).order_by(Concert.title)
        )).scalars())
    # Per-followed-tag "N concerts, M upcoming" for the Following subrows.
    tag_counts = await followed_tag_counts(session, user.id)

    # A live JST/local sample for the Time section's preview line: the current
    # instant read in both zones (invariant 1: JST first, both always present).
    _, tz_preview = fmt_dual_lines(datetime.now(UTC), tz, i18n.get_locale())

    return templates.TemplateResponse(
        request,
        "preferences.html",
        {"user": user, "presets": presets, "subs": subs, "sub_by_tag": sub_by_tag,
         "franchises": franchises, "groups": groups, "members": members,
         "solo_artists": solo_artists, "venues": venues,
         "tz": tz, "tz_auto": tz_auto, "tz_preview": tz_preview,
         "common_timezones": COMMON_TIMEZONES, "all_timezones": all_timezones(),
         "anchors": list(Anchor), "editors": editors,
         "rehearsal_enabled": settings.rehearsal_enabled,
         "has_calendar_feed": has_calendar_feed, "tag_counts": tag_counts,
         "tracked_count": tracked_count, "upcoming_count": upcoming_count,
         "pruned_concerts": pruned_concerts,
         "feed_url": f"{settings.base_url}/calendar/{feed_token}.ics" if feed_token else None,
         "has_api_token": has_api_token, "api_token": api_token,
         "preset_fill": preset_fill,
         "bot_enabled": settings.bot_enabled},
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
    next_url: str = Form("/preferences", alias="next"),
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
    return RedirectResponse(_safe_next(next_url), status_code=303)


@router.post("/presets/apply-to-following")
async def apply_default_to_following(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Fill the standing default into every followed tag that carries NO preset.

    An ACTION, not a setting. `ReminderPreset.is_default` governs FUTURE follows
    (`subscribe` reads it when the form names no preset); changing it must never
    rewrite a row. This is the retroactive half, and it only runs when the reader
    presses it.

    **It fills NULLs and NOTHING else.** The `preset_id.is_(None)` clause in the
    UPDATE below is the whole safety property of this route (owner ruling,
    2026-08-13: "for anything that already have their own preset, remain on the
    original preset"). Drop it and the statement becomes a blanket overwrite that
    silently destroys per-tag tuning a user set through `/following`'s dialog --
    a loss with no undo, no audit row, and a success page indistinguishable from
    the correct behaviour. That is the reason this route reports TWO numbers
    rather than one: the count of rows LEFT ALONE is the only evidence the reader
    gets that the fill was a fill.

    `TagSubscription.user_id == user.id` is the second load-bearing clause. There
    is no id in the request at all -- the caller is the session, so this can only
    ever touch the presser's own rows -- but the scope still has to be written,
    because a bulk UPDATE without it rewrites the whole table.

    "Left alone" counts rows holding a preset that is NOT the default: a follow
    already sitting on the default was neither filled nor overruled, and
    reporting it as "kept its own" would be a lie about tuning that does not
    exist. (The `is_not(None)` in that count is redundant in SQL -- `NULL != x`
    is NULL, never true -- and kept for the reader.)

    No rule resync, for the same reason `/subscriptions/{id}/settings` states:
    `TagSubscription.preset_id` is read only by `handle_newly_tagged`, when a
    FUTURE concert picks up a followed tag. It plans nothing now, so
    `reminder_queue` (invariant 2) is untouched by this write and there is no
    already-queued row to re-plan. Invariant 8's `reinstate_user_rules` belongs
    to CONCERT subscriptions and leg opt-outs, which do gate live reminders.

    The report rides a one-shot session flash, popped by GET /preferences, as
    NUMBERS rather than a composed sentence -- the sentence is built in the
    template so it resolves in the LOCALE OF THE PAGE THAT SHOWS IT, not the
    locale of the POST that queued it (CLAUDE.md's i18n warning: a label copied
    before it reaches a template resolves at the copy site). The redirect is the
    bare, hardcoded "/preferences" and takes no `next`: this page is the only one
    that pops the flash, so any other landing would swallow the report entirely
    and leave the action looking silent. (`/preferences` is in `_ALLOWED_NEXT`,
    so a later `next` would work -- it is left out because of the flash, not
    because the allowlist is missing it.)
    """
    default = await get_default_preset(session, user.id)
    if default is None:
        request.session["preset_fill"] = {"had_default": False, "filled": 0, "kept": 0}
        return RedirectResponse("/preferences", status_code=303)

    kept = await session.scalar(
        select(func.count())
        .select_from(TagSubscription)
        .where(
            TagSubscription.user_id == user.id,
            TagSubscription.preset_id.is_not(None),
            TagSubscription.preset_id != default.id,
        )
    )
    result = await session.execute(
        update(TagSubscription)
        .where(
            TagSubscription.user_id == user.id,
            TagSubscription.preset_id.is_(None),
        )
        .values(preset_id=default.id)
    )
    filled = result.rowcount
    await session.commit()
    request.session["preset_fill"] = {
        "had_default": True, "filled": filled, "kept": kept or 0,
    }
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


# ── The Following page ───────────────────────────────────────────────────


@router.get("/following", response_class=HTMLResponse)
async def following_page(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Every tag this viewer follows, grouped by franchise, as plain chips.

    It lives HERE, beside the routes that write `TagSubscription` (the four
    below), rather than in a router of its own: this is the read surface for
    exactly those rows, `/following` is a literal path that collides with no
    path template, and a new module would have to be registered in web/app.py
    for no separation this file does not already have. `routes/subscriptions.py`
    is a different feature entirely -- CONCERT subscriptions and leg opt-outs.

    `followed_tag_families` reads the subscription rows directly, which is what
    they are: explicit user edits, one per followed tag. It is NOT a second
    definition of "what am I tracking" -- `tracked_concert_ids` (invariant 8)
    answers the concert-level question and is untouched by this page.

    `my_presets` and `get_default_preset` fill the per-subscription dialog's
    preset `<select>`, and `followed_tag_counts` serves BOTH the head's context
    line and each dialog's -- one entry per followed tag, so "with an event
    coming up" is a tally of TAGS, not of concerts, which a sum over the map
    would double-count for anyone following both a group and its members.
    """
    families = await followed_tag_families(session, user.id)
    counts = await followed_tag_counts(session, user.id)
    presets = await my_presets(session, user.id)
    default_preset = await get_default_preset(session, user.id)
    return templates.TemplateResponse(
        request,
        "following.html",
        {"user": user, "families": families, "presets": presets,
         "default_preset": default_preset, "tag_counts": counts,
         "followed_count": len(counts),
         "live_count": sum(1 for _total, upcoming in counts.values() if upcoming)},
    )


# ── Subscriptions ────────────────────────────────────────────────────────


# `_tag_chip.html` renders four shapes, and the pressed form names its own in a
# hidden `chip` input: "count" (a directory chip, with its event count),
# "plain" (a member chip, which has never carried one), and these two -- the
# character and seiyuu halves of a split pill, where the value doubles as the
# half's CSS class.
_PILL_HALVES = {"cn", "cv"}


async def _chip_fragment(
    session: AsyncSession, tag: Tag, sub: TagSubscription | None, chip: str
) -> HTMLResponse:
    """ONE follow chip, for an htmx press to swap in place of itself.

    Rendered from `_tag_chip.html` -- the same partial /tags renders every chip
    from -- because the failure mode of a hand-written fragment is silent: a
    chip that loses `data-name` is unfindable by the search box, one that loses
    `data-tag-id` is inert in the editor's Edit mode, and one that loses its
    `unused` marking lies about a tag attached to nothing. A test compares this
    output against the page's own markup byte for byte.

    `chip` is the shape the pressed form said it was, and an unknown value
    lands on the plain chip rather than raising: the value is only ever written
    by the partial itself, and a fragment of the wrong shape is a cosmetic
    surprise where a 500 on a follow press would not be.

    The count is looked up HERE rather than trusted from the form, and only for
    the shape that shows one -- a member chip has never carried a number and
    must not grow one. It is one COUNT over `concert_tags`, the same figure
    `tag_directory_context` computes for the whole page in a GROUP BY.
    """
    if chip in _PILL_HALVES:
        html = templates.get_template("_tag_chip.html").module.follow_half(tag, chip, sub)
    else:
        count = None
        if chip == "count":
            count = await session.scalar(
                select(func.count())
                .select_from(ConcertTag)
                .where(ConcertTag.tag_id == tag.id)
            )
        html = templates.get_template("_tag_chip.html").module.tag_chip(tag, count, sub)
    return HTMLResponse(str(html))


@router.post("/subscriptions", response_class=HTMLResponse)
async def subscribe(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    tag_id: int = Form(...),
    preset_id: int = Form(0),
    notify: bool = Form(False),
    next_url: str = Form("/preferences", alias="next"),
    # Which chip shape asked, blank from every non-chip caller (Preferences,
    # the welcome wizard). Only read on the htmx branch.
    chip: str = Form(""),
):
    tag = await session.get(Tag, tag_id)
    if tag is None:
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
        # `preset_id` absent or 0 means "I did not choose" -- inherit the
        # viewer's standing default (or None, if they have none). Every /tags
        # and welcome-wizard chip sends no preset_id, so this is what makes a
        # new follow start with the preset the user already told the app they
        # want, instead of linking none at all.
        if preset_id:
            linked_preset_id = preset_id
        else:
            default = await get_default_preset(session, user.id)
            linked_preset_id = default.id if default else None
        sub = TagSubscription(
            user_id=user.id, tag_id=tag_id,
            preset_id=linked_preset_id, notify=notify,
        )
        session.add(sub)
    else:  # re-submitting updates the existing subscription
        # Only overwrite the preset when the caller EXPLICITLY chose one.
        # A bare re-submit (a stale tab, a double chip-press) posts no
        # preset_id, and must not clear a preset already linked here or set
        # via /subscriptions/{id}/settings -- that would be silent data loss.
        if preset_id:
            sub.preset_id = preset_id
        sub.notify = notify
    await session.commit()

    # A chip press on /tags swaps ITSELF (owner report, 2026-08-12: a follow
    # used to 303 back and re-render the whole directory -- 6.8 MB and 1.6 s at
    # live scale -- landing the reader back at the top of the page). Anything
    # else, JS-off chips included, keeps the redirect: htmx would FOLLOW a 303
    # and swap a whole page into a chip-sized hole, which is worse than the
    # reload it replaces, so a fragment response must never also redirect.
    if request.headers.get("HX-Request") != "true":
        return RedirectResponse(_safe_next(next_url), status_code=303)
    return await _chip_fragment(session, tag, sub, chip)


async def _owned_subscription(
    session: AsyncSession, user_id: int, sub_id: int
) -> TagSubscription:
    sub = await session.get(TagSubscription, sub_id)
    if sub is None or sub.user_id != user_id:
        raise HTTPException(status_code=404)
    return sub


@router.post("/subscriptions/{sub_id}/notify")
async def toggle_subscription_notify(
    sub_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Flip the per-tag Notify flag -- the demo's Notify `.swb`. Notify is
    just the new-event DM notice, so this needs no rule resync (unlike a
    per-concert override); it mirrors the existing /subscriptions upsert,
    which likewise does not resync when it rewrites notify."""
    sub = await _owned_subscription(session, user.id, sub_id)
    sub.notify = not sub.notify
    await session.commit()
    return RedirectResponse("/preferences#p-follow", status_code=303)


@router.post("/subscriptions/{sub_id}/settings")
async def update_subscription_settings(
    sub_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    preset_id: int = Form(0),
    notify: bool = Form(False),
    next_url: str = Form("/following", alias="next"),
):
    """Both of a subscription's settings in ONE submit -- `/following`'s
    per-tag dialog.

    The two toggles above stay: Preferences' subrows flip one field per press
    with no form to submit, and this page's dialog holds both fields open at
    once and saves them together. Writing them separately here would mean a
    dialog whose Save is two round trips, either of which can land alone.

    Same field semantics as the `/subscriptions` upsert, deliberately -- this
    is that route's write half addressed BY SUBSCRIPTION ID instead of by tag,
    which is safe on this page because it renders each followed tag exactly
    once and answers with a whole re-rendered page (see `unfollow_tag` for the
    surface where an id could go stale). `preset_id == 0` is the "none" option
    and stores NULL; any other value must be a preset this viewer owns, so
    `owned_preset` 404s rather than letting one user link another's preset.
    `notify` is a checkbox, so its absence is False -- which is what makes
    unticking it reach the DB at all.

    No rule resync: `notify` is only the new-event DM notice, and `preset_id`
    is what gets applied to FUTURE matching events, not a rule already planned
    (invariant 2's queue is untouched by either) -- the same reasoning
    `/subscriptions` and the two toggles above already run on.
    """
    sub = await _owned_subscription(session, user.id, sub_id)
    if preset_id:
        await owned_preset(session, user.id, preset_id)
    sub.preset_id = preset_id or None
    sub.notify = notify
    await session.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)


@router.post("/subscriptions/{sub_id}/auto-apply")
async def toggle_subscription_autoapply(
    sub_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """The demo's Auto-apply `.swb`: a preset either IS or ISN'T linked. On
    links the user's default preset (nothing to link without one, so it stays
    off); off clears preset_id. Same field the /subscriptions upsert sets."""
    sub = await _owned_subscription(session, user.id, sub_id)
    if sub.preset_id is not None:
        sub.preset_id = None
    else:
        default = await get_default_preset(session, user.id)
        sub.preset_id = default.id if default else None
    await session.commit()
    return RedirectResponse("/preferences#p-follow", status_code=303)


@router.post("/subscriptions/unfollow", response_class=HTMLResponse)
async def unfollow_tag(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    tag_id: int = Form(...),
    next_url: str = Form("/preferences", alias="next"),
    chip: str = Form(""),
):
    """Unfollow a TAG -- idempotent, and that is the whole point of it existing
    beside `unsubscribe` below (found in review, 2026-08-12).

    `/tags` renders the same tag more than once: a performer in two groups gets
    a chip in each, and a seiyuu can be a direct member chip AND a pill half at
    once. While a follow press reloaded the page, every copy was re-rendered in
    step. A one-chip swap cannot do that -- so with the unfollow keyed by
    SUBSCRIPTION ID, pressing the second copy posted an id the first copy had
    just deleted, got a 404, and htmx does not swap a 4xx: the reader saw a chip
    that lied and then did nothing at all until a full reload. Keyed by tag
    there is no id to go stale. No row means nothing to delete, NOT an error,
    and the answer is the follow chip either way -- the same idempotence
    `POST /subscriptions` has always had on the follow side, where re-pressing
    upserts.

    A missing TAG is still a 404: that is a bad request about the catalogue, not
    a race between two copies of a chip.

    This is a SECOND route rather than a widening of `unsubscribe`, deliberately.
    That one is posted by Preferences and the welcome wizard, which render a tag
    once and reload wholly, so they cannot go stale and gain nothing here; and a
    single route resolving the row by id OR by tag depending on which field the
    form happened to send is two identity schemes wearing one URL, with a path
    segment that is sometimes ignored. Chips post here; nothing else does.
    """
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="tag not found")
    sub = (await session.execute(
        select(TagSubscription).where(
            TagSubscription.user_id == user.id, TagSubscription.tag_id == tag_id
        )
    )).scalar_one_or_none()
    if sub is not None:
        await session.delete(sub)
        await session.commit()

    # Same split as `subscribe` above, and for the same reason -- an unfollow
    # is a press on the same chip.
    if request.headers.get("HX-Request") != "true":
        return RedirectResponse(_safe_next(next_url), status_code=303)
    return await _chip_fragment(session, tag, None, chip)


@router.post("/subscriptions/{sub_id}/delete")
async def unsubscribe(
    sub_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    next_url: str = Form("/preferences", alias="next"),
):
    """Unfollow by SUBSCRIPTION ID -- Preferences' Following rows and its tag
    picker, and the welcome wizard. Each renders a given tag once and answers
    with a whole re-rendered page, so no copy of a chip can be left holding a
    deleted id; see `unfollow_tag` above for the surface where that mattered."""
    sub = await session.get(TagSubscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status_code=404)
    await session.delete(sub)
    await session.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)


# ── Admin: editors ───────────────────────────────────────────────────────


@router.post("/admin/editors")
async def add_editor(
    admin: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    discord_id: int = Form(...),
):
    await set_editor(session, discord_id, True)
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/admin/editors/{discord_id}/remove")
async def remove_editor(
    discord_id: int,
    admin: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if discord_id in settings.editor_ids:
        raise HTTPException(
            status_code=400, detail="Editor is env-managed (EDITOR_WHITELIST) — edit .env instead"
        )
    await set_editor(session, discord_id, False)
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


# ── One-click apply on a concert ─────────────────────────────────────────


@router.post("/concerts/{event_id}/presets/{preset_id}/apply", response_class=HTMLResponse)
async def apply_preset_to_concert(
    request: Request,
    event_id: str,
    preset_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    from app.web.routes.concerts import get_concert_by_event_id, render_rules_fragment

    concert = await get_concert_by_event_id(session, event_id)
    preset = await owned_preset(session, user.id, preset_id)
    await apply_preset(session, user.id, concert.id, preset)
    await session.commit()
    return await render_rules_fragment(request, concert, user, session)


# ── Timezone ─────────────────────────────────────────────────────────────


@router.post("/me/timezone")
async def set_timezone(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    timezone: str = Form(...),
    next_url: str = Form("/preferences", alias="next"),
):
    """Manual choice: sticks, and turns browser auto-detection off."""
    try:
        ZoneInfo(timezone)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {timezone}") from e
    db_user = await ensure_user(session, user.id, user.username)
    db_user.timezone = timezone
    db_user.tz_auto = False
    await session.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)


@router.post("/me/timezone/auto")
async def set_timezone_auto(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    timezone: str = Form(...),
):
    """Browser-detected timezone. Respected only while the user hasn't overridden."""
    try:
        ZoneInfo(timezone)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {timezone}") from e
    db_user = await ensure_user(session, user.id, user.username)
    if db_user.tz_auto:
        db_user.timezone = timezone
        await session.commit()
    return HTMLResponse("", status_code=204)


@router.post("/me/timezone/reset")
async def reset_timezone_auto(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    next_url: str = Form("/preferences", alias="next"),
):
    """Back to auto: next page load re-detects from the browser. `next` (guarded
    by _safe_next, same as /me/timezone) lets the welcome wizard's Detection
    control land back on /welcome instead of /preferences -- not a new write
    path, just the same reset honouring where it was invoked from."""
    db_user = await ensure_user(session, user.id, user.username)
    db_user.tz_auto = True
    await session.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)


# ── Agent API token ──────────────────────────────────────────────────────


@router.post("/me/api-token")
async def create_api_token(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Mint (or re-mint) the agent read-API token -- require_user, generate,
    commit, same as every other mint in this file. Only its hash is stored
    (invariant 5), so the reveal this triggers is the only time the raw
    value is ever shown.

    This route DELIBERATELY DOES NOT mirror POST /me/calendar-feed's reveal
    mechanism, and that is not an oversight -- don't "restore consistency"
    by putting the token back in the URL. The calendar feed's token MUST
    live in a URL forever (a calendar client polls that URL on its own
    schedule with no cookies, per calendar.py), so its appearance in
    Caddy/Cloudflare access logs, browser history and the address bar is
    unavoidable and already an accepted cost. This token is the opposite
    shape: `db/models.py`'s own comment on `api_token_hash` says it is "sent
    as an Authorization: Bearer header rather than in a URL, which the
    calendar feed cannot do" -- so putting it in a URL here, even a
    same-origin redirect target only this browser follows, would be an
    AVOIDABLE credential leak into exactly those logs. `Referrer-Policy` does
    not help: it stops the URL leaking to a third-party Referer header, not
    its own appearance in the server's access log or this browser's history.

    So the raw value rides a one-shot session flash instead -- a signed
    cookie, not a URL -- and the redirect target is the bare, query-free
    `/preferences`. GET /preferences pops (not reads) the flash, which is
    what makes it one-shot: a second render, even of the exact same
    /preferences URL, shows nothing.

    One residual exposure the flash doesn't close: if the user never loads
    /preferences again after this 303 (closes the tab, the browser crashes),
    the raw token sits in the signed -- NOT encrypted -- session cookie,
    readable by anyone who gets that cookie, until something eventually pops
    it. Still strictly better than the query string this replaced (no
    server/proxy access log, no browser history, no Referer leak), so this is
    accepted rather than fixed here -- just worth knowing before treating the
    flash as a full mitigation."""
    token = await generate_api_token(session, user.id)
    await session.commit()
    request.session["api_token"] = token
    return RedirectResponse("/preferences", status_code=303)


# ── DM diagnostics ───────────────────────────────────────────────────────


@router.post("/me/test-dm", response_class=HTMLResponse)
async def send_test_dm(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Synchronous, explicit exception to CLAUDE.md's "never send DMs
    directly from web routes" invariant (see the invariant's own addendum
    for why) -- a manual, user-initiated diagnostic action, unlike the
    notifications-table-driven system notices. Returns a one-line htmx
    fragment (this codebase has no flash-message system), following the
    hx-post/hx-target/hx-swap idiom _rules.html already establishes."""
    if not settings.bot_enabled:
        return HTMLResponse("Discord bot isn't running in this environment.")

    from app.bot.client import bot  # lazy: avoid discord.py setup cost in web-only dev mode

    try:
        discord_user = bot.get_user(user.id) or await bot.fetch_user(user.id)
        await discord_user.send(
            "This is a test DM from dekimasen.app — your reminders are working!"
        )
        await record_dm_outcome(session, user.id, blocked=False)
        await session.commit()
        return HTMLResponse("Test DM sent!")
    except discord.Forbidden:
        await record_dm_outcome(session, user.id, blocked=True)
        await session.commit()
        return HTMLResponse("Still blocked — check your Discord privacy settings.")
    except discord.HTTPException:
        return HTMLResponse("Couldn't reach Discord, try again.")


# ── Account deletion ─────────────────────────────────────────────────────


@router.post("/me/delete")
async def delete_account(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Self-serve GDPR erasure, scoped to the AUTHENTICATED CALLER ONLY.

    The id comes from the session (require_user), never from request input, so
    a logged-in user can only ever delete themselves. delete_user cascades away
    everything personal and SET-NULLs the shared catalogue this user authored
    -- their concerts/tags survive with the author blanked (see db/service.py).
    Revoke the session through the same path logout uses (the cascade then
    removes the row too), then land the now-signed-out visitor on Home.

    The heavy confirmation lives client-side in the Account danger card (a
    deliberate second action naming the loss); the route deliberately does NOT
    require it to have run -- it just performs the erasure for the caller.
    """
    await revoke_session(request, session)
    await delete_user(session, user.id)
    await session.commit()
    return RedirectResponse("/?deleted=1", status_code=303)
