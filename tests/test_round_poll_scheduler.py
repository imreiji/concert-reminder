"""The scheduler hook and the digest: gated by a flag, held to a day, unable to
hurt delivery, and never DMed directly.

No network and no key anywhere in this file -- `run_round_poll` is stubbed at
the `loop_mod` seam, because what is under test here is the RUN ORDER around
it: when it is allowed to run, what a failure inside it costs, and what the
owner is told afterwards.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.scheduler.loop as loop_mod
from app.config import Settings, settings
from app.db.models import Notification, RoundPollState, User
from app.db.service import ROUND_POLL_NOTE_KIND, UNREPORTED_NOTE_KINDS
from app.round_poll import PollReport, build_poll_digest

ADMIN_ID = 1
BASE_URL = "https://dekimasen.app"


class FakeUser:
    def __init__(self):
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


class FakeBot:
    def __init__(self):
        self.user_obj = FakeUser()

    def get_user(self, _uid):
        return self.user_obj


@pytest_asyncio.fixture()
async def maker(db, monkeypatch):
    """The shared in-memory DB, wired into the scheduler's SessionMaker."""
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "SessionMaker", db)
    monkeypatch.setattr(loop_mod, "SessionMaker", db)
    # Tick 1 of 5: keep the health/prune cadence out of these assertions.
    monkeypatch.setattr(loop_mod, "_tick_count", 0)
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "base_url", BASE_URL)
    return db


def _recorder(monkeypatch, report=None):
    """A stub run_round_poll that records the moments it was called at."""
    calls = []

    async def fake_poll(session, now, **kw):
        calls.append(now)
        return report if report is not None else PollReport()

    monkeypatch.setattr(loop_mod, "run_round_poll", fake_poll)
    return calls


def _boom(monkeypatch):
    async def boom(*_a, **_kw):
        raise RuntimeError("the poll exploded")

    monkeypatch.setattr(loop_mod, "run_round_poll", boom)


async def _state(maker):
    async with maker() as s:
        return (await s.execute(select(RoundPollState))).scalar_one_or_none()


async def _digests(maker):
    async with maker() as s:
        return list((await s.execute(
            select(Notification).where(Notification.kind == ROUND_POLL_NOTE_KIND)
        )).scalars())


# ── The flag ─────────────────────────────────────────────────────────────


def test_the_flag_ships_off():
    """Nothing re-reads a third-party page, and nothing spends a key, until an
    operator turns it on. The CLASS default, which no local `.env` can reach."""
    assert Settings.model_fields["round_poll_enabled"].default is False


@pytest.mark.asyncio
async def test_the_pass_does_not_run_when_the_flag_is_off(maker, monkeypatch):
    """Mutation: dropping the flag check -- production starts fetching
    third-party pages, and paying for LLM calls, on deploy and unasked."""
    monkeypatch.setattr(settings, "round_poll_enabled", False)
    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert calls == []
    assert await _state(maker) is None, "and the clock was never started either"


@pytest.mark.asyncio
async def test_the_flag_on_runs_the_pass_and_commits_its_stamp(maker, monkeypatch):
    """The control for every test below: with the flag on and no stamp yet, the
    pass runs once and its own transaction is committed."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert len(calls) == 1
    state = await _state(maker)
    assert state is not None and state.last_run_at is not None


@pytest.mark.asyncio
async def test_a_poll_that_already_ran_today_is_not_repeated(maker, monkeypatch):
    """One fetch and one LLM call per quiet concert per DAY, not per minute."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    async with maker() as s:
        s.add(RoundPollState(id=1, last_run_at=datetime.now(UTC) - timedelta(hours=2)))
        await s.commit()
    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert calls == []


# ── Failure isolation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_run_is_still_stamped_as_todays_run(maker, monkeypatch):
    """THE rollback path. The block stamps the clock before the run, and the
    handler's rollback takes that stamp with it -- so the handler must re-stamp
    on the clean transaction.

    Mutation: dropping the except-branch stamp. Without it, a poll that dies on
    one malformed third-party page leaves round_poll_due true, runs again 60
    seconds later, and dies the same way forever -- with a fetch and a paid LLM
    call attached to every repeat."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    _boom(monkeypatch)

    await loop_mod.tick(FakeBot())

    state = await _state(maker)
    assert state is not None, "the rollback wiped the stamp; nothing re-wrote it"
    assert state.last_run_at is not None


@pytest.mark.asyncio
async def test_a_failed_run_is_not_retried_on_the_next_tick(maker, monkeypatch):
    """The consequence of the test above, asserted as behaviour rather than as
    a row: the tick a minute later must not poll again."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    _boom(monkeypatch)
    await loop_mod.tick(FakeBot())

    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert calls == []


@pytest.mark.asyncio
async def test_a_failing_poll_does_not_kill_the_tick(maker, monkeypatch):
    """Why the block owns its try/except and its own commit. Mutation: removing
    the try/except -- the exception then escapes `tick`, so every block AFTER
    the poll (today, the triage run) is skipped and the tick's delivered count
    is lost. A subsystem nobody has to turn on must not be able to take down
    the one that delivers reminders every minute.

    The delivered notice is the CONTROL, not the claim: it is committed before
    this block runs, so a later raise cannot un-send it. What the mutation
    actually breaks is the return below."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    async with maker() as s:
        s.add(User(discord_id=7, username="reiji"))
        await s.flush()
        s.add(Notification(user_id=7, body="hello", kind="ops_alert"))
        await s.commit()

    _boom(monkeypatch)

    assert await loop_mod.tick(FakeBot()) == 1, "the tick survives and still reports"

    async with maker() as s:
        note = (await s.execute(
            select(Notification).where(Notification.kind == "ops_alert")
        )).scalar_one()
        assert note.sent_at_utc is not None


# ── The digest ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_digest_is_queued_not_sent(maker, monkeypatch):
    """Invariant 4: notices go through the outbox, never straight out of a pass
    or a scheduler block.

    Mutation: DMing from the block. A Notification row existing does NOT by
    itself prove that -- a block that queued the row AND sent it directly would
    leave the same row -- so this asserts both halves: the row is there,
    unsent, and the bot was asked to send nothing this tick."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    _recorder(monkeypatch, PollReport(concerts_seen=2, polled=2, new_proposals=1))

    bot = FakeBot()
    await loop_mod.tick(bot)

    rows = await _digests(maker)
    assert len(rows) == 1
    assert rows[0].user_id == ADMIN_ID
    assert rows[0].sent_at_utc is None, "queued for the drain, not already sent"
    assert rows[0].concert_id is None, "NULL is what selects the plain-text path"
    assert bot.user_obj.sent == [], "nothing was DMed during the tick that queued it"


@pytest.mark.asyncio
async def test_the_queued_digest_reaches_the_admin_as_plain_text(maker, monkeypatch):
    """The other half of the outbox contract, end to end: the drain delivers it
    on the NEXT tick, and `concert_id = NULL` is what makes it plain text
    instead of a rich embed the notice has no context to build.

    Mutation: queueing it with a concert_id -- the send path would then try to
    build an embed for a notice that describes no single concert."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    _recorder(monkeypatch, PollReport(concerts_seen=1, polled=1))

    bot = FakeBot()
    await loop_mod.tick(bot)
    await loop_mod.tick(bot)

    assert len(bot.user_obj.sent) == 1
    args, kwargs = bot.user_obj.sent[0]
    assert kwargs == {}, "the plain-text path passes no embed and no view"
    assert "Round poll" in args[0]


def test_the_digest_kind_is_not_in_UNREPORTED_NOTE_KINDS():
    """It reports on a THIRD-PARTY PAGE, not on deliveries -- the `discovery`
    notice's precedent exactly. Mutation: adding it to that set, which would
    stop it being written to delivery_log, the one surface that can answer
    whether the owner was actually reached."""
    assert ROUND_POLL_NOTE_KIND not in UNREPORTED_NOTE_KINDS


@pytest.mark.asyncio
async def test_an_admin_who_never_signed_in_gets_a_users_row(maker, monkeypatch):
    """Notification.user_id is an FK to users.discord_id, so without this the
    queue raises IntegrityError at flush, far from the cause."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    _recorder(monkeypatch)

    await loop_mod.tick(FakeBot())

    async with maker() as s:
        assert await s.get(User, ADMIN_ID) is not None
    assert len(await _digests(maker)) == 1


@pytest.mark.asyncio
async def test_a_real_admins_username_survives_the_digest(maker, monkeypatch):
    """Mutation: calling ensure_user unconditionally instead of only when
    session.get(User, admin_id) returns None. ensure_user REFRESHES the
    username, so the unconditional call overwrites a real admin's name with the
    numeric placeholder on every single run."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    _recorder(monkeypatch)
    async with maker() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.commit()

    await loop_mod.tick(FakeBot())

    async with maker() as s:
        assert (await s.get(User, ADMIN_ID)).username == "reiji"


@pytest.mark.asyncio
async def test_a_run_that_found_nothing_still_reports(maker, monkeypatch):
    """What the digest is FOR. `RoundPollState` stores only `last_run_at`, on
    the reasoning that the digest answers "was it broken or was it quiet" -- so
    a quiet run must still say so. Suppressing an empty one (which is right for
    the discovery and quiet-ladder WORKLIST DMs) would make a dead pass and a
    quiet one indistinguishable, which is the silence this feature exists to
    remove."""
    monkeypatch.setattr(settings, "round_poll_enabled", True)
    _recorder(monkeypatch, PollReport(concerts_seen=3, polled=3))

    await loop_mod.tick(FakeBot())

    rows = await _digests(maker)
    assert len(rows) == 1
    assert "3 polled" in rows[0].body


# ── What the digest says ─────────────────────────────────────────────────


def _digest(**kw) -> str:
    return build_poll_digest(PollReport(**kw), base_url=BASE_URL)


def test_a_new_proposal_and_a_re_sighting_are_reported_apart():
    """The pass re-reads the same page daily, so an unreviewed proposal is
    re-proposed every morning. Folded into one number this DM would say "3 new
    proposals" every day until the owner acts, which teaches them to skim past
    the rejection list underneath.

    Mutation: reporting `new_proposals + refreshed` as one count."""
    body = _digest(concerts_seen=5, polled=5, new_proposals=1, refreshed=3)
    assert "**1 new proposal**" in body
    assert "3 proposals seen again" in body
    assert "not new" in body
    assert "4 new" not in body


def test_a_host_awaiting_approval_is_not_reported_as_one_already_declined():
    """`skipped_host` is a click at /admin/fetch-domains; `skipped_declined` is
    a human's answered no. Mutation: adding the two together -- the owner is
    then sent to a screen with nothing on it to press."""
    body = _digest(concerts_seen=2, skipped_host=1, skipped_declined=1)
    assert f"1 concert waiting on your host approval — {BASE_URL}/admin/fetch-domains" in body
    assert "1 concert on a host you already declined" in body
    assert "2 concert" not in body


def test_every_rejection_reason_is_named():
    """The point of the whole message: a real deadline discarded without a
    reason looks exactly like a page that had nothing on it.

    Mutation: reporting `rounds_rejected` as a bare count with the reasons
    dropped."""
    body = _digest(
        concerts_seen=1,
        polled=1,
        rounds_rejected=2,
        rejections=[
            "live-2026: round '2次先行' quotes a line the page does not contain",
            "live-2026: round '3次先行' has no label",
        ],
    )
    assert "2次先行' quotes a line the page does not contain" in body
    assert "3次先行' has no label" in body


def test_a_concert_that_could_not_be_read_is_named_with_its_error():
    body = _digest(concerts_seen=1, failed=1, failures=["live-2026: FetchFailed: HTTP 403"])
    assert "live-2026: FetchFailed: HTTP 403" in body
    assert "1 concert could not be read" in body


def test_a_runaway_reason_list_still_fits_one_dm():
    """Past Discord's 2000-char limit discord.py raises and the WHOLE message
    is lost -- the one outcome a message about silent discards must not have.

    Mutation: dropping the shrink loop. A bad model day produces one rejection
    line per refused round across every concert polled, which is unbounded."""
    body = _digest(
        concerts_seen=40,
        polled=40,
        rounds_rejected=200,
        rejections=[f"concert-{i}: round {i} quotes a line not on the page" for i in range(200)],
        failed=50,
        failures=[f"concert-{i}: RuntimeError: boom" for i in range(50)],
    )
    assert len(body) <= 1900
    assert "…and" in body, "and it says how many it could not name"
    assert "200 refused by the evidence rule" in body, "the real count survives the trim"
    assert "50 concerts could not be read" in body


def test_a_truncated_run_says_so():
    """Mutation: dropping the budget line. A run that stopped early looks
    identical to one that found the rest of the catalogue clean."""
    assert "time budget" in _digest(concerts_seen=40, polled=12, budget_exhausted=True)
    assert "time budget" not in _digest(concerts_seen=12, polled=12)
