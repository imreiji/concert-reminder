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
human, a host a human already refused, a round the concert already holds, a
proposal seen again rather than found. A truncated run says so
(`budget_exhausted`). The counts are what the digest DM reports; the reason
lists are what makes two different silences distinguishable.

A DAILY PASS RE-PROPOSES EVERYTHING IT HAS NOT BEEN ANSWERED ABOUT, and that
is why `report.new_proposals` counts ROWS CREATED rather than sightings. The
same page yields the same claim tomorrow; `classify_proposals` (the pure diff)
filters only rounds the concert HOLDS UNCHANGED and `dismissed_keys_for` only
rounds the owner REFUSED, so a proposal sitting unreviewed passes both and
`upsert_proposal` rewrites the row it already has. Counted as new, that would
tell the owner "1 new proposal" every single day until they act -- which
trains them to ignore the one message that also carries the rejection
reasons.

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
That reasoning is about a WRITE, and the one READ that raises out of the same
family is carved out rather than swept into it: `concert_export_yaml` opens
with `session.refresh`, which raises `InvalidRequestError` if the concert has
gone since the candidate list was built. The session is fine and the rest of
the run can still be written, so that race costs ONE concert, counted and
named (`ConcertVanished`) -- exactly as `_candidates` already treats the same
race one step earlier. Abandoning a whole run, and the digest with it, over a
concert somebody deleted mid-tick would be the silence this module is built
against.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlparse

import yaml
from sqlalchemy.exc import InvalidRequestError, SQLAlchemyError
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

# `_clip` is private and imported anyway, the way this module already imports
# `yaml_import._round_kind` and `fetching._normalize_host`: it is THE rule for
# capping an unbounded free-text field before it reaches a DM, and its own
# docstring records the exact failure -- one long field blowing the budget
# before the block-truncation loop can run. A second copy here would be a
# second rule to keep in step with the one budget both obey.
from app.domain.discovery_message import DM_CHAR_BUDGET, _clip
from app.domain.page_text import html_to_text, normalize_page_text
from app.domain.round_completion import (
    completion_prompt,
    draft_leg_dates,
    draft_leg_labels,
    parse_completion_response,
)
from app.domain.round_evidence import verify_rounds
from app.domain.round_proposals import (
    APPLIES_TO_FIELD,
    CLOSES_AT_FIELD,
    OPENS_AT_FIELD,
    PAYMENT_AT_FIELD,
    RESULTS_AT_FIELD,
    HeldRound,
    classify_proposals,
    dedupe_key,
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
# pass keeps 403ing in the other until somebody notices the row is missing.
# Not a SILENT failure -- `fetch_html` raises `FetchFailed("HTTP 403")`, which
# this pass counts and names in `failures` -- but a loud one repeated daily on
# 8 of the owner's 12 concerts, which is the franchise the catalogue is mostly
# made of.
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
    # Rows this poll CREATED, never rows it wrote. The pass re-reads the same
    # page every day, so a proposal nobody has reviewed yet is re-proposed,
    # re-diffed and re-upserted on every run -- counting those as new would
    # tell the owner "1 new proposal" every day until they act, which teaches
    # them to ignore the one message that also carries the rejection reasons.
    # `upsert_proposal` hands the row back and never refreshes `first_seen_at`
    # (a re-sighting must not reset "how long has this waited"), so that stamp
    # is what tells the two apart.
    new_proposals: int = 0
    # Seen again, still pending, row rewritten with today's reading. Reported
    # because "the page still says this" is a different fact from silence.
    refreshed: int = 0
    # Proposed, grounded, new -- and matching a key the owner dismissed. Not
    # silent: a page that keeps re-offering a refused round is worth seeing.
    skipped_dismissed: int = 0
    # Proposed, grounded -- and the concert already holds it. The ordinary
    # outcome on a page whose ladder has not moved, and counted for the same
    # reason as everything else here: an outcome with no counter and no row is
    # indistinguishable from a page that said nothing at all.
    skipped_held: int = 0
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


# The most reasons one digest ever names, per list. A real cap rather than a
# comment: `rejections` is one line per refused round across every concert
# polled, so a bad model day is unbounded, and a DM past Discord's 2000-char
# limit does not truncate -- discord.py raises and the WHOLE message is lost,
# which is the one outcome a message about silent discards must not have.
DIGEST_LIST_LIMIT = 10
# And the cap on ONE of them. Capping only the LIST is not enough, and the gap
# is not theoretical: a reason embeds model-supplied text verbatim
# (`round_evidence.py` mints `f"round {label!r}: {reason}"`), so a single
# 2,500-character label the model invented produces a 2,698-character digest
# that the shrink loop below cannot help with -- it has nothing left to drop
# once each list is down to its last entry. Past 2,000 chars discord.py raises
# HTTPException, `_send_notification` returns TRANSIENT_FAILURE, the row is
# never marked sent, and the tick retries it every 60 seconds forever while the
# digest is never delivered. `build_discovery_dm`, the precedent this module
# follows, caps every free-text field it embeds for exactly this reason.
MAX_REASON_CHARS = 200


class ConcertVanished(Exception):
    """The concert row went away between the candidate list and its read.

    Deliberately NOT a `SQLAlchemyError`, which is the one family
    `run_round_poll` refuses to absorb: that rule is about a poisoned FLUSH,
    and this is a failed read that leaves the session entirely usable. See
    `_poll_one`.
    """


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _reason_block(heading: str, reasons: list[str], kept: int) -> list[str]:
    """One headed list, with an honest "+N more" when it was trimmed.

    Each line is clipped to `MAX_REASON_CHARS` -- the reason stays
    recognisable, which is the whole point of printing it, but a model-supplied
    label cannot decide how long this DM is.
    """
    if not reasons:
        return []
    shown = reasons[:kept]
    lines = [
        f"**{heading}**",
        *(f"• {_clip(reason, MAX_REASON_CHARS)}" for reason in shown),
    ]
    dropped = len(reasons) - len(shown)
    if dropped > 0:
        lines.append(f"…and {_plural(dropped, 'more')}.")
    return ["", *lines]


def build_poll_digest(report: PollReport, *, base_url: str) -> str:
    """The digest DM for one run. Pure: no DB, no Discord, no clock.

    EVERY completed run gets one, including a run that found nothing, and that
    is a deliberate departure from `build_discovery_dm`/`build_quiet_ladder_dm`,
    which both return "" on an empty result. Those two are WORKLISTS -- an empty
    worklist is nothing to do, and a daily "nothing found" trains the reader to
    ignore the channel. This is a RUN REPORT for a subsystem that spends a paid
    key against third-party pages once a day and stores nothing else about the
    run (`RoundPollState` carries `last_run_at` and nothing more, deliberately).
    Its ABSENCE is the signal: no digest means the pass did not complete, and
    with a suppressed quiet day a broken pass and a quiet one would look
    identical -- the exact silence this whole feature is built to remove.

    Two distinctions the wording keeps apart, because each was built on purpose:

    * NEW vs SEEN AGAIN. The pass re-reads the same page daily, so a proposal
      nobody has reviewed is re-proposed every day until they act. Folded
      together, this DM would say "1 new proposal" every morning, which teaches
      its reader to skim past the rejection list underneath it.
    * WAITING ON YOU vs ALREADY REFUSED. `skipped_host` is a click at
      /admin/fetch-domains; `skipped_declined` is a human's answered no.
      Reporting a declined host as "waiting for approval" sends the reader to a
      screen with nothing on it to press.
    """
    head = (
        f"**Round poll** — {_plural(report.concerts_seen, 'quiet concert')}, "
        f"{report.polled} polled."
    )
    lines = [head, ""]

    if report.new_proposals:
        lines.append(
            f"**{_plural(report.new_proposals, 'new proposal')}** — "
            f"{base_url}/admin/quiet-ladders/proposals"
        )
    else:
        lines.append("No new proposals.")
    if report.refreshed:
        lines.append(
            f"{_plural(report.refreshed, 'proposal')} seen again, still waiting "
            "on a review — not new."
        )
    if report.skipped_held:
        lines.append(f"{_plural(report.skipped_held, 'round')} the concert already holds.")
    if report.skipped_dismissed:
        lines.append(
            f"{_plural(report.skipped_dismissed, 'round')} re-offered after you "
            "dismissed it."
        )

    # The counts the reader can act on, each naming WHERE. A declined host is
    # listed too, without a link: it is not actionable, and saying so is what
    # stops it being read as a pending approval.
    if report.skipped_no_url:
        lines.append(
            f"{_plural(report.skipped_no_url, 'concert')} with no official page to read."
        )
    if report.skipped_host:
        lines.append(
            f"{_plural(report.skipped_host, 'concert')} waiting on your host "
            f"approval — {base_url}/admin/fetch-domains"
        )
    if report.skipped_declined:
        lines.append(
            f"{_plural(report.skipped_declined, 'concert')} on a host you already "
            "declined; nothing to do."
        )
    if report.budget_exhausted:
        lines.append("Stopped at its time budget; the rest are tomorrow's.")

    # The reason lists LAST and never dropped whole: a real deadline discarded
    # without a reason is the failure this pass exists to prevent, and a count
    # with no reason beside it is the same silence one step quieter.
    kept_rejections = kept_failures = DIGEST_LIST_LIMIT
    while True:
        body = "\n".join([
            *lines,
            # The heading names the evidence rule's count, but the list is
            # every reason -- `rejections` also carries the parser's per-round
            # warnings and a kind this app does not have, which are discards
            # (or a silently downgraded kind) by another route and just as
            # worth reading.
            *_reason_block(
                f"Discarded rounds — {report.rounds_rejected} refused by the "
                "evidence rule",
                report.rejections,
                kept_rejections,
            ),
            *_reason_block(
                f"{_plural(report.failed, 'concert')} could not be read",
                report.failures,
                kept_failures,
            ),
            "",
            f"{report.tokens_in} tokens in, {report.tokens_out} out.",
        ])
        if len(body) <= DM_CHAR_BUDGET or (kept_rejections <= 1 and kept_failures <= 1):
            return body
        # Trim the longer list first, so one runaway list cannot silence the
        # other one entirely.
        if kept_rejections >= kept_failures:
            kept_rejections -= 1
        else:
            kept_failures -= 1


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


def _applies_to_labels(candidate) -> list[str]:
    """The leg LABELS this proposed round names, as a list of strings.

    Labels, never `ConcertDay` ids, and that is not a shortcut: the model is
    given the concert as a draft-vocabulary document and told to copy names out
    of its `performances`. It never sees an id, so an id is not a thing it
    could return. `verify_rounds` has already refused any round naming a leg
    the draft lacks, so what arrives here matches a real leg by name; the
    review page does the label -> leg lookup at render time.

    Coerced with `str(...).strip()` -- the SAME normalization `verify_rounds`
    compares under, so what is stored is what was checked. It is not cosmetic:
    `parse_completion_response` stringifies only the four TIMESTAMP_FIELDS and
    passes everything else through as PyYAML resolved it, so a leg labelled
    `2026-01-05` arrives here as a `datetime.date`, which is not
    JSON-serializable and would raise inside the flush -- a SQLAlchemyError,
    the one family `run_round_poll` refuses to absorb, so one such label would
    abandon the whole run.

    Empty for anything that is not a list. `verify_rounds` rejects a non-list
    `applies_to` outright, so this cannot happen through the pass; the guard is
    here so the shape of what reaches a NOT NULL JSON column does not depend on
    a check made in another module.
    """
    raw = candidate.data.get(APPLIES_TO_FIELD)
    if not isinstance(raw, list):
        return []
    return [str(leg).strip() for leg in raw]


def _fold_duplicate_keys(fresh, event_id: str, report: PollReport) -> list:
    """`fresh` with any second reading of the SAME key dropped, first one kept.

    ONE reply can carry two rounds that collapse to one `dedupe_key` -- same
    label, same opening time, differing only in `apply_closes_jst` or in which
    legs they name. `classify_proposals` cannot see that: it diffs the proposed
    rounds against the ones the concert HOLDS, and neither of these is held, so
    both survive it.

    Unfolded, both reach `upsert_proposal`, whose SELECT finds the row the
    first one just flushed -- so the second OVERWRITES its closing time and its
    evidence quotes, and both are counted as new, because the row still carries
    today's `first_seen_at`. That is a lost claim, not just a tally that reads
    one too high: the closing date an operator would have reviewed is gone, and
    nothing anywhere says a second reading existed. Hence the reason line: a
    collapse is a discard, and this module names every discard.
    """
    seen: set[str] = set()
    kept = []
    for candidate in fresh:
        key = dedupe_key(candidate.label, proposed_stamp_utc(candidate, OPENS_AT_FIELD))
        if key in seen:
            reason = (
                f"round {candidate.label!r}: a second reading of the same round in "
                "one reply; kept the first"
            )
            log.info("round poll: %s: %s", event_id, reason)
            report.rejections.append(f"{event_id}: {reason}")
            continue
        seen.add(key)
        kept.append(candidate)
    return kept


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
    try:
        draft_text = await concert_export_yaml(session, concert)
    except InvalidRequestError as exc:
        # It opens with `session.refresh(concert, [...])`, which raises this --
        # a SQLAlchemyError -- when the row has gone since `_candidates` loaded
        # it. That is a failed READ, not the poisoned flush the caller re-raises
        # on: the session is perfectly usable and the rest of the run can still
        # be written. Re-raised as something outside the SQLAlchemy tree so the
        # caller's per-concert handler counts it, which is what `_candidates`
        # already does with the identical race one step earlier.
        raise ConcertVanished(f"the concert no longer exists ({exc})") from exc
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

    held = [
        HeldRound(
            label=r.label,
            opens_at_utc=r.opens_at_utc,
            closes_at_utc=r.closes_at_utc,
            results_at_utc=r.results_at_utc,
            payment_deadline_at_utc=r.payment_deadline_at_utc,
        )
        for r in row.rounds
    ]
    # `.changed` -- a held round whose closing/results/payment date moved --
    # is not yet consumed here; a later task decides how it is stored and
    # reported. Only `.fresh` feeds the rest of this function, which keeps
    # this call site's behaviour identical to `new_proposals`' until then.
    fresh = classify_proposals(held, verdict.accepted).fresh
    # Grounded, and the concert already has it. The single most common outcome
    # of a poll and the one most easily left uncounted.
    report.skipped_held += len(verdict.accepted) - len(fresh)
    # BEFORE the dismissed check, not after: a duplicate of a key the owner
    # refused would otherwise count as two dismissals of one round.
    fresh = _fold_duplicate_keys(fresh, row.event_id, report)
    dismissed = await dismissed_keys_for(session, row.concert_id)
    for candidate in fresh:
        opens_at_utc = proposed_stamp_utc(candidate, OPENS_AT_FIELD)
        if dedupe_key(candidate.label, opens_at_utc) in dismissed:
            report.skipped_dismissed += 1
            continue
        kind_warnings: list[str] = []
        kind = _round_kind(
            candidate.data.get("kind"), f"round {candidate.label!r}", kind_warnings
        )
        for warning in kind_warnings:
            # Into `rejections` like every other reason, not only the journal:
            # the round IS stored (as `other`), but "the model named a mechanic
            # this app does not have" is exactly the sort of thing the digest's
            # reader needs to see before trusting the kind on a review screen.
            log.info("round poll: %s: %s", row.event_id, warning)
            report.rejections.append(f"{row.event_id}: {warning}")
        proposal = await upsert_proposal(
            session,
            row.concert_id,
            label=candidate.label,
            kind=kind,
            opens_at_utc=opens_at_utc,
            closes_at_utc=proposed_stamp_utc(candidate, CLOSES_AT_FIELD),
            # All four anchors, through the ONE parser -- see
            # `proposed_stamp_utc`. `verify_rounds` has already grounded each
            # of these against the page and refused the round outright if it
            # could not, so storing two of them and discarding the other two
            # threw away work that was already done and checked.
            results_at_utc=proposed_stamp_utc(candidate, RESULTS_AT_FIELD),
            payment_deadline_at_utc=proposed_stamp_utc(candidate, PAYMENT_AT_FIELD),
            applies_to_labels=_applies_to_labels(candidate),
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
        # Created today, or seen again. `first_seen_at` is the only honest
        # discriminator: `upsert_proposal` deliberately never refreshes it, so
        # a row still carrying today's `now` is one this run inserted.
        if proposal.first_seen_at == now:
            report.new_proposals += 1
        else:
            report.refreshed += 1
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
        except ConcertVanished as exc:
            # Somebody deleted this concert between the candidate list and its
            # read. ONE concert, not the run -- and deliberately NOT stamped.
            # The hazard is real and was reproduced: with the row still in this
            # session's identity map, `record_ladder_polled` issues an UPDATE
            # matching zero rows and raises `StaleDataError` at flush, which is
            # the very SQLAlchemyError this carve-out exists to avoid, arriving
            # by the back door. What PREVENTS it is the `expunge` below, not
            # this `continue`: measured 2026-08-14, either one alone suffices
            # and only removing BOTH raises. Both are kept -- the expunge so
            # nothing later in the run reads the phantom back out of the map,
            # the skip because a concert that no longer exists has nothing to
            # rotate. Skipping cannot reopen the starvation the poll's own
            # cursor fixed: `quiet_ladder_rows` can never return a deleted row.
            log.warning("round poll: %s vanished mid-run", row.event_id)
            report.failed += 1
            report.failures.append(f"{row.event_id}: {exc}")
            session.expunge(concert)
            continue
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
