"""The quiet-ladder notice: one digest per pass, to admins, never per concert."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert, Notification, User
from app.db.service import ensure_user, run_quiet_ladder_pass

ADMIN_ID = 42
OTHER_ADMIN_ID = 43
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
    # Multi-character, non-prose titles: the digest's own boilerplate
    # ("ladder", "and", "happened") already contains the letter "a", so a
    # single-letter fixture never actually discriminates -- a mutation that
    # silently dropped every newcomer but the first still passed with "a"
    # as the fixture title. "zzz" appears nowhere in the surrounding prose.
    await _quiet(session, "alpha-zzz")
    await _quiet(session, "bravo-zzz")
    await run_quiet_ladder_pass(session, NOW)

    notices = await _notices(session)
    assert len(notices) == 1, "one digest per pass, not one per concert"
    assert "alpha-zzz" in notices[0].body
    assert "bravo-zzz" in notices[0].body
    assert notices[0].concert_id is None  # plain body, not a per-concert embed


async def test_no_newcomers_sends_nothing(session):
    await run_quiet_ladder_pass(session, NOW)
    assert await _notices(session) == []


async def test_a_second_pass_sends_nothing(session):
    await _quiet(session, "alpha-zzz")
    await run_quiet_ladder_pass(session, NOW)
    assert len(await _notices(session)) == 1, "the first pass queues the digest"

    await run_quiet_ladder_pass(session, NOW)
    assert len(await _notices(session)) == 1, (
        "the second pass is self-idempotent -- reconcile_quiet_ladders sees no "
        "newcomer the second time, so it must queue nothing more"
    )


async def test_an_admin_who_never_signed_in_gets_a_user_row_first(session):
    """Notification.user_id is an FK to users.discord_id, so a queued notice
    for an admin with no row raises IntegrityError at flush, far from here."""
    await _quiet(session, "alpha-zzz")
    await run_quiet_ladder_pass(session, NOW)
    assert await session.get(User, ADMIN_ID) is not None


async def test_an_existing_admin_keeps_their_username(session):
    """ensure_user refreshes the username, so calling it unconditionally would
    overwrite a real admin's name with the placeholder on every tick."""
    await ensure_user(session, ADMIN_ID, "reiji")
    await _quiet(session, "alpha-zzz")
    await run_quiet_ladder_pass(session, NOW)

    admin = await session.get(User, ADMIN_ID)
    assert admin.username == "reiji"


async def test_every_admin_gets_their_own_notice(session, monkeypatch):
    """The single-admin fixture above conflates "one digest per pass" with
    "one digest total": a mutation that only ever queued for
    min(settings.admin_ids) would still pass every test above. Two admins,
    one notice each, identical bodies -- the same digest fanned out, not
    computed twice."""
    monkeypatch.setattr(settings, "admin_whitelist", f"{ADMIN_ID},{OTHER_ADMIN_ID}")
    await _quiet(session, "alpha-zzz")
    await run_quiet_ladder_pass(session, NOW)

    notices = await _notices(session)
    assert {n.user_id for n in notices} == {ADMIN_ID, OTHER_ADMIN_ID}
    assert len(notices) == 2
    assert notices[0].body == notices[1].body


async def test_the_pass_does_not_commit(session):
    """The exactly-once pairing (invariant: stamps + notice commit together)
    is the CALLER's job -- loop.tick commits right after calling this. If
    run_quiet_ladder_pass committed internally, a caller rollback for an
    unrelated reason could no longer take the notice back with it, and the
    stamp/notice pairing would stop being atomic."""
    await _quiet(session, "alpha-zzz")
    await session.commit()  # the concert itself is real and durable

    await run_quiet_ladder_pass(session, NOW)
    await session.rollback()

    assert await _notices(session) == [], "the queued notice must not survive a rollback"
    stamp = (await session.execute(
        select(Concert.quiet_since_utc).where(Concert.event_id == "alpha-zzz")
    )).scalar_one()
    assert stamp is None, "the quiet_since_utc stamp must not survive a rollback either"
