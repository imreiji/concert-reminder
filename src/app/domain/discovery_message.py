"""Compose the discovery DM.

Pure, and its own module rather than joining eventernote.py: that one is about
READING a source, this is about COMPOSING a message. tags_yaml/tags_diff set
the precedent for splitting on exactly that line.

Two halves, because Discord forces it: text inside a fenced block is NOT
linkified, so the readable list stays clickable and the block stays copyable.
The same content twice is deliberate.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

DM_CHAR_BUDGET = 1900
DM_LIST_LIMIT = 10
MAX_TITLE_CHARS = 70
EVENT_URL = "https://www.eventernote.com/events/{event_id}"
REVIEW_URL = "https://dekimasen.app/admin/discoveries"

PROMPT_HEADER = (
    "Add these to dekimasen.app using the add-concert skill.\n"
    "Group legs of the same tour into ONE draft."
)


@dataclass(frozen=True)
class Lead:
    event_id: str
    title: str
    date: date
    venue: str
    artist: str
    maybe_held: bool


def _clip_title(title: str) -> str:
    """Cap a title's DISPLAY length in the prose list.

    A stray 300-character eventernote title (seen in the wild on multi-leg
    tour listings) would alone blow the whole message past the budget before
    the block-truncation loop even runs -- clip here, unconditionally, rather
    than let one long title crowd out every other lead's prose line.
    """
    if len(title) <= MAX_TITLE_CHARS:
        return title
    return title[: MAX_TITLE_CHARS - 1].rstrip() + "…"


def build_discovery_dm(leads: Sequence[Lead], total: int) -> str:
    """The message, or "" when there is nothing to say.

    Silence is the correct output for a quiet day: a daily "nothing found"
    trains the reader to ignore the channel.
    """
    if not leads:
        return ""

    head = [f"**{total} new lead{'s' if total != 1 else ''} from your artists**", ""]
    by_artist: dict[str, list[Lead]] = {}
    for lead in leads:
        by_artist.setdefault(lead.artist, []).append(lead)

    for artist, group in by_artist.items():
        head.append(f"**{artist}**")
        for lead in group:
            url = EVENT_URL.format(event_id=lead.event_id)
            hint = " *(you may already have this)*" if lead.maybe_held else ""
            title = _clip_title(lead.title)
            head.append(
                f"· [{title}]({url}) — {lead.date:%d %b}, {lead.venue}{hint}"
            )
        head.append("")

    if total > len(leads):
        head.append(f"+{total - len(leads)} more — {REVIEW_URL}")
        head.append("")

    prose = "\n".join(head)
    block_lines = [
        f"{EVENT_URL.format(event_id=lead.event_id)}  {lead.date:%Y-%m-%d}  {lead.venue}"
        for lead in leads
    ]

    # The block yields FIRST. Announcing a lead in the prose above while
    # silently dropping it from the copy block is the quiet kind of wrong, so
    # when lines go, the block says so.
    kept = list(block_lines)
    while kept:
        dropped = len(block_lines) - len(kept)
        body = _assemble(prose, kept, dropped)
        if len(body) <= DM_CHAR_BUDGET:
            return body
        kept.pop()
    return _assemble(prose, [], len(block_lines))


def _assemble(prose: str, kept: Sequence[str], dropped: int) -> str:
    lines = [PROMPT_HEADER, ""]
    lines.extend(kept)
    if dropped:
        lines.append(f"# {dropped} more not shown -- see {REVIEW_URL}")
    return f"{prose}```\n" + "\n".join(lines) + "\n```"
