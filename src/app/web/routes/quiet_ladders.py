"""Round watch: which concerts in the catalogue have a ladder that has gone quiet.

  GET  /admin/quiet-ladders                    the worklist, plus one paste block
  POST /admin/quiet-ladders/{event_id}/checked stamp "I re-checked this"
  GET  /admin/quiet-ladders/proposals           the round poll's review queue

Its own module and its own page rather than a section of /admin/discoveries.
That surface answers "what exists that you are not tracking"; this answers
"what changed about what you already track". discoveries.py's own docstring
argues for splitting on exactly this line, and a router registers whole.

The worklist route WRITES ONE THING: ladder_rechecked_at_utc. It never edits a
concert -- there is no update path back in (import answers 409 for a concert
that exists, invariant 6), so a re-check ends at the concert's edit page or at
an agent, which is what the copy block is for. `/proposals` writes nothing at
all -- phase 1 of the round poll (docs/superpowers/plans/2026-08-13-round-poll-
phase-1.md) ships this page read-only; approving or dismissing a proposal is
phase 2.

Copy is English-only and NOT wrapped in _(), like /admin/deliveries and
/admin/discoveries: an operational page only admins see should not cost msgids
in three languages. Only the Preferences link to the worklist is translated;
`/proposals` has no nav link at all -- the poll's digest DM is the only way
in, same shape as the digest linking straight to a batch under
/admin/deliveries.
"""

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import RoundProposal, User
from app.db.service import (
    pending_proposal_groups,
    quiet_entry_from_row,
    quiet_ladder_rows,
    record_ladder_checked,
)
from app.db.session import get_session
from app.domain.quiet_ladder_message import build_quiet_ladder_block
from app.domain.urls import UnsafeURLError, clean_url
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web.app at startup

# The four TIMESTAMP_FIELDS keys `round_evidence.py` ever leaves in a
# proposal's evidence mapping (domain/round_evidence.py:184) -- an unexpected
# key falls back to itself rather than raising, since the evidence quotes are
# proofreading scaffolding, never data this page's correctness depends on.
_EVIDENCE_FIELD_LABELS = {
    "apply_opens_jst": "Opens",
    "apply_closes_jst": "Closes",
    "results_jst": "Results",
    "payment_deadline_jst": "Payment deadline",
}


def _evidence_lines(evidence_yaml: str) -> list[tuple[str, str]]:
    """A proposal's quoted-line evidence as (label, quote) pairs, insertion
    order preserved -- the whole reason this page exists: rendering the label
    and dates without the quote leaves an operator nothing to check the claim
    against, which is what separates a proposal from a guess.

    Best-effort like imports.py's completion-yaml preview: `evidence_yaml` is
    proofreading scaffolding beside the proposal, not data anything downstream
    depends on, so a row that no longer parses just loses its quotes rather
    than 500ing the page.
    """
    if not evidence_yaml:
        return []
    try:
        loaded = yaml.safe_load(evidence_yaml)
    except yaml.YAMLError:
        return []
    if not isinstance(loaded, dict):
        return []
    return [
        (_EVIDENCE_FIELD_LABELS.get(str(k), str(k)), str(v))
        for k, v in loaded.items()
    ]


def _safe_source_url(raw: str) -> str | None:
    """An http(s) `source_url`, or None to render no link at all.

    `source_url` is the model's own text, exactly as untrusted as an editor's
    per invariant 7: `clean_url` raises on anything that isn't a real http(s)
    URL, and a bad one just drops the link rather than putting a
    `javascript:` href on an admin's screen.
    """
    if not raw:
        return None
    try:
        return clean_url(raw)
    except UnsafeURLError:
        return None


def _proposal_row(proposal: RoundProposal) -> dict:
    return {
        "proposal": proposal,
        "evidence": _evidence_lines(proposal.evidence_yaml),
        "source_url": _safe_source_url(proposal.source_url),
    }


@router.get("/admin/quiet-ladders", response_class=HTMLResponse)
async def quiet_ladders(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = await quiet_ladder_rows(session)
    block = build_quiet_ladder_block([quiet_entry_from_row(row) for row in rows])
    return templates.TemplateResponse(
        request,
        "admin_quiet_ladders.html",
        {"user": user, "rows": rows, "copy_text": block},
    )


@router.post("/admin/quiet-ladders/{event_id}/checked")
async def mark_checked(
    event_id: str,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if not await record_ladder_checked(session, event_id):
        raise HTTPException(status_code=404, detail="no such concert")
    await session.commit()
    return RedirectResponse("/admin/quiet-ladders", status_code=303)


@router.get("/admin/quiet-ladders/proposals", response_class=HTMLResponse)
async def round_proposals(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The round poll's review queue -- the link the digest DM already sends.

    Read-only in phase 1: no buttons, nothing this route writes. `tz` follows
    the same pattern as outcomes.py/subscriptions.py rather than home.py's
    fuller context, since this page has no htmx swaps to keep in sync with.
    """
    groups = await pending_proposal_groups(session)
    db_user = await session.get(User, user.id)
    tz = db_user.timezone if db_user else settings.default_timezone
    view_groups = [
        {
            "event_id": group.event_id,
            "title": group.title,
            "rows": [_proposal_row(p) for p in group.proposals],
        }
        for group in groups
    ]
    return templates.TemplateResponse(
        request,
        "admin_round_proposals.html",
        {"user": user, "tz": tz, "groups": view_groups},
    )
