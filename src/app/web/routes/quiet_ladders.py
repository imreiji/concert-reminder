"""Round watch: which concerts in the catalogue have a ladder that has gone quiet.

  GET  /admin/quiet-ladders                      the worklist, plus one paste block
  POST /admin/quiet-ladders/{event_id}/checked   stamp "I re-checked this"
  GET  /admin/quiet-ladders/proposals             the round poll's review queue
  GET  /admin/quiet-ladders/proposals/{event_id}  one concert's proposals, as draft forms
  POST /admin/quiet-ladders/proposals/{event_id}/{proposal_id}/apply    write the round
  POST /admin/quiet-ladders/proposals/{event_id}/{proposal_id}/dismiss  never again

Its own module and its own page rather than a section of /admin/discoveries.
That surface answers "what exists that you are not tracking"; this answers
"what changed about what you already track". discoveries.py's own docstring
argues for splitting on exactly this line, and a router registers whole.

The worklist route WRITES ONE THING: ladder_rechecked_at_utc. It never edits a
concert -- there is no update path back in (import answers 409 for a concert
that exists, invariant 6), so a re-check ends at the concert's edit page or at
an agent, which is what the copy block is for. The two GET `/proposals` routes
write nothing either: phase 1 of the round poll
(docs/superpowers/plans/2026-08-13-round-poll-phase-1.md) shipped the review
queue read-only and Task 4 of phase 2's plan shipped the per-concert draft
page the same way, every field pre-filled and every control inert.

`/apply` (Task 5) is where that stops, and it is the ONE route in this feature
that puts a model's reading into the catalogue. Three rules hold it together,
each silent when broken:

* IT CREATES THE ROUND THROUGH THE EDITOR'S OWN SEAM -- `build_round` /
  `apply_round_fields` / `parse_round_legs` from routes/concerts.py, handed
  FORM values exactly as the concert editor hands them, never a hand-built
  `Round`. A second constructor is a second place the JST parse, the
  at-least-one-bound check and the empty-means-all convention can drift.
* IT ENDS IN `sync_concert` (invariant 2). `reminder_queue` is a MATERIALIZED
  outbox: a `Round` written without the sync leaves it untouched, so the row
  exists, the page says applied, the concert leaves the quiet-ladder worklist
  -- and nobody is ever reminded of that deadline. That is this feature's own
  failure mode, reintroduced by its fix, and it looks exactly like success.
* IT RE-DERIVES CHANGED-NESS ITSELF. Phase 2's write path is creates-only
  (owner ruling, 2026-08-14) and the template already hides Approve on a
  CHANGED row -- but a hidden button is not an authorisation check, so the
  route asks `classify_stored_proposal` again against the concert's LIVE
  rounds when the POST arrives. Never a stored flag: there isn't one, by
  design (see that function on why the status is derived every render).

Copy is English-only and NOT wrapped in _(), like /admin/deliveries and
/admin/discoveries: an operational page only admins see should not cost msgids
in three languages. Only the Preferences link to the worklist is translated;
neither `/proposals` nor `/proposals/{event_id}` has a nav link at all -- the
poll's digest DM, and the review queue linking to each concert's own draft
page, are the only ways in.
"""

from datetime import UTC, datetime

import yaml
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Concert, ConcertDay, RoundProposal, User
from app.db.service import (
    classify_stored_proposal,
    held_rounds_by_key,
    mark_proposal_applied,
    mark_proposal_dismissed,
    pending_proposal_groups,
    pending_proposals_for,
    quiet_entry_from_row,
    quiet_ladder_rows,
    record_ladder_checked,
    sync_concert,
)
from app.db.session import get_session
from app.domain.quiet_ladder_message import build_quiet_ladder_block
from app.domain.urls import UnsafeURLError, clean_url
from app.web.auth import SessionUser, require_admin
from app.web.routes.concerts import (
    build_round,
    get_concert_by_event_id,
    parse_round_legs,
)

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

    Defence in depth, and deliberately kept even though today's `source_url` is
    NOT model-authored: `round_poll.py` stores the concert's own
    `official_url`, which is editor-supplied and already went through
    `form_url` at the route boundary. What makes the cleaning worth its two
    lines is where the column is heading -- the completion prompt already asks
    the model for a per-round `url:`, phase 1 simply drops it, and the day a
    proposal starts carrying the model's own URL this href becomes exactly the
    invariant-7 case it is written for. `clean_url` raises on anything that is
    not a real http(s) URL, and a bad one drops the link rather than putting a
    `javascript:` href on an admin's screen.
    """
    if not raw:
        return None
    try:
        return clean_url(raw)
    except UnsafeURLError:
        return None


def _waited(first_seen: datetime, now: datetime) -> str:
    """How long the oldest proposal in a group has been waiting, in days.

    `first_seen_at` is what `pending_proposals` ORDERS BY -- "the thing nobody
    has looked at for a week is the thing to look at" -- so without it on the
    page the operator cannot see the quantity the ordering in front of them is
    built on, and a queue of five looks the same whether it arrived this
    morning or a month ago.

    Days, and a plain phrase rather than a timestamp, because that is the
    question ("has this been ignored?"); the exact sighting renders beside it
    through `dual_lines` like every other time on the site (invariant 1).
    Clamped at zero: a clock skew must not print "waiting -1 days".
    """
    days = max((now - first_seen).days, 0)
    if days == 0:
        return "less than a day"
    return f"{days} day" if days == 1 else f"{days} days"


def _proposal_row(proposal: RoundProposal) -> dict:
    return {
        "proposal": proposal,
        "evidence": _evidence_lines(proposal.evidence_yaml),
        "source_url": _safe_source_url(proposal.source_url),
    }


# The four timestamp anchors, in the SAME order `_editor_round_card.html`
# lays its own out (opens, closes, results, payment). `name` is what a future
# Approve submit would read -- matching `_editor_round_card.html`'s own
# `round_opens_at`/`round_closes_at`/`round_results_at`/`round_payment_at`
# field names, so Task 5's POST route can parse this page's form the same way
# `parse_round_legs`'s siblings already parse the concert editor's. `label`
# doubles as the key into `_EVIDENCE_FIELD_LABELS`, so a field's own quote is
# one dict lookup away.
_TIME_FIELDS = (
    {"attr": "opens_at_utc", "label": "Opens", "name": "round_opens_at"},
    {"attr": "closes_at_utc", "label": "Closes", "name": "round_closes_at"},
    {"attr": "results_at_utc", "label": "Results", "name": "round_results_at"},
    {"attr": "payment_deadline_at_utc", "label": "Payment deadline", "name": "round_payment_at"},
)


def _leg_selection(proposal: RoundProposal, legs: list[ConcertDay]) -> tuple[set[int], list[str]]:
    """Which of `legs` (this concert's own live `ConcertDay` rows) this
    proposal's `applies_to_labels` selects, and which of those labels matched
    no leg at all.

    Matched by EXACT stripped label -- the same comparison `verify_rounds`
    made before the proposal was ever stored (`known_legs = {label.strip()
    for label in leg_labels}`), against `d.label`, the same raw ja column
    `yaml_export.py`'s `performances[].label` puts in front of the model in
    the first place. Legs may have changed since the model read the page (a
    leg renamed, added, or removed), so a label that matched at poll time can
    legitimately match nothing here.

    Empty means ALL -- `Round.applies_to`'s own convention -- so a proposal
    naming no leg renders with every box ticked rather than none, which would
    read as "applies to nothing". A NON-empty list that matches NO leg (every
    label stale) falls back to that same all-ticked reading for the identical
    reason, but the stale labels are still returned as unmatched rather than
    silently dropped: the operator needs to see the model named a leg this
    concert does not have, in EITHER case, not just the partial-match one.
    """
    by_label = {d.label.strip(): d.id for d in legs}
    unmatched: list[str] = []
    matched: set[int] = set()
    for raw in proposal.applies_to_labels:
        label = str(raw).strip()
        day_id = by_label.get(label)
        if day_id is None:
            unmatched.append(label)
        else:
            matched.add(day_id)
    ticked = matched if matched else {d.id for d in legs}
    return ticked, unmatched


def _live_legs(concert: Concert) -> list[ConcertDay]:
    """The legs a round may newly be assigned to, oldest first.

    The same exclusion `edit_concert_form` applies to its own leg chips: a
    cancelled leg is nothing useful to newly assign a round to, and
    `Concert.days`' relationship ordering (`order_by="ConcertDay.
    starts_at_utc"`) already gives the rest the order an operator reads in.

    ONE function, shared by the page that RENDERS the checkboxes and the route
    that PARSES them back, because the apply route's every-box-ticked ->
    empty normalisation is a statement about the boxes the page actually drew.
    Two copies of "which legs count" that disagreed would make "all of them"
    mean two different things on the two sides of one submit.
    """
    return [d for d in concert.days if not d.cancelled]


def _draft_row(
    proposal: RoundProposal, held_by_key: dict, legs: list[ConcertDay]
) -> dict | None:
    """One proposal's whole render context, or None for a `"resolved"` one.

    `"resolved"` is the third outcome `classify_stored_proposal` can return
    but this page never shows: a proposal whose round the operator already
    fixed by hand now matches the concert's held round exactly, so it has
    nothing left to review -- the same "resolves itself" property the
    module's docstring names. Filtering it here, rather than asking the
    template to branch on a third status, keeps the template's `{% if %}` a
    plain NEW/CHANGED choice.
    """
    status = classify_stored_proposal(proposal, held_by_key)
    if status == "resolved":
        return None
    ticked_legs, unmatched_legs = _leg_selection(proposal, legs)
    return {
        "proposal": proposal,
        "status": status,
        "held": held_by_key.get(proposal.dedupe_key),
        "evidence": dict(_evidence_lines(proposal.evidence_yaml)),
        "ticked_legs": ticked_legs,
        "unmatched_legs": unmatched_legs,
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
    now = datetime.now(UTC)
    view_groups = []
    for group in groups:
        # MIN, not `proposals[0]`: the group's order is inherited from
        # `pending_proposals` today, but "the oldest one" is the fact the page
        # states, and reading it off a position would become a lie the moment
        # anything re-sorted a group.
        oldest = min(p.first_seen_at for p in group.proposals)
        view_groups.append({
            "event_id": group.event_id,
            "title": group.title,
            "rows": [_proposal_row(p) for p in group.proposals],
            "count": len(group.proposals),
            "first_seen": oldest,
            "waited": _waited(oldest, now),
        })
    return templates.TemplateResponse(
        request,
        "admin_round_proposals.html",
        {"user": user, "tz": tz, "groups": view_groups},
    )


@router.get("/admin/quiet-ladders/proposals/{event_id}", response_class=HTMLResponse)
async def round_proposal_draft(
    event_id: str,
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """One concert's pending round proposals, each as a real form pre-filled
    with the model's own values -- Task 4 of the round-poll phase-2 plan.

    Reads only; the two POSTs below are what its Approve and Dismiss buttons
    submit to. A CHANGED row renders no Approve at all (creates-only), and
    `apply_round_proposal` re-derives that same verdict rather than trusting
    the absence of the button.

    Invariant 6: keyed by `event_id`, resolved through the same
    `get_concert_by_event_id` every other concert-scoped route uses (404 on a
    bad handle). `session.refresh` loads `days` and `rounds` explicitly --
    both are lazy relationships, and touching either through a bare
    `concert.days`/`concert.rounds` without this would raise `MissingGreenlet`
    the moment this route (or its template) reads one.
    """
    concert = await get_concert_by_event_id(session, event_id)
    await session.refresh(concert, ["days", "rounds"])
    proposals = await pending_proposals_for(session, concert.id)
    db_user = await session.get(User, user.id)
    tz = db_user.timezone if db_user else settings.default_timezone

    legs = _live_legs(concert)
    held_by_key = held_rounds_by_key(concert.rounds)

    rows = [
        row for row in (_draft_row(p, held_by_key, legs) for p in proposals)
        if row is not None
    ]

    return templates.TemplateResponse(
        request,
        "admin_round_proposal_draft.html",
        {
            "user": user, "tz": tz, "concert": concert, "legs": legs,
            "rows": rows, "time_fields": _TIME_FIELDS,
        },
    )


async def _pending_proposal(
    session: AsyncSession, concert: Concert, proposal_id: int
) -> RoundProposal:
    """This concert's proposal, still awaiting a human -- or an HTTP error.

    404 for "not this concert's": invariant 6 puts the `event_id` in the URL
    and the proposal id beside it, and without this check the two are
    independent -- a proposal id from ANOTHER concert would apply its round
    onto whichever concert the path named.

    409 for "already handled", which is what makes a double-click, a stale tab
    or the back button safe. `applied_at` is stamped inside the same
    transaction as the round it produced, so a second press finds the stamp and
    stops here rather than putting a second copy of the round on a concert
    people already hold reminders for. (`classify_stored_proposal` would ALSO
    refuse that second press -- the round it just created now matches the
    proposal exactly, which is the `"resolved"` verdict -- but that is a
    happy accident of this one shape, not a guard: it does not cover a
    DISMISSED row, and it would not cover an apply whose round was later
    edited. The stamp is the actual check.)
    """
    proposal = await session.get(RoundProposal, proposal_id)
    if proposal is None or proposal.concert_id != concert.id:
        raise HTTPException(status_code=404, detail="no such proposal on this concert")
    if proposal.applied_at is not None or proposal.dismissed_at is not None:
        raise HTTPException(status_code=409, detail="this proposal has already been handled")
    return proposal


@router.post("/admin/quiet-ladders/proposals/{event_id}/{proposal_id}/apply")
async def apply_round_proposal(
    event_id: str,
    proposal_id: int,
    round_opens_at: str = Form(""),
    round_closes_at: str = Form(""),
    round_results_at: str = Form(""),
    round_payment_at: str = Form(""),
    applies_to_days: list[str] = Form(default=[]),
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Turn one proposal into a real `Round` -- the feature's only write into
    the catalogue, and the riskiest route in it.

    THE FORM WINS, NOT THE ROW. Every timestamp comes from the four
    `round_*` fields the draft page renders as inputs, never from
    `proposal.*_at_utc`. That is the entire reason those fields are editable:
    the model misreads a 当落発表 date, an admin corrects it in the box, and a
    route that read the stored proposal instead would silently discard the
    correction while showing every sign of having taken it.

    THE ROUND IS BUILT BY THE EDITOR'S OWN `build_round`, handed strings, so
    the JST->UTC parse, the at-least-one-bound 422 and the empty-means-all
    `applies_to` convention are the concert editor's and cannot drift from it.
    Its docstring names three callers; this is the fourth. `label` and `kind`
    are the proposal's own -- the page offers no input for either, since a
    round whose label an operator wants to rewrite is a round to create in the
    editor.

    THE LEGS COME BACK THROUGH `parse_round_legs`, the editor's parser, with
    the checkboxes joined into the space-separated value it already reads
    (the page renders one box per leg rather than the editor's single hidden
    chip field, and joining is cheaper than a second parser that could
    disagree about what a leg id is). `valid_day_ids` is the LIVE legs only --
    the exact set `_live_legs` drew boxes for -- so a cancelled leg's id typed
    into the request is dropped rather than assigned.

    EVERY BOX TICKED NORMALISES BACK TO EMPTY. Empty means ALL, and the two
    readings agree today and disagree the moment a leg is added: a third leg
    falls outside a frozen `[day1, day2]` array, so the round silently stops
    applying to it. Storing what the operator MEANT ("all of them") rather
    than the ids that happened to exist at 3pm is the whole point.

    CHANGED IS REFUSED HERE. See the module docstring; the template hiding the
    button is presentation, this is the check.

    `sync_concert` LAST, after the round has an id (invariant 2). Nothing
    below it may fail: the whole thing is one transaction, committed once.
    """
    concert = await get_concert_by_event_id(session, event_id)
    await session.refresh(concert, ["days", "rounds"])
    proposal = await _pending_proposal(session, concert, proposal_id)

    if classify_stored_proposal(proposal, held_rounds_by_key(concert.rounds)) != "new":
        raise HTTPException(
            status_code=409,
            detail="this concert already holds that round -- edit it on the concert page",
        )

    legs = _live_legs(concert)
    live_ids = {d.id for d in legs}
    applies_to = parse_round_legs(" ".join(applies_to_days), live_ids)
    if applies_to is not None and set(applies_to) == live_ids:
        applies_to = None

    round_ = build_round(
        concert.id,
        proposal.label,
        proposal.kind,
        round_opens_at,
        round_closes_at,
        round_results_at,
        round_payment_at,
        # The page the poll read, as the round's "apply here" link. Cleaned
        # through `_safe_source_url` rather than handed straight to
        # `form_url`: today's value is the concert's own editor-supplied
        # `official_url`, but the column is heading for the model's own `url:`
        # (see that helper), and a bad one should cost this round its link,
        # not answer 422 to an admin who typed nothing wrong.
        _safe_source_url(proposal.source_url) or "",
        applies_to=applies_to,
    )
    session.add(round_)
    await session.flush()  # the round needs an id before anything plans against it

    await mark_proposal_applied(session, proposal.id, datetime.now(UTC))
    await sync_concert(session, concert.id)
    await session.commit()
    return RedirectResponse(
        f"/admin/quiet-ladders/proposals/{event_id}", status_code=303
    )


@router.post("/admin/quiet-ladders/proposals/{event_id}/{proposal_id}/dismiss")
async def dismiss_round_proposal(
    event_id: str,
    proposal_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """"No, and never again" -- the stamp, and nothing else.

    No `Round`, so no `sync_concert`: there is nothing to schedule, and a
    dismiss that fell through to the apply path would push the model's
    unreviewed reading into the catalogue, which is the exact opposite of what
    was pressed.

    Deliberately does NOT ask `classify_stored_proposal`. Dismiss is the only
    action a CHANGED row has -- the creates-only rule refuses its APPLY, not
    its refusal -- and a dismissal it rejected would leave those proposals
    pending forever, re-proposed by the daily poll every single day.
    """
    concert = await get_concert_by_event_id(session, event_id)
    proposal = await _pending_proposal(session, concert, proposal_id)
    await mark_proposal_dismissed(session, proposal.id, datetime.now(UTC))
    await session.commit()
    return RedirectResponse(
        f"/admin/quiet-ladders/proposals/{event_id}", status_code=303
    )
