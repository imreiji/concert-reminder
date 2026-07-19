"""GDPR erasure: deleting a user must succeed and leave the shared catalogue.

The three FKs that point at users.discord_id from *community content*
(concerts.created_by, concert_audit.edited_by, tags.created_by) are
ondelete=SET NULL, not CASCADE: one editor asking to be forgotten must not
take the whole concert catalogue with them. Everything genuinely personal
(sessions, rules, presets, preset items, tag subscriptions, notifications,
round outcomes) is CASCADE and must actually disappear.

The fixture registers PRAGMA foreign_keys=ON deliberately -- without it
SQLite silently ignores every ondelete clause and this whole file would
pass while proving nothing.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import (
    Base,
    Concert,
    ConcertAudit,
    Notification,
    PresetItem,
    ReminderPreset,
    ReminderRule,
    Round,
    RoundOutcome,
    Tag,
    TagSubscription,
    User,
    WebSession,
)
from app.db.service import delete_user
from app.db.session import get_session
from app.domain.types import Anchor, LotteryOutcome, RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

EDITOR_ID = 42


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production: cascades must fire

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
async def session(db):
    async with db() as s:
        yield s


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


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _seed_user_with_everything(session) -> tuple[int, int, int, int]:
    """One user who authored community content AND owns personal rows."""
    user = User(discord_id=EDITOR_ID, username="reiji")
    session.add(user)
    await session.flush()

    concert = Concert(title="Hasunosora 5th", event_id="hasu-5th", created_by=EDITOR_ID)
    tag = Tag(name="Hasunosora", kind=TagKind.GROUP, created_by=EDITOR_ID)
    session.add_all([concert, tag])
    await session.flush()

    audit = ConcertAudit(
        concert_id=concert.id,
        edited_by=EDITOR_ID,
        changes=[{"field": "title", "before": "Old", "after": "Hasunosora 5th"}],
    )
    round_ = Round(
        concert_id=concert.id,
        kind=RoundKind.LOTTERY_ROUND,
        label="R1",
        closes_at_utc=datetime(2099, 6, 25, 14, 59, tzinfo=UTC),
    )
    preset = ReminderPreset(user_id=EDITOR_ID, name="standard")
    session.add_all([audit, round_, preset])
    await session.flush()

    session.add_all([
        WebSession(
            token_hash="a" * 64,
            user_id=EDITOR_ID,
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        ReminderRule(user_id=EDITOR_ID, concert_id=concert.id, anchor=Anchor.CLOSES),
        PresetItem(preset_id=preset.id, anchor=Anchor.CLOSES, offset_days=-3),
        TagSubscription(user_id=EDITOR_ID, tag_id=tag.id, preset_id=preset.id),
        Notification(user_id=EDITOR_ID, body="hi", concert_id=concert.id, kind="new_event"),
        RoundOutcome(user_id=EDITOR_ID, round_id=round_.id, outcome=LotteryOutcome.APPLIED),
    ])
    await session.commit()
    return concert.id, tag.id, audit.id, preset.id


async def test_deleting_a_user_keeps_the_catalogue_and_drops_personal_rows(session):
    concert_id, tag_id, audit_id, _ = await _seed_user_with_everything(session)

    await delete_user(session, EDITOR_ID)
    await session.commit()

    assert await session.get(User, EDITOR_ID) is None

    # Community content survives, anonymised.
    concert = await session.get(Concert, concert_id)
    assert concert is not None and concert.created_by is None
    tag = await session.get(Tag, tag_id)
    assert tag is not None and tag.created_by is None
    audit = await session.get(ConcertAudit, audit_id)
    assert audit is not None and audit.edited_by is None

    # Everything personal is gone (proves the existing CASCADEs really fire).
    for model in (
        WebSession,
        ReminderRule,
        ReminderPreset,
        PresetItem,
        TagSubscription,
        Notification,
        RoundOutcome,
    ):
        assert await _count(session, model) == 0, f"{model.__tablename__} rows survived"


async def test_deleting_an_unknown_user_is_a_no_op(session):
    assert await delete_user(session, 999_999) is False


async def test_concert_detail_renders_with_an_anonymised_audit_row(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C", "event_id": "c", "organizer": "Old"})
    client.post("/concerts/c/edit", data={"title": "C", "event_id": "c", "organizer": "New"})

    async with client.db() as s:
        audit = (await s.execute(select(ConcertAudit))).scalars().one()
        audit.edited_by = None
        await s.commit()

    r = client.get("/concerts/c")
    assert r.status_code == 200
    assert "Edit history" in r.text
    assert "Deleted user" in r.text
    assert ">None<" not in r.text
