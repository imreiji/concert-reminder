"""Personal calendar feed: token generation, the .ics endpoint itself, and
the preferences-page states around it.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID, VIEWER_ID = 42, 777


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False)
    c.db = db
    c.monkeypatch = monkeypatch
    return c


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def generate_feed_token(client) -> str:
    r = client.post("/me/calendar-feed")
    assert r.status_code == 303
    match = re.search(r"feed_token=([\w-]+)", r.headers["location"])
    assert match
    return match.group(1)


def create_tracked_round(
    client, closes_at: str, event_id: str = "c", opens_at: str = ""
) -> None:
    """A concert with one round, TRACKED by the caller. The feed derives from
    standing over tracked concerts now, not from reminder rules -- so this
    subscribes instead of adding a rule (which would change only DMs)."""
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": event_id,
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [opens_at], "round_closes_at": [closes_at],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["R1"],
            "round_label_zh": ["R1"], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post(f"/concerts/{event_id}/subscription", data={"state": "subscribed"})


def test_generate_feed_requires_login(client):
    assert client.post("/me/calendar-feed").status_code == 303


def test_generate_feed_creates_token_and_redirects_with_it(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed")
    assert r.status_code == 303
    assert "feed_token=" in r.headers["location"]


def test_generate_feed_honors_next_param(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed", data={"next": "/welcome"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/welcome?feed_token=")


def test_generate_feed_honors_concert_page_next(client):
    """The concert page (Task 5) is a third minting surface -- the allowlist
    is a SHAPE (/concerts/ prefix), not a hardcoded set of every concert."""
    login_as(client, EDITOR_ID, "reiji")
    create_tracked_round(client, "2099-06-25T23:59", event_id="mine")
    r = client.post("/me/calendar-feed", data={"next": "/concerts/mine"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/concerts/mine?feed_token=")


def test_generate_feed_rejects_offsite_and_odd_next(client):
    login_as(client, EDITOR_ID, "reiji")
    for bad in ("https://evil.example/x", "/\\evil.example", "/admin", "//evil"):
        r = client.post("/me/calendar-feed", data={"next": bad})
        assert r.headers["location"].startswith("/preferences?feed_token="), bad


def test_generate_feed_strips_query_and_dotdot_from_next(client):
    """The mint route appends ?feed_token= to the destination, so a query
    in `next` would produce an unparseable double-query URL and silently
    waste the one-time reveal; dot segments could normalize away from the
    page that renders it."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed", data={"next": "/concerts/mine?x=1"})
    assert r.headers["location"].startswith("/concerts/mine?feed_token=")
    r = client.post("/me/calendar-feed", data={"next": "/concerts/../admin"})
    assert r.headers["location"].startswith("/preferences?feed_token=")


def test_fresh_feed_url_shows_webcal_link_on_preferences(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed", data={"next": "/preferences"})
    page = client.get(r.headers["location"])
    assert "webcal://" in page.text


def test_calendar_feed_returns_ics_with_tracked_deadlines(client):
    login_as(client, EDITOR_ID, "reiji")
    create_tracked_round(client, "2099-06-25T23:59")
    token = generate_feed_token(client)

    r = client.get(f"/calendar/{token}.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VEVENT" in r.text
    assert "SUMMARY:C — R1 · 申込締切" in r.text
    assert "DTSTART:20990625T145900Z" in r.text  # 2099-06-25 23:59 JST -> UTC


def test_calendar_feed_qualifies_round_moments_canonically(client):
    """A no-outcome round emits opens AND closes; the canonical qualifier is
    what keeps the two apart on somebody's phone. Japanese on purpose: the
    feed has no viewer, and Japanese is this catalogue's source of truth."""
    login_as(client, EDITOR_ID, "reiji")
    create_tracked_round(
        client, "2099-06-25T23:59", opens_at="2099-06-10T10:00"
    )
    token = generate_feed_token(client)

    r = client.get(f"/calendar/{token}.ics")
    assert "SUMMARY:C — R1 · 受付開始" in r.text
    assert "SUMMARY:C — R1 · 申込締切" in r.text


def test_calendar_feed_excludes_past_deadlines(client):
    login_as(client, EDITOR_ID, "reiji")
    create_tracked_round(client, "2000-01-01T00:00")
    token = generate_feed_token(client)

    r = client.get(f"/calendar/{token}.ics")
    assert r.status_code == 200
    assert "BEGIN:VEVENT" not in r.text


def test_calendar_feed_unknown_token_404s(client):
    assert client.get("/calendar/not-a-real-token.ics").status_code == 404


def test_regenerating_feed_invalidates_old_token(client):
    login_as(client, EDITOR_ID, "reiji")
    old_token = generate_feed_token(client)
    new_token = generate_feed_token(client)
    assert old_token != new_token

    assert client.get(f"/calendar/{old_token}.ics").status_code == 404
    assert client.get(f"/calendar/{new_token}.ics").status_code == 200


def test_preferences_shows_generate_then_active_state(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/preferences")
    assert "Generate feed link" in r.text
    assert "Calendar feed active" not in r.text

    generate_feed_token(client)
    r = client.get("/preferences")  # no feed_token this time -- one-time reveal only
    assert "Calendar feed active" in r.text  # the demo's status pill
    assert "Generate a new one" in r.text    # the regenerate control
    assert "won't be shown again" not in r.text


def test_per_round_ics_download_is_gone(client):
    """Ruling 2026-08-04: the download buttons are REPLACED by the feed. A
    file is a snapshot that rots when a deadline moves; the feed re-plans."""
    login_as(client, EDITOR_ID, "reiji")
    create_tracked_round(client, "2099-06-25T23:59", event_id="gone")
    assert client.get("/rounds/1/ics").status_code == 404


def test_preferences_shows_one_time_reveal_right_after_generating(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed")
    location = r.headers["location"]
    r = client.get(location)
    assert "won't be shown again" in r.text
    assert "/calendar/" in r.text and ".ics" in r.text


def test_concert_page_offers_calendar_dialog_no_feed_state(client):
    login_as(client, EDITOR_ID, "reiji")
    create_tracked_round(client, "2099-06-25T23:59", event_id="dlg")
    page = client.get("/concerts/dlg")
    assert "Turn on my calendar feed" in page.text
    assert 'name="next" value="/concerts/dlg"' in page.text


def test_concert_page_calendar_dialog_shows_fresh_url_once(client):
    login_as(client, EDITOR_ID, "reiji")
    create_tracked_round(client, "2099-06-25T23:59", event_id="dlg2")
    r = client.post("/me/calendar-feed", data={"next": "/concerts/dlg2"})
    page = client.get(r.headers["location"])
    assert "webcal://" in page.text
    # And the has-feed state on the NEXT visit: no URL, honest copy instead.
    page = client.get("/concerts/dlg2")
    assert "webcal://" not in page.text
    assert "already" in page.text.lower()
