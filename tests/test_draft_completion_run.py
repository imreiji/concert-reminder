"""What one completion press does, and what each failure inside it costs.

No network and no key: the fetcher and the LLM client are injected, exactly as
run_sweep and run_triage take theirs.
"""

from datetime import UTC, datetime

import pytest_asyncio
import yaml
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, FetchDomain, Notification, PendingDraft, TriageRun
from app.db.service import decide_fetch_domain, ensure_user, note_fetch_domain
from app.draft_completion import complete_one, draft_source_url, run_completion
from app.llm import LlmReply

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
USER_ID = 1

SKELETON = """\
# source: https://www.eventernote.com/events/486243
title: 例）ライブ
official_url: https://eplus.jp/sf/detail/1234
performances:
- label: Day 1
  venue: Zepp Haneda
rounds: []
"""

PAGE = (
    "<html><body><p>1次先行抽選 申込締切 2026年1月10日(土)23:59</p></body></html>"
)

GOOD_REPLY = """\
rounds:
  - label: 1次先行抽選
    kind: lottery
    apply_closes_jst: 2026-01-10 23:59
    evidence:
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
"""

UNGROUNDED_REPLY = """\
rounds:
  - label: 2次先行抽選
    kind: lottery
    apply_closes_jst: 2026-02-20 23:59
    evidence:
      apply_closes_jst: "申込締切 2026年2月20日(土)23:59"
"""


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def fake_llm(reply_text):
    async def _chat(system, user, **kw):
        return LlmReply(text=reply_text, tokens_in=100, tokens_out=50)
    return _chat


async def fake_fetch(url):
    return PAGE


async def _seed(session, user_id=USER_ID, text=SKELETON):
    # Under PRAGMA foreign_keys=ON, PendingDraft.created_by must point at a
    # real users row before the insert -- ensure_user is idempotent, so
    # calling it from every _seed is safe even across several seeds in one
    # test.
    await ensure_user(session, user_id, "user")
    row = PendingDraft(draft_text=text, title="t", created_by=user_id)
    session.add(row)
    await session.flush()
    return row


def test_draft_source_url_prefers_official_over_source():
    assert draft_source_url(SKELETON) == "https://eplus.jp/sf/detail/1234"
    assert draft_source_url("source_url: https://x.example/a\nrounds: []\n") == (
        "https://x.example/a"
    )
    # eventernote_url is never used: Eventernote carries no ticket information.
    assert draft_source_url("eventernote_url: https://www.eventernote.com/events/1\n") is None


async def test_a_grounded_round_lands_in_the_draft(session):
    row = await _seed(session)
    added, rejected, _ti, _to = await complete_one(
        session, row, "1次先行抽選 申込締切 2026年1月10日(土)23:59",
        "https://eplus.jp/sf/detail/1234", llm_chat=fake_llm(GOOD_REPLY),
    )
    assert (added, rejected) == (1, 0)
    data = yaml.safe_load(row.draft_text.split("\n", 1)[1])
    assert data["rounds"][0]["apply_closes_jst"] == "2026-01-10 23:59"
    # Evidence stays OUT of the draft and beside it.
    assert "evidence" not in data["rounds"][0]
    assert "申込締切" in yaml.safe_load(row.completion_yaml)["evidence"][0]["apply_closes_jst"]


async def test_the_source_line_survives_a_completion(session):
    row = await _seed(session)
    await complete_one(
        session, row, "1次先行抽選 申込締切 2026年1月10日(土)23:59",
        "https://eplus.jp/x", llm_chat=fake_llm(GOOD_REPLY),
    )
    assert row.draft_text.startswith(
        "# source: https://www.eventernote.com/events/486243\n"
    )


async def test_an_ungrounded_round_is_dropped_and_reported(session):
    row = await _seed(session)
    added, rejected, _ti, _to = await complete_one(
        session, row, "1次先行抽選 申込締切 2026年1月10日(土)23:59",
        "https://eplus.jp/x", llm_chat=fake_llm(UNGROUNDED_REPLY),
    )
    assert (added, rejected) == (0, 1)
    assert yaml.safe_load(row.draft_text.split("\n", 1)[1])["rounds"] == []
    record = yaml.safe_load(row.completion_yaml)
    assert record["rejected"] and "not on the page" in record["rejected"][0]


async def test_an_attempt_that_found_nothing_still_marks_the_draft_tried(session):
    row = await _seed(session)
    await complete_one(session, row, "nothing here", "https://eplus.jp/x",
                       llm_chat=fake_llm("rounds: []\n"))
    # The call was paid for; a second press must not pay again.
    assert row.completion_yaml != ""


async def test_an_unapproved_host_is_never_fetched(session):
    row = await _seed(session)
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    async def explode(url):
        raise AssertionError("an unapproved host must never be fetched")

    report = await run_completion(
        session, run, NOW, fetcher=explode, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.blocked_domains == 1
    assert report.completed == 0
    # The host is now waiting for a human, and the draft is still a candidate.
    assert row.completion_yaml == ""
    assert (await session.get(FetchDomain, 1)).host == "eplus.jp"


async def test_an_approved_host_is_fetched_and_completed(session):
    await _seed(session)
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, USER_ID)
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    report = await run_completion(
        session, run, NOW, fetcher=fake_fetch, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.completed == 1 and report.rounds_added == 1
    assert run.status == "done"
    assert run.drafts_completed == 1
    assert run.tokens_in == 100 and run.tokens_out == 50


async def test_a_draft_with_no_url_is_skipped_and_left_retryable(session):
    row = await _seed(session, text="title: x\nperformances: []\nrounds: []\n")
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()
    report = await run_completion(
        session, run, NOW, fetcher=fake_fetch, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.skipped == 1 and report.completed == 0
    assert row.completion_yaml == ""


async def test_a_dead_fetch_costs_one_draft_not_the_run(session):
    # Two candidates: the ORDER matters (completion_candidates orders by id),
    # so the first fetch is the one `flaky` fails, and the second one recovers.
    await _seed(session)
    await _seed(session)
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, USER_ID)
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    calls = {"n": 0}

    async def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return PAGE

    report = await run_completion(
        session, run, NOW, fetcher=flaky, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.skipped == 1 and report.completed == 1
    assert run.status == "done"


async def test_an_empty_queue_costs_nothing_and_announces_nothing(session, monkeypatch):
    # An admin IS configured here, unlike the other tests, so this actually
    # proves _announce was skipped rather than merely proving nobody was
    # configured to hear it -- mirrors test_triage_run.py's
    # test_zero_open_leads_costs_no_llm_call.
    monkeypatch.setattr(settings, "admin_whitelist", str(USER_ID))
    await ensure_user(session, USER_ID, "user")
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    async def explode(url):
        raise AssertionError("nothing to fetch")

    report = await run_completion(session, run, NOW, fetcher=explode,
                                  llm_chat=fake_llm(GOOD_REPLY))
    assert report.drafts_seen == 0
    assert run.status == "done"
    assert (await session.execute(select(Notification))).scalars().all() == []
