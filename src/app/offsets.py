"""Sub-day reminder offsets: the text the user types, and the phrase we read back.

Lives above `db/` and beside `i18n.py` rather than inside `domain/`, because
`describe_offset` needs gettext and no `domain/` module imports `app.i18n`.
Parsing is pure and would be at home in `domain/`, but splitting five lines of
parsing from the phrase that renders them buys nothing and costs the reader the
round trip -- parse, store, format, describe -- being readable in one screen.

Both shells import this: the web layer registers `format_hhmm` and
`describe_offset` as Jinja globals, and `bot/cogs/reminders.py` calls
`describe_offset` directly, so the two never drift into two sets of msgids for
one sentence.
"""

import re

from app.i18n import gettext as _
from app.i18n import ngettext

_HHMM_RE = re.compile(r"^(?P<hours>\d{1,2})(?::(?P<minutes>\d{1,2}))?$")


def parse_hhmm(text: str) -> tuple[int, int]:
    """Parse the editor's HH:MM box into (hours, minutes).

    Accepts "0:30", "00:30", "3" (bare = hours, which is what the hours select
    this box replaced used to post) and "" (zero). Raises ValueError on
    anything else -- including minutes > 59 and hours > 23, which are rejected
    rather than normalised: a reminder that silently moved is worse than a 422.
    """
    stripped = text.strip()
    if stripped == "":
        return 0, 0
    match = _HHMM_RE.match(stripped)
    if match is None:
        raise ValueError(f"not an h:mm value: {text!r}")
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes") or 0)
    if hours > 23:
        raise ValueError(f"hours must be 0-23, got {hours}")
    if minutes > 59:
        raise ValueError(f"minutes must be 0-59, got {minutes}")
    return hours, minutes


def format_hhmm(hours: int, minutes: int) -> str:
    """The value the box re-renders with, so a saved rule reads back as typed.

    MAGNITUDE ONLY -- it takes `abs()` of both arguments and drops the sign,
    which is correct for its one caller (the editor box, which shows a
    duration with direction picked separately) but means a signed pair like
    `describe_offset` takes would silently lose its sign here. Don't feed it
    a signed offset expecting the sign back.
    """
    return f"{abs(hours)}:{abs(minutes):02d}"


def describe_offset(days: int, hours: int, minutes: int) -> str:
    """One translated phrase for a stored, SIGNED offset.

    "Same day" for a zero offset; otherwise the two largest non-zero units,
    e.g. "3 days 6 hours before", "1 hour 30 minutes before", "30 minutes
    before". Direction comes from the sign, never from an argument.
    """
    d, h, m = abs(days), abs(hours), abs(minutes)
    if (d, h, m) == (0, 0, 0):
        return _("Same day")

    parts: list[str] = []
    if d:
        parts.append(ngettext("{n} day", "{n} days", d).format(n=d))
    if h:
        parts.append(ngettext("{n} hour", "{n} hours", h).format(n=h))
    if m:
        parts.append(ngettext("{n} minute", "{n} minutes", m).format(n=m))

    # The JOINER is translatable for the same reason the sentence patterns in
    # preferences.html are: a hardcoded " " is an English typesetting rule, not
    # a universal one. English needs the space ("1 hour 30 minutes"); ja and zh
    # must NOT have it -- 「1時間 30分」/「1小时 30分钟」 reads as two fragments
    # rather than one duration. Their msgstrs are the same two slots with
    # nothing between them.
    pair = parts[:2]
    quantity = (
        _("{first} {second}").format(first=pair[0], second=pair[1])
        if len(pair) == 2
        else pair[0]
    )
    after = days > 0 or hours > 0 or minutes > 0
    pattern = _("{quantity} after") if after else _("{quantity} before")
    return pattern.format(quantity=quantity)
