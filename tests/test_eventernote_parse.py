"""The Eventernote actor-events parser: pure, and forgiving of a redesign."""

import datetime as dt
from pathlib import Path

import pytest

from app.domain.eventernote import (
    ActorEvent,
    actor_events_url,
    actor_id_from_url,
    future_events,
    parse_actor_events,
)

FIXTURE = Path(__file__).parent / "fixtures" / "eventernote_actor_events.html"


def _page():
    return parse_actor_events(FIXTURE.read_text(encoding="utf-8"))


def test_it_finds_every_event_row():
    page = _page()
    assert len(page.events) >= 10, "the fixture should hold a full page of rows"


def test_each_event_carries_an_id_a_date_and_a_title():
    for event in _page().events:
        assert event.event_id.isdigit(), event
        assert isinstance(event.date, dt.date)
        assert event.title.strip()


def test_event_ids_are_unique_within_a_page():
    ids = [e.event_id for e in _page().events]
    assert len(ids) == len(set(ids))


def test_rows_are_newest_first():
    """The stop rule depends on this ordering. If the site ever changes it,
    future_events would silently truncate at the first past row and report
    almost nothing -- so the assumption is pinned here, not just documented."""
    dates = [e.date for e in _page().events]
    assert dates == sorted(dates, reverse=True)


def test_a_page_with_no_events_yields_nothing_and_does_not_raise():
    page = parse_actor_events("<html><body><p>no events</p></body></html>")
    assert page.events == []


def test_a_truncated_page_does_not_raise():
    """A site redesign must degrade to 'found nothing', which an operator can
    see, not to a crashed scheduler tick."""
    half = FIXTURE.read_text(encoding="utf-8")[: len(FIXTURE.read_text(encoding="utf-8")) // 2]
    parse_actor_events(half)  # must not raise


# ── the stop rule ────────────────────────────────────────────────────────

def _ev(day: int) -> ActorEvent:
    return ActorEvent(
        event_id=str(day), title=f"show {day}", date=dt.date(2026, 8, day), venue="v"
    )


def test_future_events_takes_the_prefix_and_stops_at_the_first_past_row():
    rows = [_ev(20), _ev(15), _ev(10), _ev(5)]
    assert [e.date.day for e in future_events(rows, dt.date(2026, 8, 12))] == [20, 15]


def test_an_event_today_counts_as_future():
    """A deadline can still be today. Excluding today would drop same-day
    announcements, which are the most urgent leads there are."""
    rows = [_ev(20), _ev(12), _ev(5)]
    assert [e.date.day for e in future_events(rows, dt.date(2026, 8, 12))] == [20, 12]


def test_an_all_past_page_yields_nothing():
    assert future_events([_ev(5), _ev(4)], dt.date(2026, 8, 12)) == []


def test_it_stops_rather_than_filters():
    """Take-while, not filter: a stray out-of-order future row AFTER a past row
    must not resurrect the walk, because that would mean reading the whole
    18-page history of every artist."""
    rows = [_ev(20), _ev(5), _ev(25)]
    assert [e.date.day for e in future_events(rows, dt.date(2026, 8, 12))] == [20]


# ── URL helpers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.eventernote.com/actors/Liyuu/34637", "34637"),
    ("https://www.eventernote.com/actors/%E5%A4%A7%E8%A5%BF/25872", "25872"),
    ("https://www.eventernote.com/actors/x/5847/events", "5847"),
    ("https://example.com/actors/x/1", None),
    ("", None),
    ("not a url", None),
])
def test_actor_id_from_url(url, expected):
    assert actor_id_from_url(url) == expected


def test_actor_events_url_percent_encodes_the_name():
    url = actor_events_url("34637", "大西亜玖璃")
    assert url.startswith("https://www.eventernote.com/actors/")
    assert url.endswith("/34637/events")
    assert "大西" not in url, "the name segment must be percent-encoded"
