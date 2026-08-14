"""Round poll: re-read each QUIET concert's own official page and record what
the ladder appears to have grown, as reviewable proposals.

Sits ABOVE `db/` exactly like `discovery.py`, `triage.py` and
`draft_completion.py`: it imports `domain/`, `app.llm`, `app.fetching` and the
db FACADE, and nothing in `db/` imports it. This module is the RUN ORDER only
-- which concerts, in what sequence, and what a failure at each step costs.
Every judgement it makes is somebody else's pure function: whether a concert is
worth re-reading is `db/quiet_ladders.py`, whether a proposed round may be
believed at all is `domain/round_evidence.py`, and whether it is NEW is
`domain/round_proposals.py`. Design:
docs/superpowers/specs/2026-08-13-round-poll-design.md.

IT REUSES PHASE 2's PROMPT AND PHASE 2's SAFETY RULE, deliberately, rather than
owning a second of either. `completion_prompt` already asks a model to fill in
`rounds:` for one draft-vocabulary document given one page of text, and a
concert rendered by `concert_export_yaml` IS such a document -- so the only
difference between this pass and `draft_completion` is which candidate list it
walks. A second prompt would be a second thing to keep in step with
`verify_rounds`, and the day they drifted the drift would show up as fabricated
deadlines rather than as an error.

NOTHING IS DROPPED SILENTLY, and this is the whole reason the module is shaped
the way it is. Its worst failure is invisible by construction: a real deadline
the model found, discarded without a reason, looks exactly like a page that had
nothing on it, and nobody ever knows to look. So every outcome is COUNTED and
every discard carries its reason into `PollReport` -- a round `verify_rounds`
refused (`rejections`), a page that could not be fetched or a reply that could
not be parsed (`failures`), a concert with no page to read, a host waiting on a
human, a host a human already refused. A truncated run says so
(`budget_exhausted`). The counts are what the digest DM reports; the reason
lists are what makes two different silences distinguishable.

WHAT IT NEVER DOES. It writes no `Round`: a proposal is a claim about a
third-party page made by a model, and phase 1 puts nothing in front of a user
at all. It never writes `Concert.ladder_rechecked_at_utc` -- that column is the
OWNER's "I have re-checked this one" stamp and orders /admin/quiet-ladders, so
a machine writing it would silently mark the worklist as attended. It stamps
`ladder_polled_at_utc` instead. It queues no notification and commits nothing:
the scheduler block owns the transaction, the daily stamp and the digest.

THE ORDER IS THE POLL's OWN STAMP, and that is not a second cursor -- it is the
one `record_ladder_polled` writes. `quiet_ladder_rows` sorts by
`ladder_rechecked_at_utc`, the HUMAN's stamp, which the poll must never touch:
consumed in that order the sequence is byte-identical on every run, so the
moment the wall clock bites, the same head is re-read every day and the tail is
never read at all. That is precisely the starvation `discovery.py`'s budget
comment records ("a fixed start point would have been worse than no budget"),
and the reason its sweep keeps a cursor. Sorting the SAME candidate set by the
poll's own stamp before walking it costs one attribute read -- the concert rows
are already in this session's identity map, loaded by the candidate query
itself -- and makes tomorrow's run resume where today's stopped. It defines
nothing about WHICH concerts are worth re-reading; that stays
`quiet_ladder_rows`, the one place it is allowed to live.

A CONCERT WE SPENT AN ATTEMPT ON IS STAMPED WHETHER OR NOT IT SUCCEEDED, for
the same reason. Stamping only successes would let one permanently-broken page
(a 403, a host that redirects off the approved set) hold the head of the queue
forever and starve everything behind it -- the failure the order above exists
to prevent, re-introduced by the sympathetic-looking rule. A concert that cost
NO attempt (no URL, an unapproved host) is not stamped and does not need to be:
it consumes no budget, so it starves nothing, and leaving it unstamped keeps
"when did the poll last read this page" honest.

THE BUDGET IS A WALL CLOCK, not a count. Owner ruling, 2026-08-13: no per-run
cap, because each concert is its OWN prompt -- the 511-lead classify failure
does not transfer, since that one put N items in ONE call and outgrew its
context. What a count cannot bound is TIME, and the run sits inline in the
reminder tick: httpx's timeout is per READ, so a server dripping bytes holds a
connection with no deadline at all, which is why `FETCH_DEADLINE_SECONDS`
exists on top. `heartbeat.beat()` fires per concert, since /healthz goes
unhealthy after 180s and a run reading fifteen pages would otherwise page the
owner about a perfectly healthy app.

`SQLAlchemyError` is the one failure this module does not absorb: a failed
flush poisons the session, so nothing after it can persist, and stepping over
it would spend the rest of the run's paid LLM calls writing nothing at all.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlparse

import yaml
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.db.models import Concert
from app.db.service import (
    approved_fetch_hosts,
    concert_export_yaml,
    dismissed_keys_for,
    note_fetch_domain,
    quiet_ladder_rows,
    record_ladder_polled,
    upsert_proposal,
)
from app.domain.page_text import html_to_text, normalize_page_text
from app.domain.round_completion import (
    completion_prompt,
    draft_leg_dates,
    draft_leg_labels,
    parse_completion_response,
)
from app.domain.round_evidence import verify_rounds
from app.domain.round_proposals import (
    CLOSES_AT_FIELD,
    OPENS_AT_FIELD,
    HeldRound,
    dedupe_key,
    new_proposals,
    proposed_stamp_utc,
)

# Private on purpose and imported anyway, the way draft_completion imports
# `fetching._normalize_host`: this is the ONE reading of a draft's `kind:` text
# into a RoundKind (unknown -> `other`, with a warning), and phase 2 will apply
# an accepted proposal through the draft parser that uses it. A second mapping
# here would decide a round's kind differently from the code that eventually
# writes it, and neither would raise.
from app.domain.yaml_import import _round_kind

# The per-host user-agent exceptions, shared rather than copied. That table is
# a NAMED EXCEPTION PER HOST with a measurement attached to each row (see
# draft_completion), and it grows: a second copy means a host added for one
# pass silently 403s in the other, and the symptom -- "the poll finds nothing
# on eight of twelve concerts" -- looks exactly like a quiet ladder.
from app.draft_completion import HOST_USER_AGENTS
from app.fetching import ApprovedPublicHosts, _normalize_host, fetch_html
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

# Names the pass, not the app: a third party reading its logs should be able to
# tell which of this app's two page-readers knocked. The per-host exceptions
# above are shared; the honest default is this module's own.
ROUND_POLL_USER_AGENT = "dekimasen.app/1.0 (round poll)"
# Sequential with a pause, the sweep's constant and the sweep's reason:
# parallel requests at a third party is how an IP gets blocked. The rate stays
# what a person clicking would produce.
ROUND_POLL_DELAY_SECONDS = 1.0
# A TOTAL deadline per page. httpx's own timeout is per READ operation, so a
# server dripping bytes under the size cap can hold a connection open
# indefinitely without ever tripping it.
FETCH_DEADLINE_SECONDS = 30.0
# The whole run's wall clock. Same value as the sweep's and the completion
# press's, and bounding the same thing: how long the reminder tick is held.
ROUND_POLL_BUDGET_SECONDS = 240.0


@dataclass
class PollReport:
    """What one poll did. Every field is reportable; the two lists are what
    keep a discard distinguishable from a silence."""

    concerts_seen: int = 0
    # Fetched, asked and answered. A concert whose page simply held no round is
    # POLLED with no new proposal -- deliberately not the same as a failure.
    polled: int = 0
    skipped_no_url: int = 0
    # Waiting on a human's click at /admin/fetch-domains. Counted apart from
    # `skipped_declined` for the reason draft_completion counts
    # `blocked_domains` apart from `skipped`: nothing failed, somebody just has
    # not answered yet, and the remedy is a click rather than a fix.
    skipped_host: int = 0
    # A human already said no. Reporting these as "waiting on approval" would
    # send the owner to a screen where there is nothing to press.
    skipped_declined: int = 0
    failed: int = 0
    new_proposals: int = 0
    # Proposed, grounded, new -- and matching a key the owner dismissed. Not
    # silent: a page that keeps re-offering a refused round is worth seeing.
    skipped_dismissed: int = 0
    rounds_rejected: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    # True when the budget ran out and concerts were left for tomorrow.
    # Recorded rather than merely logged: a truncation only the journal knows
    # about is the silent degradation this repo keeps finding.
    budget_exhausted: bool = False
    # WHY, one line per discard, prefixed with the concert's event_id.
    # `rejections` is `verify_rounds`' verdict (plus the parser's per-round
    # warnings); `failures` is a concert the pass could not read at all.
    rejections: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _user_agent_for(url: str) -> str:
    """`ROUND_POLL_USER_AGENT`, unless this host is one of the few that refuse
    a non-browser agent outright (`draft_completion.HOST_USER_AGENTS`).

    The host is normalized through the SAME `_normalize_host` the approval
    policy uses, so a `WWW.`-cased or trailing-dot URL cannot miss the table by
    spelling. A malformed host falls through to the default rather than
    raising: `fetch_html` is about to reject it anyway, and the UA lookup is
    not where that decision gets made.
    """
    try:
        host = _normalize_host(urlparse(url).hostname or "")
    except (UnicodeError, ValueError):
        return ROUND_POLL_USER_AGENT
    return HOST_USER_AGENTS.get(host, ROUND_POLL_USER_AGENT)


async def _fetch_page(url: str, hosts: set[str]) -> str:
    """One official page, under the approved-public policy."""
    return await fetch_html(
        url,
        policy=ApprovedPublicHosts(lambda host: host in hosts),
        user_agent=_user_agent_for(url),
    )


async def _candidates(
    session: AsyncSession, rows, report: PollReport
) -> list[tuple[object, Concert]]:
    """The candidate rows paired with their concerts, least-recently-polled
    first.

    `session.get` rather than a query: `quiet_ladder_rows` has already loaded
    every concert in the catalogue into this session's identity map, so these
    are hits, not round trips -- and the pass needs the ORM row anyway, to
    render the document the prompt is built from.

    A concert that vanished between the candidate query and here is COUNTED,
    not skipped quietly. It is a race of milliseconds and will never happen in
    practice, which is exactly why an unexplained gap in the numbers would be
    unreadable if it ever did.
    """
    pairs: list[tuple[object, Concert]] = []
    for row in rows:
        concert = await session.get(Concert, row.concert_id)
        if concert is None:
            report.failed += 1
            report.failures.append(f"{row.event_id}: the concert no longer exists")
            continue
        pairs.append((row, concert))
    far_past = datetime.min.replace(tzinfo=UTC)
    # Never polled before ever polled, then oldest poll. STABLE, so
    # `quiet_ladder_rows`' own longest-unattended order survives as the
    # tiebreak among concerts the poll has never seen.
    pairs.sort(key=lambda pair: (
        pair[1].ladder_polled_at_utc is not None,
        pair[1].ladder_polled_at_utc or far_past,
    ))
    return pairs


async def _poll_one(
    session: AsyncSession,
    row,
    concert: Concert,
    url: str,
    now: datetime,
    report: PollReport,
    *,
    fetch: Callable[[str], Awaitable[str]],
    chat,
) -> None:
    """Read one concert's page and record what it proposes.

    Raises whatever the fetch, the call or the parse raises -- the caller owns
    what a failure costs, because that is the run-order decision and this is
    the step.
    """
    async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
        html = await fetch(url)
    page = normalize_page_text(html_to_text(html))

    # The concert as a draft-vocabulary document -- the export builder, not a
    # second adapter onto `concert_to_yaml`. `concert_export_yaml` is already
    # the ONE place ORM rows become this vocabulary (the zip export and the
    # per-concert download share it precisely so they cannot drift), and the
    # prompt's whole contract is that its input is that vocabulary.
    draft_text = await concert_export_yaml(session, concert)
    system, user = completion_prompt(draft_text, page)
    reply = await chat(system, user)
    report.tokens_in += reply.tokens_in
    report.tokens_out += reply.tokens_out

    proposed, warnings = parse_completion_response(reply.text)
    verdict = verify_rounds(
        proposed,
        page,
        draft_leg_labels(draft_text),
        # UTC's date: `verify_rounds` uses it only for the +-2/+3 YEAR
        # plausibility window, so a day's drift at the boundary cannot matter,
        # and `now` is aware UTC by invariant 1.
        now.date(),
        leg_dates=draft_leg_dates(draft_text),
    )
    # Both lists, because both are rounds that will not be shown: `rejected` is
    # a round the evidence rule refused, `warnings` a round the parser could
    # not read (no label, evidence that was not a mapping). Counted apart --
    # `rounds_rejected` is the evidence rule's verdict, which is the number
    # worth watching -- but neither is dropped.
    for reason in (*verdict.rejected, *warnings):
        log.info("round poll: %s: %s", row.event_id, reason)
    report.rounds_rejected += len(verdict.rejected)
    report.rejections.extend(
        f"{row.event_id}: {reason}" for reason in (*verdict.rejected, *warnings)
    )

    held = [HeldRound(label=r.label, opens_at_utc=r.opens_at_utc) for r in row.rounds]
    dismissed = await dismissed_keys_for(session, row.concert_id)
    for candidate in new_proposals(held, verdict.accepted):
        opens_at_utc = proposed_stamp_utc(candidate, OPENS_AT_FIELD)
        if dedupe_key(candidate.label, opens_at_utc) in dismissed:
            report.skipped_dismissed += 1
            continue
        kind_warnings: list[str] = []
        kind = _round_kind(
            candidate.data.get("kind"), f"round {candidate.label!r}", kind_warnings
        )
        for warning in kind_warnings:
            log.info("round poll: %s: %s", row.event_id, warning)
        await upsert_proposal(
            session,
            row.concert_id,
            label=candidate.label,
            kind=kind,
            opens_at_utc=opens_at_utc,
            closes_at_utc=proposed_stamp_utc(candidate, CLOSES_AT_FIELD),
            # The quotes, keyed by the field each one grounds -- already
            # trimmed by `verify_rounds` to the timestamps it actually checked
            # against the page, so nothing unverified can ride onto a review
            # screen under a heading that says it was read from the page.
            evidence_yaml=yaml.safe_dump(
                dict(candidate.evidence), allow_unicode=True, sort_keys=False
            ),
            source_url=url,
            now=now,
        )
        report.new_proposals += 1
    report.polled += 1


async def run_round_poll(
    session: AsyncSession,
    now: datetime,
    *,
    fetcher: Callable[[str], Awaitable[str]] | None = None,
    chat=llm.chat,
) -> PollReport:
    """Poll every quiet concert once, and report what happened to each.

    `fetcher` and `chat` are injected so tests never touch the network or spend
    a real key -- one seam per external system, the shape `run_sweep`,
    `run_triage` and `run_completion` all use. The transaction stays the
    caller's: this flushes, never commits, and neither stamps the daily clock
    nor queues the digest (the scheduler block owns both, because a run that
    fails must still be stamped).
    """
    report = PollReport()
    rows = await quiet_ladder_rows(session, now)
    report.concerts_seen = len(rows)
    if not rows:
        # No quiet concert is the healthy state, and it costs nothing.
        return report

    hosts = await approved_fetch_hosts(session)
    fetch = fetcher or (lambda url: _fetch_page(url, hosts))
    deadline = monotonic() + ROUND_POLL_BUDGET_SECONDS
    pairs = await _candidates(session, rows, report)

    for index, (row, concert) in enumerate(pairs):
        # Checked at the TOP, before anything is fetched: the budget caps how
        # long the reminder tick is held, so the answer must be "stop" before
        # the next page is asked for, not after.
        if monotonic() >= deadline:
            report.budget_exhausted = True
            log.warning(
                "round poll: %.0fs budget spent after %d concert(s); %d left for tomorrow",
                ROUND_POLL_BUDGET_SECONDS, index, len(pairs) - index,
            )
            break

        url = (row.official_url or "").strip()
        if not url:
            # Nothing to read. Not an error: a quiet concert nobody gave a page
            # is a fact worth reporting, and the remedy is an editor typing one
            # in, after which the next poll picks it up.
            report.skipped_no_url += 1
            continue

        # The RAW hostname, because `note_fetch_domain` wants exactly that
        # shape (its docstring says so) and normalizes it itself. The
        # comparison against `hosts` goes through the identical
        # `fetching._normalize_host` the approval used, or an unapproved host
        # would be compared under a different rule than the one that approved
        # it.
        raw_host = urlparse(url).hostname
        if not raw_host:
            report.failed += 1
            report.failures.append(f"{row.event_id}: {url!r} names no host")
            continue
        try:
            host = _normalize_host(raw_host)
        except ValueError:
            host = None
        if host is None or host not in hosts:
            try:
                domain = await note_fetch_domain(session, raw_host, url, now)
            except ValueError:
                # A host `note_fetch_domain` refuses to store (URL-shaped,
                # carrying a port, or an IPv6 literal, whose colons `urlparse`
                # keeps and `_normalize_host` lets through) is one no approval
                # screen could ever unblock either. It costs this concert, not
                # the run: an unhandled ValueError here would abandon every
                # concert behind it.
                report.failed += 1
                report.failures.append(
                    f"{row.event_id}: {raw_host!r} is not a storable hostname"
                )
                continue
            # ONE call answers both questions. An existing row -- pending,
            # approved or declined -- comes back UNTOUCHED by that function's
            # own contract, so a host a human refused is never re-recorded and
            # the approval queue cannot regrow it; `declined_at` is simply what
            # tells the two outcomes apart here.
            if domain.declined_at is not None:
                report.skipped_declined += 1
            else:
                report.skipped_host += 1
            continue

        heartbeat.beat()
        try:
            # Before the fetch, not after: the pause is what keeps the rate at
            # one page at a time even when the previous fetch was instant.
            await asyncio.sleep(ROUND_POLL_DELAY_SECONDS)
            await _poll_one(
                session, row, concert, url, now, report, fetch=fetch, chat=chat
            )
        except SQLAlchemyError:
            # NOT one failed concert. A failed flush POISONS the session, so
            # nothing after this point can persist -- absorbing it would spend
            # the rest of the run's paid calls writing nothing at all. The
            # scheduler's block rolls back and re-stamps the daily clock on a
            # cleaned transaction.
            log.exception("round poll: the session is poisoned; abandoning the run")
            raise
        except Exception as exc:
            # A dead fetch, an unusable reply, a parse that failed: one thing
            # here, a concert that could not be polled. One must not cost the
            # rest -- and it is named in the report, not only in the journal.
            log.exception("round poll: could not poll %s from %s", row.event_id, url)
            report.failed += 1
            report.failures.append(f"{row.event_id}: {type(exc).__name__}: {exc}")

        # Reached on success AND on failure, and only for a concert an attempt
        # was actually spent on -- see the module docstring. NEVER
        # `ladder_rechecked_at_utc`, which is the owner's.
        await record_ladder_polled(session, row.concert_id, now)

    return report
