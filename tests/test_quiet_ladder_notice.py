"""The quiet-ladder notice: one digest per pass, to admins, never per concert."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert, Notification, User
from app.db.service import ensure_user, run_quiet_ladder_pass

ADMIN_ID = 42
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))


async def _quiet(session, event_id):
    await ensure_user(session, 99, "editor")
    session.add(Concert(title=event_id, event_id=event_id, created_by=99))
    await session.flush()


async def _notices(session):
    return list((await session.execute(
        select(Notification).where(Notification.kind == "quiet_ladder")
    )).scalars())


async def test_one_digest_names_every_newcomer(session):
    await _quiet(session, "a")
    await _quiet(session, "b")
    await run_quiet_ladder_pass(session, NOW)

    notices = await _notices(session)
    assert len(notices) == 1, "one digest per pass, not one per concert"
    assert "a" in notices[0].body and "b" in notices[0].body
    assert notices[0].concert_id is None  # plain body, not a per-concert embed


async def test_no_newcomers_sends_nothing(session):
    await run_quiet_ladder_pass(session, NOW)
    assert await _notices(session) == []


async def test_a_second_pass_sends_nothing(session):
    await _quiet(session, "a")
    await run_quiet_ladder_pass(session, NOW)
    await run_quiet_ladder_pass(session, NOW)
    assert len(await _notices(session)) == 1


async def test_an_admin_who_never_signed_in_gets_a_user_row_first(session):
    """Notification.user_id is an FK to users.discord_id, so a queued notice
    for an admin with no row raises IntegrityError at flush, far from here."""
    await _quiet(session, "a")
    await run_quiet_ladder_pass(session, NOW)
    assert await session.get(User, ADMIN_ID) is not None


async def test_an_existing_admin_keeps_their_username(session):
    """ensure_user refreshes the username, so calling it unconditionally would
    overwrite a real admin's name with the placeholder on every tick."""
    await ensure_user(session, ADMIN_ID, "reiji")
    await _quiet(session, "a")
    await run_quiet_ladder_pass(session, NOW)

    admin = await session.get(User, ADMIN_ID)
    assert admin.username == "reiji"
