"""The approval queue's reads and writes, and the completion candidate list."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.db.models import PendingDraft, User
from app.db.service import (
    approved_fetch_hosts,
    completion_candidates,
    decide_fetch_domain,
    fetch_domain_rows,
    note_fetch_domain,
    pending_fetch_domain_count,
    pending_triage_run,
    request_triage,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def session(db):
    """conftest's `session`, plus the two users this module's rows point at.

    Overrides the shared fixture deliberately -- `request_triage` and the
    `PendingDraft` rows below carry FKs to `users.discord_id`, and with
    PRAGMA foreign_keys=ON (which conftest registers) an unseeded database
    fails them at flush, several frames from the actual subject.
    """
    async with db() as s:
        s.add_all([User(discord_id=1, username="a"), User(discord_id=2, username="b")])
        await s.flush()
        yield s


async def test_noting_a_host_twice_makes_one_pending_row(session):
    a = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", NOW)
    b = await note_fetch_domain(session, "EPLUS.JP", "https://eplus.jp/b", NOW)
    assert a.id == b.id
    # The FIRST url is kept: it is what the approver was told about.
    assert b.first_seen_url == "https://eplus.jp/a"
    assert await pending_fetch_domain_count(session) == 1


async def test_note_fetch_domain_strips_a_trailing_dns_root_dot(session):
    row = await note_fetch_domain(session, "eplus.jp.", "https://eplus.jp/a", NOW)
    assert row.host == "eplus.jp"


async def test_note_fetch_domain_idna_encodes_a_unicode_host(session):
    row = await note_fetch_domain(
        session, "日本語.jp", "https://xn--wgv71a119e.jp/a", NOW
    )
    assert row.host == "xn--wgv71a119e.jp"


async def test_note_fetch_domain_rejects_a_host_with_a_port(session):
    # `_normalize_host` alone does NOT reject this shape (verified: it comes
    # back unchanged but for case), so the function's own shape check is
    # what stands between a careless caller and a garbage row that
    # `approved_fetch_hosts()` could never match.
    with pytest.raises(ValueError):
        await note_fetch_domain(session, "eplus.jp:443", "https://eplus.jp/a", NOW)


async def test_note_fetch_domain_rejects_a_full_url(session):
    with pytest.raises(ValueError):
        await note_fetch_domain(
            session, "https://eplus.jp/a", "https://eplus.jp/a", NOW
        )


async def test_note_fetch_domain_falls_back_to_strip_lower_for_unencodable_idna(session):
    # A label over 63 characters cannot be IDNA-encoded. Such a host can
    # never pass the fetch guard either way (it raises `HostNotAllowed` there
    # for the identical reason), so the fallback only needs to be readable,
    # not future-lookup-correct -- pinned here rather than left self-reported.
    bad = "a" * 70 + ".example"
    row = await note_fetch_domain(session, "  " + bad.upper() + "  ", "https://x/a", NOW)
    assert row.host == bad.lower()


async def test_fetch_domain_rows_puts_pending_first_and_newest_first_within_that(session):
    # A boolean expression as an ORDER BY key is not obviously well-defined,
    # so this pins the actual SQLite ordering rather than assuming it: False
    # (pending) sorts before True (decided) ascending, and within each group
    # first_seen_at DESC puts the newest row first.
    old_pending = await note_fetch_domain(session, "old.example", "https://old.example/1", NOW)
    new_pending = await note_fetch_domain(
        session, "new.example", "https://new.example/1", NOW + timedelta(minutes=1)
    )
    decided = await note_fetch_domain(
        session, "decided.example", "https://decided.example/1", NOW + timedelta(minutes=2)
    )
    await decide_fetch_domain(session, decided.id, True, NOW, 1)

    rows = await fetch_domain_rows(session)
    assert [r.id for r in rows] == [new_pending.id, old_pending.id, decided.id]


async def test_only_approved_hosts_come_back(session):
    row = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", NOW)
    await note_fetch_domain(session, "spam.example", "https://spam.example/a", NOW)
    assert await approved_fetch_hosts(session) == set()
    await decide_fetch_domain(session, row.id, True, NOW, 1)
    assert await approved_fetch_hosts(session) == {"eplus.jp"}
    assert await pending_fetch_domain_count(session) == 1


async def test_a_declined_host_is_neither_approved_nor_pending(session):
    row = await note_fetch_domain(session, "spam.example", "https://spam.example/a", NOW)
    await decide_fetch_domain(session, row.id, False, NOW, 1)
    assert await approved_fetch_hosts(session) == set()
    assert await pending_fetch_domain_count(session) == 0


async def test_deciding_an_already_decided_host_does_not_flip_it(session):
    row = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", NOW)
    assert await decide_fetch_domain(session, row.id, True, NOW, 1) is True
    assert await decide_fetch_domain(session, row.id, False, NOW, 1) is False
    assert await approved_fetch_hosts(session) == {"eplus.jp"}


async def test_noting_a_decided_host_never_reopens_it(session):
    row = await note_fetch_domain(session, "spam.example", "https://spam.example/a", NOW)
    await decide_fetch_domain(session, row.id, False, NOW, 1)
    again = await note_fetch_domain(session, "spam.example", "https://spam.example/b", NOW)
    assert again.declined_at is not None
    assert await pending_fetch_domain_count(session) == 0


def _draft(text, user=1, **over):
    row = PendingDraft(draft_text=text, title="t", created_by=user)
    for k, v in over.items():
        setattr(row, k, v)
    return row


async def test_completion_candidates_are_this_users_open_roundless_untried_drafts(session):
    wanted = _draft("title: a\nrounds: []\n")
    has_rounds = _draft("title: b\nrounds:\n- label: r\n")
    tried = _draft("title: c\nrounds: []\n", completion_yaml="rejected: []\n")
    other_user = _draft("title: d\nrounds: []\n", user=2)
    discarded = _draft("title: e\nrounds: []\n", discarded_at=NOW)
    session.add_all([wanted, has_rounds, tried, other_user, discarded])
    await session.flush()

    rows = await completion_candidates(session, 1)
    assert [r.title for r in rows] == ["t"]
    assert [r.id for r in rows] == [wanted.id]


async def test_a_triage_pass_record_does_not_spend_the_completion_pass_s_turn(session):
    """Two passes write `completion_yaml` now, and they read DIFFERENT PAGES:
    phase 1 reads Eventernote, phase 2 the official page. So a draft phase 1
    could not ground is precisely one that still wants phase 2 -- while a
    record from phase 2 itself, or from before the marker existed, still means
    "already paid for" (the reading that withholds an attempt rather than
    buying a second one)."""
    from_triage = _draft(
        "title: a\nrounds: []\n",
        completion_yaml="source_url: https://www.eventernote.com/events/1\npass: triage\n",
    )
    from_completion = _draft(
        "title: b\nrounds: []\n",
        completion_yaml="source_url: https://example.com/\npass: completion\n",
    )
    legacy = _draft("title: c\nrounds: []\n", completion_yaml="evidence: []\n")
    unreadable = _draft("title: d\nrounds: []\n", completion_yaml="[[ not yaml\n")
    session.add_all([from_triage, from_completion, legacy, unreadable])
    await session.flush()

    rows = await completion_candidates(session, 1)
    assert [r.id for r in rows] == [from_triage.id]


async def test_requesting_two_kinds_queues_two_runs(session):
    a = await request_triage(session, NOW, 1)
    b = await request_triage(session, NOW, 1, kind="complete")
    assert a.id != b.id
    # Idempotent PER KIND: a second press of the same button reuses its row.
    assert (await request_triage(session, NOW, 1, kind="complete")).id == b.id
    # The scheduler asks for the oldest of any kind.
    assert (await pending_triage_run(session)).id == a.id
    assert (await pending_triage_run(session, kind="complete")).id == b.id
