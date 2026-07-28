"""Transition machine wired to the DB and the outbox.

The fixture registers PRAGMA foreign_keys=ON deliberately: Notification.user_id
is a FK to users.discord_id, and the ensure_user call this code makes is
load-bearing -- without it, queuing an alert for an admin who has never logged
in violates the constraint.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Notification, OpsCheckState, User
from app.db.service import ensure_user, evaluate_and_alert
from app.ops import CheckResult

ADMIN = 111
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # cascades silently skip without this

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
async def session(maker):
    async with maker() as s:
        yield s


@pytest.fixture(autouse=True)
def _admins(monkeypatch):
    """The repo-root .env supplies a real token and a real admin list, so both
    are pinned rather than assumed. `bot_enabled` is a property over
    `discord_token`; pydantic models allow plain attribute assignment, so
    patching the field really does flip the property (verified by
    test_nothing_queues_when_bot_disabled, which patches it the other way)."""
    from app.db import service

    monkeypatch.setattr(service.settings, "admin_whitelist", str(ADMIN))
    monkeypatch.setattr(service.settings, "discord_token", "x")  # bot_enabled


async def _run(session, ok, now):
    return await evaluate_and_alert(session, [CheckResult("backup", ok, "detail")], now)


async def test_first_failing_observation_is_silent_then_alerts(session):
    assert await _run(session, False, NOW) == 0
    assert await _run(session, False, NOW + timedelta(minutes=5)) == 1
    notes = (await session.execute(select(Notification))).scalars().all()
    assert len(notes) == 1
    assert notes[0].user_id == ADMIN
    assert notes[0].kind == "ops_alert"
    assert notes[0].concert_id is None  # keeps it on loop.py's plain-text path
    assert "backup" in notes[0].body


async def test_healthy_baseline_never_alerts(session):
    assert await _run(session, True, NOW) == 0
    assert await _run(session, True, NOW + timedelta(minutes=5)) == 0
    assert (await session.execute(select(Notification))).scalars().all() == []


async def test_state_is_persisted(session):
    """Every StoredState field must round-trip. The dataclass-to-row mapping is
    hand-copied in both directions; asserting only `ok` would let a dropped
    pending_* column through, and those two carry the whole anti-flap rule."""
    await _run(session, True, NOW)
    row = await session.get(OpsCheckState, "backup")
    assert row is not None
    assert row.ok is True
    assert row.changed_at == NOW
    assert row.last_notified_at is None
    assert row.pending_ok is None
    assert row.pending_since is None

    # One failing observation against a healthy baseline: not yet confirmed, so
    # `ok` must stay True while the pending pair records the dissenting sighting.
    later = NOW + timedelta(minutes=5)
    await _run(session, False, later)
    await session.refresh(row)
    assert row.ok is True
    assert row.pending_ok is False
    assert row.pending_since == later

    # ...and the pending pair must be readable back as StoredState on the next
    # pass, or the confirmation never happens and nothing ever alerts.
    assert await _run(session, False, NOW + timedelta(minutes=10)) == 1
    await session.refresh(row)
    assert row.ok is False
    assert row.pending_ok is None
    assert row.pending_since is None


async def test_alert_queues_for_admin_without_a_user_row(session):
    """ensure_user must run first or the Notification FK fails."""
    await _run(session, False, NOW)
    await _run(session, False, NOW + timedelta(minutes=5))
    await session.commit()  # would raise IntegrityError if ensure_user was skipped


async def test_alerting_does_not_rename_an_existing_admin(session):
    """Alerting must not touch the users row beyond guaranteeing it exists.
    ensure_user refreshes the username, so calling it unconditionally would
    overwrite a logged-in admin's real name with the placeholder every time a
    check changed state."""
    await ensure_user(session, ADMIN, "reiji")
    await _run(session, False, NOW)
    await _run(session, False, NOW + timedelta(minutes=5))
    assert (await session.get(User, ADMIN)).username == "reiji"


async def test_non_alerting_checks_never_queue(session):
    await evaluate_and_alert(session, [CheckResult("dms", False, "x")], NOW)
    await evaluate_and_alert(
        session, [CheckResult("dms", False, "x")], NOW + timedelta(minutes=5)
    )
    assert (await session.execute(select(Notification))).scalars().all() == []
    # State is still tracked -- only the DM is suppressed.
    assert (await session.get(OpsCheckState, "dms")).ok is False


class _FakeUser:
    def __init__(self, sent):
        self._sent = sent

    async def send(self, body=None, *, embed=None, view=None):
        self._sent.append(embed.title if embed is not None else body)


class _FakeBot:
    def __init__(self):
        self.sent = []

    def get_user(self, _uid):
        return _FakeUser(self.sent)


async def _seed_pending_dm(maker):
    async with maker() as s:
        await ensure_user(s, ADMIN, "reiji")
        s.add(Notification(user_id=ADMIN, body="hello", kind="ops_alert"))
        await s.commit()


async def test_a_broken_health_check_cannot_lose_delivered_reminders(maker, monkeypatch):
    """The whole reason health evaluation sits after the delivery commit.

    The tick has already put DMs on the wire by the time health runs; if a
    failure there rolled the tick back, every delivered message would be
    re-sent next tick. Assert the bookkeeping survives an exploding evaluator.
    """
    import app.scheduler.loop as loop_mod

    await _seed_pending_dm(maker)

    async def boom(*_a, **_kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(loop_mod, "SessionMaker", maker)
    monkeypatch.setattr(loop_mod, "evaluate_and_alert", boom)
    monkeypatch.setattr(loop_mod, "_tick_count", loop_mod.HEALTH_EVERY_N_TICKS - 1)

    bot = _FakeBot()
    delivered = await loop_mod.tick(bot)  # must not raise

    assert delivered == 1
    assert bot.sent == ["hello"]  # plain-text path: no concert_id, no embed
    async with maker() as s:
        # Scoped to the seeded kind: delivering it also logs it, and the log
        # write queues a delivery_digest for the admin, so an unscoped
        # scalar_one() now sees two rows. The claim under test is about THIS
        # notification's bookkeeping.
        note = (
            await s.execute(select(Notification).where(Notification.kind == "ops_alert"))
        ).scalar_one()
        assert note.sent_at_utc is not None  # committed before health ran


async def test_health_runs_only_every_nth_tick(maker, monkeypatch):
    import app.scheduler.loop as loop_mod

    calls = []

    async def spy(_session, _results, _now):
        calls.append(1)
        return 0

    monkeypatch.setattr(loop_mod, "SessionMaker", maker)
    monkeypatch.setattr(loop_mod, "evaluate_and_alert", spy)
    monkeypatch.setattr(loop_mod, "_tick_count", 0)

    for _ in range(loop_mod.HEALTH_EVERY_N_TICKS):
        await loop_mod.tick(_FakeBot())
    assert len(calls) == 1


async def test_tick_counter_advances_even_when_delivery_raises(maker, monkeypatch):
    """The counter gates health evaluation, so it must not depend on the tick
    succeeding. reminder_loop swallows tick exceptions and retries forever: a
    tick that raises every minute would otherwise freeze the counter, health
    would never run again, and -- since heartbeat.beat() fires before tick() --
    scheduler_ok would stay true and the uptime monitor would stay quiet too.
    Silence in exactly the scenario that most needs an alert.
    """
    import app.scheduler.loop as loop_mod

    async def boom(*_a, **_kw):
        raise RuntimeError("due_reminders exploded")

    monkeypatch.setattr(loop_mod, "SessionMaker", maker)
    monkeypatch.setattr(loop_mod, "due_reminders", boom)
    monkeypatch.setattr(loop_mod, "_tick_count", 0)

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await loop_mod.tick(_FakeBot())

    assert loop_mod._tick_count == 3


async def test_a_failing_alert_does_not_consume_the_nag_clock(session, monkeypatch):
    """In web-only mode the DM is suppressed, so last_notified_at must stay
    untouched. Advancing it would make the alert look already-sent, and the
    first 24h of alerts would vanish on a server that gains a DISCORD_TOKEN
    later."""
    from app.db import service

    monkeypatch.setattr(service.settings, "discord_token", "")
    await _run(session, False, NOW)
    await _run(session, False, NOW + timedelta(minutes=5))  # would have alerted
    assert (await session.get(OpsCheckState, "backup")).last_notified_at is None

    # Bot comes online: the very next confirmed-failing pass must alert.
    monkeypatch.setattr(service.settings, "discord_token", "x")
    assert await _run(session, False, NOW + timedelta(minutes=10)) == 1


async def test_nothing_queues_when_bot_disabled(session, monkeypatch):
    """A laptop's disk is not an operational signal. This also proves the
    _admins fixture's discord_token patch reaches `bot_enabled` at all: flip it
    the other way and the queuing stops."""
    from app.db import service

    monkeypatch.setattr(service.settings, "discord_token", "")
    assert await _run(session, False, NOW) == 0
    assert await _run(session, False, NOW + timedelta(minutes=5)) == 0
    assert (await session.execute(select(Notification))).scalars().all() == []
