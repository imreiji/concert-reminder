"""The Eventernote discovery review surface.

  GET  /admin/discoveries                    every open lead, plus one paste block
  POST /admin/discoveries/sweep              ask the scheduler to sweep now
  POST /admin/discoveries/sweep/{tag_id}     read ONE artist's page, inline
  POST /admin/discoveries/{lead_id}/dismiss  wave one off for good

The per-tag sweep lives HERE rather than in routes/tags.py even though its
button is on the Tags page: a router registers whole, so putting an admin-only
discovery write next to that module's editor-gated routes would split the
feature's gating across two files. Every discovery route is admin-only, in one
place, and that is the property worth keeping.

Its own module rather than a section of admin.py: that one is the delivery
log, the broadcast and the catalogue round-trip, and discovery is a fourth
unrelated concern. tags_yaml/tags_diff set the precedent for splitting on
exactly that line, and a router registers whole, so the split has to happen at
the module.

This surface WRITES ONE THING: `dismissed_at`. It never creates a concert --
Eventernote carries no ticket information at all, so a lead can say "this
exists and you are not tracking it" and nothing more. Turning one into a
concert is an agent following .claude/skills/add-concert, which is what the
copy block is for.

Copy is English-only and NOT wrapped in _(), like /admin/deliveries and
/admin/broadcast: an operational page only admins see should not cost msgids
in three languages (tests/test_i18n_catalogues.py would enforce them). Only
the Preferences LINK to it is translated.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DiscoveredEvent, Tag
from app.db.service import (
    discovery_status,
    dismiss_lead,
    dismissed_reason_counts,
    leads_matching_existing_legs,
    open_leads,
    request_sweep,
)
from app.db.session import get_session
from app.discovery import sweep_one_tag
from app.domain.discovery_message import Lead, build_discovery_dm
from app.domain.types import DISMISS_REASON_LABELS, DismissReason
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web.app at startup

# The closed vocabulary of `?swept=` codes. A CODE, never a sentence: this
# codebase has no flash-message system (see routes/rehearsal.py, which puts its
# note text straight in `?note=`), and the URL of a page an operator lands on
# ends up in logs, history and any Referer that leaves the box. A fixed
# four-word vocabulary carries nothing about the operator, nothing about which
# artist was checked, and nothing editor-typed -- the wording lives in the
# template, where it is also easier to keep in the page's own voice. The
# template matches these literally, so an invented code renders no banner at
# all rather than anything of the visitor's choosing.
SWEPT_CODES = frozenset({"ok", "no_actor", "failed"})


@dataclass(frozen=True)
class LeadRow:
    """One row of the review table: the stored lead, plus the two things the
    row needs that are not columns on it."""

    lead: DiscoveredEvent
    artist: str
    maybe_held: bool


async def _artist_names(session: AsyncSession, leads: list[DiscoveredEvent]) -> dict[int, str]:
    """Tag id -> name for the tags that surfaced these leads.

    ONE query for the whole page rather than a `session.get` per row: a
    first-sweep backlog is hundreds of leads long, and this is a plain lookup
    (the same shape as admin.py's broadcast_status counting its pending rows),
    not business logic that belongs in db/service.py.
    """
    ids = {lead.first_seen_via_tag_id for lead in leads if lead.first_seen_via_tag_id}
    if not ids:
        return {}
    rows = (await session.execute(select(Tag.id, Tag.name).where(Tag.id.in_(ids)))).all()
    return {tag_id: name for tag_id, name in rows}


def _to_lead(row: LeadRow) -> Lead:
    """A stored row adapted to the pure message layer's plain dataclass.

    Near-twin of app/discovery.py's `_lead`, deliberately not shared: that one
    resolves the artist out of the sweep's in-memory dict of tags it just read,
    this one out of a database lookup, and the only common part is the field
    mapping. What MUST stay shared is `build_discovery_dm` itself -- two
    formatters would drift, and the block's job (a prompt an agent can act on)
    is identical in the DM and here.
    """
    return Lead(
        event_id=row.lead.eventernote_event_id,
        title=row.lead.title,
        date=row.lead.event_date,
        venue=row.lead.venue,
        artist=row.artist,
        maybe_held=row.maybe_held,
    )


@router.get("/admin/discoveries", response_class=HTMLResponse)
async def discoveries(
    request: Request,
    swept: str | None = None,
    new: int = 0,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Every lead awaiting triage.

    ANNOUNCED IS NOT TRIAGED: `open_leads` deliberately does not filter on
    `announced_at`, because the sweep marks every fresh lead announced whether
    the DM listed it or merely counted it -- so this page is where the "+N
    more" of a first sweep is actually reachable. The column is SHOWN instead,
    and it means SEEN EARLIER, not DESCRIBED: `mark_leads_announced` stamps
    every fresh lead, so a date here separates a lead an earlier sweep already
    reported from one that arrived today, and says nothing about whether the DM
    named it or only counted it. The page copy has to say that -- reading a
    date as "I have already read about this" and skipping the row is exactly
    how a merely-counted lead gets lost.
    """
    leads = await open_leads(session)
    hinted = await leads_matching_existing_legs(session, leads)
    names = await _artist_names(session, leads)
    # How the last sweep went, shown whether or not there are leads -- an empty
    # table is exactly where "is this still working?" is the question, and a
    # broken sweep, a blocked IP and a quiet day all render the same empty table
    # without it.
    state = await discovery_status(session)
    rows = [
        LeadRow(
            lead=lead,
            artist=names.get(lead.first_seen_via_tag_id or 0, ""),
            maybe_held=lead.id in hinted,
        )
        for lead in leads
    ]
    return templates.TemplateResponse(
        request,
        "admin_discoveries.html",
        {
            "user": user,
            "rows": rows,
            # ALL of them: `total` equal to the count so there is no "+N more"
            # line, and budget=None so the block itself is not truncated
            # either. The DM caps both because Discord has a character limit;
            # this page is where its "+N more" points, so a lead dropped HERE
            # would be reachable from nowhere.
            "copy_text": build_discovery_dm(
                [_to_lead(row) for row in rows], total=len(rows), budget=None
            ),
            "hinted": len(hinted),
            "last_run_at": state.last_run_at if state else None,
            "last_fetched": state.last_fetched if state else None,
            "last_failed": state.last_failed if state else None,
            "last_truncated": state.last_truncated if state else None,
            # Whether a "Sweep now" press is still waiting for a tick. Shown so
            # a second press is not needed: the button writes a request and
            # returns instantly, which without this line is indistinguishable
            # from a button that did nothing.
            "sweep_pending": bool(state and state.sweep_requested_at),
            # How the per-tag sweep that redirected here went, or None on a
            # plain visit. Filtered against the closed vocabulary so a
            # hand-typed value cannot select a branch by accident; the template
            # compares literals anyway, so this is belt and braces.
            "swept": swept if swept in SWEPT_CODES else None,
            "swept_new": max(new, 0),
            "reason_counts": await dismissed_reason_counts(session),
            "dismiss_reasons": list(DismissReason),
            "dismiss_reason_labels": DISMISS_REASON_LABELS,
        },
    )


@router.post("/admin/discoveries/sweep")
async def sweep_now(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Ask the scheduler to sweep on its next tick.

    It does NOT sweep. A sweep is bounded at SWEEP_BUDGET_SECONDS (240) and a
    live one measured 442s before that budget existed -- neither is a thing an
    HTTP request may hold, and running it here would fork the one carefully
    bounded execution path in two. This writes `sweep_requested_at`; the tick
    picks it up within 60 seconds and calls the same `run_sweep`.

    No 404 branch, unlike dismiss: there is no such thing as a request that
    could not be made, and asking twice is harmless (the page disables the
    button while one is pending). 303, never 307, for dismiss's reason -- the
    POST must not be replayed against the page it lands on.
    """
    await request_sweep(session, datetime.now(UTC))
    await session.commit()
    return RedirectResponse("/admin/discoveries", status_code=303)


@router.post("/admin/discoveries/sweep/{tag_id}")
async def sweep_tag(
    tag_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Read ONE artist's Eventernote page, now, in this request.

    The opposite call from `sweep_now` above, and deliberately so: a FULL sweep
    is 86 third-party fetches bounded at SWEEP_BUDGET_SECONDS (240) and no HTTP
    request may hold that, so it is queued. ONE fetch is 1-10 seconds and is
    already bounded at FETCH_DEADLINE_SECONDS (30) -- an ordinary length for a
    request, and queueing it would mean waiting up to a minute for an answer
    the operator is sitting there watching for.

    It leaves the daily sweep's bookkeeping entirely alone -- no `last_run_at`,
    no cursor, no DM. `sweep_one_tag` carries the reasons.

    404 on an unknown tag, following dismiss: reporting a read that never
    happened as a cheerful 303 is how a stale link looks like it worked.

    303, never 307, for dismiss's reason -- the POST must not be replayed
    against the page it lands on. It redirects to /admin/discoveries rather
    than back to /tags because that is where anything found is visible; the
    result rides back in `?swept=`, a closed code (see SWEPT_CODES).
    """
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="no such tag")
    report = await sweep_one_tag(session, tag, datetime.now(UTC))
    # Committed on every path: a failed or non-actor sweep never reaches
    # `record_discovered`, so there is nothing to write and this is a no-op --
    # while making the commit conditional would put the one path that DOES
    # write behind a branch that has to stay correct forever.
    await session.commit()
    return RedirectResponse(
        f"/admin/discoveries?swept={report.status}&new={report.new_leads}",
        status_code=303,
    )


@router.post("/admin/discoveries/{lead_id}/dismiss")
async def dismiss(
    lead_id: int,
    reason: Annotated[DismissReason, Form()],
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Wave a lead off for good, recording which taxonomy class it was.

    `reason` is typed as the enum rather than validated by hand, so an invented
    class is a 422 from FastAPI before anything is written -- this column's
    whole value is that every row in it is a real human judgment.

    404 on a False from `dismiss_lead` -- an unknown id, or one already
    dismissed. Reporting a write that did not happen as a cheerful 303 is how a
    double-submit looks like it worked.

    303, never 307: the POST must not be replayed against the page it lands on.
    """
    if not await dismiss_lead(session, lead_id, datetime.now(UTC), reason):
        raise HTTPException(status_code=404, detail="no such lead")
    await session.commit()
    return RedirectResponse("/admin/discoveries", status_code=303)
