"""The Eventernote discovery review surface.

  GET  /admin/discoveries                    every open lead, plus one paste block
  POST /admin/discoveries/{lead_id}/dismiss  wave one off for good

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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DiscoveredEvent, Tag
from app.db.service import dismiss_lead, leads_matching_existing_legs, open_leads
from app.db.session import get_session
from app.domain.discovery_message import Lead, build_discovery_dm
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web.app at startup


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
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Every lead awaiting triage.

    ANNOUNCED IS NOT TRIAGED: `open_leads` deliberately does not filter on
    `announced_at`, because the sweep marks every fresh lead announced whether
    the DM listed it or merely counted it -- so this page is where the "+N
    more" of a first sweep is actually reachable. The column is SHOWN instead:
    on a backlog it is what separates a lead the DM already named from one that
    arrived today.
    """
    leads = await open_leads(session)
    hinted = await leads_matching_existing_legs(session, leads)
    names = await _artist_names(session, leads)
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
        },
    )


@router.post("/admin/discoveries/{lead_id}/dismiss")
async def dismiss(
    lead_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Wave a lead off for good.

    404 on a False from `dismiss_lead` -- an unknown id, or one already
    dismissed. Reporting a write that did not happen as a cheerful 303 is how a
    double-submit looks like it worked.

    303, never 307: the POST must not be replayed against the page it lands on.
    """
    if not await dismiss_lead(session, lead_id, datetime.now(UTC)):
        raise HTTPException(status_code=404, detail="no such lead")
    await session.commit()
    return RedirectResponse("/admin/discoveries", status_code=303)
