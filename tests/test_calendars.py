"""Calendar feeds: the roster's shape, the host-pinned fetch, the adaptation.

Never touches the network. `feed_leads` is pure and is tested on fixture
strings; `fetch_feed`'s guard is tested exactly the way
`tests/test_discovery_fetch.py` tests `fetch_actor_events`' -- through an
httpx.MockTransport, so the SSRF guard itself really runs.
"""

from datetime import date

import httpx
import pytest

from app.calendars import (
    ALLOWED_HOST,
    CALENDAR_FEEDS,
    CalendarFeed,
    CalendarFetchError,
    feed_leads,
    fetch_feed,
)

OK = f"https://{ALLOWED_HOST}/calendar/ical/x%40group.calendar.google.com/public/basic.ics"


def _ics(*vevents: str) -> str:
    body = "\r\n".join(vevents)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}\r\nEND:VCALENDAR\r\n"


def _vevent(uid: str, summary: str, day: str, location: str | None = None) -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{summary}",
        f"DTSTART;VALUE=DATE:{day}",
    ]
    if location is not None:
        lines.append(f"LOCATION:{location}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


TODAY = date(2026, 8, 3)

EVENTS = CalendarFeed(key="ll-test", label="Test events", url=OK, dates_are="event")
DEADLINES = CalendarFeed(
    key="tix-test", label="Test deadlines", url=OK, dates_are="deadline"
)
FILTERED = CalendarFeed(
    key="ll-filtered",
    label="Test filtered",
    url=OK,
    dates_are="event",
    include_prefixes=("ライブ:", "イベント:"),
)


# --- feed_leads -------------------------------------------------------------


def test_an_empty_prefix_tuple_takes_every_vevent():
    text = _ics(
        _vevent("a", "ライブ: X", "20260910"),
        _vevent("b", "誕生日: だれか", "20260911"),
    )
    leads, skipped = feed_leads(EVENTS, text, TODAY)
    assert [lead.event.title for lead in leads] == ["ライブ: X", "誕生日: だれか"]
    assert skipped == 0


def test_a_non_matching_summary_is_filtered_not_counted_as_skipped():
    """Filtered and skipped are different facts and must not be conflated.

    `skipped` means UNREADABLE -- a VEVENT the parser could not build. A
    SUMMARY the feed's prefix list does not want is the filter working as
    designed, and folding it into the skip count would make a healthy feed
    look broken on the status line.
    """
    text = _ics(
        _vevent("a", "ライブ: X", "20260910"),
        _vevent("b", "CD: なにか", "20260911"),
        _vevent("c", "ラジオ: なにか", "20260912"),
    )
    leads, skipped = feed_leads(FILTERED, text, TODAY)
    assert [lead.event.title for lead in leads] == ["ライブ: X"]
    assert skipped == 0


def test_an_unreadable_vevent_is_counted_skipped():
    text = _ics(
        _vevent("a", "ライブ: X", "20260910"),
        "BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:ライブ: Y\r\nEND:VEVENT",  # no DTSTART
    )
    leads, skipped = feed_leads(EVENTS, text, TODAY)
    assert [lead.event.event_id for lead in leads] == ["ll-test:a"]
    assert skipped == 1


def test_a_prefix_match_is_anchored_at_the_start():
    """`ライブ映像無料公開: ...` is a real LL-Fans summary and is not a live.

    This is why every event-feed prefix in the roster carries its colon.
    """
    text = _ics(
        _vevent("a", "ライブ映像無料公開: X", "20260910"),
        _vevent("b", "なにかのライブ: Y", "20260911"),
    )
    leads, _ = feed_leads(FILTERED, text, TODAY)
    assert leads == []


def test_past_events_are_dropped_and_today_counts():
    text = _ics(
        _vevent("past", "ライブ: 昨日", "20260802"),
        _vevent("today", "ライブ: 今日", "20260803"),
        _vevent("future", "ライブ: あした", "20260804"),
    )
    leads, _ = feed_leads(EVENTS, text, TODAY)
    assert [lead.event.event_id for lead in leads] == ["ll-test:today", "ll-test:future"]


def test_a_lead_is_namespaced_sourced_and_tagless():
    text = _ics(_vevent("abc123@google.com", "ライブ: X", "20260910", "Zepp Haneda"))
    (lead,), _ = feed_leads(EVENTS, text, TODAY)
    assert lead.event.event_id == "ll-test:abc123@google.com"
    assert lead.event.title == "ライブ: X"
    assert lead.event.date == date(2026, 9, 10)
    assert lead.event.venue == "Zepp Haneda"
    assert lead.source == "ll-test"
    # A calendar feed carries no tag: nothing on the concert surfaced it.
    assert lead.tag_id is None


def test_a_missing_location_becomes_an_empty_venue():
    text = _ics(_vevent("a", "ライブ: X", "20260910"))
    (lead,), _ = feed_leads(EVENTS, text, TODAY)
    assert lead.event.venue == ""


def test_the_deadline_flag_comes_from_dates_are():
    text = _ics(_vevent("a", "なにか 一般発売", "20260910"))
    (event_lead,), _ = feed_leads(EVENTS, text, TODAY)
    (deadline_lead,), _ = feed_leads(DEADLINES, text, TODAY)
    assert event_lead.date_is_deadline is False
    assert deadline_lead.date_is_deadline is True


# --- the roster -------------------------------------------------------------


def test_feed_keys_are_unique_ascii_and_colon_free():
    """The key is the lead-id namespace (`"{key}:{uid}"`), so a colon in one
    would make the namespace ambiguous, and a non-ASCII one would travel badly
    through a String column read by eye on /admin/discoveries."""
    keys = [feed.key for feed in CALENDAR_FEEDS]
    assert len(keys) == len(set(keys))
    for key in keys:
        assert key.isascii() and key, key
        assert ":" not in key, key


def test_every_feed_url_is_on_the_allowed_host():
    assert ALLOWED_HOST == "calendar.google.com"
    for feed in CALENDAR_FEEDS:
        assert feed.url.startswith(f"https://{ALLOWED_HOST}/"), feed.key


def test_every_feed_declares_what_its_dates_mean_and_carries_a_label():
    for feed in CALENDAR_FEEDS:
        assert feed.dates_are in ("event", "deadline"), feed.key
        assert feed.label.strip(), feed.key


def test_event_feed_prefixes_carry_their_colon():
    """An event prefix is a whole SUMMARY category, so it is anchored with its
    separator -- a bare `ライブ` would also take `ライブ映像無料公開:`. Deadline
    feeds are the opposite by construction: their round type sits BETWEEN the
    prefix and the colon (`最速先行 受付終了 (23:59): ...`)."""
    for feed in CALENDAR_FEEDS:
        if feed.dates_are != "event":
            continue
        for prefix in feed.include_prefixes:
            assert prefix.endswith(":"), (feed.key, prefix)


def test_the_imas_ticket_feed_is_single_purpose():
    """It is a deadline calendar and nothing else, so it filters nothing."""
    (imas,) = [feed for feed in CALENDAR_FEEDS if feed.key == "imas-tix"]
    assert imas.dates_are == "deadline"
    assert imas.include_prefixes == ()


# --- fetch_feed -------------------------------------------------------------


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_it_fetches_an_allowed_url():
    async def handler(request):
        return httpx.Response(200, text="BEGIN:VCALENDAR")

    assert "VCALENDAR" in await fetch_feed(OK, transport=_transport(handler))


@pytest.mark.parametrize(
    "url",
    [
        "http://calendar.google.com/calendar/ical/x/public/basic.ics",  # not https
        "https://evil.example.com/calendar/ical/x/public/basic.ics",  # wrong host
        "https://calendar.google.com.evil.example/x.ics",  # suffix trick
    ],
)
async def test_a_disallowed_url_is_refused_before_any_request(url):
    async def handler(request):
        raise AssertionError("no request should have been made")

    with pytest.raises(CalendarFetchError):
        await fetch_feed(url, transport=_transport(handler))


async def test_a_redirect_off_host_is_refused():
    """`match` is load-bearing: without it, dropping the redirect hook would
    still go green, because the unfollowed 302 surfaces as an ordinary
    CalendarFetchError. Pinning HostNotAllowed's wording is what makes this
    test about the guard rather than about "something went wrong"."""

    async def handler(request):
        if request.url.host == ALLOWED_HOST:
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        raise AssertionError("followed the redirect off-host")

    with pytest.raises(CalendarFetchError, match="only https"):
        await fetch_feed(OK, transport=_transport(handler))


async def test_an_oversized_body_is_aborted():
    async def handler(request):
        return httpx.Response(200, content=b"x" * 6_000_000)

    with pytest.raises(CalendarFetchError):
        await fetch_feed(OK, transport=_transport(handler))


async def test_a_non_200_raises():
    async def handler(request):
        return httpx.Response(503)

    with pytest.raises(CalendarFetchError):
        await fetch_feed(OK, transport=_transport(handler))
