"""AI draft completion: fill a pending skeleton's rounds from its official page.

Phase 2 of the AI pipeline. Sits ABOVE `db/` exactly like `triage.py` and
`discovery.py`: it imports `domain/`, `app.llm`, `app.fetching` and
`db.service`, and nothing in `db/` imports it. This module is the RUN ORDER
only -- which drafts, in what sequence, and what a failure at each step costs.
The prompt and the merge are pure (`domain/round_completion.py`); the rule that
decides whether a proposed round may exist at all is pure too
(`domain/round_evidence.py`), and that separation is the point: the safety
property is testable without a database, a network or a key.

WHAT PHASE 1 LEFT. A skeleton draft has legs, a cast and `rounds: []`, always,
because a round is the one promise this app makes to a user -- "a deadline it
names is real" -- and phase 1 could only keep that promise by emitting none.
This fills them in, and the promise is kept a different way: EVERY round the
model proposes must quote the page text it read each timestamp from, and every
quote must be findable in the same text the model was given. What the code
decides, not what the prompt asked for. `verify_rounds` is this module's
`strip_rounds`.

NOTHING IS DROPPED SILENTLY. A rejected round is recorded with its reason on
the draft's own `completion_yaml` and rendered on its preview. A real deadline
quietly discarded is exactly as harmful as a fake one quietly kept: in both
cases the operator has no way to know to look. The same rule covers a call
that produced nothing usable at all -- an unparseable reply
(`CompletionResponseError`) or a stored draft `merge_rounds` refuses to touch
(`DraftMergeError`) still writes `completion_yaml` recording why, before
re-raising, so the LLM call that was already paid for is never paid for
twice on the next press.

THE FETCH IS THE WIDENING, AND IT IS PAID FOR. A draft's `official_url` is by
nature an arbitrary host, so this is the first fetch in this app that is not
host-pinned. Three things stand in for the pin: a host is fetched only after an
admin approved it by name (`FetchDomain`), only over https, and only when every
address it resolves to is public. The approval queue is the part a human is in,
and an unapproved host costs a skipped draft rather than a refused run -- the
draft stays a candidate and the next press picks it up. A host `note_fetch_domain`
itself refuses to store (URL-shaped, carrying a port, or blank -- `urlparse`
keeps an IPv6 literal's colons, which slips past this module's own checks) is
one no approval screen could ever unblock either, so that ALSO costs a skipped
draft, never a run-ending exception: this module's one hard rule about model-
and agent-authored free text is that nothing it puts in `official_url` may take
the whole press down with it.

WHAT IT NEVER DOES. It creates no concert: a completed draft is still a
`PendingDraft` whose preview a human presses commit on, so `import_commit`
stays the only write path into `concerts`. It never fetches `eventernote_url`
(Eventernote carries no ticket information at all, so the request could not
contain the answer). It never searches for a page: the URL is in the draft, or
the page is pasted by hand.

THE BUDGET, and it is the same shape as triage's. At most COMPLETION_DRAFT_CAP
fetch+call pairs per press whatever the queue's size, fetches SEQUENTIAL with a
pause (parallel requests at a third party is how an IP gets blocked), a TOTAL
deadline per fetch because httpx's timeout is per READ, and a wall clock over
the whole loop because the cap bounds the CALLS and only a clock bounds the
TIME. `heartbeat.beat()` per draft: the scheduler beats before `tick()` and
/healthz goes unhealthy at 180s, so a run that fetches fifteen pages with a
pause each would otherwise page the owner about a perfectly healthy app. The
loop genuinely is alive, so beating in it is honest. Unlike triage.py, there is
no beat BEFORE the loop: triage needs one because a single classify call over
the whole queue precedes its loop and can itself run long, but nothing here
does any I/O before the first per-draft beat -- reading `completion_candidates`
is one fast DB query, and every check ahead of the first fetch (parsing the
draft's URL, comparing its host against the approved set) is pure. The first
slow thing this run ever does is inside the loop, and the loop beats before it.

It FLUSHES, never commits -- the scheduler's block owns the transaction and its
own rollback, and the run row's failure marking happens THERE, on a cleaned
transaction, or a rollback would restore the row to "requested" and a dead run
would re-fire fifteen fetches and fifteen paid calls every sixty seconds
forever. `SQLAlchemyError` is the one failure this module does not absorb: a
poisoned session cannot persist anything, so stepping over it would spend
fourteen more paid calls writing nothing at all.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlparse

import yaml
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.config import settings
from app.db.models import Notification, PendingDraft, TriageRun, User
from app.db.service import (
    approved_fetch_hosts,
    completion_candidates,
    ensure_user,
    note_fetch_domain,
)
from app.domain.page_text import html_to_text, normalize_page_text
from app.domain.round_completion import (
    CompletionResponseError,
    DraftMergeError,
    completion_prompt,
    draft_leg_labels,
    merge_rounds,
    parse_completion_response,
)
from app.domain.round_evidence import verify_rounds
from app.fetching import ApprovedPublicHosts, _normalize_host, fetch_html
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

COMPLETION_USER_AGENT = "dekimasen.app/1.0 (draft completion)"
# At most this many fetch+call pairs per press. Lower than triage's 25 because
# an official ticket page is a far bigger read than an Eventernote event page.
COMPLETION_DRAFT_CAP = 15
COMPLETION_DELAY_SECONDS = 1.0
FETCH_DEADLINE_SECONDS = 30.0
COMPLETION_BUDGET_SECONDS = 240.0


@dataclass
class CompletionReport:
    drafts_seen: int = 0
    completed: int = 0
    rounds_added: int = 0
    rounds_rejected: int = 0
    # A draft whose host is waiting on a human. Counted apart from `skipped`
    # for the reason SweepReport counts calendar skips apart: nothing failed,
    # somebody just has not answered yet, and the remedy is a click rather
    # than a fix.
    blocked_domains: int = 0
    skipped: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    budget_exhausted: bool = False


def draft_source_url(draft_text: str) -> str | None:
    """The page to read for this draft's rounds, or None.

    `official_url` first, then `source_url`. NEVER `eventernote_url`: that page
    has no ticket information on it at all, so fetching it would spend a
    request on a page that cannot contain the answer -- the same fact phase 1's
    draft prompt states to the model.
    """
    try:
        data = yaml.safe_load(draft_text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("official_url", "source_url"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return None


async def _fetch_page(url: str, hosts: set[str]) -> str:
    """One official page, under the approved-public policy."""
    return await fetch_html(
        url,
        policy=ApprovedPublicHosts(lambda host: host in hosts),
        user_agent=COMPLETION_USER_AGENT,
    )


async def complete_one(
    session: AsyncSession,
    row: PendingDraft,
    page_text: str,
    source_url: str,
    *,
    llm_chat=llm.chat,
) -> tuple[int, int, int, int]:
    """One draft, one call: propose rounds, keep the grounded ones.

    Shared by the batch runner and the paste fallback route, so the two cannot
    drift on what counts as a grounded round -- the fallback exists because the
    fetch declined, not because the rules are different when it does.

    Returns (rounds_added, rounds_rejected, tokens_in, tokens_out) and writes
    `completion_yaml` whether or not any round survived: the call was paid for,
    and a second press must not pay for it again -- including when the reply
    or the stored draft turns out to be unusable (see the try/except below).
    """
    page = normalize_page_text(page_text)
    reply = await llm_chat(*completion_prompt(row.draft_text, page))
    try:
        proposed, warnings = parse_completion_response(reply.text)
        for warning in warnings:
            log.warning("completion: draft %s: %s", row.id, warning)

        # UTC's date, not a local one: the plausibility window is ±2/+3
        # YEARS, so a day's drift at the boundary cannot matter, and a naive
        # datetime.now() in a codebase whose one hard rule is aware-UTC is a
        # bad example to leave lying around (invariant 1).
        verdict = verify_rounds(
            proposed, page, draft_leg_labels(row.draft_text), datetime.now(UTC).date()
        )
        merged_text = merge_rounds(row.draft_text, [r.data for r in verdict.accepted])
    except (CompletionResponseError, DraftMergeError) as exc:
        # The reply is already in hand -- the call was paid for -- so a
        # reply that turns out to be unusable (not YAML, not a mapping) or a
        # stored draft merge_rounds refuses to touch (a hand-corrupted body)
        # must still leave a paid-for mark on the row, or completion_candidates
        # hands it right back next press and this pays for the same junk
        # reply twice. Recorded as a rejection like any other, so the
        # operator sees WHAT went wrong rather than a draft that silently
        # never progresses. Re-raised so run_completion's caller still counts
        # this as a skipped draft -- the row changed, the outcome did not.
        row.completion_yaml = yaml.safe_dump(
            {
                "source_url": source_url,
                "evidence": [],
                "rejected": [f"the model's reply could not be used: {exc}"],
            },
            allow_unicode=True,
            sort_keys=False,
        )
        await session.flush()
        raise

    row.draft_text = merged_text
    row.completion_yaml = yaml.safe_dump(
        {
            "source_url": source_url,
            "evidence": [dict(r.evidence) for r in verdict.accepted],
            "rejected": list(verdict.rejected) + list(warnings),
        },
        allow_unicode=True,
        sort_keys=False,
    )
    await session.flush()
    return len(verdict.accepted), len(verdict.rejected), reply.tokens_in, reply.tokens_out


def _finish(run: TriageRun, now: datetime, report: CompletionReport) -> None:
    """Copy the report onto the run row. Every count is written, zeros
    included: on this table a written 0 means "looked, found none" and NULL
    means "never got there"."""
    run.status = "done"
    run.finished_at = now
    run.drafts_completed = report.completed
    run.rounds_added = report.rounds_added
    run.rounds_rejected = report.rounds_rejected
    run.blocked_domains = report.blocked_domains
    run.skipped = report.skipped
    run.tokens_in = report.tokens_in
    run.tokens_out = report.tokens_out


async def _announce(session: AsyncSession, report: CompletionReport) -> None:
    """Queue ONE admin notice per run. Never sends a DM itself (invariant 4).

    Reuses kind="triage": it reports on a model's proposals exactly as phase
    1's notice does, and a second kind behaving identically would only be a
    second thing to remember to keep out of UNREPORTED_NOTE_KINDS.
    """
    body = (
        f"Draft completion finished: {report.completed} draft(s) completed, "
        f"{report.rounds_added} round(s) added, {report.rounds_rejected} rejected, "
        f"{report.blocked_domains} draft(s) waiting on domain approval, "
        f"{report.skipped} skipped.\n"
        "Review: https://dekimasen.app/concerts/import/pending"
    )
    for admin_id in sorted(settings.admin_ids):
        # An admin who has never logged in has no users row, and
        # Notification.user_id is an FK to it. Guarded on absence rather than
        # calling ensure_user unconditionally: that refreshes the username and
        # would overwrite a real admin's name with this placeholder every run.
        if await session.get(User, admin_id) is None:
            await ensure_user(session, admin_id, str(admin_id))
        session.add(Notification(user_id=admin_id, body=body, kind="triage"))


async def run_completion(
    session: AsyncSession,
    run: TriageRun,
    now: datetime,
    *,
    fetcher: Callable[[str], Awaitable[str]] | None = None,
    llm_chat=llm.chat,
) -> CompletionReport:
    """Complete this run's requester's open skeleton drafts.

    `fetcher` and `llm_chat` are injected so tests never touch the network or
    spend a real key -- one seam per external system, the shape `run_sweep` and
    `run_triage` both use. The transaction stays the caller's: this flushes,
    never commits.
    """
    report = CompletionReport()
    run.started_at = now

    rows = await completion_candidates(session, run.requested_by)
    report.drafts_seen = len(rows)
    if not rows:
        # Nothing to complete, so nothing to pay for and nobody to tell.
        _finish(run, now, report)
        await session.flush()
        return report

    hosts = await approved_fetch_hosts(session)
    fetch = fetcher or (lambda url: _fetch_page(url, hosts))
    deadline = monotonic() + COMPLETION_BUDGET_SECONDS
    attempts = 0

    for index, row in enumerate(rows):
        # Checked at the TOP, before anything is fetched: the budget caps how
        # long the reminder tick is held, so the answer must be "stop" before
        # the next page is asked for, not after.
        if monotonic() >= deadline:
            report.budget_exhausted = True
            log.warning(
                "completion: %.0fs budget spent after %d draft(s); %d left for the next press",
                COMPLETION_BUDGET_SECONDS, index, len(rows) - index,
            )
            break
        if attempts >= COMPLETION_DRAFT_CAP:
            log.info(
                "completion: cap (%d) reached; %d draft(s) left for the next press",
                COMPLETION_DRAFT_CAP, len(rows) - attempts,
            )
            break

        url = draft_source_url(row.draft_text)
        if not url:
            # No page to read. Left retryable on purpose: an editor can add the
            # URL, or paste the page, and the next press picks it up.
            report.skipped += 1
            continue
        # urlparse(url).hostname is the RAW hostname -- no manual lowercasing
        # here, because `note_fetch_domain` below wants that exact raw shape
        # (its docstring says so) and does its own normalization. The
        # comparison against `hosts` (already-normalized by
        # `approved_fetch_hosts`) goes through the identical
        # `fetching._normalize_host` an unapproved host would otherwise be
        # compared under a different rule than the one that approved it.
        raw_host = urlparse(url).hostname
        if not raw_host:
            report.skipped += 1
            continue
        try:
            host = _normalize_host(raw_host)
        except ValueError:
            host = None
        if host is None or host not in hosts:
            # Record it for a human and move on. NOT a failure: the remedy is a
            # click on /admin/fetch-domains, and the draft stays a candidate.
            try:
                await note_fetch_domain(session, raw_host, url, now)
            except ValueError:
                # note_fetch_domain refuses a host shaped like a URL, a
                # host:port, or blank after stripping -- and urlparse() keeps
                # an IPv6 literal's colons intact (`2606:4700::1111`), which
                # both `_normalize_host` above and this check let straight
                # through. Such a host can never be approved either way, so
                # there is nothing for a human to click: this is a skip, not
                # a pending approval, and (critically) not left to propagate
                # out of the loop -- an unhandled ValueError here would kill
                # the whole run and, worse, leave every draft already
                # completed this press unmarked, so the next press re-pays
                # for them and dies on this same row again.
                log.warning(
                    "completion: draft %s: %r is not a storable hostname", row.id, raw_host
                )
                report.skipped += 1
                continue
            report.blocked_domains += 1
            continue

        attempts += 1
        heartbeat.beat()
        try:
            await asyncio.sleep(COMPLETION_DELAY_SECONDS)
            async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
                html = await fetch(url)
            added, rejected, tokens_in, tokens_out = await complete_one(
                session, row, html_to_text(html), url, llm_chat=llm_chat
            )
            report.tokens_in += tokens_in
            report.tokens_out += tokens_out
            report.rounds_added += added
            report.rounds_rejected += rejected
            report.completed += 1
        except SQLAlchemyError:
            # NOT one skipped draft. A failed flush POISONS the session, so
            # nothing after this point can persist -- absorbing it would pay for
            # up to fourteen more calls to write nothing and then close the run
            # out as "done". Re-raised BEFORE the generic handler below; the
            # scheduler's block rolls back and marks the row failed on a
            # cleaned transaction.
            log.exception("completion: the session is poisoned; abandoning the run")
            raise
        except Exception:
            # Fetch, LLM, parse and merge failures are one thing here: a draft
            # that did not get completed. One must not cost the rest.
            log.exception("completion: could not complete draft %s from %s", row.id, url)
            report.skipped += 1
            continue

    _finish(run, now, report)
    await _announce(session, report)
    await session.flush()
    return report
