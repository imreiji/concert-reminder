"""The local rehearsal harness. Gated off in production by config."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import client as bot_client
from app.config import settings
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    Notification,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundQualifier,
    User,
)
from app.db.service import (
    REHEARSAL_EVENT_ID,
    cancel_rehearsal_show,
    get_rehearsal_concert,
    notify_newly_cancelled_legs,
    pull_rehearsal_forward,
    rehearsal_queue_rows,
    rehearsal_rows,
    seed_rehearsal,
    teardown_rehearsal,
)
from app.db.session import get_session
from app.domain.rehearsal import expected_buttons
from app.domain.types import Anchor, LotteryOutcome, RoundKind
from app.web import auth
from app.web.app import create_app
from app.web.routes.rehearsal import LOCALES, SHAPES

ADMIN_ID, PLAIN_ID = 42, 777


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
    # Registration is decided AT create_app() time, so the flag must be on
    # BEFORE the app is built -- otherwise every route test in this file 404s.
    monkeypatch.setattr(settings, "rehearsal_enabled", True)
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
    """Drives the real OAuth callback, which CREATES the user row -- so no test
    here seeds the admin itself (that is an IntegrityError, not a shortcut)."""

    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def route_paths(routes) -> set[str]:
    """Every path in an app's route table, flattened.

    `app.routes` is NOT flat: this FastAPI wraps each `include_router` call in
    an `_IncludedRouter` that carries no `.path` of its own and exposes the
    real routes through `.original_router`. Reading `.path` off the top level
    alone would therefore see none of the included routers -- and the
    flag-off assertion below would pass for the wrong reason, forever.
    """
    out: set[str] = set()
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            out |= route_paths(inner.routes)
        path = getattr(r, "path", None)
        if path:
            out.add(path)
    return out


def test_the_router_is_not_registered_when_the_flag_is_off(monkeypatch):
    """THE safety model, asserted directly. With the flag off the route must
    not exist at all -- not 403, not 404-from-a-guard, but absent from the
    application's route table. Production never sets the flag, so a
    'pull every reminder forward' button is unreachable by construction
    rather than by a permission check somebody could get wrong."""
    monkeypatch.setattr(settings, "rehearsal_enabled", False)
    paths = route_paths(create_app().routes)
    # A control: the flattening genuinely reaches included routers, so an
    # absent /admin/rehearsal means absent, not merely unreachable by this walk.
    assert "/admin/broadcast" in paths
    assert "/admin/rehearsal" not in paths


def test_the_router_is_registered_when_the_flag_is_on(monkeypatch):
    monkeypatch.setattr(settings, "rehearsal_enabled", True)
    paths = route_paths(create_app().routes)
    assert "/admin/rehearsal" in paths


def test_the_flag_defaults_to_off():
    """A developer opts in; nobody opts out."""
    assert settings.model_fields["rehearsal_enabled"].default is False


def test_page_renders_for_an_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/rehearsal")
    assert r.status_code == 200
    assert "Rehearsal" in r.text


def test_a_signed_in_non_admin_gets_403(client):
    """require_admin stays on the routes as a second layer, in case a deploy
    is ever misconfigured with the flag on."""
    login_as(client, PLAIN_ID, "someone")
    assert client.get("/admin/rehearsal").status_code == 403


@pytest.mark.asyncio
async def test_seed_builds_the_canonical_scenario(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()

        assert concert.event_id == REHEARSAL_EVENT_ID
        days = (await s.execute(
            select(ConcertDay)
            .where(ConcertDay.concert_id == concert.id)
            .order_by(ConcertDay.starts_at_utc)
        )).scalars().all()
        assert len(days) == 2
        rounds = (await s.execute(select(Round).where(
            Round.concert_id == concert.id))).scalars().all()
        assert len(rounds) == 3
        kinds = {r.kind for r in rounds}
        assert kinds == {RoundKind.LOTTERY_ROUND, RoundKind.FCFS_SALE, RoundKind.UPGRADE}


@pytest.mark.asyncio
async def test_the_lottery_round_carries_all_four_anchors_and_both_legs(db):
    """One round yields the whole ladder, and spanning two legs is what
    exercises the per-day RoundOutcomeDay materialization."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        r1 = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.LOTTERY_ROUND))).scalar_one()
        assert r1.opens_at_utc and r1.closes_at_utc
        assert r1.results_at_utc and r1.payment_deadline_at_utc
        assert len(r1.applies_to) == 2


@pytest.mark.asyncio
async def test_the_upgrade_round_qualifies_on_the_lottery_round(db):
    """Before a WON on R1 the viewer is ineligible; after it, eligible. That
    gate is what this round exists to prove end to end."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        upgrade = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.UPGRADE))).scalar_one()
        lottery = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.LOTTERY_ROUND))).scalar_one()
        pairs = (await s.execute(select(RoundQualifier).where(
            RoundQualifier.upgrade_round_id == upgrade.id))).scalars().all()
        assert [p.qualifying_round_id for p in pairs] == [lottery.id]


@pytest.mark.asyncio
async def test_seed_queues_reminders_through_the_real_planner(db):
    """The point of seeding real rules: sync_rule and the pure planner compute
    the fire times, so what the harness later pulls forward is a genuine
    plan, not a fabricated row."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        queued = (await s.execute(select(ReminderQueue))).scalars().all()
        anchors = {q.anchor for q in queued}
        assert Anchor.OPENS in anchors
        assert Anchor.CLOSES in anchors
        assert Anchor.RESULTS in anchors
        assert Anchor.PAYMENT in anchors
        assert Anchor.EVENT_START in anchors


@pytest.mark.asyncio
async def test_seed_is_idempotent(db):
    """Start twice leaves ONE rehearsal concert -- the harness reseeds from a
    clean slate rather than accumulating."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        concerts = (await s.execute(select(Concert).where(
            Concert.event_id == REHEARSAL_EVENT_ID))).scalars().all()
        assert len(concerts) == 1


@pytest.mark.asyncio
async def test_teardown_removes_the_concert_but_not_the_user(db):
    """Cascades take the days, rounds, queue rows and outcomes. Users,
    presets and subscriptions are never touched."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        assert await teardown_rehearsal(s) is True
        await s.commit()
        assert await get_rehearsal_concert(s) is None
        assert (await s.execute(select(ReminderQueue))).scalars().all() == []
        assert await s.get(User, ADMIN_ID) is not None


@pytest.mark.asyncio
async def test_teardown_with_nothing_seeded_is_a_no_op(db):
    async with db() as s:
        assert await teardown_rehearsal(s) is False


@pytest.mark.asyncio
async def test_pull_forward_moves_the_soonest_unsent_row_into_the_past(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        before = sorted(
            (await s.execute(select(ReminderQueue))).scalars().all(),
            key=lambda q: q.fire_at_utc,
        )
        pulled = await pull_rehearsal_forward(s)
        await s.commit()
        assert pulled is not None
        assert pulled.id == before[0].id
        assert pulled.fire_at_utc < datetime.now(UTC)


@pytest.mark.asyncio
async def test_pull_forward_never_touches_another_concert_s_rows(db):
    """The spec's hard rule. There is no queue id parameter, so the only rows
    reachable are the rehearsal concert's -- a harness that could fire an
    arbitrary reminder early is the version of this feature worth designing
    out."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        other = Concert(event_id="real", title="Real", title_en="Real")
        s.add(other)
        await s.flush()
        day = ConcertDay(concert_id=other.id, label="D",
                         starts_at_utc=datetime.now(UTC) + timedelta(days=5))
        s.add(day)
        await s.flush()
        rule = ReminderRule(user_id=ADMIN_ID, concert_id=other.id,
                            anchor=Anchor.EVENT_START, offset_days=0, offset_hours=0)
        s.add(rule)
        await s.flush()
        far = datetime.now(UTC) + timedelta(days=5)
        s.add(ReminderQueue(rule_id=rule.id, day_id=day.id,
                            anchor=Anchor.EVENT_START, fire_at_utc=far))
        await s.commit()

        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()

        # Directly: the scoped view cannot see the other concert's row.
        assert day.id not in {r.day_id for r in await rehearsal_queue_rows(s)}

        # And by exhaustion: drain every rehearsal row, then check the guarded
        # row survived. Each pulled row MUST be marked sent here, exactly as
        # the real tick does on delivery -- pull-forward rewrites fire_at to
        # now-1s, which sorts the row straight back to the front, so without
        # that the loop re-pulls row one forever, never reaches the guarded
        # row, and passes for a reason that has nothing to do with scoping.
        for _ in range(20):
            pulled = await pull_rehearsal_forward(s)
            if pulled is None:
                break
            pulled.sent_at_utc = datetime.now(UTC)
            await s.commit()
        else:
            raise AssertionError("the drain never exhausted -- it is not draining")

        untouched = (await s.execute(select(ReminderQueue).where(
            ReminderQueue.day_id == day.id))).scalar_one()
        assert untouched.fire_at_utc == far
        assert untouched.sent_at_utc is None


@pytest.mark.asyncio
async def test_pull_forward_skips_rows_already_sent(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        rows = sorted((await s.execute(select(ReminderQueue))).scalars().all(),
                      key=lambda q: q.fire_at_utc)
        rows[0].sent_at_utc = datetime.now(UTC)
        await s.commit()
        pulled = await pull_rehearsal_forward(s)
        await s.commit()
        assert pulled.id == rows[1].id


@pytest.mark.asyncio
async def test_pull_forward_returns_none_when_everything_is_sent(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        for row in (await s.execute(select(ReminderQueue))).scalars():
            row.sent_at_utc = datetime.now(UTC)
        await s.commit()
        assert await pull_rehearsal_forward(s) is None


@pytest.mark.asyncio
async def test_cancelling_a_leg_queues_the_cancellation_notice(db):
    """notify_newly_cancelled_legs must run BEFORE sync_concert, which deletes
    the queue rows it inspects. Get that order wrong and the notice is silent."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        n = await cancel_rehearsal_show(s)
        await s.commit()
        assert n >= 1
        notes = (await s.execute(select(Notification).where(
            Notification.kind == "leg_cancelled"))).scalars().all()
        assert len(notes) == 1


@pytest.mark.asyncio
async def test_cancelling_takes_every_live_leg_because_the_notice_is_concert_scoped(db):
    """WHY the button calls the whole show off rather than dropping one leg.
    notify_newly_cancelled_legs is silent for a user who still has a live
    reminder anywhere on the concert, so cancelling Day 2 of two queues
    nothing -- Day 1's EVENT_START and R1's four anchors are still standing.
    A per-leg button would demonstrate the leg_cancelled DM by never sending
    it. Pinning the real rule here stops a later 'tidy-up' reinstating it."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()

        # The rule itself: one leg down, other reminders alive -> no notice.
        day2 = (await s.execute(
            select(ConcertDay)
            .where(ConcertDay.concert_id == concert.id)
            .order_by(ConcertDay.starts_at_utc.desc())
        )).scalars().first()
        day2.cancelled = True
        await s.flush()
        assert await notify_newly_cancelled_legs(s, concert.id, {day2.id}) == 0
        day2.cancelled = False
        await s.flush()

        assert await cancel_rehearsal_show(s) == 1
        await s.commit()
        legs = (await s.execute(select(ConcertDay).where(
            ConcertDay.concert_id == concert.id))).scalars().all()
        assert all(leg.cancelled for leg in legs)


@pytest.mark.asyncio
async def test_cancelling_with_no_live_leg_left_is_a_no_op(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        assert await cancel_rehearsal_show(s) == 1
        await s.commit()
        assert await cancel_rehearsal_show(s) == 0


@pytest.mark.asyncio
async def test_cancelling_with_nothing_seeded_is_a_no_op(db):
    async with db() as s:
        assert await cancel_rehearsal_show(s) == 0


def test_expected_buttons_match_the_anchor_and_outcome_gating():
    """The page names what SHOULD appear on the row it just pulled. Without
    this the harness is a trigger; with it, an oracle -- it distinguishes
    'no button rendered' from 'wrong button rendered', which is the whole
    difference between watching DMs arrive and testing them.

    Every tuple here was read off build_reminder_message, including its
    TRAILING button: every reminder ends with remind-later on a CLOSES row and
    snooze on any other, so an expectation that stopped at the capture buttons
    would call a correct DM wrong on five of these seven rows."""
    assert expected_buttons(Anchor.CLOSES, None) == ("applied", "notapplied", "remindlater")
    # Past the starting state the capture pair is gone and only the trailing
    # button is left -- and on a CLOSES row that is remind-later, not snooze.
    assert expected_buttons(Anchor.CLOSES, LotteryOutcome.APPLIED) == ("remindlater",)
    assert expected_buttons(Anchor.RESULTS, None) == ("won", "lost", "snooze")
    assert expected_buttons(Anchor.RESULTS, LotteryOutcome.APPLIED) == ("won", "lost", "snooze")
    assert expected_buttons(Anchor.RESULTS, LotteryOutcome.WON) == ("snooze",)
    assert expected_buttons(Anchor.PAYMENT, LotteryOutcome.WON) == ("paid", "snooze")
    assert expected_buttons(Anchor.PAYMENT, LotteryOutcome.LOST) == ("snooze",)
    assert expected_buttons(Anchor.OPENS, None) == ("snooze",)
    assert expected_buttons(Anchor.EVENT_START, None) == ("snooze",)


def test_expected_buttons_ask_leg_by_leg_when_a_round_covers_two_legs():
    """The canonical scenario's R1 covers BOTH legs, so its results DM is the
    per-leg view, not the flat Won/Lost pair -- and step 4 of the walk is
    exactly where an oracle claiming won/lost would send the operator hunting
    for a button that is correctly absent."""
    assert expected_buttons(Anchor.RESULTS, None, 2) == (
        "wonall", "wonday", "wonday", "lostall", "snooze",
    )
    # The DM asks a long tour a batch of legs at a time; the shortcuts do not
    # multiply with them.
    assert expected_buttons(Anchor.RESULTS, None, 9) == (
        "wonall", "wonday", "wonday", "wonday", "wonday", "lostall", "snooze",
    )
    # One leg is not a per-leg question, whatever the round's applies_to says.
    assert expected_buttons(Anchor.RESULTS, None, 1) == ("won", "lost", "snooze")


@pytest.mark.asyncio
async def test_the_state_rows_expect_buttons_for_the_next_row_only(db):
    """An expectation is only meaningful for the row about to fire: every row
    below it will be read under an outcome the walk has not reached yet."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        rows = await rehearsal_rows(s, ADMIN_ID)
        assert rows
        assert rows[0].anchor is Anchor.OPENS
        assert rows[0].is_next
        assert rows[0].expected == ("snooze",)
        assert not any(r.is_next for r in rows[1:])
        assert all(r.expected == () for r in rows[1:])
        assert all(r.subject for r in rows)


@pytest.mark.asyncio
async def test_a_sent_row_is_never_the_next_one(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        pulled = await pull_rehearsal_forward(s)
        pulled.sent_at_utc = datetime.now(UTC)
        await s.commit()
        rows = await rehearsal_rows(s, ADMIN_ID)
        sent = [r for r in rows if r.queue_id == pulled.id]
        assert sent and sent[0].sent and not sent[0].is_next
        assert sum(1 for r in rows if r.is_next) == 1


def test_start_seeds_and_end_tears_down(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    assert client.post("/admin/rehearsal/start").status_code == 303
    page = client.get("/admin/rehearsal")
    assert "Rehearsal Concert" in page.text
    assert client.post("/admin/rehearsal/end").status_code == 303
    assert "Rehearsal Concert" not in client.get("/admin/rehearsal").text


def test_next_reports_what_it_pulled_and_what_to_expect(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/rehearsal/start")
    client.post("/admin/rehearsal/next")
    page = client.get("/admin/rehearsal")
    assert "opens" in page.text.lower()
    # The oracle reaches the page, not just the service layer: the first row
    # of the walk is an OPENS reminder, whose only button is snooze.
    assert "snooze" in page.text.lower()


def test_the_page_says_cancelling_the_show_is_terminal(client, monkeypatch):
    """Step 8 kills the upgrade round's rows with everything else, so an
    operator who presses it at step 5 has ended the walk without being told."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    assert "terminal" in client.get("/admin/rehearsal").text.lower()


def test_the_actions_are_admin_only(client):
    login_as(client, PLAIN_ID, "someone")
    for path in ("start", "next", "cancel-show", "end"):
        assert client.post(f"/admin/rehearsal/{path}").status_code == 403


def capture_dms(monkeypatch) -> list[tuple[tuple, dict]]:
    """Point the route's lazy `from app.bot.client import bot` at a fake.

    The import happens INSIDE the handler, exactly as /me/test-dm does it, so
    patching the module attribute is enough and no Discord gateway is ever
    involved -- the same shape tests/test_crud.py uses for the test DM."""
    sent: list[tuple[tuple, dict]] = []

    class FakeUser:
        async def send(self, *args, **kwargs):
            sent.append((args, kwargs))

    class FakeBot:
        def get_user(self, _discord_id):
            return FakeUser()

    monkeypatch.setattr(bot_client, "bot", FakeBot())
    return sent


def test_the_shape_catalogue_sends_the_chosen_shape(client, monkeypatch):
    """Independent of the pipeline half: renders a builder directly under a
    chosen locale, so a copy or translation change can be re-checked in
    seconds without constructing the state a real DM needs."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "x")
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/rehearsal/start")
    sent = capture_dms(monkeypatch)

    r = client.post("/admin/rehearsal/shape", data={"shape": "reminder_closes", "locale": "ja"})
    assert r.status_code == 303
    assert len(sent) == 1


def test_the_shape_catalogue_needs_the_bot(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "")
    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/rehearsal/shape", data={"shape": "reminder_closes", "locale": "en"})
    assert r.status_code == 303  # reports, does not crash


def test_every_catalogued_shape_actually_builds(client, monkeypatch):
    """All eight, in one pass. A shape whose builder cannot be fed from the
    seeded scenario is a catalogue entry that 500s the first time the operator
    picks it, and the picker offers all eight unconditionally."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "x")
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/rehearsal/start")
    sent = capture_dms(monkeypatch)

    for shape, _label in SHAPES:
        r = client.post("/admin/rehearsal/shape", data={"shape": shape, "locale": "en"})
        assert r.status_code == 303, shape
    assert len(sent) == len(SHAPES) == 8


def test_the_locale_picker_reaches_the_reminder_builder(client, monkeypatch):
    """The catalogue's whole point is fast ja/zh copy review, and getting the
    locale wrong is SILENT -- nothing raises, the embed just comes out in the
    operator's language. Two sends of the SAME shape must differ."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "x")
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/rehearsal/start")
    sent = capture_dms(monkeypatch)

    client.post("/admin/rehearsal/shape", data={"shape": "reminder_closes", "locale": "en"})
    client.post("/admin/rehearsal/shape", data={"shape": "reminder_closes", "locale": "ja"})
    en, ja = sent[0][1]["embed"], sent[1][1]["embed"]
    assert "closes" in en.description
    assert "closes" not in ja.description
    assert en.description != ja.description
    # The concert title is UGC, not a msgid: it proves loc_field got the
    # locale too, which set_locale alone would never have supplied.
    assert en.title.endswith("Rehearsal Concert")
    assert ja.title.endswith("リハーサル公演")


def test_the_locale_picker_reaches_the_notice_builders(client, monkeypatch):
    """NoticeContext and LegCancelledContext resolve their UGC from the
    RECIPIENT's users.language, not get_locale() -- a different one of the
    three locale sources. Pin both, since set_locale alone reaches neither."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "x")
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/rehearsal/start")
    sent = capture_dms(monkeypatch)

    for shape in ("new_event", "leg_cancelled"):
        sent.clear()
        client.post("/admin/rehearsal/shape", data={"shape": shape, "locale": "en"})
        client.post("/admin/rehearsal/shape", data={"shape": shape, "locale": "zh"})
        en, zh = sent[0][1]["embed"], sent[1][1]["embed"]
        assert en.title.endswith("Rehearsal Concert"), shape
        assert zh.title.endswith("彩排演出"), shape


@pytest.mark.asyncio
async def test_the_catalogue_leaves_the_operator_s_language_alone(client, monkeypatch):
    """Sending a ja shape must not silently switch the operator's account to
    Japanese -- the catalogue writes nothing, it only pretends for one build."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "x")
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/rehearsal/start")
    capture_dms(monkeypatch)
    client.post("/admin/rehearsal/shape", data={"shape": "new_event", "locale": "ja"})

    async with client.db() as s:
        assert (await s.get(User, ADMIN_ID)).language == "en"


def test_the_shape_catalogue_reports_when_nothing_is_seeded(client, monkeypatch):
    """The operator simply has not pressed Start yet; every builder needs the
    seeded concert, so say so rather than raising."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "discord_token", "x")
    login_as(client, ADMIN_ID, "reiji")
    sent = capture_dms(monkeypatch)
    r = client.post("/admin/rehearsal/shape", data={"shape": "new_event", "locale": "en"})
    assert r.status_code == 303
    assert sent == []
    assert "Start" in client.get(r.headers["location"]).text


def test_the_picker_offers_every_shape_and_locale(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    page = client.get("/admin/rehearsal").text
    for shape, label in SHAPES:
        assert f'value="{shape}"' in page
        assert label in page
    for code, label in LOCALES:
        assert f'value="{code}"' in page
        assert label in page


def test_the_shape_catalogue_is_admin_only(client):
    login_as(client, PLAIN_ID, "someone")
    r = client.post("/admin/rehearsal/shape", data={"shape": "new_event", "locale": "en"})
    assert r.status_code == 403
