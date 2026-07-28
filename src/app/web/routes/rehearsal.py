"""The local rehearsal harness: seed a canonical concert, pull its reminders
forward, and send every DM shape on demand.

Registered ONLY when `settings.rehearsal_enabled` is true, which production
never sets -- see web/app.py. `require_admin` is a second layer for a
misconfigured deploy, not the primary guard.

Its own module rather than a section of admin.py: a router registers whole,
and admin.py serves /admin/deliveries and /admin/broadcast, which must exist
in production.

English-only and not wrapped in _(), following /me/test-dm and
/admin/deliveries -- this page only ever renders on a developer machine.

  GET  /admin/rehearsal              the walk, the actions, and the state table
  POST /admin/rehearsal/start        seed (or reseed) the canonical scenario
  POST /admin/rehearsal/next         pull the soonest unsent reminder forward
  POST /admin/rehearsal/cancel-show  cancel every live leg -> leg_cancelled DM
  POST /admin/rehearsal/end          delete the concert; cascades take the rest
  POST /admin/rehearsal/shape        render ONE DM shape in ONE language, now

Every action is a plain form POST answering 303 back to the page: each one
rewrites the state table wholesale, and there is nothing here worth an htmx
fragment on a page only its own author ever loads. A one-line result rides
back in `?note=` -- this codebase has no flash-message system.
"""

from datetime import UTC, datetime
from urllib.parse import quote

import discord
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import i18n
from app.bot.messages import (
    build_leg_cancelled_message,
    build_new_event_message,
    build_reminder_message,
)
from app.config import settings
from app.db.models import Concert, ConcertDay, Round, User
from app.db.service import (
    DueReminder,
    _round_info,
    cancel_rehearsal_show,
    get_rehearsal_concert,
    leg_cancelled_context,
    notice_context,
    pull_rehearsal_forward,
    rehearsal_queue_rows,
    rehearsal_rows,
    seed_rehearsal,
    teardown_rehearsal,
)
from app.db.session import get_session
from app.domain.reminders import anchor_time
from app.domain.types import Anchor, LotteryOutcome
from app.i18n import loc_field
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web.app at startup

# The eight shapes, in the order the picker offers them: the five reminder
# anchors, the two notice embeds, and the plain-text ops alert. English labels,
# like the rest of this page.
SHAPES: tuple[tuple[str, str], ...] = (
    ("reminder_opens", "Reminder — round opens"),
    ("reminder_closes", "Reminder — round closes"),
    ("reminder_results", "Reminder — results announced"),
    ("reminder_payment", "Reminder — payment due"),
    ("reminder_event_start", "Reminder — performance starts"),
    ("new_event", "Notice — new event"),
    ("leg_cancelled", "Notice — performance cancelled"),
    ("ops_alert", "Ops alert (plain text)"),
)

SHAPE_ANCHORS: dict[str, Anchor] = {
    "reminder_opens": Anchor.OPENS,
    "reminder_closes": Anchor.CLOSES,
    "reminder_results": Anchor.RESULTS,
    "reminder_payment": Anchor.PAYMENT,
    "reminder_event_start": Anchor.EVENT_START,
}

# Language NAMES are never translated -- base.html's cycle chip rule, and the
# same reason applies here: you have to recognise the language before you can
# read anything rendered in it.
LOCALES: tuple[tuple[str, str], ...] = (("en", "EN"), ("zh", "中文"), ("ja", "日本語"))

# An ops alert is composed by evaluate_and_alert as a bare f-string with no
# gettext anywhere -- English by construction, for one admin recipient. The
# locale picker therefore does nothing to this shape ON PURPOSE; it is in the
# catalogue for its LAYOUT (a plain-text DM, no embed, no buttons), which is
# the one thing the other seven cannot show.
OPS_ALERT_BODY = (
    "dekimasen.app check `scheduler_heartbeat` FAILING: no scheduler tick in 4 minutes"
)


def _back(note: str) -> RedirectResponse:
    return RedirectResponse(f"/admin/rehearsal?note={quote(note)}", status_code=303)


@router.get("/admin/rehearsal", response_class=HTMLResponse)
async def rehearsal(
    request: Request,
    note: str | None = None,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "rehearsal.html",
        {
            "user": user,
            "note": note,
            "concert": await get_rehearsal_concert(session),
            "rows": await rehearsal_rows(session, user.id),
            "shapes": SHAPES,
            "locales": LOCALES,
        },
    )


@router.post("/admin/rehearsal/start")
async def start(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Idempotent: seed_rehearsal tears down any previous rehearsal first, so
    pressing this twice leaves one concert rather than two."""
    await seed_rehearsal(session, user.id)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/next")
async def next_reminder(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """No queue id crosses this boundary -- the row is resolved through the
    rehearsal concert, so no other concert's reminder is reachable from here."""
    await pull_rehearsal_forward(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/cancel-show")
async def cancel_show(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Terminal, and the template says so: this kills every remaining queue
    row, the upgrade round's included."""
    await cancel_rehearsal_show(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/end")
async def end(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await teardown_rehearsal(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


# ── The shape catalogue ──────────────────────────────────────────────────


async def _reminder_for(
    session: AsyncSession, concert: Concert, db_user: User, anchor: Anchor, locale: str
) -> DueReminder:
    """A DueReminder for one anchor, built from the SEEDED concert.

    Everything here is real except the wait: the round, the leg, the anchor
    time and the label all come from the rehearsal rows, so the embed the
    operator reads is composed from the same inputs a delivered reminder is.

    Two fields are part of the SHAPE rather than of the operator's progress.
    The outcome is chosen so the shape renders its characteristic buttons --
    PAYMENT only offers Paid from WON, so reading the live outcome would make
    the payment shape button-less until the pipeline walk had reached step 4,
    which is precisely the coupling this half exists to avoid. And covered_days
    is filled on a RESULTS row exactly when two or more live legs are covered,
    which is due_reminders' own rule and what turns the flat Won/Lost pair into
    the per-leg view.
    """
    days = list((await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id)
        .order_by(ConcertDay.starts_at_utc, ConcertDay.id)
    )).scalars())
    rounds = list((await session.execute(
        select(Round).where(Round.concert_id == concert.id).order_by(Round.id)
    )).scalars())

    # `anchor_time` over service's own ORM->domain adapter rather than a local
    # switch: the Anchor -> Round field map lives in exactly one place
    # (domain/reminders.py) and this must not become a second copy. It returns
    # None for EVENT_START, which is what leaves that shape day-anchored.
    round_ = next((r for r in rounds if anchor_time(_round_info(r), anchor) is not None), None)
    live = [d for d in days if not d.cancelled] or days
    day = live[0] if live else None
    covered = [
        d for d in live
        if round_ is not None and (not round_.applies_to or d.id in round_.applies_to)
    ]
    # A genuine queue id where one exists, so the trailing snooze/remind-later
    # button addresses a real row instead of erroring on press. Zero is the
    # honest fallback once the walk has sent everything.
    queue_id = next(
        (q.id for q in await rehearsal_queue_rows(session) if q.anchor is anchor), 0
    )
    return DueReminder(
        queue_id=queue_id,
        discord_id=db_user.discord_id,
        user_timezone=db_user.timezone,
        user_language=locale,
        concert_title=loc_field(concert, "title", locale),
        anchor=anchor,
        fire_at_utc=datetime.now(UTC),
        concert_id=concert.id,
        day_id=day.id if round_ is None and day else None,
        round_id=round_.id if round_ else None,
        round_label=loc_field(round_, "label", locale) if round_ else None,
        round_kind=round_.kind.value if round_ else None,
        outcome=LotteryOutcome.WON if anchor is Anchor.PAYMENT else None,
        anchor_time_utc=(
            anchor_time(_round_info(round_), anchor) if round_
            else (day.starts_at_utc if day else None)
        ),
        url=round_.url if round_ else None,
        day_label=loc_field(day, "label", locale) if round_ is None and day else None,
        covered_days=(
            tuple((d.id, loc_field(d, "label", locale)) for d in covered)
            if anchor is Anchor.RESULTS and len(covered) >= 2 else ()
        ),
    )


async def _build_shape(
    session: AsyncSession, concert: Concert, db_user: User, shape: str, locale: str
):
    """(embed, view) for the seven embed shapes, or a plain string for the
    ops alert. The caller has already set the locale ContextVar.

    The two NOTICE shapes need a second thing done. NoticeContext and
    LegCancelledContext resolve their UGC fields from the RECIPIENT's
    `users.language`, not from `get_locale()` -- the right rule for a notice
    composed once for many recipients, and a different one of the three locale
    sources CLAUDE.md warns about. `set_locale` alone would therefore render
    the prose in the picked language and the concert title in the operator's,
    silently. So the operator's own row carries the chosen locale for the
    length of the build; the route rolls the session back afterwards, which is
    free because the catalogue writes nothing at all.
    """
    anchor = SHAPE_ANCHORS.get(shape)
    if anchor is not None:
        return build_reminder_message(
            await _reminder_for(session, concert, db_user, anchor, locale)
        )
    if shape == "ops_alert":
        return OPS_ALERT_BODY

    db_user.language = locale
    if shape == "new_event":
        return build_new_event_message(
            await notice_context(session, concert.id, db_user.discord_id)
        )
    return build_leg_cancelled_message(
        await leg_cancelled_context(session, concert.id, db_user.discord_id)
    )


@router.post("/admin/rehearsal/shape")
async def send_shape(
    shape: str = Form(...),
    locale: str = Form("en"),
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Render ONE DM shape through the real builders, in ONE chosen language,
    and send it to the operator now.

    This direct send is a SECOND deliberate exception to invariant 4 ("never
    send DMs directly from web routes"), alongside POST /me/test-dm, and for
    the same reason: a manual, user-initiated, one-at-a-time diagnostic is a
    different animal from a system-initiated notice, which still needs the
    outbox for its retry, ordering and audit properties. This one can claim
    something /me/test-dm cannot -- the route does not exist in production at
    all (see the module docstring), so the carve-out is local by construction
    rather than by discipline. Don't read either as licence for a third.

    It is deliberately independent of the pipeline half: no queue row, no
    tick, no walk state. That is what makes it the fastest ja/zh copy review
    in the project -- eight embeds in three languages, checked in a minute --
    and it is also why the payment shape composes itself under a WON outcome
    instead of the operator's real progress.

    Like /me/test-dm it REPORTS rather than raises: a bot-less environment,
    an unseeded harness or a closed DM all come back as a note on the page.
    """
    labels = dict(SHAPES)
    if shape not in labels:
        return _back(f"Unknown shape {shape!r}.")
    if locale not in dict(LOCALES):
        return _back(f"Unknown locale {locale!r}.")
    if not settings.bot_enabled:
        return _back("Discord bot isn't running in this environment — nothing was sent.")
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return _back("Nothing seeded — press Start first; every shape is built from that concert.")

    from app.bot.client import bot  # lazy: avoid discord.py setup cost in web-only dev mode

    db_user = await session.get(User, user.id)
    try:
        # Same discipline as scheduler/loop.py:deliver -- set it around the
        # build, reset in the finally. A leaked locale renders somebody else's
        # language and nothing raises.
        i18n.set_locale(locale)
        payload = await _build_shape(session, concert, db_user, shape, locale)
        discord_user = bot.get_user(user.id) or await bot.fetch_user(user.id)
        if isinstance(payload, str):
            await discord_user.send(payload)
        else:
            embed, view = payload
            await discord_user.send(embed=embed, view=view)
    except discord.Forbidden:
        return _back("Blocked — check your Discord privacy settings.")
    except discord.HTTPException:
        return _back("Couldn't reach Discord, try again.")
    finally:
        i18n.set_locale("en")
        # The catalogue writes NOTHING. Rolling back is what un-does the
        # borrowed users.language above, whether or not an autoflush wrote it.
        await session.rollback()
    return _back(f"Sent: {labels[shape]} ({locale}).")
