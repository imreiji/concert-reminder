"""TriageRun: a request queue of one, the service CRUD around it, and the
runner that drains it.

Mirrors DiscoveryState's request/stamp shape (see test_discovery_sweep.py),
but a triage run is a ROW per request rather than a single-row state table --
each run keeps its own counts, so /admin/discoveries/triage can show a
history rather than only "last run".

The second half of the file exercises `app.triage.run_triage` with canned LLM
replies and a canned page fetch, the way test_discovery_sweep.py exercises
`run_sweep` with a canned actor page: no network, no key, no scheduler."""

import logging
from datetime import UTC, date, datetime, timedelta

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import service
from app.db.models import (
    DiscoveredEvent,
    Notification,
    PendingDraft,
    TriageRun,
    User,
)
from app.db.service import ensure_user
from app.domain.prune_list import parse_prune_list
from app.domain.triage_prompts import TriageResponseError
from app.domain.yaml_import import parse_draft
from app.llm import LlmError, LlmReply
from app.scheduler import heartbeat
from app.triage import run_triage

NOW = datetime(2026, 8, 5, 3, 0, tzinfo=UTC)
ADMIN_ID = 900001
# An admin id with no `users` row -- the Notification FK case run_triage has to
# guard, exactly as run_sweep does.
STRANGER_ADMIN_ID = 900002


async def test_request_triage_is_idempotent_while_one_is_pending(db):
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        first = await service.request_triage(s, NOW, ADMIN_ID)
        second = await service.request_triage(s, NOW + timedelta(minutes=1), ADMIN_ID)
        assert first.id == second.id
        rows = (await s.execute(select(TriageRun))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "requested"


async def test_pending_picks_the_oldest_requested_row(db):
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        run = await service.request_triage(s, NOW, ADMIN_ID)
        run.status = "failed"  # simulate a finished one, then request again
        await s.flush()
        newer = await service.request_triage(s, NOW + timedelta(hours=1), ADMIN_ID)
        assert (await service.pending_triage_run(s)).id == newer.id
        assert (await service.latest_triage_run(s)).id == newer.id


async def test_mark_triage_failed_refetches_by_id(db):
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        run = await service.request_triage(s, NOW, ADMIN_ID)
        run_id = run.id
        await s.commit()
    async with db() as s:  # fresh session: the loop's post-rollback state
        await service.mark_triage_failed(s, run_id, NOW, "boom " * 100)
        await s.commit()
    async with db() as s:
        row = await service.get_triage_run(s, run_id)
        assert row.status == "failed"
        assert row.finished_at == NOW
        assert len(row.error) <= 300


async def test_pending_draft_texts_excludes_committed_and_discarded(db):
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        s.add(PendingDraft(draft_text="open one", title="a", created_by=ADMIN_ID))
        s.add(PendingDraft(draft_text="done", title="b", created_by=ADMIN_ID,
                           committed_at=NOW))
        s.add(PendingDraft(draft_text="gone", title="c", created_by=ADMIN_ID,
                           discarded_at=NOW))
        await s.flush()
        assert await service.pending_draft_texts(s) == ["open one"]


# ── The runner (app/triage.py) ────────────────────────────────────────────
#
# Two canned replies stand in for the two prompts: one classify response over
# the whole lead batch, then one draft response per surviving production.

CLASSIFY_REPLY = """dismiss:
  stage: ['481833']
survivors:
  - title: "学マス LIVE"
    lead_ids: ['486243']
    representative: '486243'
  - title: "calendar only"
    lead_ids: ['imas-tickets:x@google.com']
    representative: null
"""

# Two survivors that BOTH have an Eventernote page, so the draft loop makes two
# fetch+LLM passes rather than skipping the second as calendar-only. That is what
# lets the budget test show a stop mid-loop and the poisoned-session test show
# that nothing was spent after the failure.
TWO_DRAFTABLE_REPLY = """dismiss: {}
survivors:
  - title: "学マス LIVE"
    lead_ids: ['486243']
    representative: '486243'
  - title: "もうひとつ"
    lead_ids: ['481833']
    representative: '481833'
"""

# The page every draft call in this file reads, shaped like the real
# Eventernote event page it stands in for: a script the text pass must drop, a
# leg's own facts, and a 受付期間 block stating the whole round. The quoted
# ladder line is VERBATIM from eventernote.com/events/473609, whose deadline
# the owner's catalogue records to the minute.
PAGE_HTML = """<html><head><style>.x{color:red}</style></head><body>
<script>var tracking = "2020年1月1日（水）00:00";</script>
<h1>学マス LIVE</h1>
<div>開催日時 2026-09-12 (土) 開場 15:00 開演 16:00</div>
<div>開催場所 Zepp Haneda</div>
<p>■最速抽選先行受付期間（一般指定席のみ販売）※本先行受付は抽選受付です。
2026年4月5日（日）21:00 ～ 2026年4月19日（日）23:59</p>
</body></html>"""

# The evidence line as it reads AFTER html_to_text: the newline inside the <p>
# collapses to one space, which is the text the model is shown and therefore
# the text a verbatim quote has to be.
LADDER_LINE = (
    "■最速抽選先行受付期間（一般指定席のみ販売）※本先行受付は抽選受付です。 "
    "2026年4月5日（日）21:00 ～ 2026年4月19日（日）23:59"
)

# Fenced, and carrying the round the page really states, quoted verbatim: the
# owner's 2026-08-10 ruling is that such a round SURVIVES, verified by
# `verify_rounds` rather than deleted unread.
DRAFT_REPLY = f"""```yaml
title: 学マス LIVE
title_en: Gakumas Live
title_zh: 学马斯演唱会
rounds:
  - label: 最速抽選先行
    kind: lottery_round
    applies_to: [Day 1]
    apply_opens_jst: 2026-04-05 21:00
    apply_closes_jst: 2026-04-19 23:59
    evidence:
      apply_opens_jst: "{LADDER_LINE}"
      apply_closes_jst: "{LADDER_LINE}"
performances:
  - label: Day 1
    venue: Zepp Haneda
```"""

# The same draft, with a deadline the page never states and a quote that is not
# on it -- the plain hallucination, and the one this whole change rests on
# being caught.
INVENTED_DRAFT_REPLY = """```yaml
title: 学マス LIVE
title_en: Gakumas Live
title_zh: 学马斯演唱会
rounds:
  - label: 最速先行
    kind: lottery_round
    apply_closes_jst: 2026-09-01 23:59
    evidence:
      apply_closes_jst: "申込締切 2026年9月1日（火）23:59"
performances:
  - label: Day 1
    venue: Zepp Haneda
```"""


def _llm(replies):
    """A canned chat function: one reply per call, in order."""
    calls = []

    async def fake(system, user):
        calls.append((system, user))
        return LlmReply(text=replies[len(calls) - 1], tokens_in=100, tokens_out=50)

    fake.calls = calls
    return fake


def _forbidden_llm():
    """A chat function that fails the test if it is called at all."""

    async def fake(system, user):
        raise AssertionError("run_triage spent an LLM call it should not have")

    return fake


async def _fake_page(url, transport=None):
    return PAGE_HTML


async def _seed_leads(s):
    s.add(DiscoveredEvent(source_event_id="481833", title="朗読劇なにか",
                          event_date=date(2026, 9, 1), venue="サンシャイン劇場"))
    s.add(DiscoveredEvent(source_event_id="486243", title="学マス LIVE",
                          event_date=date(2026, 9, 12), venue="Zepp Haneda"))
    s.add(DiscoveredEvent(source_event_id="imas-tickets:x@google.com",
                          source="imas-tickets", date_is_deadline=True,
                          title="なにかの締切", event_date=date(2026, 9, 15), venue=""))
    await s.flush()


async def test_happy_path_stores_prune_and_creates_a_skeleton_draft(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        report = await run_triage(s, run, NOW,
                                  fetcher=_fake_page,
                                  llm_chat=_llm([CLASSIFY_REPLY, DRAFT_REPLY]))
        assert run.status == "done"
        assert run.finished_at == NOW
        assert parse_prune_list(run.prune_yaml).entries[0].event_id == "481833"
        assert report.leads_seen == 3
        assert report.productions == 2
        assert report.dismissals == 1
        assert report.drafts == 1 and report.calendar_skipped == 1
        # EVERY column _finish writes, asserted on the row rather than only on
        # the report: setting an unmapped attribute on a mapped instance
        # succeeds silently, so a typo'd column name here would never surface
        # -- and Task 6's status strip renders dismissals_proposed and skipped.
        assert run.started_at is not None
        assert run.leads_seen == 3
        assert run.productions == 2
        assert run.dismissals_proposed == 1
        assert run.drafts_created == 1
        assert run.skipped == 0
        assert run.calendar_skipped == 1
        # Both phases billed: one classify call plus one draft call.
        assert run.tokens_in == 200 and run.tokens_out == 100
        assert run.rounds_added == 1 and run.rounds_rejected == 0
        drafts = await service.pending_drafts(s, ADMIN_ID)
        assert len(drafts) == 1
        # THE safety pin, inverted by the 2026-08-10 ruling: a round the page
        # really states, quoted verbatim, now SURVIVES -- verified rather than
        # deleted unread.
        assert "最速抽選先行" in drafts[0].draft_text
        assert "2026-04-19 23:59" in drafts[0].draft_text
        # Scaffolding never rides into the document that gets committed.
        assert "evidence" not in drafts[0].draft_text
        assert "eventernote.com/events/486243" in drafts[0].draft_text
        notes = (await s.execute(select(Notification))).scalars().all()
        assert [n.kind for n in notes] == ["triage"]
        assert notes[0].concert_id is None


async def test_a_round_the_page_does_not_state_is_rejected_with_its_reason(db, monkeypatch):
    """THE guarantee this change rests on. `strip_rounds` kept it by deleting
    every round; `verify_rounds` keeps it by refusing the ones whose quote is
    not on the page -- and says so, because a real deadline quietly discarded
    is exactly as harmful as a fake one quietly kept."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        report = await run_triage(s, run, NOW, fetcher=_fake_page,
                                  llm_chat=_llm([CLASSIFY_REPLY, INVENTED_DRAFT_REPLY]))

        assert report.drafts == 1, "the draft still lands -- only the round is refused"
        assert report.rounds_added == 0 and report.rounds_rejected == 1
        [draft] = await service.pending_drafts(s, ADMIN_ID)
        assert "2026-09-01 23:59" not in draft.draft_text
        assert yaml.safe_load(draft.draft_text.split("\n", 1)[1])["rounds"] == []
        # ...and the operator can see WHY, on the preview that reads this row.
        record = yaml.safe_load(draft.completion_yaml)
        assert record["evidence"] == []
        assert len(record["rejected"]) == 1
        assert "最速先行" in record["rejected"][0]
        assert "not on the page" in record["rejected"][0]
        assert record["source_url"] == "https://www.eventernote.com/events/486243"


async def test_a_round_quoting_the_real_page_line_survives_with_its_evidence(db, monkeypatch):
    """The other half of the same ruling, measured on a real page: over 13 live
    productions the model read 7 real rounds, every one verifiable on its own
    page, and `strip_rounds` deleted all of them."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        report = await run_triage(s, run, NOW, fetcher=_fake_page,
                                  llm_chat=_llm([CLASSIFY_REPLY, DRAFT_REPLY]))

        assert report.rounds_added == 1 and report.rounds_rejected == 0
        [draft] = await service.pending_drafts(s, ADMIN_ID)
        parsed = parse_draft(draft.draft_text)
        [round_] = parsed.rounds
        assert round_.label == "最速抽選先行"
        assert round_.applies_to_labels == ["Day 1"]
        # The evidence lives BESIDE the draft, positionally against its rounds,
        # exactly as the completion pass writes it -- the preview renders it
        # under "Read from the ticket page:".
        record = yaml.safe_load(draft.completion_yaml)
        assert record["rejected"] == []
        assert record["evidence"][0]["apply_closes_jst"] == LADDER_LINE


async def test_the_model_is_shown_page_text_not_raw_html(db, monkeypatch):
    """The one property `round_evidence.py` rests on: the text the model reads
    and the text the verifier searches must be the SAME text. Phase 1 used to
    show raw HTML under a 120k cap while `verify_rounds` searches collapsed
    text under a 60k one, so a quote could be "not on the page" for a
    transformation the model never saw."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        llm = _llm([CLASSIFY_REPLY, DRAFT_REPLY])
        await run_triage(s, run, NOW, fetcher=_fake_page, llm_chat=llm)

        _system, user = llm.calls[1]
        assert "<div>" not in user and "<script>" not in user
        assert "2020年1月1日" not in user, "a <script> body is not page text"
        assert LADDER_LINE in user
        assert "開場 15:00 開演 16:00" in user, "the leg's own facts survive the pass"


async def test_a_draft_with_no_grounded_round_still_awaits_the_completion_pass(db, monkeypatch):
    """Phase 1 reads Eventernote; phase 2 reads the OFFICIAL page. A phase-1
    record must therefore not spend phase 2's one attempt -- `rounds: []` plus
    a completion record is a draft that has never had its official page read."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        await run_triage(s, run, NOW, fetcher=_fake_page,
                         llm_chat=_llm([CLASSIFY_REPLY, INVENTED_DRAFT_REPLY]))

        [draft] = await service.pending_drafts(s, ADMIN_ID)
        assert draft.completion_yaml != ""
        assert [r.id for r in await service.completion_candidates(s, ADMIN_ID)] == [draft.id]


async def test_a_drafted_survivor_is_not_redrafted_next_press(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        first = await service.request_triage(s, NOW, ADMIN_ID)
        first_report = await run_triage(s, first, NOW,
                                        fetcher=_fake_page,
                                        llm_chat=_llm([CLASSIFY_REPLY, DRAFT_REPLY]))
        assert first_report.drafts == 1

        later = NOW + timedelta(minutes=5)
        second = await service.request_triage(s, later, ADMIN_ID)
        assert second.id != first.id  # the first run is done, so this is a new row
        # A REAL draft reply is canned for the second run, deliberately: with
        # only the classify reply available, a second call would raise
        # IndexError inside the draft loop's `except Exception` and be counted
        # as a skip -- so the whole trace would look identical whether
        # containment worked or was deleted outright. Given a usable reply, a
        # broken containment mints a second PendingDraft and fails below.
        second_llm = _llm([CLASSIFY_REPLY, DRAFT_REPLY])
        second_report = await run_triage(s, second, later,
                                         fetcher=_fake_page, llm_chat=second_llm)
        assert second_report.drafts == 0
        assert second_report.skipped == 1
        assert len(await service.pending_drafts(s, ADMIN_ID)) == 1
        # Classify only: the draft call is never spent on a production already
        # sitting in the pending queue, which is the point of the check.
        assert len(second_llm.calls) == 1


async def test_a_failing_draft_is_skipped_and_the_run_survives(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        report = await run_triage(s, run, NOW,
                                  fetcher=_fake_page,
                                  llm_chat=_llm([CLASSIFY_REPLY, "not yaml at all: [["]))
        assert run.status == "done"
        assert report.drafts == 0
        assert report.skipped == 1
        # The classify half still landed: one bad production costs only itself.
        assert parse_prune_list(run.prune_yaml).entries[0].event_id == "481833"
        assert await service.pending_drafts(s, ADMIN_ID) == []


async def test_an_unusable_classify_response_fails_the_run(db, monkeypatch, caplog):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        with caplog.at_level(logging.ERROR, logger="app.triage"):
            with pytest.raises(TriageResponseError):
                await run_triage(s, run, NOW,
                                 fetcher=_fake_page,
                                 llm_chat=_llm(["not yaml at all: [["]))
        # The runner does NOT mark its own failure: the scheduler's handler does,
        # on a cleaned transaction (mark_triage_failed).
        assert run.status == "requested"
        # Diagnosing the production incident needed the reply text itself,
        # which the exception alone never carried.
        [record] = caplog.records
        assert "not yaml at all: [[" in record.getMessage()


async def test_zero_open_leads_costs_no_llm_call(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        run = await service.request_triage(s, NOW, ADMIN_ID)
        report = await run_triage(s, run, NOW,
                                  fetcher=_fake_page, llm_chat=_forbidden_llm())
        assert report.leads_seen == 0
        assert run.status == "done"
        assert run.leads_seen == 0 and run.tokens_in == 0
        # Nothing happened, so nobody is told about it.
        assert (await s.execute(select(Notification))).scalars().all() == []


async def test_the_draft_loop_beats_the_heartbeat(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    beats = []
    monkeypatch.setattr(heartbeat, "beat", lambda: beats.append(1))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        report = await run_triage(s, run, NOW,
                                  fetcher=_fake_page,
                                  llm_chat=_llm([CLASSIFY_REPLY, DRAFT_REPLY]))
        # One beat before the classify call plus one per drafted survivor -- the
        # calendar-only one fetches nothing. The classify beat is not optional:
        # the tick's own beat fires before delivery, and delivery plus a classify
        # call over the whole queue can outlast the 180s health threshold on its
        # own, before the loop's first beat ever runs.
        assert report.drafts == 1
        assert len(beats) == 2


async def test_the_draft_loop_stops_when_its_wall_clock_budget_runs_out(db, monkeypatch):
    """The run holds the reminder tick, exactly as the sweep does, and the
    per-production heartbeat means the blackout raises no alarm. So the draft
    loop keeps a wall clock and leaves the rest of the survivors for the next
    press -- containment is what stops the next press re-drafting the done ones.

    The clock is faked rather than slept through: a real 240s budget is not a
    test. `monotonic` is called once for the deadline and once per survivor, so
    the second survivor's check is the one that lands past it."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr("app.triage.TRIAGE_DELAY_SECONDS", 0)
    clock = iter([0.0, 0.0, 1000.0])
    monkeypatch.setattr("app.triage.monotonic", lambda: next(clock))

    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        llm = _llm([TWO_DRAFTABLE_REPLY, DRAFT_REPLY, DRAFT_REPLY])
        report = await run_triage(s, run, NOW, fetcher=_fake_page, llm_chat=llm)

        assert report.budget_exhausted is True
        assert run.status == "done", "a truncated run is finished, not failed"
        assert report.drafts == 1, "the second survivor's check landed past the deadline"
        assert report.skipped == 0, "stopping early is not a failure to draft"
        assert len(llm.calls) == 2, "nothing past the budget was even asked for"
        assert len(await service.pending_drafts(s, ADMIN_ID)) == 1


async def test_a_poisoned_session_stops_the_run(db, monkeypatch):
    """A failed flush poisons the session: nothing after it can persist, so
    absorbing the error into the per-production skip would pay up to 24 more
    fetch+LLM calls to write nothing. SQLAlchemyError therefore re-raises, and
    the scheduler's handler marks the run failed on a cleaned transaction."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr("app.triage.TRIAGE_DELAY_SECONDS", 0)

    async def boom(*args, **kwargs):
        raise OperationalError("INSERT INTO pending_drafts", {}, Exception("disk I/O"))

    monkeypatch.setattr("app.triage.create_pending_drafts", boom)

    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        llm = _llm([TWO_DRAFTABLE_REPLY, DRAFT_REPLY, DRAFT_REPLY])
        with pytest.raises(OperationalError):
            await run_triage(s, run, NOW, fetcher=_fake_page, llm_chat=llm)
        # Classify plus exactly ONE draft call: the second survivor was never
        # paid for. The runner marks no failure of its own, as ever.
        assert len(llm.calls) == 2
        assert run.status == "requested"


async def test_an_admin_who_never_signed_in_gets_a_user_row(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(STRANGER_ADMIN_ID))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")  # the requester, not the admin
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        await run_triage(s, run, NOW,
                         fetcher=_fake_page,
                         llm_chat=_llm([CLASSIFY_REPLY, DRAFT_REPLY]))
        # Notification.user_id is an FK to users.discord_id, so the flush inside
        # run_triage would raise IntegrityError without the ensure_user guard.
        assert await s.get(User, STRANGER_ADMIN_ID) is not None
        notes = (await s.execute(select(Notification))).scalars().all()
        assert [n.user_id for n in notes] == [STRANGER_ADMIN_ID]


# ── The classify pass is BATCHED ──────────────────────────────────────────
#
# One call over the whole queue failed two ways against a real 511-lead queue
# on 2026-08-09 (see TRIAGE_CLASSIFY_BATCH in app/triage.py). These pin the
# slicing, the merge, and the failure policy that batching makes possible.

# Two batches' worth of replies, for a batch size monkeypatched down to 2.
# `open_leads` hands them over event_date DESC, so a batch of two is
# [imas-tickets:x@google.com, 486243] and then [481833] -- the replies below
# answer the slices the runner really makes, not the order _seed_leads writes.
BATCH_ONE_REPLY = """dismiss:
  release: ['imas-tickets:x@google.com']
survivors:
  - title: "学マス LIVE"
    lead_ids: ['486243']
    representative: '486243'
"""

BATCH_TWO_REPLY = """dismiss:
  stage: ['481833']
survivors: []
"""


def _batches_of_two(monkeypatch):
    """Three seeded leads sliced two at a time -- two classify calls."""
    monkeypatch.setattr("app.triage.TRIAGE_CLASSIFY_BATCH", 2)


async def test_classify_is_sliced_into_batches_and_merged(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    _batches_of_two(monkeypatch)
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        llm = _llm([BATCH_ONE_REPLY, BATCH_TWO_REPLY, DRAFT_REPLY])
        report = await run_triage(s, run, NOW, fetcher=_fake_page, llm_chat=llm)

        # Two classify calls, each seeing only its own slice of the queue.
        assert len(llm.calls) == 3
        assert "486243" in llm.calls[0][1]
        assert "imas-tickets:x@google.com" in llm.calls[0][1]
        assert "481833" not in llm.calls[0][1], "a lead belongs to exactly one batch"
        assert "481833" in llm.calls[1][1]

        # Both batches' dismissals survive the merge, under their own reasons,
        # in one document the prune-list parser still accepts.
        entries = parse_prune_list(run.prune_yaml).entries
        assert {e.event_id: e.reason.value for e in entries} == {
            "481833": "stage", "imas-tickets:x@google.com": "release",
        }
        assert report.dismissals == 2
        assert report.productions == 1
        assert report.drafts == 1
        # Every call is billed to the run, not just the last batch.
        assert report.tokens_in == 300 and report.tokens_out == 150
        assert run.tokens_in == 300 and run.tokens_out == 150


async def test_every_batch_beats_the_heartbeat(db, monkeypatch):
    """Per batch, for the reason the draft loop beats per production: a queue
    long enough to need slicing is a queue whose classify phase alone can
    outlast the 180s health threshold."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    _batches_of_two(monkeypatch)
    beats = []
    monkeypatch.setattr(heartbeat, "beat", lambda: beats.append(1))
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        await run_triage(s, run, NOW, fetcher=_fake_page,
                         llm_chat=_llm([BATCH_ONE_REPLY, BATCH_TWO_REPLY, DRAFT_REPLY]))
        assert len(beats) == 3       # two classify batches + one drafted survivor


async def test_one_unusable_batch_is_stepped_over(db, monkeypatch):
    """One bad batch is a partial loss, not a total one -- the same reasoning
    that keeps one bad production from costing the other twenty-four. The
    surviving batch's dismissals and survivors still land and the run finishes."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    _batches_of_two(monkeypatch)
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        llm = _llm([BATCH_ONE_REPLY, "not yaml at all: [[", DRAFT_REPLY])
        report = await run_triage(s, run, NOW, fetcher=_fake_page, llm_chat=llm)

        assert run.status == "done"
        assert report.classify_batches_failed == 1
        assert report.skipped == 0, "a lost batch is not a production that failed to draft"
        assert [e.event_id for e in parse_prune_list(run.prune_yaml).entries] == [
            "imas-tickets:x@google.com"
        ]
        assert report.drafts == 1
        # The junk reply was still billed: the tokens were spent either way.
        assert report.tokens_in == 300


async def test_a_batch_whose_llm_call_raises_is_stepped_over_too(db, monkeypatch):
    """The 2026-08-09 failure #1 in miniature: the call itself blew up (an
    8,192-token cap hit exactly, finish_reason "length"). Unbatched that killed
    the whole press for a queue it had already been paid for."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    _batches_of_two(monkeypatch)
    replies = [BATCH_ONE_REPLY, DRAFT_REPLY]
    calls = []

    async def flaky(system, user):
        calls.append(user)
        if len(calls) == 2:
            raise LlmError("DeepSeek reply truncated (finish_reason: length)")
        return LlmReply(text=replies[0] if len(calls) == 1 else replies[1],
                        tokens_in=100, tokens_out=50)

    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        report = await run_triage(s, run, NOW, fetcher=_fake_page, llm_chat=flaky)

        assert run.status == "done"
        assert report.classify_batches_failed == 1
        assert report.productions == 1 and report.drafts == 1


async def test_every_batch_failing_still_fails_the_run(db, monkeypatch):
    """The original reasoning survives at its limit: when NO batch came back
    usable there is no partial run to salvage, so it propagates and the
    scheduler marks the row failed on a cleaned transaction."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    _batches_of_two(monkeypatch)
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        llm = _llm(["not yaml at all: [[", "nor is this: [["])
        with pytest.raises(TriageResponseError):
            await run_triage(s, run, NOW, fetcher=_fake_page, llm_chat=llm)
        assert len(llm.calls) == 2, "every batch is tried before the run is given up on"
        assert run.status == "requested"


async def test_the_admin_notice_names_a_lost_batch(db, monkeypatch):
    """A partial classify is a silent degradation otherwise: the queue looks
    triaged, and the leads in the lost batch are simply never mentioned again.
    Recorded in the notice for the same reason budget_exhausted is recorded."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    _batches_of_two(monkeypatch)
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        await _seed_leads(s)
        run = await service.request_triage(s, NOW, ADMIN_ID)
        await run_triage(s, run, NOW, fetcher=_fake_page,
                         llm_chat=_llm([BATCH_ONE_REPLY, "junk [[", DRAFT_REPLY]))
        [note] = (await s.execute(select(Notification))).scalars().all()
        assert "1 classify batch" in note.body
