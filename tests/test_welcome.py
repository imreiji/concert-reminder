"""First-run guided setup: the wizard's own routes (GET /welcome dispatch,
POST /welcome/advance, POST /welcome/skip-all). The new-user-redirect half
that sends a brand-new login here lives in test_auth.py.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, ReminderPreset, TagSubscription, User
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID, FAN_ID = 42, 777


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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


async def _onboarding_step(client, discord_id: int) -> int:
    async with client.db() as s:
        user = await s.get(User, discord_id)
        return user.onboarding_step


async def _welcomed_at(client, discord_id: int):
    async with client.db() as s:
        user = await s.get(User, discord_id)
        return user.welcomed_at


def test_welcome_requires_login(client):
    assert client.get("/welcome").status_code == 303


def test_welcome_shows_step_0_for_a_brand_new_user(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/welcome")
    assert r.status_code == 200
    assert "Follow some artists" in r.text


def test_welcome_redirects_to_index_once_done(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/skip-all")
    r = client.get("/welcome")
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_advance_increments_step_by_one(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/advance")
    assert r.status_code == 303
    assert r.headers["location"] == "/welcome"
    assert await _onboarding_step(client, FAN_ID) == 1


async def test_advance_stops_at_total_steps(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(10):
        client.post("/welcome/advance")
    assert await _onboarding_step(client, FAN_ID) == 5


async def test_skip_all_jumps_straight_to_done(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/skip-all")
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert await _onboarding_step(client, FAN_ID) == 5


def test_final_advance_hands_off_to_setup(client):
    """Crossing into done (step 4 -> 5) is the capture flow's entry: the last
    Continue lands on /setup, not back on /welcome (which would bounce to /)."""
    login_as(client, FAN_ID, "fan")
    for _ in range(4):
        client.post("/welcome/advance")  # 0 -> 1 -> 2 -> 3 -> 4
    r = client.post("/welcome/advance")  # 4 -> 5 (done)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"


async def test_advancing_past_the_last_step_stamps_welcomed_at(client):
    """Finishing the wizard is what the OAuth callback reads to stop sending
    this account back here, so crossing into done must leave the stamp."""
    login_as(client, FAN_ID, "fan")
    assert await _welcomed_at(client, FAN_ID) is None  # mid-wizard: still owed
    for _ in range(5):
        client.post("/welcome/advance")  # 0 -> 5 (done)
    stamped = await _welcomed_at(client, FAN_ID)
    assert stamped is not None
    assert stamped.tzinfo is not None  # invariant 1: aware UTC only


async def test_skip_all_stamps_welcomed_at(client):
    """Skipping IS finishing -- the wizard was offered and answered, so it
    must not be re-offered on the next login."""
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/skip-all")
    assert await _welcomed_at(client, FAN_ID) is not None


def test_earlier_advances_stay_on_welcome(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/advance")  # 0 -> 1, still mid-wizard
    assert r.status_code == 303
    assert r.headers["location"] == "/welcome"


def test_skip_all_still_lands_on_index(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/skip-all")
    assert r.headers["location"] == "/"


async def test_step_0_subscribe_form_returns_to_welcome(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Gakumas", "name_zh": "Gakumas", "name": "Gakumas", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1, "next": "/welcome"})
    assert r.headers["location"] == "/welcome"
    async with client.db() as s:
        subs = (await s.execute(select(TagSubscription))).scalars().all()
    assert len(subs) == 1 and subs[0].user_id == FAN_ID
    # "Gakumas" alone would render either way (it's the tag name in the
    # picker); the "✓" only appears once sub_by_tag actually has this tag.
    assert "Gakumas ✓" in client.get("/welcome").text


async def test_skipping_step_1_does_not_create_a_preset(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    r = client.get("/welcome")
    assert "Skip this" in r.text
    client.post("/welcome/advance")  # step 1 -> 2, no preset created
    async with client.db() as s:
        presets = (await s.execute(select(ReminderPreset))).scalars().all()
    assert presets == []


def test_step_1_renders_the_three_preset_cards(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    r = client.get("/welcome")
    assert "Relaxed" in r.text and "Standard" in r.text and "Frequent" in r.text
    # The demo drops "30 minutes" -- PresetItem has no minutes column.
    assert "30 minutes" not in r.text


# The five standard-template rows as parallel form arrays (offset "days:hours",
# direction, anchor) -- exactly what the fine-tune UI submits on "Create preset".
# Aligned by index across the three lists, the way the browser's repeated
# form keys arrive.
_STANDARD_FORM = {
    "offset": ["0:0", "3:0", "1:0", "0:0", "1:0"],
    "direction": ["before", "before", "before", "before", "before"],
    "anchor": ["opens", "closes", "closes", "results", "payment"],
}


async def test_step_1_submit_creates_default_preset_with_items(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    r = client.post("/welcome/preset", data=_STANDARD_FORM)
    assert r.status_code == 303
    assert r.headers["location"] == "/welcome"
    async with client.db() as s:
        presets = (await s.execute(select(ReminderPreset))).scalars().all()
        assert len(presets) == 1
        preset = presets[0]
        assert preset.is_default is True
        await s.refresh(preset, ["items"])
        # before = a negative offset; the "when it" moment rows are 0/0.
        rows = sorted((i.anchor.value, i.offset_days, i.offset_hours) for i in preset.items)
    assert rows == sorted([
        ("opens", 0, 0), ("closes", -3, 0), ("closes", -1, 0),
        ("results", 0, 0), ("payment", -1, 0),
    ])
    # Submitting the preset advances the wizard to the timezone step.
    assert await _onboarding_step(client, FAN_ID) == 2


async def test_step_1_fine_tuned_rule_persists_direction_and_hours(client):
    """An edited row -- an 'after' relation and an hours-only offset -- lands as
    the right PresetItem: after = a positive sign, 3 hours = offset_hours +3."""
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    data = {"offset": ["0:3"], "direction": ["after"], "anchor": ["results"]}
    r = client.post("/welcome/preset", data=data)
    assert r.headers["location"] == "/welcome"
    async with client.db() as s:
        preset = (await s.execute(select(ReminderPreset))).scalar_one()
        await s.refresh(preset, ["items"])
        item = preset.items[0]
    assert item.anchor.value == "results"
    assert item.offset_days == 0
    assert item.offset_hours == 3


async def test_step_1_empty_rules_rejected_and_creates_no_preset(client):
    """C1: the fine-tune list can be cleared down to zero rows client-side
    (the "×" on every row), so a tampered/edge-case POST can arrive with no
    rules at all. That must never create a zero-item default preset -- a
    default that silently never reminds, contradicting preferences.py's
    "no empty-preset limbo" invariant -- so it's rejected with a 422 and the
    wizard step must not advance either."""
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    r = client.post("/welcome/preset", data={})  # no offset/direction/anchor at all
    assert r.status_code == 422
    async with client.db() as s:
        presets = (await s.execute(select(ReminderPreset))).scalars().all()
    assert presets == []
    assert await _onboarding_step(client, FAN_ID) == 1


async def test_step_1_malformed_row_returns_422_not_500(client):
    """C2: the offset/anchor <select>s are closed, so only a tampered POST
    can send a value outside their vocabulary -- but the route must still
    fail cleanly (422) rather than raising an unhandled ValueError (500)."""
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    data = {"offset": ["0:0"], "direction": ["before"], "anchor": ["not-a-real-anchor"]}
    r = client.post("/welcome/preset", data=data)
    assert r.status_code == 422
    async with client.db() as s:
        presets = (await s.execute(select(ReminderPreset))).scalars().all()
    assert presets == []


def test_step_1_sentence_slot_order_en(client):
    """The reminder rows read as sentences built from locale-ordered slots. In
    English the offset select precedes the anchor select."""
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    r = client.get("/welcome")
    assert 'data-role="offset"' in r.text and 'data-role="anchor"' in r.text
    assert r.text.index('data-role="offset"') < r.text.index('data-role="anchor"')


def test_step_1_clone_template_present_and_translated_under_ja(client):
    """The JS clones a server-rendered <template id="remrule-template"> instead
    of assembling English DOM. Under ja it must exist and carry the translated
    pattern text, and the slot order flips (anchor select before offset)."""
    from app import i18n

    i18n.reset_catalog_cache()
    try:
        login_as(client, FAN_ID, "fan")
        client.post("/welcome/advance")  # step 0 -> 1
        client.cookies.set("lang", "ja")
        r = client.get("/welcome")
        assert 'id="remrule-template"' in r.text
        # ja: {anchor}の{offset}{direction}に通知。 -> anchor before offset.
        assert r.text.index('data-role="anchor"') < r.text.index('data-role="offset"')
        assert "に通知" in r.text  # translated pattern text, not English DOM
    finally:
        i18n.reset_catalog_cache()


async def test_welcome_shows_step_2_timezone(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # 0 -> 1
    client.post("/welcome/advance")  # 1 -> 2
    r = client.get("/welcome")
    assert "Confirm your timezone" in r.text


async def test_step_2_renders_both_timezone_selects(client):
    """The timezone step carries the zone select AND a Detection select, the
    latter reflecting tz_auto -- a brand-new user is browser-auto by default,
    so 'Follow my browser' is the selected option."""
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # 0 -> 1
    client.post("/welcome/advance")  # 1 -> 2
    r = client.get("/welcome")
    assert r.status_code == 200
    assert 'name="timezone"' in r.text
    assert 'id="tz-detect"' in r.text
    assert '<option value="follow" selected>' in r.text


async def test_step_2_set_timezone_returns_to_welcome(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")
    client.post("/welcome/advance")
    r = client.post("/me/timezone", data={"timezone": "Asia/Tokyo", "next": "/welcome"})
    assert r.headers["location"] == "/welcome"
    # "Asia/Tokyo" alone would render regardless (it's already one of the
    # ~400 option values in the picker); only the `selected` marker proves
    # the wizard is actually showing the NEW value, not just any option.
    assert 'value="Asia/Tokyo" selected' in client.get("/welcome").text


async def test_welcome_shows_step_3_test_dm(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(3):
        client.post("/welcome/advance")  # 0 -> 1 -> 2 -> 3
    r = client.get("/welcome")
    assert "Send a test DM" in r.text


async def test_welcome_shows_step_4_calendar_feed(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(4):
        client.post("/welcome/advance")  # 0 -> 1 -> 2 -> 3 -> 4
    r = client.get("/welcome")
    assert "Get your calendar feed" in r.text
    # The continue button reads the same forward-facing label whether or
    # not a feed was generated yet (P5 demo-parity: no "Skip this" here).
    assert "Continue to my events" in r.text


async def test_step_4_generate_feed_returns_to_welcome_with_link_shown(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(4):
        client.post("/welcome/advance")
    r = client.post("/me/calendar-feed", data={"next": "/welcome"})
    assert r.headers["location"].startswith("/welcome?feed_token=")
    r = client.get(r.headers["location"])
    assert "feed link is ready" in r.text
    assert "Continue" in r.text
