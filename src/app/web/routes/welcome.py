"""First-run guided setup: a five-step wizard offered once at first login
(see auth.py's callback -- a brand-new row redirects here instead of /).
Each step reuses an existing action's route verbatim; this file only
sequences them.

  GET  /welcome              current step's screen (redirects to / once done)
  POST /welcome/advance      move to the next step
  POST /welcome/skip-all     jump straight to done
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Tag, TagSubscription
from app.db.service import (
    create_preset_from_rules,
    ensure_user,
    group_members,
    set_default_preset,
)
from app.db.session import get_session
from app.domain.timezones import fmt_dual_lines
from app.domain.types import Anchor, TagKind
from app.web.auth import SessionUser, require_user
from app.web.routes.preferences import COMMON_TIMEZONES, all_timezones

router = APIRouter()

templates = None  # set by web.app at startup

TOTAL_STEPS = 5

# The reminder-step vocabulary, defined ONCE here and handed to the template
# (the JS seeds/edits rows from these; invariant 7: passed via `| tojson`, not
# `| safe`). Offsets are expressible in days+hours only -- PresetItem has no
# minutes column (minute offsets are a separate wishlist item), so the demo's
# "30 minutes" option is deliberately dropped. A "0:0" offset is the "when it
# happens" moment, which carries no before/after.
OFFSET_OPTIONS = [
    {"value": "0:0", "label": "when", "moment": True},
    {"value": "0:1", "label": "1 hour", "moment": False},
    {"value": "0:3", "label": "3 hours", "moment": False},
    {"value": "0:6", "label": "6 hours", "moment": False},
    {"value": "1:0", "label": "1 day", "moment": False},
    {"value": "3:0", "label": "3 days", "moment": False},
    {"value": "5:0", "label": "5 days", "moment": False},
    {"value": "7:0", "label": "1 week", "moment": False},
]

# Two phrasings per anchor keep every rule reading as a grammatical sentence:
# the verb phrase after "when" (a moment offset) and the noun phrase after
# "before"/"after" (a duration offset). Covers all five real anchors.
ANCHOR_ORDER = ["opens", "closes", "results", "payment", "event_start"]
ANCHOR_MOMENT = {
    "opens": "applications open", "closes": "applications close",
    "results": "results come out", "payment": "payment is due",
    "event_start": "the show starts",
}
ANCHOR_NOUN = {
    "opens": "the opening", "closes": "the deadline", "results": "the results",
    "payment": "the payment deadline", "event_start": "the show",
}

# The three starter presets as rule sets, defined once. Each rule is
# (offset_days, offset_hours, direction, anchor); direction is "before"/"after"
# and only meaningful for non-moment offsets. The default (standard) reminds
# once for opens/results/payment and gives closes -- the round with an action
# to take -- a couple of run-ups; nothing on the show itself.
PRESET_TEMPLATES = {
    "relaxed": [
        [0, 0, "before", "opens"],
        [1, 0, "before", "closes"],
        [0, 0, "before", "results"],
        [0, 0, "before", "payment"],
    ],
    "standard": [
        [0, 0, "before", "opens"],
        [3, 0, "before", "closes"],
        [1, 0, "before", "closes"],
        [0, 0, "before", "results"],
        [1, 0, "before", "payment"],
    ],
    "onball": [
        [1, 0, "before", "opens"],
        [0, 0, "before", "opens"],
        [5, 0, "before", "closes"],
        [3, 0, "before", "closes"],
        [1, 0, "before", "closes"],
        [0, 0, "before", "results"],
        [3, 0, "before", "payment"],
        [1, 0, "before", "payment"],
    ],
}


@router.get("/welcome", response_class=HTMLResponse)
async def welcome(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    feed_token: str = "",
):
    db_user = await ensure_user(session, user.id, user.username)
    if db_user.onboarding_step >= TOTAL_STEPS:
        return RedirectResponse("/", status_code=303)

    step = db_user.onboarding_step
    context = {"user": user, "step": step, "bot_enabled": settings.bot_enabled}

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
        # The reminder-step data the fine-tune UI seeds and edits from. The
        # standard rows are also emitted server-side so the list is populated
        # with JS off (no flash, and a JS-off submit still creates a preset).
        context.update({
            "offset_options": OFFSET_OPTIONS,
            "anchor_order": ANCHOR_ORDER,
            "anchor_moment": ANCHOR_MOMENT,
            "anchor_noun": ANCHOR_NOUN,
            "preset_templates": PRESET_TEMPLATES,
            "standard_rules": PRESET_TEMPLATES["standard"],
        })
    elif step == 2:
        # A live JST/local sample so the user sees the dual render in the flesh
        # (invariant 1: JST first, both zones always present).
        _, tz_preview = fmt_dual_lines(datetime.now(UTC), db_user.timezone)
        context.update({
            "tz": db_user.timezone, "tz_auto": db_user.tz_auto, "tz_preview": tz_preview,
            "common_timezones": COMMON_TIMEZONES, "all_timezones": all_timezones(),
        })
    elif step == 4:
        context.update({
            "has_calendar_feed": bool(db_user.calendar_token_hash),
            "feed_url": f"{settings.base_url}/calendar/{feed_token}.ics" if feed_token else None,
        })

    return templates.TemplateResponse(request, "welcome.html", context)


@router.post("/welcome/preset")
async def create_wizard_preset(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    offset: list[str] = Form(default=[]),
    direction: list[str] = Form(default=[]),
    anchor: list[str] = Form(default=[]),
):
    """The reminder step's submit: zip the fine-tune rows' parallel arrays into
    a real ReminderPreset via the shared service (no second write path), mark it
    the user's default, then advance into the timezone step.

    Each offset arrives as "days:hours" (the fine-tune UI's own encoding, since
    PresetItem stores days+hours only); direction and anchor ride alongside as
    matching arrays. A short/mismatched tail is ignored by zip's own truncation.
    """
    db_user = await ensure_user(session, user.id, user.username)
    rules: list[tuple[int, int, str, Anchor]] = []
    for off, dir_, anc in zip(offset, direction, anchor, strict=False):
        days_str, _, hours_str = off.partition(":")
        rules.append((int(days_str or 0), int(hours_str or 0), dir_, Anchor(anc)))
    preset = await create_preset_from_rules(session, user.id, "My reminders", rules)
    await set_default_preset(session, user.id, preset.id)
    # Same forward-only step gate the wizard runs on; never regress a step.
    db_user.onboarding_step = max(db_user.onboarding_step, 2)
    await session.commit()
    return RedirectResponse("/welcome", status_code=303)


@router.post("/welcome/advance")
async def advance(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    db_user.onboarding_step = min(db_user.onboarding_step + 1, TOTAL_STEPS)
    await session.commit()
    # Crossing into done hands off to the first-run capture flow -- the reveal
    # at /setup/ready is the wizard's payoff. Earlier advances stay on the
    # wizard. (skip-all still lands on /, skipping the capture flow too.)
    if db_user.onboarding_step >= TOTAL_STEPS:
        return RedirectResponse("/setup", status_code=303)
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
