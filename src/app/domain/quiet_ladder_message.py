"""The quiet-ladder digest DM, and the copy block the admin page renders.

Pure (no DB, no Discord), like domain/discovery_message.py, which this mirrors.

TWO functions over ONE dataclass in ONE module. `build_discovery_dm` serves its
DM and its copy block through a single formatter with a `budget` parameter,
under the ruling that a second formatter would drift; the spirit of that ruling
is what the shared `QuietEntry` and the shared module buy here. They stay two
functions because they answer different questions -- the DM is a nudge, the
block is a prompt an agent acts on -- and folding them would either bloat the
DM with URLs or starve the block of them. Forcing one function would either
bloat the DM with URLs it doesn't need, or starve the block of the URLs and
known rounds it exists to carry.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from app.domain.discovery_message import DM_CHAR_BUDGET
from app.domain.timezones import utc_to_jst

DM_LIST_LIMIT = 10


@dataclass(frozen=True)
class QuietRoundInfo:
    """One round a quiet concert already carries, moments and all.

    Moments render in JST, labelled (`YYYY-MM-DD HH:MM JST`), never bare UTC.
    This block is pasted into an agent whose write-back format --
    `yaml_export._jst_str`, the `apply_closes_jst`/`results_jst`/
    `payment_deadline_jst` draft fields it fills in -- is JST and rendered
    with the IDENTICAL `%Y-%m-%d %H:%M` shape. An unlabelled UTC string here
    would be silently indistinguishable from that JST one, and an agent
    transcribing it into a `_jst` field would move a real deadline nine hours
    earlier with nothing raising -- the mis-comparison against the ticket
    page would be the mild failure; landing a wrong deadline in the DB is the
    real one. The `JST` suffix is the point: the one place this app
    legitimately emits UTC, the agent read API, self-labels via
    `isoformat()`, and an unlabelled wall-clock string otherwise has no
    precedent in this codebase.

    A round with no moments at all still needs saying: it distinguishes a
    round the catalogue holds with nothing left to happen (fully resolved)
    from a round whose organiser page never gave a date to begin with.
    """

    label: str
    opens_at_utc: datetime | None
    closes_at_utc: datetime | None
    results_at_utc: datetime | None
    payment_deadline_at_utc: datetime | None


@dataclass(frozen=True)
class QuietEntry:
    """One concert whose ladder holds no future anchor.

    The seam between this module and its callers: `db/quiet_ladders.py`'s
    `quiet_entry_from_row` is the ONE adapter from a `QuietLadder` row into
    this shape, so a field added here is visibly unhandled in both
    `build_quiet_ladder_dm` and `build_quiet_ladder_block` rather than
    silently missing from one.
    """

    title: str
    title_en: str | None
    event_id: str
    leg_dates: tuple[date, ...]
    rounds: tuple[QuietRoundInfo, ...]
    official_url: str | None
    eventernote_url: str | None
    source_url: str | None


def _dates(entry: QuietEntry) -> str:
    if not entry.leg_dates:
        return "no dates announced"
    return ", ".join(d.strftime("%d %b") for d in entry.leg_dates)


def _title(entry: QuietEntry) -> str:
    if entry.title_en and entry.title_en != entry.title:
        return f"{entry.title} / {entry.title_en}"
    return entry.title


def _moment(dt: datetime) -> str:
    return utc_to_jst(dt).strftime("%Y-%m-%d %H:%M") + " JST"


def _round_line(r: QuietRoundInfo) -> str:
    moments = []
    if r.opens_at_utc:
        moments.append(f"opens {_moment(r.opens_at_utc)}")
    if r.closes_at_utc:
        moments.append(f"closes {_moment(r.closes_at_utc)}")
    if r.results_at_utc:
        moments.append(f"results {_moment(r.results_at_utc)}")
    if r.payment_deadline_at_utc:
        moments.append(f"payment due {_moment(r.payment_deadline_at_utc)}")
    if not moments:
        return f"{r.label} (no moments recorded)"
    return f"{r.label} — {', '.join(moments)}"


def _rounds(entry: QuietEntry) -> str:
    if not entry.rounds:
        return "no rounds recorded"
    return "; ".join(_round_line(r) for r in entry.rounds)


def build_quiet_ladder_dm(
    entries: Sequence[QuietEntry],
    total: int,
    *,
    base_url: str,
    budget: int | None = DM_CHAR_BUDGET,
) -> str:
    """The digest, or "" when there is nothing to say.

    Silence is the NORMAL output. This pass runs every tick, so a "nothing
    found" message would be 1,440 DMs a day -- the same mistake discovery's
    daily note warns about, an order of magnitude louder.

    Shrinks the whole message until it fits, like build_discovery_dm, so the
    named concerts and the "+N more" count never disagree. `total` is the real
    backlog, whatever gets rendered.
    """
    if not entries:
        return ""

    kept = list(entries[:DM_LIST_LIMIT])
    while True:
        body = _compose(kept, total, base_url)
        if budget is None or len(body) <= budget or len(kept) <= 1:
            return body
        kept.pop()


def _compose(kept: Sequence[QuietEntry], total: int, base_url: str) -> str:
    head = (
        f"**{total} concert{'' if total == 1 else 's'} went quiet** — no future "
        "deadline left on the ladder, and the show has not happened yet."
    )
    lines = [f"• {e.title} ({_dates(e)})" for e in kept]
    dropped = total - len(kept)
    if dropped > 0:
        lines.append(f"…and {dropped} more.")
    tail = f"Re-check them: {base_url}/admin/quiet-ladders"
    return "\n".join([head, "", *lines, "", tail])


def build_quiet_ladder_block(entries: Sequence[QuietEntry]) -> str:
    """The paste-ready block: everything an agent needs to re-check a ladder.

    The rounds already known, WITH their moments, are the load-bearing part.
    Without them the agent re-proposes rounds the catalogue already holds --
    a bare label is not enough to tell it that a round is fully resolved
    (every moment past) rather than simply undated.

    NO budget: a web page has no character cap, and a block that silently
    dropped concerts on the very page the DM points at would leave them
    reachable from nowhere. Same reasoning as build_discovery_dm's `budget=None`
    call site.
    """
    if not entries:
        return ""
    out: list[str] = []
    for e in entries:
        out.append(f"- {e.event_id}: {_title(e)}")
        out.append(f"  dates: {_dates(e)}")
        out.append(f"  rounds held: {_rounds(e)}")
        if e.official_url:
            out.append(f"  official: {e.official_url}")
        if e.eventernote_url:
            out.append(f"  eventernote: {e.eventernote_url}")
        if e.source_url:
            out.append(f"  source: {e.source_url}")
    return "\n".join(out)
