"""`PendingDraft` rows: the multi-draft import batch.

The ONE place this app keeps step state, and the module docstring in
`web/routes/imports.py` explains why that is not a contradiction -- a batch of
fifty concerts each needing a human-read preview is a work queue, not a wizard
step. `import_commit` remains the only write path into `concerts`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.discovery_events import dismiss_lead
from app.db.models import (
    ConcertDay,
    DiscoveredEvent,
    DiscoveryState,
    FetchDomain,
    PendingDraft,
    Tag,
    TriageRun,
)
from app.domain.prune_list import PruneList
from app.domain.timezones import utc_to_jst
from app.domain.types import (
    DismissReason,
)
from app.domain.yaml_import import DraftBatch, DraftError, parse_draft
from app.fetching import _normalize_host

# ── Pending drafts (multi-draft triage) ───────────────────────────────────
#
# See PendingDraft's own docstring for why this is a work batch and not the
# step state this app otherwise avoids: an agent can hand back fifty to a
# hundred parsed drafts in one paste, and reading each one's preview is not
# one sitting.


async def create_pending_drafts(
    session: AsyncSession, batch: DraftBatch, created_by: int
) -> list[PendingDraft]:
    """Persist every parsed document of a paste as its own triage row.

    One row per `ParsedDraft`, carrying that document's own verbatim text and
    its already-parsed title. `batch.errors` never reaches here -- there is
    no preview to triage for a document that failed to parse at all.
    """
    rows = [
        PendingDraft(draft_text=draft.text, title=draft.parsed.title, created_by=created_by)
        for draft in batch.drafts
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def pending_drafts(session: AsyncSession, user_id: int) -> list[PendingDraft]:
    """The still-open rows from `user_id`'s own pastes -- neither committed
    nor discarded. Scoped to the pasting user: two editors triaging their own
    batches at once is the expected case, not an exotic one."""
    rows = await session.execute(
        select(PendingDraft)
        .where(
            PendingDraft.created_by == user_id,
            PendingDraft.committed_at.is_(None),
            PendingDraft.discarded_at.is_(None),
        )
        .order_by(PendingDraft.id)
    )
    return list(rows.scalars())


async def mark_pending_committed(
    session: AsyncSession, pending_id: int, concert_id: int, now: datetime
) -> bool:
    """Stamp a row committed once its preview has produced a real concert.

    False, without re-stamping, when the row is unknown or already committed
    -- the same double-submit rule `dismiss_lead` follows, so a duplicated
    request can never silently rewrite which concert a draft claims to have
    produced.
    """
    row = await session.get(PendingDraft, pending_id)
    if row is None or row.committed_at is not None:
        return False
    row.committed_at = now
    row.concert_id = concert_id
    await session.flush()
    return True


async def discard_pending_draft(session: AsyncSession, pending_id: int, now: datetime) -> bool:
    """Stamp a row discarded -- an editor's "not this one", read past the
    preview. False when the row is unknown or already resolved either way,
    for the same double-submit reason as `mark_pending_committed`."""
    row = await session.get(PendingDraft, pending_id)
    if row is None or row.committed_at is not None or row.discarded_at is not None:
        return False
    row.discarded_at = now
    await session.flush()
    return True


@dataclass(frozen=True)
class PlannedDismissal:
    """One lead a prune list names.

    `reason` is the FILE's own say-so -- not necessarily what the row ends up
    holding. For `to_dismiss` it is exactly what apply_prune will write; for
    `already` it is only what the file asked for, since apply_prune leaves an
    already-dismissed row's own reason untouched (see apply_prune).

    `stored_reason` is what the row ALREADY carries (None if it predates the
    column, or if the lead isn't dismissed yet). For `already` this is the
    truth a screen owes a human: "file says stage, already dismissed as
    release" is the disagreement the `dismiss_reason` column exists to make
    measurable, and dropping it here would throw that away at the one place
    it is visible before nothing happens.

    `first_seen_via_tag_id` rides along purely for display -- the artist tag
    that surfaced the lead, the same column /admin/discoveries reads via its
    own `_artist_names` helper. Copying it here costs no extra query (the row
    is already fully loaded in plan_prune), and a screen asking a human to
    approve 300 permanent dismissals is far more reviewable with "which
    artist" on every line than without it. Defaulted so the several
    hand-built PrunePlan/PlannedDismissal fixtures in this file's own test
    suite, written before this field existed, still construct.

    `source`/`date_is_deadline` mirror the same two `DiscoveredEvent` columns
    the review page (/admin/discoveries) and the DM digest already read --
    the prune plan is a third surface over the same rows, and a calendar
    lead here has no Eventernote page either: the template must gate its
    events link on `source == "eventernote"` exactly as admin_discoveries.html
    does, and the FEED's own label belongs where the artist name goes when
    there is no `first_seen_via_tag_id` to look up. Both defaulted for the
    same fixture-compatibility reason as `first_seen_via_tag_id`."""

    lead_id: int
    event_id: str
    title: str
    event_date: date
    reason: DismissReason
    stored_reason: DismissReason | None
    first_seen_via_tag_id: int | None = None
    source: str = "eventernote"
    date_is_deadline: bool = False


@dataclass(frozen=True)
class PrunePlan:
    """What a prune list WOULD do, before any of it is written.

    Four buckets, all worth showing a human before they approve anything:
    - to_dismiss: an open lead this file would dismiss.
    - unknown: an event_id matching no DiscoveredEvent row at all -- usually
      a stale file (a mistyped id, or a lead that no longer exists).
    - already: a lead this file names that is already dismissed -- usually a
      re-paste of an earlier file. apply_prune leaves these rows exactly as
      it found them.
    - catalogued: a lead this file names that has since become a concert
      (`concert_id` set) -- the file predates that, and dismissing it would
      stamp a `dismiss_reason` on a row the catalogue already has, polluting
      `dismissed_reason_counts` (the classifier scorecard the column exists
      for) with a judgment about something that is no longer an open lead at
      all. Checked BEFORE `dismissed_at`: a lead that is somehow both
      catalogued and dismissed is reported as catalogued, since "this is now
      a real concert" is the more useful thing to tell the operator.
      `open_leads` (the main /admin/discoveries listing) already excludes
      these two ways -- `concert_id IS NULL` and `dismissed_at IS NULL` -- so
      this mirrors that same pair of exits rather than inventing a third.

    `warnings` carries `PruneList.warnings` forward (currently just "listed
    twice under the same reason") so the one tell of a sloppy agent file is
    not silently dropped between parsing and rendering. Defaulted to `()` for
    the same fixture-compatibility reason `PlannedDismissal.first_seen_via_
    tag_id` is.
    """

    to_dismiss: tuple[PlannedDismissal, ...]
    unknown: tuple[str, ...]
    already: tuple[PlannedDismissal, ...]
    catalogued: tuple[PlannedDismissal, ...] = ()
    warnings: tuple[str, ...] = ()


async def plan_prune(session: AsyncSession, prune: PruneList) -> PrunePlan:
    """Join a parsed prune list against the catalogue -- ONE query however
    many entries the file names (`source_event_id IN (...)`), not a
    query per entry: 300 entries must not be 300 round trips.

    Sorts every entry into exactly one of PrunePlan's four buckets and
    writes NOTHING -- looking is not doing, and this plan is rendered before
    a human has agreed to any of it.
    """
    ids = [entry.event_id for entry in prune.entries]
    rows = (await session.execute(
        select(DiscoveredEvent).where(DiscoveredEvent.source_event_id.in_(ids))
    )).scalars()
    by_event_id = {row.source_event_id: row for row in rows}

    to_dismiss: list[PlannedDismissal] = []
    unknown: list[str] = []
    already: list[PlannedDismissal] = []
    catalogued: list[PlannedDismissal] = []

    for entry in prune.entries:
        row = by_event_id.get(entry.event_id)
        if row is None:
            unknown.append(entry.event_id)
            continue
        planned = PlannedDismissal(
            lead_id=row.id, event_id=entry.event_id, title=row.title,
            event_date=row.event_date, reason=entry.reason,
            stored_reason=(
                DismissReason(row.dismiss_reason) if row.dismiss_reason else None
            ),
            first_seen_via_tag_id=row.first_seen_via_tag_id,
            source=row.source, date_is_deadline=row.date_is_deadline,
        )
        if row.concert_id is not None:
            catalogued.append(planned)
        elif row.dismissed_at is not None:
            already.append(planned)
        else:
            to_dismiss.append(planned)

    return PrunePlan(
        to_dismiss=tuple(to_dismiss), unknown=tuple(unknown), already=tuple(already),
        catalogued=tuple(catalogued), warnings=tuple(prune.warnings),
    )


async def apply_prune(session: AsyncSession, plan: PrunePlan, now: datetime) -> int:
    """Write a plan built from a FRESH parse -- one dismiss_lead call per
    lead in `to_dismiss` (never a bulk UPDATE; dismiss_lead is the single
    writer), returning how many rows were actually written.

    Does not re-derive the plan and does not touch `unknown`/`already`/
    `catalogued` at all: plan_prune already sorted those out, and dismiss_lead
    itself refuses an already-dismissed row, so even a stale plan racing a
    second apply cannot re-stamp one.

    FLUSHES per dismiss_lead call, never commits -- the caller owns the
    transaction and its outcome. That means a raise partway through the loop
    (an unexpected DB error, say) rolls the WHOLE batch back via the caller's
    session rather than leaving some leads dismissed and others not: nothing
    here durably survives until the caller commits.
    """
    written = 0
    for planned in plan.to_dismiss:
        if await dismiss_lead(session, planned.lead_id, now, planned.reason):
            written += 1
    return written


@dataclass(frozen=True)
class PruneReport:
    """What POST /admin/discoveries/prune/apply actually did -- a SNAPSHOT,
    not a plan, and deliberately a different type from PrunePlan (mirrors
    TagImportReport vs. tags_diff's ImportPlan in routes/admin.py, for the
    same reason: rendering the PLAN again after applying would show a
    submit button over lead ids that are now gone, and a second press would
    report "Dismissed 0 leads" -- reading exactly like the first press
    silently failed, when in fact it worked. `to_dismiss` deliberately has
    no counterpart here; `unknown`/`already`/`catalogued`/`warnings` carry
    forward for display since they are informational rather than
    actionable."""

    dismissed: int
    unknown: tuple[str, ...]
    already: tuple[PlannedDismissal, ...]
    catalogued: tuple[PlannedDismissal, ...]
    warnings: tuple[str, ...]


async def mark_leads_announced(
    session: AsyncSession, lead_ids: Sequence[int], now: datetime
) -> None:
    """Stamp leads as announced so the next sweep does not repeat them.

    Written through the ORM rather than a bulk UPDATE on purpose: the caller
    is holding these very rows (record_discovered just returned them), and a
    bulk statement would leave those instances claiming announced_at is None.
    """
    ids = list(lead_ids)
    if not ids:
        return
    rows = (await session.execute(
        select(DiscoveredEvent).where(DiscoveredEvent.id.in_(ids))
    )).scalars()
    for row in rows:
        row.announced_at = now
    await session.flush()


async def leads_matching_existing_legs(
    session: AsyncSession, leads: Sequence[DiscoveredEvent]
) -> set[int]:
    """The HINT set: ids of leads landing on the same date and venue as an
    existing leg.

    NOT a suppression -- see the section note. Two shows on one day at one
    venue is the normal shape of a Japanese concert day, so this can only ever
    say "you may already have this", and the caller must keep every one of
    these leads in its list.
    """
    candidates = [lead for lead in leads if lead.venue]
    if not candidates:
        return set()

    # DiscoveredEvent.event_date is a JST calendar date; ConcertDay.starts_at_utc
    # is an aware UTC instant, and the two only agree after a conversion (a leg
    # at 2026-11-14 16:00 UTC is 2026-11-15 in JST). The SQL window is
    # deliberately one day loose on each side -- it only has to be a superset;
    # utc_to_jst settles the actual date per leg below.
    dates = {lead.event_date for lead in candidates}
    midnight = datetime.min.time()
    lo = datetime.combine(min(dates), midnight, tzinfo=UTC) - timedelta(days=1)
    hi = datetime.combine(max(dates), midnight, tzinfo=UTC) + timedelta(days=1)

    rows = (await session.execute(
        select(ConcertDay.starts_at_utc, Tag.name, Tag.name_en)
        .join(Tag, Tag.id == ConcertDay.venue_tag_id)
        .where(ConcertDay.starts_at_utc >= lo, ConcertDay.starts_at_utc <= hi)
    )).all()

    # Both the Japanese name and the English variant count: Eventernote writes
    # whichever the venue is commonly listed under.
    seen: set[tuple[date, str]] = set()
    for starts_at_utc, name, name_en in rows:
        jst_date = utc_to_jst(starts_at_utc).date()
        for label in (name, name_en):
            if label:
                seen.add((jst_date, label.casefold()))

    return {
        lead.id for lead in candidates
        if (lead.event_date, lead.venue.casefold()) in seen
    }


# One sweep a day. The scheduler ticks every 60s, so the cadence has to live
# somewhere durable: in-memory state would re-sweep on every restart, and a
# sweep ends in a DM.
DISCOVERY_INTERVAL = timedelta(hours=24)
DISCOVERY_STATE_ID = 1


async def discovery_due(session: AsyncSession, now: datetime) -> bool:
    """Has it been a day? True also when the sweep has never run at all."""
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    if state is None or state.last_run_at is None:
        return True
    return now - state.last_run_at >= DISCOVERY_INTERVAL


async def stamp_discovery_run(
    session: AsyncSession,
    now: datetime,
    *,
    fetched: int | None = None,
    failed: int | None = None,
    truncated: bool | None = None,
) -> None:
    """Start the 24h clock, and record how the sweep went.

    Called on EVERY sweep, including one that found nothing -- a quiet day that
    left the clock unset would re-sweep on the very next tick, which is 86
    third-party fetches a minute.

    The counts are ALWAYS assigned, defaults included. A caller with no report
    to give (scheduler.loop re-stamping after a sweep raised) means the counts
    are unknown, and unknown must clear them: leaving yesterday's 74/0 beside
    today's timestamp would read as a healthy sweep on the day the sweep died.
    The sweep CURSOR is deliberately not here for exactly that reason -- see
    `set_sweep_cursor`.

    A pending manual request is cleared HERE, and here specifically, because
    this is the one call every sweep reaches on every exit: run_sweep's own
    `finally`, and scheduler.loop's re-stamp after a sweep raised and its
    rollback undid that finally. A request that survived a failure would re-run
    the sweep 60 seconds later and fail the same way forever -- the
    86-fetches-a-minute trap the 24h clock exists to prevent. It obeys
    last_run_at's rule ("a sweep happened"), not the cursor's ("progress
    accumulated"), which is why it is a keyword-less unconditional write and
    not its own writer.
    """
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    if state is None:
        state = DiscoveryState(id=DISCOVERY_STATE_ID)
        session.add(state)
    state.last_run_at = now
    state.last_fetched = fetched
    state.last_failed = failed
    state.last_truncated = truncated
    state.sweep_requested_at = None
    await session.flush()


async def request_sweep(session: AsyncSession, now: datetime) -> None:
    """Ask the scheduler to sweep on its next tick.

    THE button does not run the sweep. A sweep costs up to
    SWEEP_BUDGET_SECONDS, which no HTTP request may hold, and an inline run
    would be a second execution path for something whose whole design is a
    single carefully bounded one. This writes a request; scheduler.loop picks
    it up within TICK_SECONDS and calls the same `run_sweep` the daily job
    calls.

    Unconditional rather than set-if-absent: the stored instant is "when an
    operator last asked", and the only reader is a boolean. (A second request
    arriving while a sweep is already in flight is therefore cleared by that
    sweep's stamp -- a ~4 minute window in which one click is absorbed. Left
    alone deliberately: the page then shows no request pending, so the operator
    can simply press it again, and the alternative is threading the exact
    timestamp through both stamping paths to serve a button.)
    """
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    if state is None:
        state = DiscoveryState(id=DISCOVERY_STATE_ID)
        session.add(state)
    state.sweep_requested_at = now
    await session.flush()


async def sweep_requested(session: AsyncSession) -> bool:
    """Is a manual sweep waiting for the next tick?

    A named predicate beside `discovery_due` because scheduler.loop asks the
    two together and they are NOT the same question: `discovery_due` is the
    24h clock and is only consulted when the flag is on, while a manual request
    runs the sweep whichever way the flag is set.
    """
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    return state is not None and state.sweep_requested_at is not None


async def set_sweep_cursor(session: AsyncSession, tag_id: int | None) -> None:
    """Where the next sweep starts. None means the head of the list.

    Its OWN writer rather than a keyword on `stamp_discovery_run`, because the
    two obey opposite rules and folding them together would quietly break this
    one. The counts describe a single sweep, so "no report" must clear them;
    the cursor is accumulated PROGRESS through the artist list, so "no report"
    must leave it alone. scheduler.loop re-stamps after a sweep raised -- and if
    that re-stamp reset the cursor, a run of failures would pin the sweep at the
    head of the list forever, which is precisely the starvation the cursor
    exists to prevent.
    """
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    if state is None:
        state = DiscoveryState(id=DISCOVERY_STATE_ID)
        session.add(state)
    state.sweep_cursor_tag_id = tag_id
    await session.flush()


async def discovery_status(session: AsyncSession) -> DiscoveryState | None:
    """The last sweep's record, or None before the first one ever runs.

    Exists so /admin/discoveries can answer "is the sweep still working?" --
    without it a broken sweep and a quiet one both render an empty table, which
    is how a site redesign becomes a silent no-op that looks like good news.
    """
    return await session.get(DiscoveryState, DISCOVERY_STATE_ID)


# --- AI triage (/admin/discoveries/triage) ---


async def request_triage(
    session: AsyncSession, now: datetime, requested_by: int, kind: str = "classify"
) -> TriageRun:
    """Ask for a run of `kind`, or hand back the one of that kind already waiting.

    Idempotent PER KIND, not globally: the classify button and the completion
    button are two different asks, and a completion request arriving while a
    classify run is still queued must make its own row rather than silently
    returning -- and re-rendering as -- the other button's pending run.
    """
    pending = await pending_triage_run(session, kind=kind)
    if pending is not None:
        return pending
    run = TriageRun(requested_at=now, requested_by=requested_by, kind=kind)
    session.add(run)
    await session.flush()
    return run


async def pending_triage_run(
    session: AsyncSession, kind: str | None = None
) -> TriageRun | None:
    """The oldest run still waiting to be picked up, or None.

    `kind=None` means any kind, which is what the SCHEDULER asks: one tick
    runs one run, so the two kinds serialize against each other by
    construction. A button asks for its own kind, to render its own
    disabled state.
    """
    query = select(TriageRun).where(TriageRun.status == "requested")
    if kind is not None:
        query = query.where(TriageRun.kind == kind)
    return (await session.execute(
        query.order_by(TriageRun.id).limit(1)
    )).scalar_one_or_none()


async def latest_triage_run(
    session: AsyncSession, kind: str | None = None
) -> TriageRun | None:
    """The most recent run of `kind` (any status), for an admin page's
    "last result".

    `kind=None` means any kind -- there is no caller that actually wants
    that today; every admin page reads its OWN kind's history, the same
    reason `pending_triage_run` takes the same parameter (a completion run's
    classify columns are NULL, and vice versa, so a kind-blind read renders
    the wrong run's numbers under the other button's label).
    """
    query = select(TriageRun).order_by(TriageRun.id.desc())
    if kind is not None:
        query = query.where(TriageRun.kind == kind)
    return (await session.execute(query.limit(1))).scalar_one_or_none()


async def get_triage_run(session: AsyncSession, run_id: int) -> TriageRun | None:
    return await session.get(TriageRun, run_id)


async def mark_triage_failed(
    session: AsyncSession, run_id: int, now: datetime, note: str
) -> None:
    """Record that a run died, from a CLEANED transaction.

    Re-fetches by id rather than taking a TriageRun instance -- mirrors
    stamp_discovery_run's re-stamp-after-rollback shape: the caller here is
    scheduler.loop, invoked after the poisoned session that ran triage was
    rolled back, so any object it was holding is detached and writing through
    it would raise or silently no-op. `note` is truncated to fit the column
    (see TriageRun.error); a message that already fits passes through whole.
    """
    run = await session.get(TriageRun, run_id)
    if run is None:
        return
    run.status = "failed"
    run.finished_at = now
    run.error = note[:300]
    await session.flush()


async def api_draft_rows(
    session: AsyncSession, user_id: int, *, limit: int = 200, offset: int = 0
) -> tuple[list[dict], int]:
    """This user's open drafts, plus the pre-paging total.

    Built on `pending_drafts`, which is already scoped to the pasting user and
    already ordered by id -- unique, so totally ordered and safe to page.

    `has_rounds` is a REAL PARSE through `parse_draft`, not a string sniff --
    it asks the exact same question `completion_candidates` already asks
    ("does this draft have `rounds`") through the exact same function, so the
    two never disagree. An earlier version of this endpoint checked for the
    literal substring `"rounds: []"`, which is wrong in at least three
    directions this parse gets right: a draft with NO `rounds:` key at all
    (an agent's freshly-authored skeleton, before any completion pass) does
    not contain that substring either, so the sniff reported `has_rounds=True`
    for a draft that has never had a round in it; `rounds: []` written with
    different whitespace (`rounds:  []`, or spread over two lines) defeats a
    literal match; and a real, non-empty `rounds:` list never contains that
    substring, so the sniff was RIGHT there only by coincidence of never being
    asked to distinguish it from the empty case in the one place it was wrong.
    A row whose text no longer parses at all (`DraftError` -- YAML rot, or a
    hand-edited row that broke) reports `has_rounds=False` rather than 500ing
    the whole list: there is no rounds list to report either way, and this is
    the same "unreadable" bucket `completion_candidates` silently skips past.
    The detail endpoint's `draft_text` is where an agent would see why.
    """
    rows = await pending_drafts(session, user_id)
    total = len(rows)
    out = []
    for r in rows[offset : offset + limit]:
        try:
            has_rounds = bool(parse_draft(r.draft_text).rounds)
        except DraftError:
            has_rounds = False
        out.append(
            {
                "id": r.id,
                "title": r.title,
                "created_at": r.created_at.isoformat(),
                "has_rounds": has_rounds,
                "has_completion": bool(r.completion_yaml),
            }
        )
    return out, total


async def api_draft_detail(session: AsyncSession, draft_id: int, user_id: int) -> dict | None:
    """One draft's full text AND its completion evidence.

    Both together is the point: this is the iteration loop, where an agent
    reads its own draft alongside the evidence/rejection result rather than
    having a human relay either.

    None for another user's draft, which the caller renders as 404 -- invariant
    5's ownership rule. A 403 would confirm the row exists.
    """
    row = await session.get(PendingDraft, draft_id)
    if row is None or row.created_by != user_id:
        return None
    return {
        "id": row.id,
        "title": row.title,
        "created_at": row.created_at.isoformat(),
        "committed_at": row.committed_at.isoformat() if row.committed_at else None,
        "discarded_at": row.discarded_at.isoformat() if row.discarded_at else None,
        "draft_text": row.draft_text,
        "completion_yaml": row.completion_yaml,
    }


async def pending_draft_texts(session: AsyncSession) -> list[str]:
    """The verbatim text of every still-open PendingDraft.

    The duplicate-containment input for the triage runner: before an LLM
    proposes a new concert draft, it needs to know what an editor already has
    sitting in the pending-review batch (invariant: import_commit is the only
    write path, but a triage pass proposing a draft nobody asked for a second
    time wastes a token budget and an editor's attention alike). Committed and
    discarded rows are done -- see PendingDraft's own docstring -- so neither
    belongs in what is still open.
    """
    rows = (await session.execute(
        select(PendingDraft.draft_text)
        .where(PendingDraft.committed_at.is_(None), PendingDraft.discarded_at.is_(None))
    )).scalars().all()
    return list(rows)


# -- AI draft completion (phase 2) ----------------------------------------


async def note_fetch_domain(
    session: AsyncSession, host: str, url: str, now: datetime
) -> FetchDomain:
    """Record that something wanted to fetch `host`, and hand back its row.

    `host` MUST already be a bare hostname -- no scheme, no path, no port, no
    userinfo -- the exact shape `urlparse(url).hostname` produces, which is
    how every real caller gets one: `ApprovedPublicHosts.check_async`
    (`app/fetching.py`) extracts a host from a URL the identical way before
    ever calling `is_approved`. This is checked, not assumed: verified
    empirically that `_normalize_host` alone does NOT reject the shapes a
    careless caller might pass instead -- `"https://eplus.jp/a"` and
    `"eplus.jp:443"` both come back changed only in case, not refused. Storing
    either would fail CLOSED but SILENTLY: `approved_fetch_hosts()` could
    never match it against a real lookup, so an admin who thinks they just
    approved a host would find the real host stays blocked forever with no
    error anywhere. A loud `ValueError` at this table's one write path is far
    cheaper than that.

    The SINGLE write path that creates a `FetchDomain`, and the only place the
    host is normalized -- two spellings of one host must never become two rows
    with two different verdicts. Normalization reuses `fetching._normalize_host`
    rather than a second `.strip().lower()`: that is the EXACT function
    `ApprovedPublicHosts.check_async` runs on a host before calling
    `is_approved`, so this store and that read must agree byte-for-byte or an
    approval recorded here silently fails to match a lookup done there (or
    vice versa) -- see task 5's contract note. A host `_normalize_host` cannot
    encode (a label over 63 characters, or otherwise unencodable IDNA) falls
    back to a plain strip+lower: such a host can never pass the fetch guard
    either way (it raises `HostNotAllowed` there for the identical reason), so
    that fallback only affects what the approval screen displays for an
    already-doomed host, never a future lookup match.

    An existing row (pending, approved OR declined) comes back untouched:
    re-noting must never reopen a decision a human already made, and must
    never overwrite `first_seen_url`, which is what the approver was actually
    told about.
    """
    if not host.strip() or any(c in host for c in "/:@"):
        raise ValueError(
            f"{host!r} is not a bare hostname -- pass urlparse(url).hostname, "
            "never the URL itself or a host:port string"
        )
    try:
        host = _normalize_host(host)
    except ValueError:
        host = host.strip().lower()
    existing = (await session.execute(
        select(FetchDomain).where(FetchDomain.host == host)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    row = FetchDomain(host=host, first_seen_at=now, first_seen_url=url[:1000])
    session.add(row)
    await session.flush()
    return row


async def approved_fetch_hosts(session: AsyncSession) -> set[str]:
    """Every host an admin has approved. Loaded once per run and closed over
    by the fetch policy, so a run makes one query rather than one per draft."""
    rows = (await session.execute(
        select(FetchDomain.host).where(FetchDomain.approved_at.is_not(None))
    )).scalars().all()
    return set(rows)


async def fetch_domain_rows(session: AsyncSession) -> list[FetchDomain]:
    """Every recorded host, pending first and newest first within that --
    the approval screen's whole content."""
    rows = await session.execute(
        select(FetchDomain).order_by(
            FetchDomain.approved_at.is_not(None) | FetchDomain.declined_at.is_not(None),
            FetchDomain.first_seen_at.desc(),
        )
    )
    return list(rows.scalars())


async def pending_fetch_domain_count(session: AsyncSession) -> int:
    """How many hosts are waiting on a human. Drives the callout on the
    pending-drafts page: a blocked completion run has to be discoverable from
    where the button was pressed, not only from an admin page nobody opened."""
    return (await session.execute(
        select(func.count())
        .select_from(FetchDomain)
        .where(FetchDomain.approved_at.is_(None), FetchDomain.declined_at.is_(None))
    )).scalar_one()


async def decide_fetch_domain(
    session: AsyncSession, domain_id: int, approve: bool, now: datetime, decided_by: int
) -> bool:
    """Approve or decline one host. False when it is unknown or already
    decided -- the same double-submit rule `discard_pending_draft` follows, so
    a refreshed POST cannot flip a verdict."""
    row = await session.get(FetchDomain, domain_id)
    if row is None or row.approved_at is not None or row.declined_at is not None:
        return False
    if approve:
        row.approved_at = now
    else:
        row.declined_at = now
    row.decided_by = decided_by
    await session.flush()
    return True


async def completion_candidates(session: AsyncSession, user_id: int) -> list[PendingDraft]:
    """This user's open drafts that an AI completion pass should try.

    Three filters, and the third is the containment rule: still open, no rounds
    yet, and not already attempted. `completion_yaml` is written only when an
    LLM call actually happened, so a draft skipped for a missing URL, an
    unapproved domain or a dead fetch stays a candidate and the next press
    retries it once the reason is fixed.

    "No rounds yet" is decided by parsing, because that is where the answer
    lives -- the pending list already re-parses every row for its counts, and
    caching a flag at write time would freeze today's parser against
    tomorrow's (PendingDraft's own reason for storing text, not a parse).
    """
    rows = await session.execute(
        select(PendingDraft)
        .where(
            PendingDraft.created_by == user_id,
            PendingDraft.committed_at.is_(None),
            PendingDraft.discarded_at.is_(None),
            PendingDraft.completion_yaml == "",
        )
        .order_by(PendingDraft.id)
    )
    candidates = []
    for row in rows.scalars():
        try:
            if not parse_draft(row.draft_text).rounds:
                candidates.append(row)
        except DraftError:
            # A row that no longer parses cannot be completed, and is already
            # surfaced as "couldn't be re-read" on the list. Skipping it here
            # keeps one unreadable row from costing the batch.
            continue
    return candidates
