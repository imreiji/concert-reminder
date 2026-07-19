"""parse_ramen_event: pure-function tests against real saved pages.

Two fixtures on purpose: one with announced lottery rounds (the common
case), one with none yet (a normal state, not a parse failure -- the
"more details will be announced later" case).
"""

from datetime import datetime
from pathlib import Path

import pytest

from app.domain.ingest import IngestError, parse_ramen_event
from app.domain.types import RoundKind

FIXTURES = Path(__file__).parent / "fixtures"
GRADUATION_URL = "https://ramen.events/hasunosora-103rd-class-graduation-concert/"
WELCOMING_URL = "https://ramen.events/hasunosora-106th-class-welcoming-concert/"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_title():
    parsed = parse_ramen_event(load("ramen_graduation_concert.html"), GRADUATION_URL)
    assert parsed.title == (
        "Hasunosora Jogakuin School Idol Club Link Live Dream "
        "~103rd Class Graduation Concert~"
    )


def test_parses_venue():
    parsed = parse_ramen_event(load("ramen_graduation_concert.html"), GRADUATION_URL)
    assert parsed.venue_name == "Nippon Budokan"


def test_parses_multiple_named_days():
    parsed = parse_ramen_event(load("ramen_graduation_concert.html"), GRADUATION_URL)
    assert [d.label for d in parsed.days] == ["Day 1", "Day 2"]
    assert parsed.days[0].starts_at_jst == datetime(2027, 1, 23, 17, 0)
    assert parsed.days[1].starts_at_jst == datetime(2027, 1, 24, 15, 30)


def test_parses_lottery_rounds():
    parsed = parse_ramen_event(load("ramen_graduation_concert.html"), GRADUATION_URL)
    assert len(parsed.rounds) == 2
    r1, r2 = parsed.rounds
    assert r1.kind is RoundKind.LOTTERY_ROUND
    assert r1.opens_at_jst == datetime(2026, 10, 14, 12, 0)
    assert r1.closes_at_jst == datetime(2026, 11, 8, 23, 59)
    assert "Day 1 Lottery" in r1.label
    assert r2.opens_at_jst == datetime(2026, 9, 25, 12, 0)
    assert r2.closes_at_jst == datetime(2026, 11, 8, 23, 59)


def test_rounds_default_url_to_official_site():
    parsed = parse_ramen_event(load("ramen_graduation_concert.html"), GRADUATION_URL)
    assert all(r.url and "lovelive-anime.jp" in r.url for r in parsed.rounds)


def test_no_warnings_when_fully_parsed():
    parsed = parse_ramen_event(load("ramen_graduation_concert.html"), GRADUATION_URL)
    assert parsed.warnings == []


# ── The "no rounds announced yet" case -- a normal state, not an error ────


def test_single_day_event_uses_when_label():
    parsed = parse_ramen_event(load("ramen_welcoming_concert.html"), WELCOMING_URL)
    assert [d.label for d in parsed.days] == ["Day 1"]
    assert parsed.days[0].starts_at_jst == datetime(2027, 1, 22, 17, 0)


def test_event_with_no_rounds_yet_warns_instead_of_failing():
    parsed = parse_ramen_event(load("ramen_welcoming_concert.html"), WELCOMING_URL)
    assert parsed.rounds == []
    assert any("no lottery" in w for w in parsed.warnings)


# ── Failure modes ──────────────────────────────────────────────────────


def test_unrecognizable_page_raises_ingest_error():
    with pytest.raises(IngestError):
        parse_ramen_event("<html><body><p>not an event page</p></body></html>", "https://x/")


def test_missing_venue_and_days_warns():
    html = """
    <html><body><article>
      <h1 class="gh-article-title">Some Concert</h1>
      <section class="gh-content"><p>no basic info list here</p></section>
    </article></body></html>
    """
    parsed = parse_ramen_event(html, "https://ramen.events/some-concert/")
    assert parsed.title == "Some Concert"
    assert parsed.days == []
    assert parsed.venue_name is None
    assert "no performance days found -- fill in manually" in parsed.warnings
    assert "no venue found -- fill in manually" in parsed.warnings


def test_unsafe_official_url_is_dropped_with_a_warning():
    """A remote page's <a href> is attacker-controlled if ramen.events is
    ever compromised; a javascript: href must never survive into the draft
    the editor commits. Dropping it (falling back to the source URL) keeps
    the preview usable, and the warning tells the human reviewer why."""
    html = """
    <html><body><article>
      <h1 class="gh-article-title">Some Concert</h1>
      <section class="gh-content">
        <p>Official website: <a href="javascript:alert(1)">here</a></p>
        <h3>Day 1 Lottery #1</h3>
        <ul><li>Apply within: 2026-10-14 12:00 to 2026-11-08 23:59</li></ul>
      </section>
    </article></body></html>
    """
    parsed = parse_ramen_event(html, "https://ramen.events/some-concert/")
    assert parsed.rounds[0].url == "https://ramen.events/some-concert/"
    assert any("unsafe" in w.lower() for w in parsed.warnings)
