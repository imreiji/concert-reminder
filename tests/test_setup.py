"""The first-run capture flow over HTTP: /setup (prune),
/setup/applications, /setup/ready. Renders read purely from DB truth; the
two POSTs are idempotent batch writes. Client/login fixtures mirror
test_welcome.py.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ConcertTag,
    Round,
    Tag,
    TagSubscription,
)
from app.db.service import concert_subscription_states, tracked_concert_ids
from app.domain.types import RoundKind, SubscriptionState, TagKind
from app.web import auth
from app.web.app import create_app

FAN_ID = 777

# Real-future timestamps: the routes call the service with the real clock.
FUTURE = datetime(2099, 6, 20, 12, tzinfo=UTC)
FUTURE_LATER = datetime(2099, 6, 25, 12, tzinfo=UTC)
PAST = datetime(2000, 1, 1, 12, tzinfo=UTC)


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
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    from app.db.session import get_session
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


async def seed_followed_concert(
    client, event_id: str, title: str, tag_name: str, *, closes: datetime = FUTURE,
    kind: TagKind = TagKind.GROUP,
) -> int:
    """A concert tagged with a tag FAN_ID follows, with one future round.
    Returns the concert id."""
    async with client.db() as s:
        tag = (await s.execute(
            Tag.__table__.select().where(Tag.name == tag_name)
        )).first()
        if tag is None:
            tag_obj = Tag(name=tag_name, kind=kind)
            s.add(tag_obj)
            await s.flush()
            s.add(TagSubscription(user_id=FAN_ID, tag_id=tag_obj.id))
            tag_id = tag_obj.id
        else:
            tag_id = tag[0]
        concert = Concert(title=title, event_id=event_id, created_by=FAN_ID)
        s.add(concert)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag_id))
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="FC presale",
            closes_at_utc=closes,
        ))
        await s.commit()
        return concert.id


# ── GET /setup (screen 1) ────────────────────────────────────────────────


def test_setup_requires_login(client):
    assert client.get("/setup").status_code == 303


async def test_setup_renders_found_tiles(client):
    login_as(client, FAN_ID, "fan")
    c1 = await seed_followed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    c2 = await seed_followed_concert(client, "ll-fest", "Love Live Fes", "Aqours")

    r = client.get("/setup")
    assert r.status_code == 200
    assert "We found 2" in r.text
    assert "Aqours 9th Live" in r.text and "Love Live Fes" in r.text
    assert "Aqours" in r.text
    assert f'value="{c1}" checked' in r.text
    assert f'value="{c2}" checked' in r.text


async def test_setup_renders_pruned_tile_unchecked(client):
    login_as(client, FAN_ID, "fan")
    cid = await seed_followed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    async with client.db() as s:
        from app.db.service import set_concert_subscription
        await set_concert_subscription(s, FAN_ID, cid, SubscriptionState.OPTED_OUT)
        await s.commit()

    r = client.get("/setup")
    assert r.status_code == 200
    assert f'value="{cid}"' in r.text
    assert f'value="{cid}" checked' not in r.text


async def test_setup_tile_start_date_uses_day_month_not_dual_time(client):
    """The tile's `.v` line shows the concert's own START date (a
    performance date, not a deadline), so it must render via day_month --
    date only, no time-of-day, no zone -- per fmt_day_month's own docstring
    and every other tile's convention. It used to wrongly go through
    dual_lines, printing a full JST+local clock line for a bare date.

    Uses seed_concert (no default round) so the only dual-time-eligible
    timestamp on the tile is the day itself -- a round's own deadline
    legitimately keeps the dual JST+local format (invariant 1), so mixing
    one in here would make the assertion ambiguous."""
    login_as(client, FAN_ID, "fan")
    cid = await seed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    async with client.db() as s:
        s.add(ConcertDay(concert_id=cid, label="Day 1", starts_at_utc=FUTURE))
        await s.commit()

    r = client.get("/setup")
    assert r.status_code == 200
    # day_month: date only, no weekday, no time, no zone.
    assert "20 Jun" in r.text
    # The old dual_lines rendering would have produced this exact shape
    # ("Sat 20 Jun" + "21:00 JST · HH:MM <tz>") -- must be gone.
    assert "Sat 20 Jun" not in r.text
    assert "21:00 JST" not in r.text


async def test_setup_empty_state(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/setup")
    assert r.status_code == 200
    assert "/discover" in r.text


async def test_prune_submit_writes_and_redirects(client):
    login_as(client, FAN_ID, "fan")
    c1 = await seed_followed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    c2 = await seed_followed_concert(client, "ll-fest", "Love Live Fes", "Aqours")

    r = client.post("/setup/prune", data={"keep": [c1], "shown": [c1, c2]})
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/applications"

    async with client.db() as s:
        tracked = await tracked_concert_ids(s, FAN_ID)
        states = await concert_subscription_states(s, FAN_ID)
    assert c2 not in tracked and c1 in tracked
    assert states.get(c2) is SubscriptionState.OPTED_OUT


async def test_prune_submit_ignores_forged_ids(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/setup/prune", data={"keep": [9999], "shown": [9999]})
    assert r.status_code == 303
    async with client.db() as s:
        assert await concert_subscription_states(s, FAN_ID) == {}


# ── extra seeding for screens 2 and 3 ────────────────────────────────────


async def _followed_tag_id(s, tag_name: str, kind: TagKind) -> int:
    row = (await s.execute(Tag.__table__.select().where(Tag.name == tag_name))).first()
    if row is not None:
        return row[0]
    tag = Tag(name=tag_name, kind=kind)
    s.add(tag)
    await s.flush()
    s.add(TagSubscription(user_id=FAN_ID, tag_id=tag.id))
    await s.flush()
    return tag.id


async def seed_concert(
    client, event_id: str, title: str, tag_name: str, *, kind: TagKind = TagKind.GROUP,
) -> int:
    """A followed concert with NO round of its own, so a test can attach
    exactly the rounds it needs via add_round."""
    async with client.db() as s:
        tag_id = await _followed_tag_id(s, tag_name, kind)
        concert = Concert(title=title, event_id=event_id, created_by=FAN_ID)
        s.add(concert)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag_id))
        await s.commit()
        return concert.id


async def add_round(client, concert_id: int, label: str, **timing) -> int:
    async with client.db() as s:
        r = Round(
            concert_id=concert_id, kind=RoundKind.LOTTERY_ROUND, label=label, **timing
        )
        s.add(r)
        await s.flush()
        rid = r.id
        await s.commit()
        return rid


async def set_outcome(client, round_id: int, outcome) -> None:
    from app.db.service import record_round_outcome
    async with client.db() as s:
        await record_round_outcome(s, FAN_ID, round_id, outcome)
        await s.commit()


async def prune(client, concert_id: int) -> None:
    from app.db.service import set_concert_subscription
    async with client.db() as s:
        await set_concert_subscription(s, FAN_ID, concert_id, SubscriptionState.OPTED_OUT)
        await s.commit()


# ── GET/POST /setup/applications (screen 2) ──────────────────────────────


def test_applications_requires_login(client):
    assert client.get("/setup/applications").status_code == 303


def test_ready_requires_login(client):
    assert client.get("/setup/ready").status_code == 303


async def test_applications_renders_only_qualifying_rounds(client):
    login_as(client, FAN_ID, "fan")
    live = await seed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    await add_round(client, live, "Open lottery", opens_at_utc=PAST, closes_at_utc=FUTURE)
    await add_round(client, live, "Awaiting lottery", closes_at_utc=PAST, results_at_utc=FUTURE)
    await add_round(client, live, "Decided lottery", closes_at_utc=PAST, results_at_utc=PAST)
    pruned = await seed_concert(client, "muse-live", "Muse Live", "Aqours")
    await add_round(client, pruned, "Pruned lottery", opens_at_utc=PAST, closes_at_utc=FUTURE)
    await prune(client, pruned)

    r = client.get("/setup/applications")
    assert r.status_code == 200
    assert "Open lottery" in r.text and "Still open" in r.text
    assert "Awaiting lottery" in r.text and "Awaiting result" in r.text
    assert "Decided lottery" not in r.text
    assert "Pruned lottery" not in r.text


async def test_applications_round_label_renders_in_viewers_language(client):
    """Screen 2's round label must localize like the concert title one line
    above it (setup.html's title already goes through `loc()`; only the
    label was left as the raw `row.round_.label`). Uses a Japanese original
    plus a zh variant so a locale-source mistake (rendering the raw column)
    fails loudly instead of coincidentally passing -- same shape as
    test_concert_page.py's test_round_label_renders_in_the_viewers_language."""
    login_as(client, FAN_ID, "fan")
    concert = await seed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    await add_round(
        client, concert, "1次先行抽選",
        label_zh="第一轮先行", label_en="1st-round lottery",
        opens_at_utc=PAST, closes_at_utc=FUTURE,
    )

    client.cookies.set("lang", "zh")
    r = client.get("/setup/applications")
    assert r.status_code == 200
    assert "第一轮先行" in r.text
    assert "1次先行抽選" not in r.text, "the Japanese label must not leak to a zh viewer"


async def test_applications_empty_state(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/setup/applications")
    assert r.status_code == 200
    assert "Nothing to ask" in r.text
    assert "Finish" in r.text


async def test_applications_submit_records_and_redirects(client):
    login_as(client, FAN_ID, "fan")
    concert = await seed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    applied = await add_round(client, concert, "R1", opens_at_utc=PAST, closes_at_utc=FUTURE)
    skipped = await add_round(client, concert, "R2", opens_at_utc=PAST, closes_at_utc=FUTURE_LATER)

    r = client.post("/setup/applications", data={"applied": [applied]})
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/ready"

    from app.db.models import RoundOutcome
    async with client.db() as s:
        rows = {
            o.round_id: o.outcome
            for o in (await s.execute(RoundOutcome.__table__.select())).all()
        }
    from app.domain.types import LotteryOutcome
    assert rows.get(applied) is LotteryOutcome.APPLIED
    assert skipped not in rows


async def test_applications_submit_ignores_decided_round_id(client):
    login_as(client, FAN_ID, "fan")
    concert = await seed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    await add_round(client, concert, "Live day", closes_at_utc=FUTURE)  # keeps it upcoming
    decided = await add_round(client, concert, "Decided", closes_at_utc=PAST, results_at_utc=PAST)

    r = client.post("/setup/applications", data={"applied": [decided]})
    assert r.status_code == 303
    from app.db.models import RoundOutcome
    async with client.db() as s:
        assert (await s.execute(RoundOutcome.__table__.select())).all() == []


# ── GET /setup/ready (screen 3) ──────────────────────────────────────────


async def test_ready_renders_tallies(client):
    login_as(client, FAN_ID, "fan")
    from app.domain.types import LotteryOutcome
    a = await seed_concert(client, "aqours-a", "Aqours A", "Aqours")
    r_applied = await add_round(client, a, "R1", opens_at_utc=PAST, closes_at_utc=FUTURE)
    await set_outcome(client, r_applied, LotteryOutcome.APPLIED)
    b = await seed_concert(client, "aqours-b", "Payment Concert B", "Aqours")
    r_won = await add_round(
        client, b, "R1", closes_at_utc=PAST, results_at_utc=PAST,
        payment_deadline_at_utc=FUTURE_LATER,
    )
    await set_outcome(client, r_won, LotteryOutcome.WON)

    r = client.get("/setup/ready")
    assert r.status_code == 200
    assert "Your board is ready" in r.text
    assert ">2<" in r.text and ">1<" in r.text  # tracking 2, applied 1 / payment due 1
    assert "Payment Concert B" in r.text


async def test_ready_without_payment_due_has_no_narrative(client):
    login_as(client, FAN_ID, "fan")
    a = await seed_concert(client, "aqours-a", "Aqours A", "Aqours")
    await add_round(client, a, "R1", opens_at_utc=PAST, closes_at_utc=FUTURE)

    r = client.get("/setup/ready")
    assert r.status_code == 200
    assert "waiting on a payment" not in r.text


async def test_ready_next_deadline_stat_does_not_overflow_big_tile(client):
    """The reveal screen's fourth tally (next deadline) used to cram the full
    one-line dual string ('Sat 2099-06-20 21:00 JST (09:00 ADT)') into the
    same big-font `.n` tile as the three short digit tallies, overflowing it.
    It must now render a short value in `.n` and keep the dual JST+local time
    (invariant 1) in a smaller, separate line."""
    login_as(client, FAN_ID, "fan")
    a = await seed_concert(client, "aqours-a", "Aqours A", "Aqours")
    await add_round(client, a, "R1", opens_at_utc=PAST, closes_at_utc=FUTURE)

    r = client.get("/setup/ready")
    assert r.status_code == 200
    # The big-font stat tile holds only the short date -- no clock, no
    # parenthesised second zone, nothing that made the old string overflow.
    assert '<div class="n">Sat 20 Jun</div>' in r.text
    assert '<div class="n">Sat 2099-06-20' not in r.text
    # Both zones are still present (invariant 1), just not inside `.n`.
    assert "21:00 JST" in r.text
    assert "09:00 ADT" in r.text


async def test_ready_step_tracker_matches_demo_shape(client):
    """The step tracker reconciles with the demo: the completed /welcome
    wizard steps as static done-pills, a Preferences escape link, and just
    the two real capture-flow dots (no third "Ready" dot -- the demo shows
    both capture dots as done on the reveal screen, not a separate node)."""
    login_as(client, FAN_ID, "fan")

    r = client.get("/setup/ready")
    assert r.status_code == 200
    for label in ("Follow", "Timezone", "Reminders", "Test DM"):
        assert f'<span class="stepdot done">{label}</span>' in r.text
    assert '<a class="more" href="/preferences">' in r.text
    assert "&larr; Preferences" in r.text or "← Preferences" in r.text
    # Both capture-flow dots read done on the reveal screen; no third dot.
    assert '<span class="stepdot done">Your events</span>' in r.text
    assert '<span class="stepdot done">Applications</span>' in r.text
    assert ">Ready<" not in r.text


async def test_prune_screen_step_tracker_marks_first_dot_on(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/setup")
    assert r.status_code == 200
    assert '<span class="stepdot on">Your events</span>' in r.text
    assert '<span class="stepdot">Applications</span>' in r.text


async def test_applications_screen_step_tracker_marks_second_dot_on(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/setup/applications")
    assert r.status_code == 200
    assert '<span class="stepdot done">Your events</span>' in r.text
    assert '<span class="stepdot on">Applications</span>' in r.text


async def test_rerun_reflects_prior_choices(client):
    login_as(client, FAN_ID, "fan")
    keep = await seed_concert(client, "aqours-a", "Aqours A", "Aqours")
    await add_round(client, keep, "Keep round", opens_at_utc=PAST, closes_at_utc=FUTURE)
    drop = await seed_concert(client, "aqours-b", "Aqours B", "Aqours")
    applied = await add_round(client, drop, "Apply round", opens_at_utc=PAST, closes_at_utc=FUTURE)

    # First pass: prune `drop`, apply its round is not possible (pruned), so
    # apply the surviving concert's round instead.
    client.post("/setup/prune", data={"keep": [keep], "shown": [keep, drop]})
    # Re-enable drop so its round qualifies, then apply it.
    client.post("/setup/prune", data={"keep": [keep, drop], "shown": [keep, drop]})
    client.post("/setup/applications", data={"applied": [applied]})

    # Now prune drop again for the re-run assertion.
    client.post("/setup/prune", data={"keep": [keep], "shown": [keep, drop]})

    r1 = client.get("/setup")
    assert f'value="{drop}" checked' not in r1.text
    assert f'value="{keep}" checked' in r1.text

    r2 = client.get("/setup/applications")
    assert "Apply round" not in r2.text  # already applied -> gone

    # A second identical POST to each endpoint changes nothing.
    from app.db.models import RoundOutcome
    async with client.db() as s:
        before = len((await s.execute(RoundOutcome.__table__.select())).all())
    client.post("/setup/prune", data={"keep": [keep], "shown": [keep, drop]})
    client.post("/setup/applications", data={"applied": [applied]})
    async with client.db() as s:
        after = len((await s.execute(RoundOutcome.__table__.select())).all())
        states = await concert_subscription_states(s, FAN_ID)
    assert before == after
    assert states.get(drop) is SubscriptionState.OPTED_OUT
