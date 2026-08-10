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

ROUNDS ARE GROUNDED IN CODE, always. `verify_rounds`
(`domain/round_evidence.py`) runs on EVERY round of EVERY generated draft, and
keeps only the ones whose verbatim quote it can find in the same page text the
model was shown. The failure it prevents is unchanged and is still this app's
worst -- an invented `apply_closes_jst` reaching a real user as a real reminder
for a deadline that never existed -- but the way it prevents it changed on
2026-08-10, by owner ruling: from `strip_rounds` deleting every round to
`verify_rounds` refusing the unquotable ones. The prompt asks; the prompt is
not the guarantee, this is.

WHY THAT CHANGED, measured. `strip_rounds` rested on the claim that Eventernote
pages carry no ticket data. They routinely carry the whole ladder in their
free-text description: in a live run over 13 real productions the model read 7
real rounds, every one verifiable on its own page, and `strip_rounds` deleted
all of them. The rule was right when it shipped, because phase 1 had no way to
tell a read deadline from an invented one; `round_evidence.py` is now that way,
and it is measured too -- in the same run it accepted 39 rounds across three
models with zero invented timestamps. And Eventernote is sometimes the ONLY
surviving source: an official page routinely drops a round once it closes, so a
deadline phase 1 declines to read is one phase 2 can never recover.

THE MODEL READS TEXT, NOT HTML, for one reason: `verify_rounds` searches the
text the model was given, so the two must be the same string. `html_to_text`
produces it once (as it already does for phase 2) under the one `PAGE_TEXT_CAP`,
and the leg facts survive that conversion -- the real 2026-08-10 sample page
went 28,296 characters of HTML to 5,141 of text keeping its date, doors/start,
venue, cast, related links and its 受付期間 block intact.

NOTHING IS DROPPED SILENTLY. Every rejection is written to the new draft's
`PendingDraft.completion_yaml`, the record phase 2 already writes and the
preview already renders, because a real deadline quietly discarded is exactly
as harmful as a fake one quietly kept -- in both cases the operator has no way
to know to look. That record is marked `pass: triage`, which is what stops it
consuming phase 2's own attempt: phase 1 read Eventernote, phase 2 reads the
OFFICIAL page, and a draft this pass could not ground is exactly one that still
wants the other page read (`db/drafts.py:completion_candidates`).

THE BUDGET. One classify call per TRIAGE_CLASSIFY_BATCH leads, then at most
TRIAGE_DRAFT_CAP fetch+draft pairs -- so a press costs at most
ceil(queue/60) + 25 LLM calls and 25 third-party fetches. The draft cap is
what makes the DRAFT half's cost predictable; survivors past it wait for the
next press, which will not re-propose the ones already drafted (see the
containment step). The classify half scales with the queue, as it must -- every
lead has to be read once -- but the batch size bounds each individual CALL,
which is the thing that broke on 2026-08-09 when it was unbounded: the reply
overran the output cap, and given room it instead lost track of "each lead
exactly once" and was rejected whole. The measurements are on the constant.
Fetches are SEQUENTIAL with a pause, for the reason `discovery.py` gives: 25
parallel requests at a third party is how an IP gets blocked. Each fetch
carries a TOTAL deadline, because httpx's own timeout is per READ and a server
dripping bytes would otherwise hold the tick open with no bound at all. Over the
whole draft loop sits TRIAGE_BUDGET_SECONDS, the sweep's wall clock: the cap
bounds the CALLS, and only a clock bounds the TIME.

Two operational rules inherited wholesale from the sweep:

  - It BEATS THE HEARTBEAT per classify batch and per drafted production.
    The scheduler beats before `tick()` and /healthz goes unhealthy at 180s; a
    run that fetches 25 pages with a pause each occupies the tick well past
    that, so without a beat per production it pages the owner about a perfectly
    healthy app. The classify beats are the same rule one step earlier:
    delivery plus a classify pass long enough to need slicing (nine calls over
    a 511-lead queue took 60s) ages the tick's own beat past 180s before the
    draft loop starts. The run genuinely is alive, so beating in both loops is
    honest.
  - It FLUSHES, never commits. The scheduler's block owns the transaction and
    its own rollback -- and the run row's failure marking happens THERE, on a
    cleaned transaction, because a rollback would otherwise restore the row to
    "requested" and a dead run would re-fire 26 LLM calls every 60 seconds
    forever. That is `stamp_discovery_run`'s two-halves pattern in row form.

Every per-production failure -- fetch, LLM, parse -- is caught, counted and
stepped over, and so is every per-BATCH classify failure -- which is something
slicing MADE possible: one bad batch is a partial loss where one bad call over
the whole queue was a total one. Only a press where EVERY classify batch failed
still propagates, because only then is there nothing to salvage. One bad
production must not cost the other twenty-four, which is
`run_sweep`'s philosophy and for the same reason: the alternative is a run that
dies partway and hands back nothing at all. ONE class of failure is exempt: a
`SQLAlchemyError` poisons the session, so nothing after it can persist and
stepping over it would spend twenty-four more paid calls on writes that cannot
land. That one propagates and the run is marked failed.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

import httpx
from sqlalchemy.exc import SQLAlchemyError
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
from app.domain.page_text import html_to_text
from app.domain.prune_list import parse_prune_list
from app.domain.round_completion import (
    TRIAGE_PASS,
    completion_record,
    draft_leg_labels,
    merge_rounds,
    parse_completion_response,
)
from app.domain.round_evidence import Verdict, verify_rounds
from app.domain.triage_prompts import (
    ClassifyResult,
    LeadLine,
    Survivor,
    TriageResponseError,
    classify_prompt,
    draft_prompt,
    extract_yaml,
    merge_classify_results,
    parse_classify_response,
)
from app.domain.yaml_import import parse_drafts
from app.fetching import PinnedHost, fetch_html
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
# How many leads one classify call reads. TRIAGE_DRAFT_CAP bounds the draft
# half; this is the classify half's equivalent, and it exists because the
# unbounded version failed twice against a real 511-lead queue on 2026-08-09:
#   - at DeepSeek's 8,192 default output cap the reply hit the cap exactly
#     (`finish_reason: length`) and the press returned nothing, billed anyway;
#   - given a raised cap the reply completed at 27,142 output tokens and was
#     then rejected outright, one lead id under two dismiss reasons -- which
#     `parse_prune_list` treats as fatal for the WHOLE list. 494 of the 511
#     leads had been placed more than once. The model cannot hold "each lead
#     exactly once" across a list that long.
# The SAME queue at 60: 9 calls, every one `finish_reason: stop` inside the
# shipped 8,192 cap, largest reply 1,473 output tokens, zero duplicate-reason
# failures, 9,485 output tokens total against 27,142, 60s against 124s.
# Cheaper, faster and correct, so this is not a safety tax. Raising it walks
# back toward the incoherence above, which does not announce itself -- it
# arrives as one unusable batch.
# The ACCEPTED cost: collapsing repeats is a judgment WITHIN a batch, so a tour
# whose legs straddle a boundary comes back as two productions and is drafted
# twice, for an editor to merge. `open_leads` orders by event_date DESC, which
# keeps a tour's legs adjacent and makes that rare -- and two drafts an editor
# merges is a far cheaper failure than one reply the parser rejects whole.
TRIAGE_CLASSIFY_BATCH = 60
# Sequential with a pause, exactly as the sweep does it.
TRIAGE_DELAY_SECONDS = 1.0
# A TOTAL deadline per page: httpx's timeout is per READ, so without this the
# last fetch of a run is unbounded and "the run is bounded" would be a comment
# rather than a fact.
FETCH_DEADLINE_SECONDS = 30.0
# A WALL CLOCK over the draft loop, the same 240s `run_sweep` keeps and for the
# same reason: the run occupies the reminder tick, reminder_loop is strictly
# serial, and the per-production heartbeat means a long run raises no alarm.
# The cap bounds the CALLS; this bounds the TIME, which the cap alone cannot --
# 25 productions each waiting out FETCH_DEADLINE_SECONDS plus an LLM call is
# well past any sane share of a tick. Survivors past it simply wait for the next
# press, which containment stops from re-drafting the ones already done.
TRIAGE_BUDGET_SECONDS = 240.0


def _source_line(representative: str) -> str:
    """The provenance line a generated draft carries, and the containment key.

    ONE expression, used by the prepend and by the check that reads it back --
    they are the same string by construction or containment silently stops
    working. It ends in a newline on purpose: matching the bare URL let
    `/events/4862` contain-match inside a stored `/events/48624` and skip a
    production nobody had drafted.
    """
    return f"# source: {EVENT_URL.format(id=representative)}\n"


def _ground_rounds(
    reply_text: str, page: str, now: datetime
) -> tuple[str, Verdict, list[str]]:
    """One draft reply, with only the rounds it proved left in it.

    The SAME three pure steps `draft_completion.complete_one` runs, over the
    same three functions, so the two passes cannot drift on what counts as a
    grounded round -- parse the reply (which lifts `evidence` out of each round
    and normalizes its timestamps to the draft vocabulary's text), verify every
    round against the page, then rewrite the `rounds:` key with the survivors.
    What could NOT be shared is the surrounding half of `complete_one`: it
    amends a stored `PendingDraft` this pass has not created yet, and it merges
    into a document a human may already have proofread, where this merges into
    the model's own fresh reply.

    `merge_rounds` is the rewrite for both, and here it also does what
    `strip_rounds` used to: a reply whose body is not a mapping raises rather
    than being coerced to `{}` -- there was never a draft in it to save.

    `now` supplies `verify_rounds`' plausibility date. The run's own clock, not
    a fresh `datetime.now()`: this module already takes `now` from the caller,
    and the window it feeds is +/- years wide, so nothing turns on which.
    """
    proposed, warnings = parse_completion_response(reply_text)
    draft_yaml = extract_yaml(reply_text)
    verdict = verify_rounds(proposed, page, draft_leg_labels(draft_yaml), now.date())
    text = merge_rounds(draft_yaml, [r.data for r in verdict.accepted])
    return text, verdict, warnings


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
    # Rounds this pass read off an Eventernote page and GROUNDED, and the ones
    # `verify_rounds` refused. Counted apart from `skipped` (a production that
    # produced no draft at all): a rejected round costs its draft nothing, and
    # both numbers reach the admin notice because a run that reads twenty
    # rounds and grounds none is a calibration problem the counts announce and
    # nothing else would.
    rounds_added: int = 0
    rounds_rejected: int = 0
    skipped: int = 0
    calendar_skipped: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    # Classify batches whose call or whose reply could not be used. Counted
    # APART from `skipped`, which means "a production that did not produce a
    # draft": a lost batch is a set of leads that were never classified at
    # all, so folding the two together would report a partial classify as a
    # handful of failed drafts. Reported to the admins for the same reason
    # `budget_exhausted` is: the queue otherwise looks triaged and the leads in
    # the lost batch are simply never mentioned again.
    classify_batches_failed: int = 0
    # True when TRIAGE_BUDGET_SECONDS ran out and survivors were left for the
    # next press. Recorded rather than merely logged, exactly as SweepReport
    # records its own: a truncation only the journal knows about is a silent
    # degradation.
    budget_exhausted: bool = False


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
        policy=PinnedHost(ALLOWED_HOST),
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
    # Shared with the completion run, and they mean the same thing on both:
    # rounds that reached a draft, and rounds `verify_rounds` refused. They
    # were phase-2-only while phase 1 emitted no rounds at all; now that it
    # grounds them, leaving these NULL would report "never got there" about a
    # number this run genuinely measured.
    run.rounds_added = report.rounds_added
    run.rounds_rejected = report.rounds_rejected
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
    lines = [
        f"AI triage finished: {report.dismissals} dismissal(s) proposed, "
        f"{report.drafts} draft(s) queued, {report.skipped} skipped "
        f"({report.leads_seen} open lead(s) read).",
        # Named on every run, zeros included: rounds are what phase 1 started
        # keeping on 2026-08-10, and "0 grounded, 9 rejected" is the shape a
        # miscalibrated prompt takes. The per-round reasons are on each
        # draft's own preview; this is the number that says go and look.
        f"{report.rounds_added} round(s) grounded on the page, "
        f"{report.rounds_rejected} rejected as unquotable.",
    ]
    if report.classify_batches_failed:
        # Named rather than swallowed: a lost batch's leads were never
        # classified, so they appear nowhere in the counts above and the
        # queue reads as fully triaged when it is not.
        lines.append(
            f"{report.classify_batches_failed} classify batch(es) came back "
            f"unusable -- those leads went unread and stay in the queue."
        )
    lines.append("Review: https://dekimasen.app/admin/discoveries")
    body = "\n".join(lines)
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

    A classify batch that comes back unusable is caught, counted
    (`classify_batches_failed`) and stepped over -- the draft loop's "one bad
    production must not cost the other twenty-four", one step earlier, and it
    only became available once the classify pass was sliced: unbatched, the
    one call that decides what everything else does had no partial to salvage.
    It still has none when EVERY batch fails, and that case propagates as
    before, for the scheduler's handler to mark failed on a cleaned
    transaction.

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

    # 2. Classify, one call per BATCH of TRIAGE_CLASSIFY_BATCH leads. Collapsing
    #    repeats into productions is a judgment about a batch AS A WHOLE and
    #    cannot be made a lead at a time -- but it does not need the whole
    #    QUEUE either, and asking for the whole queue is what broke twice on
    #    2026-08-09 (see the constant). A batch is still a batch; only its size
    #    is now chosen here rather than by however long the sweep's queue got.
    #    Beaten per batch, for the reason the draft loop beats per production:
    #    the tick beats once before delivery, and delivery plus a classify pass
    #    long enough to need slicing outlasts MAX_AGE_SECONDS (180s) easily --
    #    so without a beat here /healthz pages the owner about a perfectly
    #    healthy app.
    batches = [
        lines[i : i + TRIAGE_CLASSIFY_BATCH]
        for i in range(0, len(lines), TRIAGE_CLASSIFY_BATCH)
    ]
    per_batch: list[ClassifyResult] = []
    last_classify_error: Exception | None = None
    for number, batch in enumerate(batches, start=1):
        heartbeat.beat()
        try:
            reply = await llm_chat(*classify_prompt(batch))
        except Exception as exc:
            # The call itself failed -- a transport error, a non-200, or the
            # truncated reply that started all this. ONE bad batch is a
            # partial loss, not a total one, which is the draft loop's
            # philosophy applied one step earlier: a batch that came back
            # unusable must not cost the other eight. No SQLAlchemyError
            # carve-out here, unlike the draft loop: nothing in this loop
            # touches the session, so there is no poisoned session to protect.
            last_classify_error = exc
            report.classify_batches_failed += 1
            log.exception(
                "triage: classify batch %d/%d failed; its leads go unclassified",
                number, len(batches),
            )
            continue
        # Accounted before the parse, as in the draft loop: the tokens were
        # billed whether or not the reply turns out to be usable.
        report.tokens_in += reply.tokens_in
        report.tokens_out += reply.tokens_out
        try:
            result = parse_classify_response(reply.text)
        except TriageResponseError as exc:
            # Diagnosing the 2026-08-05 production incident (a reply that
            # YAML-loaded to something other than a mapping) took a hand-rolled
            # server probe because nothing logged what the model actually said.
            # Head+tail keeps the log line bounded on a long reply while still
            # showing the shape of the failure at both ends.
            last_classify_error = exc
            report.classify_batches_failed += 1
            log.error(
                "triage classify reply unusable (batch %d/%d): %s -- "
                "reply head: %r -- tail: %r",
                number, len(batches), exc, reply.text[:500], reply.text[-200:],
            )
            continue
        per_batch.append(result)

    if not per_batch:
        # EVERY batch failed. The original reasoning survives exactly at its
        # limit: there is no partial run to salvage when nothing came back
        # usable, so this propagates (as the batch's own error, whatever kind
        # it was) and the scheduler's handler marks the row failed on a
        # cleaned transaction. One survivor is enough to keep going -- the
        # press then reports what it did read and says what it lost.
        # Never None here: `rows` is non-empty by the early return above, so
        # there is at least one batch, and an empty `per_batch` means every
        # one of them recorded its error.
        raise last_classify_error

    result = merge_classify_results(per_batch)
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
    #    have not made once. Matched as that WHOLE LINE (`_source_line`), never
    #    the bare URL: `/events/4862` is a substring of `/events/48624`.
    taken = await pending_draft_texts(session)
    candidates: list[Survivor] = []
    for survivor in result.survivors:
        if survivor.representative is not None:
            marker = _source_line(survivor.representative)
            if any(marker in text for text in taken):
                report.skipped += 1
                continue
        candidates.append(survivor)

    # 4. Draft, in the order the model delivered them -- which follows
    #    `open_leads`' own event_date DESC, so the FURTHEST-FUTURE production is
    #    drafted first and a cap or a budget leaves the soonest ones behind.
    #    That is the calibration consequence docs/deploy.md spells out for the
    #    operator; it is not accidental and the fix is not here.
    deadline = monotonic() + TRIAGE_BUDGET_SECONDS
    attempts = 0
    for index, survivor in enumerate(candidates):
        # Checked at the TOP, before anything is fetched: the budget caps how
        # long the reminder tick is held, so the answer has to be "stop" before
        # the next page is asked for, not after.
        if monotonic() >= deadline:
            report.budget_exhausted = True
            log.warning(
                "triage: %.0fs budget spent after %d production(s); "
                "%d left for the next press",
                TRIAGE_BUDGET_SECONDS, index, len(candidates) - index,
            )
            break
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
                html = await fetcher(url)
            # ONE text, produced once and used twice -- the prompt below and
            # the verifier in `_ground_rounds`. Two normalizations would let a
            # real quote read as "not on the page" for a transformation the
            # model never saw, which is `page_text.py`'s whole reason to exist.
            page = html_to_text(html)
            reply = await llm_chat(*draft_prompt(survivor, lines, page))
            # Accounted before anything can fail below it: the tokens were
            # billed whether or not the reply turns out to be usable.
            report.tokens_in += reply.tokens_in
            report.tokens_out += reply.tokens_out
            # Whatever the model returned, only the rounds it can PROVE
            # survive -- the one rule this module's docstring puts in capitals.
            text, verdict, warnings = _ground_rounds(reply.text, page, now)
            for warning in warnings + list(verdict.rejected):
                log.warning("triage: draft for %s: %s", survivor.title, warning)
            # The provenance line, and the containment key: the next run reads
            # it back out of `pending_draft_texts` as a substring, through the
            # same `_source_line` that writes it here.
            text = _source_line(survivor.representative) + text
            batch = parse_drafts(text)
            if len(batch.drafts) != 1 or batch.errors:
                raise ValueError(
                    f"expected exactly one parseable draft, got "
                    f"{len(batch.drafts)} and errors {batch.errors}"
                )
            rows = await create_pending_drafts(session, batch, created_by=run.requested_by)
            # Exactly one row, by the check above. The grounding record goes on
            # it rather than into it: a draft is a document that gets committed
            # into `concerts`, and this is proofreading scaffolding an operator
            # reads on the preview -- including every rejection, since a real
            # deadline quietly discarded is as harmful as a fake one kept.
            # A production that fails BEFORE this line leaves no row to carry
            # its reasons and simply gets re-attempted next press; that is the
            # pre-existing shape of a skip here (phase 1 mints the row, so
            # there is nothing yet to mark), not something this change added.
            rows[0].completion_yaml = completion_record(
                source_url=url,
                source_pass=TRIAGE_PASS,
                evidence=[r.evidence for r in verdict.accepted],
                rejected=list(verdict.rejected) + list(warnings),
            )
            await session.flush()
            report.drafts += 1
            report.rounds_added += len(verdict.accepted)
            report.rounds_rejected += len(verdict.rejected)
        except SQLAlchemyError:
            # NOT one skipped production. A failed flush POISONS the session, so
            # nothing after this point can persist -- absorbing it would pay up
            # to 24 more fetch+LLM calls to write nothing at all, and then close
            # the run out as "done". Re-raised BEFORE the generic handler below,
            # which is the only thing keeping it out of the skip count; the
            # scheduler's block rolls back and marks the row failed on a cleaned
            # transaction, the path an unusable classify response already takes.
            log.exception("triage: the session is poisoned; abandoning the run")
            raise
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
