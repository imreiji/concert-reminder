"""What one completion press does, and what each failure inside it costs.

No network and no key: the fetcher and the LLM client are injected, exactly as
run_sweep and run_triage take theirs.
"""

from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db.models import FetchDomain, Notification, PendingDraft, TriageRun
from app.db.service import decide_fetch_domain, ensure_user, note_fetch_domain
from app.domain.round_completion import CompletionResponseError, DraftMergeError
from app.draft_completion import (
    COMPLETION_USER_AGENT,
    _user_agent_for,
    complete_one,
    draft_source_url,
    run_completion,
)
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


async def test_llm_timeout_defaults_to_none_and_is_not_passed_to_llm_chat(session):
    """`run_completion`'s batch loop never sets `llm_timeout` -- it holds no
    HTTP request open, so it must keep llm.chat's own 120s default rather
    than have some other value silently imposed on it. Confirmed by checking
    `llm_chat` never received a `timeout` kwarg at all, not merely that it
    received the "right" one -- a stray `timeout=None` reaching a real
    provider client would be its own bug."""
    seen_kwargs = {}

    async def _chat(system, user, **kw):
        seen_kwargs.update(kw)
        return LlmReply(text=GOOD_REPLY, tokens_in=1, tokens_out=1)

    row = await _seed(session)
    await complete_one(
        session, row, "1次先行抽選 申込締切 2026年1月10日(土)23:59",
        "https://eplus.jp/x", llm_chat=_chat,
    )
    assert "timeout" not in seen_kwargs


async def test_llm_timeout_when_given_is_threaded_to_llm_chat(session):
    """The paste route's one call site (test_draft_completion_preview.py)
    passes an explicit, shorter timeout so a slow call fails legibly before
    Cloudflare's proxy wall does it for them, uncontrolled. This is the unit
    half of that contract: complete_one must actually forward whatever value
    it is given."""
    seen_kwargs = {}

    async def _chat(system, user, **kw):
        seen_kwargs.update(kw)
        return LlmReply(text=GOOD_REPLY, tokens_in=1, tokens_out=1)

    row = await _seed(session)
    await complete_one(
        session, row, "1次先行抽選 申込締切 2026年1月10日(土)23:59",
        "https://eplus.jp/x", llm_chat=_chat, llm_timeout=42.0,
    )
    assert seen_kwargs.get("timeout") == 42.0


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


async def test_an_approved_host_is_fetched_and_completed(session, monkeypatch):
    # An admin is configured so the positive half of invariant 4 (a
    # non-empty run queues exactly one Notification per admin) is actually
    # exercised here, mirroring test_triage_run.py's happy-path test.
    monkeypatch.setattr(settings, "admin_whitelist", str(USER_ID))
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
    # EVERY column _finish writes, asserted on the row rather than only on
    # the report: setting an unmapped attribute on a mapped instance
    # succeeds silently, so a typo'd column name in _finish would never
    # surface (mirrors test_triage_run.py's happy-path assertion).
    assert run.started_at == NOW
    assert run.finished_at == NOW
    assert run.drafts_completed == 1
    assert run.rounds_added == 1
    assert run.rounds_rejected == 0
    assert run.blocked_domains == 0
    assert run.skipped == 0
    assert run.tokens_in == 100 and run.tokens_out == 50
    notes = (await session.execute(select(Notification))).scalars().all()
    assert [n.kind for n in notes] == ["triage"]
    assert notes[0].concert_id is None


async def test_fetcher_none_uses_the_approved_host_policy_wiring(session, monkeypatch):
    """Every other test injects a fetcher; nothing exercises the four-line
    production wiring (`fetch = fetcher or (lambda url: _fetch_page(url,
    hosts))`) that binds the run's own approved-host set to the fetch
    policy. Monkeypatch `_fetch_page` itself (module-global, so the lambda's
    call-time lookup sees the replacement) rather than `fetch_html`, so this
    stays a pure wiring test and not a second copy of ApprovedPublicHosts'
    own tests."""
    monkeypatch.setattr("app.draft_completion.COMPLETION_DELAY_SECONDS", 0)
    await _seed(session)
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, USER_ID)
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    calls = []

    async def fake_fetch_page(url, hosts):
        calls.append((url, hosts))
        return PAGE

    monkeypatch.setattr("app.draft_completion._fetch_page", fake_fetch_page)

    report = await run_completion(
        session, run, NOW, fetcher=None, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.completed == 1
    assert calls == [("https://eplus.jp/sf/detail/1234", {"eplus.jp"})]


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


async def test_an_unstorable_host_is_skipped_not_the_run(session):
    """official_url is model- or agent-authored free text. urlparse() keeps
    an IPv6 literal's colons (`2606:4700::1111`), which slip past this
    module's own host-normalize check -- so the draft reaches
    note_fetch_domain, which refuses to store a host containing ':' and
    raises ValueError. That must cost this one draft, not the run: an
    unhandled ValueError here would kill the whole press and leave every
    draft already completed unmarked, so the next press re-pays for them and
    dies on this same row again."""
    bad = await _seed(session, text=(
        "title: x\nofficial_url: https://[2606:4700::1111]/a\n"
        "performances: []\nrounds: []\n"
    ))
    await _seed(session)  # a normal, approved-host candidate behind it
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, USER_ID)
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    report = await run_completion(
        session, run, NOW, fetcher=fake_fetch, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert run.status == "done"
    assert report.skipped == 1
    assert report.completed == 1
    assert report.blocked_domains == 0
    assert bad.completion_yaml == ""
    assert (await session.execute(select(FetchDomain))).scalars().all() == [domain]


async def test_an_unparseable_reply_still_marks_the_draft_tried(session):
    row = await _seed(session)
    with pytest.raises(CompletionResponseError):
        await complete_one(
            session, row, "some page text", "https://eplus.jp/x",
            llm_chat=fake_llm("not yaml at all: [["),
        )
    # The call was paid for; a second press must not pay again.
    assert row.completion_yaml != ""
    record = yaml.safe_load(row.completion_yaml)
    assert record["rejected"]


async def test_a_draft_merge_error_still_marks_the_draft_tried(session):
    # A hand-corrupted stored draft: not a YAML mapping at all, so
    # merge_rounds refuses to touch it. Currently unreachable through
    # completion_candidates (it gates on parse_draft succeeding first) but
    # reachable once a paste-fallback route calls complete_one directly on
    # arbitrary stored text -- exercised the same way here.
    row = await _seed(session, text="- just a list\n")
    with pytest.raises(DraftMergeError):
        await complete_one(
            session, row, "some page text", "https://eplus.jp/x",
            llm_chat=fake_llm(GOOD_REPLY),
        )
    assert row.completion_yaml != ""
    record = yaml.safe_load(row.completion_yaml)
    assert record["rejected"]


async def test_a_poisoned_session_stops_the_run(session, monkeypatch):
    """A failed flush poisons the session: nothing after it can persist, so
    absorbing the error into the per-draft skip would pay for more calls to
    write nothing. SQLAlchemyError therefore re-raises, and the scheduler's
    handler marks the run failed on a cleaned transaction -- mirrors
    test_triage_run.py's test_a_poisoned_session_stops_the_run."""
    monkeypatch.setattr("app.draft_completion.COMPLETION_DELAY_SECONDS", 0)
    await _seed(session)
    await _seed(session)
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, USER_ID)
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    async def boom(*args, **kwargs):
        raise OperationalError("UPDATE pending_drafts", {}, Exception("disk I/O"))

    # complete_one is looked up as a bare module-global name each call, so
    # patching it here reaches run_completion's internal call exactly as
    # test_triage_run.py patches create_pending_drafts.
    monkeypatch.setattr("app.draft_completion.complete_one", boom)

    with pytest.raises(OperationalError):
        await run_completion(
            session, run, NOW, fetcher=fake_fetch, llm_chat=fake_llm(GOOD_REPLY)
        )
    # The runner marks no failure of its own, as ever.
    assert run.status == "requested"


async def test_the_draft_cap_bounds_llm_calls_per_press(session, monkeypatch):
    monkeypatch.setattr("app.draft_completion.COMPLETION_DELAY_SECONDS", 0)
    monkeypatch.setattr("app.draft_completion.COMPLETION_DRAFT_CAP", 2)
    for _ in range(4):
        await _seed(session)
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, USER_ID)
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    calls = []

    async def counting_llm(system, user, **kw):
        calls.append(1)
        return LlmReply(text=GOOD_REPLY, tokens_in=100, tokens_out=50)

    report = await run_completion(
        session, run, NOW, fetcher=fake_fetch, llm_chat=counting_llm
    )
    assert len(calls) == 2, "the cap bounds the CALLS, whatever the queue's size"
    assert report.completed == 2
    assert report.drafts_seen == 4
    assert run.status == "done"


async def test_the_budget_stops_the_loop_and_leaves_survivors_as_candidates(
    session, monkeypatch
):
    """The clock is faked rather than slept through: a real 240s budget is
    not a test. monotonic is called once for the deadline and once per
    draft, so the second draft's check is the one that lands past it --
    mirrors test_triage_run.py's equivalent budget test."""
    monkeypatch.setattr("app.draft_completion.COMPLETION_DELAY_SECONDS", 0)
    clock = iter([0.0, 0.0, 1000.0])
    monkeypatch.setattr("app.draft_completion.monotonic", lambda: next(clock))

    await _seed(session)
    later = await _seed(session)
    domain = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, USER_ID)
    run = TriageRun(requested_at=NOW, requested_by=USER_ID, kind="complete")
    session.add(run)
    await session.flush()

    report = await run_completion(
        session, run, NOW, fetcher=fake_fetch, llm_chat=fake_llm(GOOD_REPLY)
    )
    assert report.budget_exhausted is True
    assert report.completed == 1, "the second draft's check landed past the deadline"
    assert run.status == "done", "a truncated run is finished, not failed"
    # Left untouched and still a candidate for the next press.
    assert later.completion_yaml == ""


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


# -- The per-host User-Agent table -----------------------------------------
#
# `lovelive-anime.jp` answers the app's own UA with a 403 (owner ruling,
# 2026-08-10; see HOST_USER_AGENTS). These pin the exception's SHAPE -- that it
# is per-host and that the honest UA remains the default everywhere else --
# because the failure mode of getting this wrong is silent: a UA that leaks to
# every host is not visibly different from one that does not until somebody
# reads the outbound request.


def test_the_default_user_agent_is_the_honest_one():
    for url in (
        "https://idolmaster-official.jp/live_event/gkmas_livetour_shirube/",
        "https://zombielandsaga-movie.com/collaboration/detail.php?id=1002644",
        "https://example.com/",
    ):
        assert _user_agent_for(url) == COMPLETION_USER_AGENT, url


def test_the_refusing_host_gets_a_browser_string():
    ua = _user_agent_for("https://www.lovelive-anime.jp/hasunosora/")
    assert ua != COMPLETION_USER_AGENT
    assert ua.startswith("Mozilla/5.0")


def test_the_table_is_matched_on_the_normalized_host():
    # Same three spellings the approval policy normalizes together: casing, a
    # trailing DNS root dot, and the bare domain an editor might paste. A miss
    # on any of them is a 403 nobody would connect to a URL's spelling.
    for url in (
        "https://WWW.LoveLive-Anime.JP/yuigaoka/",
        "https://www.lovelive-anime.jp./yuigaoka/",
        "https://lovelive-anime.jp/yuigaoka/",
    ):
        assert _user_agent_for(url).startswith("Mozilla/5.0"), url


def test_a_malformed_host_falls_through_to_the_default():
    # fetch_html is about to reject these anyway; the UA lookup is not where
    # that decision belongs, and it must not raise on the way there.
    for url in ("not-a-url", "", "https:///nohost", "http://[::bad::]/x"):
        assert _user_agent_for(url) == COMPLETION_USER_AGENT, url
