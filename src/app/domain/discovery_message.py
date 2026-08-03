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
# The most leads one DM ever names. Applied INSIDE build_discovery_dm rather
# than by the caller slicing first: the caller does not know how many will
# actually fit, and the two halves have to agree on the answer.
DM_LIST_LIMIT = 10
MAX_TITLE_CHARS = 70
MAX_VENUE_CHARS = 40
MAX_ARTIST_CHARS = 40
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
    # True when `date` is an APPLICATION DEADLINE, not a performance date (the
    # imas ticket calendar and the LL-Fans deadline subs -- see
    # DiscoveredEvent.date_is_deadline). Rendered as an ADDITIVE "申込締切 "
    # prefix on the date, never a reordering, in both the prose line and the
    # copy-block line -- the triage skill parses the block by field position.
    deadline: bool = False
    # Which pipeline surfaced this lead: "eventernote", or a CalendarFeed.key.
    # A calendar lead has no Eventernote page to link -- EVENT_URL is an
    # Eventernote path, and a calendar lead's event_id is a namespaced
    # "<feed key>:<uid>", not an Eventernote numeric id -- so both halves
    # below must gate the link/URL on this rather than building one blindly.
    source: str = "eventernote"


def _clip(text: str, n: int) -> str:
    """Cap a free-text field's DISPLAY length in the prose list.

    `title`, `venue` and `artist` are all unbounded scraped free text (see
    `domain/eventernote.py`'s `_venue`, which gets its text the same way the
    title does) -- any one of them running long would blow the whole message
    past the budget before the block-truncation loop even runs. Clip each
    one here, unconditionally, rather than let one long field crowd out
    every other lead's prose line.
    """
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def build_discovery_dm(
    leads: Sequence[Lead], total: int, *, budget: int | None = DM_CHAR_BUDGET
) -> str:
    """The message, or "" when there is nothing to say.

    Silence is the correct output for a quiet day: a daily "nothing found"
    trains the reader to ignore the channel.

    `budget` is the CHANNEL's character cap, not the message's, which is why it
    is a parameter and not the constant it used to be. Discord's is
    DM_CHAR_BUDGET and stays the default, so the DM path is unchanged; the
    /admin/discoveries copy block passes None, because a web page has no cap and
    a block that silently drops leads on the very page the DM's "+N more" line
    points to would leave those leads reachable from nowhere. ONE formatter
    either way -- a second one would drift, and the block's job (a prompt an
    agent can act on) is identical in both places.

    THE TWO HALVES ALWAYS NAME THE SAME LEADS (owner ruling, 2026-07-31).
    Trimming the block alone shipped first and it inverted the message's own
    priorities: the prose half is DUPLICATE content, the block is the
    deliverable ("paste this to an agent"), and measured against real
    Hasunosora-length Japanese titles ten leads in prose left THREE in the
    block. So the number of leads is chosen once, by shrinking the whole
    message until it fits, and both halves render exactly that many. The "+N
    more" count and the block's own "N more not shown" line are both against
    `total`, so they stay honest about the real backlog whatever gets rendered.
    """
    if not leads:
        return ""

    if budget is None:
        # Unbounded: nothing to trade off, so none of the shrinking below runs
        # at all. Kept as its own branch rather than a huge int, so an uncapped
        # caller is obviously total instead of merely unlikely to trip.
        return _compose(leads, total)

    # DM_LIST_LIMIT first, and it is a real cap rather than a comment: past ten
    # a digest stops being scannable whatever the character budget allows.
    kept = list(leads[:DM_LIST_LIMIT])
    while kept:
        body = _compose(kept, total)
        if len(body) <= budget:
            return body
        kept.pop()

    # Nothing listable fits. Say how many there are and where they live -- a
    # short message that is entirely "+N more" still gets the maintainer to the
    # review page, which is where all of them are anyway.
    body = _compose([], total)
    if len(body) <= budget:
        return body

    # HARD FLOOR, and only reachable on an absurdly small budget now that the
    # prose shrinks alongside the block. Going over the budget is worse than
    # truncating: past Discord's real 2000 cap, discord.py raises and the WHOLE
    # DM is lost, so the maintainer hears nothing that day from the one code
    # path whose job is to guarantee that can't happen.
    prose, _ = _prose_and_block([], total)
    return _hard_truncate(prose, total, budget)


def _compose(leads: Sequence[Lead], total: int) -> str:
    """One message over ONE set of leads -- both halves, same leads."""
    prose, block_lines = _prose_and_block(leads, total)
    return _assemble(prose, block_lines, max(total - len(leads), 0))


def _prose_and_block(leads: Sequence[Lead], total: int) -> tuple[str, list[str]]:
    """The readable half and the copyable half, over the same leads.

    Split out from the assembly so the shrink loop can build a whole candidate
    message per size and measure it, rather than trimming one half against a
    prose half that was already fixed.
    """
    head = [
        f"**{total} new lead{'s' if total != 1 else ''} from your artists and feeds**",
        "",
    ]
    by_artist: dict[str, list[Lead]] = {}
    for lead in leads:
        by_artist.setdefault(lead.artist, []).append(lead)

    for artist, group in by_artist.items():
        head.append(f"**{_clip(artist, MAX_ARTIST_CHARS)}**")
        for lead in group:
            hint = " *(you may already have this)*" if lead.maybe_held else ""
            title = _clip(lead.title, MAX_TITLE_CHARS)
            venue = _clip(lead.venue, MAX_VENUE_CHARS)
            date_str = f"{'申込締切 ' if lead.deadline else ''}{lead.date:%d %b}"
            if lead.source == "eventernote":
                url = EVENT_URL.format(event_id=lead.event_id)
                head.append(f"· [{title}]({url}) — {date_str}, {venue}{hint}")
            else:
                # No page to link -- see Lead.source's docstring.
                head.append(f"· {title} — {date_str}, {venue}{hint}")
        head.append("")

    if total > len(leads):
        head.append(f"+{total - len(leads)} more — {REVIEW_URL}")
        head.append("")

    block_lines = []
    for lead in leads:
        date_str = f"{'申込締切 ' if lead.deadline else ''}{lead.date:%Y-%m-%d}"
        location = (
            EVENT_URL.format(event_id=lead.event_id)
            if lead.source == "eventernote"
            else lead.event_id
        )
        block_lines.append(f"{location}  {date_str}  {lead.venue}")
    return "\n".join(head), block_lines


def _assemble(prose: str, kept: Sequence[str], dropped: int) -> str:
    lines = [PROMPT_HEADER, ""]
    lines.extend(kept)
    if dropped:
        lines.append(f"# {dropped} more not shown -- see {REVIEW_URL}")
    return f"{prose}```\n" + "\n".join(lines) + "\n```"


def _hard_truncate(prose: str, dropped: int, budget: int) -> str:
    """Cut the prose down to the budget.

    ITS OWN GUARANTEE HAS A FLOOR: the fenced footer (the prompt header, the
    "N more not shown" line and the two fences) is ~175 characters and cannot
    be cut, so a budget below that comes back over it. Nothing real is near
    that -- Discord's is 1900 -- but the bound is "budget or ~175, whichever is
    larger", not "budget".
    """
    footer = _assemble("", [], dropped)
    available = max(budget - len(footer), 0)
    return _assemble(prose[:available], [], dropped)
