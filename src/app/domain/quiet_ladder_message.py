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
from datetime import date

from app.domain.discovery_message import DM_CHAR_BUDGET

DM_LIST_LIMIT = 10


@dataclass(frozen=True)
class QuietEntry:
    """One concert whose ladder holds no future anchor.

    The seam between this module and its callers: the scheduler pass and the
    admin route each adapt their own row objects into this shape, so a field
    added here is visibly unhandled in both `build_quiet_ladder_dm` and
    `build_quiet_ladder_block` rather than silently missing from one.
    """

    title: str
    event_id: str
    leg_dates: tuple[date, ...]
    round_labels: tuple[str, ...]
    official_url: str | None
    eventernote_url: str | None


def _dates(entry: QuietEntry) -> str:
    if not entry.leg_dates:
        return "no dates announced"
    return ", ".join(d.strftime("%d %b") for d in entry.leg_dates)


def _rounds(entry: QuietEntry) -> str:
    if not entry.round_labels:
        return "no rounds recorded"
    return ", ".join(entry.round_labels)


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

    The rounds already known are the load-bearing part. Without them the agent
    re-proposes rounds the catalogue already holds, which is the failure this
    block exists to avoid.

    NO budget: a web page has no character cap, and a block that silently
    dropped concerts on the very page the DM points at would leave them
    reachable from nowhere. Same reasoning as build_discovery_dm's `budget=None`
    call site.
    """
    if not entries:
        return ""
    out: list[str] = []
    for e in entries:
        out.append(f"- {e.event_id}: {e.title}")
        out.append(f"  dates: {_dates(e)}")
        out.append(f"  rounds held: {_rounds(e)}")
        if e.official_url:
            out.append(f"  official: {e.official_url}")
        if e.eventernote_url:
            out.append(f"  eventernote: {e.eventernote_url}")
    return "\n".join(out)
