"""AI triage: one LLM pass over the open discovery queue, on request.

Sits ABOVE db/ exactly like `discovery.py` and `ops.py`: it imports `domain/`,
`app.llm`, `app.fetching` and `db.service`, and nothing in `db/` imports it.
The prompts and every piece of response handling live in
`domain/triage_prompts.py` (pure); the provider plumbing lives in `app/llm.py`.
This module is only the RUN ORDER -- what is asked, in what sequence, and what
a failure at each step costs.

WHAT IT IS. `.claude/skills/triage-leads/SKILL.md` describes an agent reading a
paste of discovery leads by hand: collapse repeats into productions, classify
keep-vs-dismiss, then draft the keepers in the add-concert vocabulary. This
runs the same three passes with a model instead of an agent, on the button at
/admin/discoveries. It is a REQUEST-driven job, not a daily one -- a `TriageRun`
row is both the request and the record -- because it costs real money per press
and because its output is a proposal a human still reads.

WHAT IT NEVER DOES. It creates no concert and dismisses no lead. The classify
half is stored as `run.prune_yaml`, which is the SAME text a human pastes into
the existing prune-plan flow, so the plan/apply screen stays the only path to a
permanent dismissal. The draft half writes `PendingDraft` rows, which is the
same queue a human's multi-draft paste writes, so `import_commit` stays the
only write path into `concerts` (invariant 6's neighbourhood). Both halves
therefore cross a parser boundary this app already trusts, rather than earning
a new one because the text came from a model.

ROUNDS ARE STRIPPED IN CODE, always. `strip_rounds` runs on EVERY generated
draft regardless of what the model returned, because the failure it prevents is
this app's worst: an invented `apply_closes_jst` reaches a real user as a real
reminder for a deadline that never existed. The prompt asks for `rounds: []`;
the prompt is not the guarantee, this is.

THE BUDGET. One classify call over the whole queue, then at most
TRIAGE_DRAFT_CAP fetch+draft pairs -- so a press costs at most 1 + 25 LLM calls
and 25 third-party fetches, whatever the queue's size. The cap is what makes
the cost of a press predictable; survivors past it wait for the next press,
which will not re-propose the ones already drafted (see the containment step).
Fetches are SEQUENTIAL with a pause, for the reason `discovery.py` gives: 25
parallel requests at a third party is how an IP gets blocked. Each fetch
carries a TOTAL deadline, because httpx's own timeout is per READ and a server
dripping bytes would otherwise hold the tick open with no bound at all.

Two operational rules inherited wholesale from the sweep:

  - It BEATS THE HEARTBEAT inside its own loop. The scheduler beats before
    `tick()` and /healthz goes unhealthy at 180s; a run that fetches 25 pages
    with a pause each occupies the tick well past that, so without a beat per
    production it pages the owner about a perfectly healthy app. The loop
    genuinely is alive, so beating in it is honest.
  - It FLUSHES, never commits. The scheduler's block owns the transaction and
    its own rollback -- and the run row's failure marking happens THERE, on a
    cleaned transaction, because a rollback would otherwise restore the row to
    "requested" and a dead run would re-fire 26 LLM calls every 60 seconds
    forever. That is `stamp_discovery_run`'s two-halves pattern in row form.

Every per-production failure -- fetch, LLM, parse -- is caught, counted and
stepped over. One bad production must not cost the other twenty-four, which is
`run_sweep`'s philosophy and for the same reason: the alternative is a run that
dies partway and hands back nothing at all.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.config import settings
from app.db.models import Notification, TriageRun, User
from app.db.service import (
    create_pending_drafts,
    ensure_user,
    open_leads,
    pending_draft_texts,
)
from app.domain.prune_list import parse_prune_list
from app.domain.triage_prompts import (
    LeadLine,
    Survivor,
    classify_prompt,
    draft_prompt,
    extract_yaml,
    parse_classify_response,
    strip_rounds,
)
from app.domain.yaml_import import parse_drafts
from app.fetching import fetch_html
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

ALLOWED_HOST = "www.eventernote.com"
# Spelled out here rather than imported from `app.discovery`, for the reason
# `app/calendars.py` spells its own out: a UA string is not a security control,
# and duplicating it costs less than the import it would otherwise take. (The
# host pin IS a security control, and that one is shared -- `fetching.py`.)
TRIAGE_USER_AGENT = "dekimasen.app/1.0 (event discovery)"
# The page a survivor's representative leg lives on. The generated draft
# embeds this exact URL as its first line, which is what the containment check
# in run_triage matches against.
EVENT_URL = "https://www.eventernote.com/events/{id}"
# At most this many fetch+draft pairs per press. See THE BUDGET above: it is
# what makes the cost of pressing the button predictable rather than a function
# of however long the queue happens to be.
TRIAGE_DRAFT_CAP = 25
# Sequential with a pause, exactly as the sweep does it.
TRIAGE_DELAY_SECONDS = 1.0
# A TOTAL deadline per page: httpx's timeout is per READ, so without this the
# last fetch of a run is unbounded and "the run is bounded" would be a comment
# rather than a fact.
FETCH_DEADLINE_SECONDS = 30.0


@dataclass
class TriageReport:
    """What one run did, for the scheduler's log line and the run row.

    `skipped` and `calendar_skipped` are two different absences and are counted
    apart on purpose: a skip is something that went wrong (a fetch, an LLM
    call, a parse) or was already in hand, while a calendar skip is a survivor
    with no Eventernote page to read at all -- nothing failed, there was simply
    nothing to fetch, and those stay with the agent workflow.
    """

    leads_seen: int = 0
    productions: int = 0
    dismissals: int = 0
    drafts: int = 0
    skipped: int = 0
    calendar_skipped: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


async def fetch_event_page(
    url: str, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Fetch one Eventernote event page. `transport` is test-only.

    Unlike `discovery.fetch_actor_events` this does NOT translate `FetchError`
    into an error of its own: the draft loop's per-production `except Exception`
    already treats a fetch failure, an LLM failure and a parse failure
    identically -- as one skipped production -- so a bespoke exception class
    here would name a distinction nothing acts on.
    """
    return await fetch_html(
        url,
        allowed_host=ALLOWED_HOST,
        user_agent=TRIAGE_USER_AGENT,
        transport=transport,
    )


def _lead_line(row) -> LeadLine:
    """A stored lead adapted to the pure prompt layer's plain dataclass."""
    return LeadLine(
        source_event_id=row.source_event_id,
        title=row.title,
        date_iso=row.event_date.isoformat(),
        venue=row.venue,
        date_is_deadline=row.date_is_deadline,
        source=row.source,
    )


def _finish(run: TriageRun, now: datetime, report: TriageReport) -> None:
    """Copy the report onto the run row and close it out.

    Every count is written, including the zeros of a run that saw nothing: on
    THIS table a written 0 means "looked, found none" and NULL means "never
    got there", which is the distinction the nullable columns exist to keep
    (see TriageRun.leads_seen).
    """
    run.status = "done"
    run.finished_at = now
    run.leads_seen = report.leads_seen
    run.productions = report.productions
    run.dismissals_proposed = report.dismissals
    run.drafts_created = report.drafts
    run.skipped = report.skipped
    run.calendar_skipped = report.calendar_skipped
    run.tokens_in = report.tokens_in
    run.tokens_out = report.tokens_out


async def _announce(session: AsyncSession, report: TriageReport) -> None:
    """Queue ONE admin notice per run. Never sends a DM itself (invariant 4).

    `kind="triage"` with `concert_id=None` falls through
    `scheduler.loop._notification_context` to the plain-text path, exactly as
    `discovery` and `ops_alert` do, so the send code needs no changes. It is
    deliberately NOT in UNREPORTED_NOTE_KINDS -- that set is for notices that
    report ON deliveries, and this one reports on a model's proposals.
    """
    body = (
        f"AI triage finished: {report.dismissals} dismissal(s) proposed, "
        f"{report.drafts} draft(s) queued, {report.skipped} skipped "
        f"({report.leads_seen} open lead(s) read).\n"
        "Review: https://dekimasen.app/admin/discoveries"
    )
    for admin_id in sorted(settings.admin_ids):
        # An admin who has never logged into the web app has no users row, and
        # Notification.user_id is a FK to it. Guarded on absence rather than
        # calling ensure_user unconditionally: that refreshes the username,
        # which would overwrite a real admin's name with this placeholder on
        # every single run.
        if await session.get(User, admin_id) is None:
            await ensure_user(session, admin_id, str(admin_id))
        session.add(Notification(user_id=admin_id, body=body, kind="triage"))


async def run_triage(
    session: AsyncSession,
    run: TriageRun,
    now: datetime,
    *,
    fetcher: Callable[[str], Awaitable[str]] = fetch_event_page,
    llm_chat=llm.chat,
) -> TriageReport:
    """Classify the open leads, then draft the survivors that are worth it.

    `fetcher` and `llm_chat` are injected so tests never touch the network or
    spend a real key -- one seam per external system, the same shape
    `run_sweep` uses for its two fetchers.

    An unusable CLASSIFY response propagates (`TriageResponseError`): there is
    no partial run to salvage when the one call that decides what everything
    else does came back as junk, and the scheduler's handler marks the row
    failed on a cleaned transaction. Every per-production failure below is the
    opposite case and is absorbed.

    The transaction stays the caller's: this flushes, never commits.
    """
    report = TriageReport()
    run.started_at = now

    # 1. What is actually waiting. `open_leads` is the SAME loader
    #    /admin/discoveries renders, so the model reads exactly the queue the
    #    operator is looking at -- a second query here could drift from it.
    rows = await open_leads(session)
    report.leads_seen = len(rows)
    if not rows:
        # Nothing to classify, so nothing to pay for and nobody to tell: a
        # "triage found nothing" DM on an empty queue is the daily-noise
        # failure run_sweep avoids by staying silent on a quiet day.
        _finish(run, now, report)
        await session.flush()
        return report
    lines = [_lead_line(row) for row in rows]

    # 2. Classify: one call over the whole batch, because collapsing repeats
    #    into productions is a judgment about the batch AS A WHOLE and cannot
    #    be made a lead at a time.
    reply = await llm_chat(*classify_prompt(lines))
    report.tokens_in += reply.tokens_in
    report.tokens_out += reply.tokens_out
    result = parse_classify_response(reply.text)
    for warning in result.warnings:
        log.warning("triage: %s", warning)
    run.prune_yaml = result.prune_yaml
    if result.prune_yaml:
        # Counted by re-parsing rather than by summing the model's own lists:
        # the prune list is what the operator will actually apply, so the
        # number shown has to be the number that parser sees.
        report.dismissals = len(parse_prune_list(result.prune_yaml).entries)
    report.productions = len(result.survivors)

    # 3. Containment. A generated draft's first line is `# source: <EVENT_URL>`,
    #    so a survivor whose page URL already occurs in a still-open PendingDraft
    #    is one an editor has not triaged yet -- re-drafting it would spend a
    #    fetch and an LLM call to hand them a second copy of a decision they
    #    have not made once.
    taken = await pending_draft_texts(session)
    candidates: list[Survivor] = []
    for survivor in result.survivors:
        if survivor.representative is not None:
            url = EVENT_URL.format(id=survivor.representative)
            if any(url in text for text in taken):
                report.skipped += 1
                continue
        candidates.append(survivor)

    # 4. Draft, oldest-first as the model delivered them, capped.
    attempts = 0
    for survivor in candidates:
        if attempts >= TRIAGE_DRAFT_CAP:
            log.info(
                "triage: draft cap (%d) reached; %d production(s) left for the next run",
                TRIAGE_DRAFT_CAP, len(candidates) - attempts,
            )
            break
        if survivor.representative is None:
            # Calendar-only: there is no Eventernote page to read, and a draft
            # written from a feed line alone would be a guess. Counted apart
            # from `skipped` because nothing failed -- these stay with the
            # agent workflow, which can search for the official page.
            report.calendar_skipped += 1
            continue

        attempts += 1
        url = EVENT_URL.format(id=survivor.representative)
        # Per production, not per run: see THE BUDGET above.
        heartbeat.beat()
        try:
            await asyncio.sleep(TRIAGE_DELAY_SECONDS)
            async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
                page = await fetcher(url)
            reply = await llm_chat(*draft_prompt(survivor, lines, page))
            # Accounted before anything can fail below it: the tokens were
            # billed whether or not the reply turns out to be usable.
            report.tokens_in += reply.tokens_in
            report.tokens_out += reply.tokens_out
            # UNCONDITIONALLY, whatever the model returned -- the one rule this
            # module's docstring puts in capitals.
            text = strip_rounds(extract_yaml(reply.text))
            # The provenance line, and the containment key: the next run reads
            # it back out of `pending_draft_texts` as a substring.
            text = f"# source: {url}\n{text}"
            batch = parse_drafts(text)
            if len(batch.drafts) != 1 or batch.errors:
                raise ValueError(
                    f"expected exactly one parseable draft, got "
                    f"{len(batch.drafts)} and errors {batch.errors}"
                )
            await create_pending_drafts(session, batch, created_by=run.requested_by)
            report.drafts += 1
        except Exception:
            # Fetch, LLM and parse failures are one thing here: a production
            # that did not produce a draft. One must not cost the rest.
            log.exception("triage: could not draft %s from %s", survivor.title, url)
            report.skipped += 1
            continue

    # 5. Close the run out and tell the admins once.
    _finish(run, now, report)
    await _announce(session, report)
    await session.flush()
    return report
