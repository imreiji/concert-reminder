"""Parse a ramen.events post into a draft concert.

ramen.events (a Ghost blog) has no structured markup for event data -- it's
hand-written prose that follows a consistent author convention within one
`<section class="gh-content gh-canvas is-body">`:

  <ul><li>Day 1: 2027-01-23 (Saturday), Doors 16:00 / Starts 17:00</li>
      <li>Venue: Nippon Budokan</li>...</ul>
  <h3>Day 1 Lottery #1: ...</h3><p>...</p>
  <ul><li>Apply within: 2026-10-14 12:00 to 2026-11-08 23:59</li></ul>

Single-day events use "When: ..." instead of "Day N: ...". Events with no
lottery announced yet have zero rounds -- that's a normal, common case, not
a parse failure (see ramen_welcoming_concert.html fixture).

Pure function: no I/O, no jst_to_utc conversion. Returned datetimes are
naive JST wall-clock values, exactly like a web form's <input
type="datetime-local">, so they flow through the SAME boundary
(concerts.parse_jst) as manually-entered data -- never a second path.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup
from bs4 import Tag as BS4Tag

from app.domain.types import RoundKind
from app.domain.urls import UnsafeUrlError, clean_url

DAY_LINE = re.compile(
    r"^(?P<label>Day\s+\d+|When):\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s*\([^)]*\)"
    r"(?:,?\s*Doors\s+(?P<doors>\d{1,2}:\d{2}))?"
    r"(?:\s*/\s*Starts\s+(?P<starts>\d{1,2}:\d{2}))?",
    re.IGNORECASE,
)
VENUE_LINE = re.compile(r"^Venue:\s*(?P<venue>.+)$", re.IGNORECASE)
APPLY_WITHIN = re.compile(
    r"Apply within:\s*(?P<opens>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})"
    r"\s+to\s+(?P<closes>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})",
    re.IGNORECASE,
)

_KIND_KEYWORDS: list[tuple[str, RoundKind]] = [
    ("general sale", RoundKind.GENERAL_SALE),
    ("first-come", RoundKind.GENERAL_SALE),
    ("result", RoundKind.RESULT_ANNOUNCEMENT),
    ("announce", RoundKind.RESULT_ANNOUNCEMENT),
    ("payment", RoundKind.PAYMENT_DEADLINE),
    ("stream", RoundKind.STREAM_TICKET_SALE),
    ("配信", RoundKind.STREAM_TICKET_SALE),
    ("lottery", RoundKind.LOTTERY_ROUND),
    ("抽選", RoundKind.LOTTERY_ROUND),
]


class IngestError(Exception):
    """The page doesn't look like a ramen.events event post at all."""


@dataclass
class ParsedDay:
    label: str
    starts_at_jst: datetime


@dataclass
class ParsedRound:
    label: str
    kind: RoundKind
    opens_at_jst: datetime | None
    closes_at_jst: datetime | None
    url: str | None


@dataclass
class ParsedConcert:
    title: str
    venue_name: str | None
    days: list[ParsedDay] = field(default_factory=list)
    rounds: list[ParsedRound] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _guess_kind(text: str) -> RoundKind:
    lowered = text.lower()
    for keyword, kind in _KIND_KEYWORDS:
        if keyword in lowered:
            return kind
    return RoundKind.OTHER


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")


def _official_url(content: BS4Tag, warnings: list[str]) -> str | None:
    """The scraped href is remote, third-party markup -- a `javascript:` one
    would end up in an href on our own pages. Drop it rather than raise: the
    rest of the parse is still useful, the import preview is reviewed by a
    human before anything is written, and the warning tells that human what
    was thrown away."""
    first_p = content.find("p")
    if first_p and "official website" in first_p.get_text().lower():
        link = first_p.find("a")
        if link and link.get("href"):
            try:
                return clean_url(link["href"])
            except UnsafeUrlError:
                warnings.append(
                    f"ignored an unsafe official-website link: {link['href']!r}"
                )
    return None


def _parse_basic_info(content: BS4Tag, warnings: list[str]) -> tuple[list[ParsedDay], str | None]:
    days: list[ParsedDay] = []
    venue: str | None = None
    for li in content.find_all("li"):
        text = li.get_text(strip=True)
        m = DAY_LINE.match(text)
        if m:
            starts = m.group("starts") or m.group("doors")
            if starts is None:
                warnings.append(f"no start/doors time found for line: {text!r}")
                continue
            label = m.group("label")
            label = "Day 1" if label.lower() == "when" else label
            days.append(ParsedDay(
                label=label,
                starts_at_jst=_parse_datetime(f"{m.group('date')} {starts}"),
            ))
            continue
        vm = VENUE_LINE.match(text)
        if vm and venue is None:
            venue = vm.group("venue").strip()
    if not days:
        warnings.append("no performance days found -- fill in manually")
    if venue is None:
        warnings.append("no venue found -- fill in manually")
    return days, venue


def _parse_rounds(
    content: BS4Tag, fallback_url: str | None, warnings: list[str]
) -> list[ParsedRound]:
    rounds: list[ParsedRound] = []
    for heading in content.find_all("h3"):
        label = heading.get_text(strip=True)[:200]
        # Scan siblings between this heading and the next h2/h3 for "Apply within".
        opens = closes = None
        for sib in heading.find_next_siblings():
            if not isinstance(sib, BS4Tag):
                continue
            if sib.name in ("h2", "h3"):
                break
            for li in [sib] if sib.name == "li" else sib.find_all("li"):
                m = APPLY_WITHIN.search(li.get_text())
                if m:
                    opens = _parse_datetime(m.group("opens"))
                    closes = _parse_datetime(m.group("closes"))
        if opens is None and closes is None:
            continue  # not a round heading (e.g. "Upgrade Tickets" has no dates)
        rounds.append(ParsedRound(
            label=label, kind=_guess_kind(label),
            opens_at_jst=opens, closes_at_jst=closes,
            url=fallback_url,
        ))
    if not rounds:
        warnings.append("no lottery/sale rounds found -- may not be announced yet")
    return rounds


def parse_ramen_event(html: str, source_url: str) -> ParsedConcert:
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.select_one("h1.gh-article-title") or soup.find("h1") or soup.title
    if title_tag is None or not title_tag.get_text(strip=True):
        raise IngestError("couldn't find a title -- this doesn't look like a ramen.events post")
    title = title_tag.get_text(strip=True)

    content = soup.select_one("section.gh-content")
    if content is None:
        raise IngestError("couldn't find the article body -- page layout may have changed")

    warnings: list[str] = []
    days, venue = _parse_basic_info(content, warnings)
    fallback_url = _official_url(content, warnings) or source_url
    rounds = _parse_rounds(content, fallback_url, warnings)

    return ParsedConcert(
        title=title, venue_name=venue, days=days, rounds=rounds, warnings=warnings
    )
