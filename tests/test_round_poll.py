"""What one round poll does, and what each failure inside it costs.

No network and no key: the fetcher and the LLM client are injected, exactly as
`run_sweep`, `run_triage` and `run_completion` take theirs.

The failure this suite exists to catch is invisible by construction -- a real
deadline the model found, discarded without a reason, looks identical to a page
that had nothing on it. So the assertions below are about what is COUNTED and
what REASON is recorded, not merely about what ended up in the table.
"""

from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy import select

from app import round_poll as rp
from app.db.models import Concert, ConcertDay, FetchDomain, Round, RoundProposal
from app.db.service import (
    decide_fetch_domain,
    ensure_user,
    note_fetch_domain,
)
from app.domain.types import RoundKind
from app.draft_completion import HOST_USER_AGENTS
from app.llm import LlmReply
from app.round_poll import run_round_poll

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
USER_ID = 42
HOST = "eplus.jp"
URL = "https://eplus.jp/sf/detail/1234"

# Captured BEFORE the autouse fixture below patches it away, so the politeness
# test can still assert what production actually waits.
REAL_DELAY = rp.ROUND_POLL_DELAY_SECONDS

PAGE = (
    "<html><body><p>1次先行抽選 受付開始 2026年1月5日(月)12:00 "
    "申込締切 2026年1月10日(土)23:59</p></body></html>"
)

GOOD_REPLY = """\
rounds:
  - label: 1次先行抽選
    kind: lottery_round
    apply_opens_jst: 2026-01-05 12:00
    apply_closes_jst: 2026-01-10 23:59
    evidence:
      apply_opens_jst: "受付開始 2026年1月5日(月)12:00"
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
"""

# Same shape, quoting a line the page does not contain: the fabricated deadline
# `verify_rounds` exists to refuse.
UNGROUNDED_REPLY = """\
rounds:
  - label: 2次先行抽選
    kind: lottery_round
    apply_closes_jst: 2026-02-20 23:59
    evidence:
      apply_closes_jst: "申込締切 2026年2月20日(金)23:59"
"""

# The same round with the rest of its ladder on the page: the 当落発表 and the
# 入金期限 the prompt has always asked for, plus the leg it names. Every stamp
# here is quoted verbatim below, because `verify_rounds` throws away a round
# whose evidence it cannot find on the page.
PAGE_FULL = (
    "<html><body><p>1次先行抽選 受付開始 2026年1月5日(月)12:00 "
    "申込締切 2026年1月10日(土)23:59 当落発表 2026年1月15日(木)18:00 "
    "入金期限 2026年1月20日(火)23:59</p></body></html>"
)

FULL_REPLY = """\
rounds:
  - label: 1次先行抽選
    kind: lottery_round
    applies_to: [Day 1]
    apply_opens_jst: 2026-01-05 12:00
    apply_closes_jst: 2026-01-10 23:59
    results_jst: 2026-01-15 18:00
    payment_deadline_jst: 2026-01-20 23:59
    evidence:
      apply_opens_jst: "受付開始 2026年1月5日(月)12:00"
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
      results_jst: "当落発表 2026年1月15日(木)18:00"
      payment_deadline_jst: "入金期限 2026年1月20日(火)23:59"
"""

# TWO legs named badly in the two ways a real reply names them badly, because
# the writer's `str(leg).strip()` is two guards in one line and each half needs
# its own leg to bite on:
#
# * `2026-01-05` UNQUOTED -- how a model writes a bare token, and how the
#   prompt's own example writes `applies_to: [Day 1]`. PyYAML resolves it to a
#   `datetime.date`, which `str()` is what saves. Written with a space in
#   place of a strip and this leg still passes.
# * `"　夜公演　"` wrapped in IDEOGRAPHIC SPACE (U+3000), quoted so the
#   whitespace survives into the value. `.strip()` is what saves this one, and
#   `str()` alone leaves it untouched. U+3000 rather than U+0020 on purpose:
#   CLAUDE.md records this project being bitten by SQLite's `trim()` (U+0020
#   only) disagreeing with Python's Unicode-aware `.strip()` on exactly the
#   Japanese data this app is full of, and a stray one around a leg label is an
#   ordinary typo.
#
# The whitespace has to arrive in the REPLY rather than on the `ConcertDay`:
# `draft_leg_labels` strips its own side, so a padded column label would be
# trimmed before it ever became a leg the model could name.
DATE_LEG_REPLY = """\
rounds:
  - label: 1次先行抽選
    kind: lottery_round
    applies_to: [2026-01-05, "　夜公演　"]
    apply_opens_jst: 2026-01-05 12:00
    apply_closes_jst: 2026-01-10 23:59
    evidence:
      apply_opens_jst: "受付開始 2026年1月5日(月)12:00"
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
"""

OPENS_UTC = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)    # 2026-01-05 12:00 JST
CLOSES_UTC = datetime(2026, 1, 10, 14, 59, tzinfo=UTC)  # 2026-01-10 23:59 JST
RESULTS_UTC = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)   # 2026-01-15 18:00 JST
PAYMENT_UTC = datetime(2026, 1, 20, 14, 59, tzinfo=UTC)  # 2026-01-20 23:59 JST


# A non-zero SENTINEL, never 0: the pause test asserts the recorded sleeps
# against this value, and with 0 an implementation that had dropped the rate
# limit entirely (`asyncio.sleep(0)` hard-coded, or no sleep at all reached
# through a stub that records nothing) would compare equal to a real one.
TEST_DELAY = 0.001


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    """A real 1s pause per concert would cost the suite a second per fetch.
    `test_the_pass_pauses_before_every_fetch` pins the production value."""
    monkeypatch.setattr(rp, "ROUND_POLL_DELAY_SECONDS", TEST_DELAY)


def fake_chat(reply_text):
    async def _chat(system, user, **kw):
        return LlmReply(text=reply_text, tokens_in=100, tokens_out=50)
    return _chat


def recording_fetch(*, fail_on: str | None = None):
    """A fetcher that records what it was asked for. Returns (fetch, calls) so
    a test can assert a fetch did NOT happen -- raising instead would be
    swallowed by the pass's own per-concert except and counted as a failure."""
    calls: list[str] = []

    async def _fetch(url):
        calls.append(url)
        if fail_on is not None and fail_on in url:
            raise RuntimeError("boom")
        return PAGE
    return _fetch, calls


async def _concert(
    session, event_id, *, official_url=URL, rounds=(), polled_at=None,
    leg_labels=("Day 1",),
):
    """One QUIET concert: future legs and no future anchor on any round.

    `leg_labels` is a parameter because `ConcertDay.label` is free editor text,
    so neither a DATE-SHAPED label nor one a round names with stray whitespace
    is exotic -- see `test_leg_labels_are_stored_as_the_strings_verify_matched`,
    the one test that needs either.
    """
    await ensure_user(session, USER_ID, "reiji")
    concert = Concert(
        title=event_id, event_id=event_id, official_url=official_url,
        created_by=USER_ID, ladder_polled_at_utc=polled_at,
    )
    session.add(concert)
    await session.flush()
    for offset, leg_label in enumerate(leg_labels):
        session.add(ConcertDay(
            concert_id=concert.id, label=leg_label,
            starts_at_utc=datetime(2026, 12, 1 + offset, 10, 0, tzinfo=UTC),
        ))
    for label, opens_at_utc in rounds:
        session.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND,
            label=label, opens_at_utc=opens_at_utc,
        ))
    await session.flush()
    return concert


async def _approve(session, host=HOST):
    # FetchDomain.decided_by is an FK to users, and PRAGMA foreign_keys=ON:
    # approving before any concert is seeded needs the row to exist first.
    await ensure_user(session, USER_ID, "reiji")
    domain = await note_fetch_domain(session, host, f"https://{host}/x", NOW)
    await decide_fetch_domain(session, domain.id, True, NOW, USER_ID)
    return domain


async def _proposals(session):
    return list((await session.execute(
        select(RoundProposal).order_by(RoundProposal.id)
    )).scalars())


async def test_a_grounded_round_becomes_a_pending_proposal(session):
    """The positive control the rest of the suite is measured against, asserted
    field by field on the ROW: `upsert_proposal` writing a wrong column, or the
    JST->UTC conversion being skipped, would leave a proposal that reads fine
    in a count and names the wrong hour on the page."""
    await _approve(session)
    concert = await _concert(session, "grew")
    fetch, calls = recording_fetch()

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert calls == [URL]
    assert (report.polled, report.new_proposals, report.failed) == (1, 1, 0)
    assert (report.tokens_in, report.tokens_out) == (100, 50)
    [proposal] = await _proposals(session)
    assert proposal.concert_id == concert.id
    assert proposal.label == "1次先行抽選"
    assert proposal.kind is RoundKind.LOTTERY_ROUND
    assert proposal.opens_at_utc == OPENS_UTC
    assert proposal.closes_at_utc == CLOSES_UTC
    assert proposal.source_url == URL
    assert proposal.first_seen_at == NOW
    assert proposal.dismissed_at is None and proposal.applied_at is None
    # The quote a reviewer judges the claim by. Without it the page is a guess.
    assert yaml.safe_load(proposal.evidence_yaml) == {
        "apply_opens_jst": "受付開始 2026年1月5日(月)12:00",
        "apply_closes_jst": "申込締切 2026年1月10日(土)23:59",
    }
    # The poll's own stamp, and ONLY the poll's own stamp: writing the owner's
    # would silently mark /admin/quiet-ladders' worklist as attended.
    assert concert.ladder_polled_at_utc == NOW
    assert concert.ladder_rechecked_at_utc is None


async def test_the_poll_persists_results_payment_and_legs(session):
    """The whole round reaches the row, not the half phase 1 stored.

    The prompt has asked for `results_jst`, `payment_deadline_jst` and
    `applies_to` since it was written, and `verify_rounds` grounds each of them
    against the page BEFORE the pass ever sees the round -- so phase 1's writer
    was discarding work that had already been done and checked. A results
    announcement and a payment deadline are two of the anchors this app exists
    to remind people about.

    Mutation: reverting `_poll_one` to pass only label/kind/opens/closes, which
    is exactly what shipped in phase 1 -- so this test is what stops a revert.
    Three separate assertions, because dropping any ONE of the three arguments
    is a separate edit and a single combined check would let the other two go.

    `applies_to` is asserted as the leg LABEL, not a `ConcertDay` id: the model
    is handed a draft document and never sees this database, so a label is the
    only thing it could name.
    """
    await _approve(session)
    await _concert(session, "whole-ladder")

    async def fetch(url):
        return PAGE_FULL

    report = await run_round_poll(
        session, NOW, fetcher=fetch, chat=fake_chat(FULL_REPLY)
    )

    assert (report.polled, report.new_proposals, report.rounds_rejected) == (1, 1, 0)
    [proposal] = await _proposals(session)
    assert proposal.results_at_utc == RESULTS_UTC
    assert proposal.payment_deadline_at_utc == PAYMENT_UTC
    assert proposal.applies_to_labels == ["Day 1"]
    # The two phase 1 already stored, so a widening that shuffled the columns
    # (payment landing in results, say) cannot hide behind the new ones.
    assert proposal.opens_at_utc == OPENS_UTC
    assert proposal.closes_at_utc == CLOSES_UTC


async def test_a_round_naming_no_legs_stores_an_empty_list(session):
    """A tour-wide round names no leg, and that is the COMMON case the prompt
    describes. Mutation: storing None (or the string "None") when `applies_to`
    is absent -- the convention "empty means every leg" then has two spellings
    and every reader downstream has to know both.

    `GOOD_REPLY` carries no `applies_to` at all, which is precisely the shape
    this asserts about.
    """
    await _approve(session)
    await _concert(session, "tour-wide")
    fetch, _ = recording_fetch()

    await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    [proposal] = await _proposals(session)
    assert proposal.applies_to_labels == []
    assert proposal.results_at_utc is None
    assert proposal.payment_deadline_at_utc is None


async def test_leg_labels_are_stored_as_the_strings_verify_matched(session):
    """What is STORED must be what `verify_rounds` MATCHED: `str(leg).strip()`.

    THE ONLY TEST THAT COVERS THAT COERCION, and it exists because every
    plausible way to write the line without it is green everywhere else. Two
    halves, two legs, because each half needs a different leg to bite on and a
    fixture that exercises only one leaves the other free to be deleted:

    * `str()` -- the leg named `2026-01-05` unquoted. Without it the flush
      raises and the WHOLE RUN dies (see the chain below).
    * `.strip()` -- the leg named `"　夜公演　"` in ideographic spaces. Without
      it the run survives and the damage is quieter: the stored label is not
      the label `verify_rounds` accepted, so the review page looks it up and
      finds nothing, and a round that names a real leg renders as naming an
      unmatched one. `str(date(...))` carries no whitespace, so the date leg
      alone cannot exercise this even in principle -- which is exactly how
      dropping `.strip()` stayed green across all 38 tests.

    The `str()` chain, every link of it ordinary:

    * `ConcertDay.label` is free editor text, and a date is a perfectly normal
      thing to call a leg of a two-night run;
    * the model names it the way the prompt's own example names a leg --
      `applies_to: [Day 1]`, unquoted -- and PyYAML resolves an unquoted
      `2026-01-05` to a `datetime.date`;
    * `parse_completion_response` stringifies only the four TIMESTAMP_FIELDS
      and passes every other key through as resolved, so the `date` survives;
    * `verify_rounds` ACCEPTS the round, because it compares
      `str(leg).strip()` against the draft's leg labels -- and the draft dumps
      that label quoted, so the two match exactly;
    * the flush then raises `StatementError` wrapping `TypeError: Object of
      type date is not JSON serializable`, which is a `SQLAlchemyError` -- the
      one family `run_round_poll` deliberately re-raises rather than absorbs.

    So the cost is not one bad column on one proposal. It is the whole run,
    every paid call in it, and the digest that would have said why.

    Mutations, each its own edit: dropping `str(...).strip()` whole at EITHER
    guard alone is now caught by the other (defence in depth, deliberate);
    dropping it at both raises out of `run_round_poll`. Dropping ONLY the
    `.strip()`, at both, is the quiet one and fails on the second leg.
    """
    await _approve(session)
    await _concert(session, "date-legs", leg_labels=("2026-01-05", "夜公演"))

    async def fetch(url):
        return PAGE

    report = await run_round_poll(
        session, NOW, fetcher=fetch, chat=fake_chat(DATE_LEG_REPLY)
    )

    # The round was believed, not refused: this is a coercion test, and it
    # would be worthless against a round `verify_rounds` had already thrown out.
    # Both legs passed rule 7, which is the premise the assertion below rests
    # on -- the stored form must equal the form that was matched.
    assert (report.polled, report.new_proposals, report.rounds_rejected) == (1, 1, 0)
    [proposal] = await _proposals(session)
    assert proposal.applies_to_labels == ["2026-01-05", "夜公演"]
    # Strings, not something that merely COMPARES equal to one. `date` does
    # not, but a future coercion that stringified lazily would.
    assert all(isinstance(leg, str) for leg in proposal.applies_to_labels)


async def test_a_concert_with_no_official_url_is_skipped_and_counted(session):
    """A quiet concert nobody gave a page is a fact worth reporting, not an
    error. Mutation: crashing on the None, or skipping without counting."""
    await _approve(session)
    concert = await _concert(session, "no-url", official_url=None)
    fetch, calls = recording_fetch()

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert report.skipped_no_url == 1
    assert (report.polled, report.failed, report.concerts_seen) == (0, 0, 1)
    assert calls == []
    assert await _proposals(session) == []
    # NOT stamped: no attempt was spent, so it consumed no budget and starves
    # nothing. Rotating it to the back of tomorrow's run would be a lie about
    # when the poll last read this page.
    assert concert.ladder_polled_at_utc is None


async def test_an_unknown_host_is_recorded_pending_and_the_concert_skipped(session):
    """Mutation: fetching anyway. BOTH halves are asserted -- the row alone
    would pass with the fetch still firing, which is the whole point of the
    approval gate."""
    concert = await _concert(session, "unknown", official_url="https://tickets.example.com/a")
    fetch, calls = recording_fetch()

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert calls == []
    assert report.skipped_host == 1
    assert (report.polled, report.failed, report.skipped_declined) == (0, 0, 0)
    [domain] = (await session.execute(select(FetchDomain))).scalars().all()
    assert domain.host == "tickets.example.com"
    assert domain.approved_at is None and domain.declined_at is None
    # No attempt was spent, so the cursor must not move: the day the host is
    # approved this concert is still at the head of the queue.
    assert concert.ladder_polled_at_utc is None


async def test_a_declined_host_is_skipped_and_not_re_proposed(session):
    """Mutation: treating declined like unknown, which puts a host a human
    already refused back in front of them as a pending approval."""
    await ensure_user(session, USER_ID, "reiji")  # FetchDomain.decided_by is an FK
    domain = await note_fetch_domain(
        session, "declined.example.com", "https://declined.example.com/first", NOW
    )
    await decide_fetch_domain(session, domain.id, False, NOW, USER_ID)
    await _concert(session, "declined", official_url="https://declined.example.com/second")
    fetch, calls = recording_fetch()

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert calls == []
    assert report.skipped_declined == 1
    assert report.skipped_host == 0 and report.polled == 0
    # One row, still declined, still remembering the URL the approver was shown.
    rows = (await session.execute(select(FetchDomain))).scalars().all()
    assert len(rows) == 1
    assert rows[0].declined_at == NOW
    assert rows[0].approved_at is None
    assert rows[0].first_seen_url == "https://declined.example.com/first"


async def test_an_ungrounded_round_is_rejected_with_its_reason(session):
    """`verify_rounds`' job, and the most important check in this file.
    Mutation: trusting the model's reply. The REASON is asserted, not merely
    the round's absence -- 'dropped silently' and 'rejected with a reason' must
    not look the same to this test."""
    await _approve(session)
    await _concert(session, "ungrounded")
    fetch, _calls = recording_fetch()

    report = await run_round_poll(
        session, NOW, fetcher=fetch, chat=fake_chat(UNGROUNDED_REPLY)
    )

    assert await _proposals(session) == []
    assert report.new_proposals == 0
    assert report.rounds_rejected == 1
    # The page WAS read and the model DID answer: a refused round is not a
    # failed concert, and reporting it as one would hide a working poll.
    assert (report.polled, report.failed) == (1, 0)
    assert len(report.rejections) == 1
    assert report.rejections[0].startswith("ungrounded: ")
    assert "2次先行抽選" in report.rejections[0]
    assert "not on the page" in report.rejections[0]


async def test_one_concert_failing_does_not_stop_the_run(session):
    """Mutation: letting the exception escape. The middle page dies; the third
    concert must still be polled, and the death must be named."""
    await _approve(session)
    await _concert(session, "first", official_url="https://eplus.jp/first")
    second = await _concert(session, "second", official_url="https://eplus.jp/boom")
    third = await _concert(session, "third", official_url="https://eplus.jp/third")
    fetch, calls = recording_fetch(fail_on="boom")

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert sorted(calls) == [
        "https://eplus.jp/boom", "https://eplus.jp/first", "https://eplus.jp/third",
    ]
    assert (report.polled, report.failed) == (2, 1)
    assert len(report.failures) == 1
    assert report.failures[0].startswith("second: ")
    assert "boom" in report.failures[0]
    # The concert behind the failure was really polled, not merely counted.
    assert third.id in {p.concert_id for p in await _proposals(session)}
    # The FAILURE is stamped too -- the other half of the cursor, and the half
    # that is invisible without this line. An attempt was spent on it, so
    # tomorrow's run must start behind it; stamping only successes lets one
    # permanently-403ing page hold the head of the queue forever and starve
    # every concert behind it the moment the wall clock bites.
    assert second.ladder_polled_at_utc == NOW


async def test_an_identical_round_still_produces_nothing(session):
    """End-to-end over the pure diff: the model re-reads a page it read before
    and re-offers a round the catalogue already has, IDENTICAL on every field
    `classify_proposals` compares. Mutation: dropping the `new_proposals`/
    `changed_proposals` diff, which re-proposes every existing round every
    day.

    The regression guard on Task 2's HELD bucket: `closes_at_utc` must match
    too, not only `label`/`opens_at_utc` -- a round matching only the dedupe
    key but differing elsewhere is CHANGED (see the test below), not held, and
    this fixture would wrongly land in `skipped_held` under the old
    fresh-only behaviour if it left `closes_at_utc` unset."""
    await _approve(session)
    concert = await _concert(session, "held")
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND,
        label="1次先行抽選", opens_at_utc=OPENS_UTC, closes_at_utc=CLOSES_UTC,
    ))
    await session.flush()
    fetch, _calls = recording_fetch()

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert await _proposals(session) == []
    assert (report.new_proposals, report.changed_proposals) == (0, 0)
    # Counted, not merely absent: a grounded round the concert already holds is
    # the commonest outcome of a poll, and with no row and no counter it reads
    # exactly like a page that said nothing at all.
    assert report.skipped_held == 1
    # Grounded, just not new: this must not be reported as a rejection either.
    assert (report.polled, report.rounds_rejected, report.failed) == (1, 0, 0)


async def test_a_moved_closing_date_is_stored_as_a_proposal(session):
    """A concert is quiet precisely because its stored deadlines are in the
    past, so a postponed closing date is the likeliest true find this pass can
    make. Mutation: dropping `changed` from what gets upserted -- the pass
    would find the postponement and throw it away, which is phase 1's
    behaviour and exactly what the module docstring used to say was
    deliberately not yet done."""
    await _approve(session)
    concert = await _concert(session, "moved", rounds=[("1次先行抽選", OPENS_UTC)])
    fetch, _calls = recording_fetch()

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert (report.polled, report.changed_proposals, report.new_proposals) == (1, 1, 0)
    assert report.skipped_held == 0
    [proposal] = await _proposals(session)
    assert proposal.concert_id == concert.id
    assert proposal.label == "1次先行抽選"
    assert proposal.opens_at_utc == OPENS_UTC
    # The page's NEW closing time, not the None the row held before.
    assert proposal.closes_at_utc == CLOSES_UTC
    assert proposal.first_seen_at == NOW


async def test_a_changed_proposal_counts_separately_from_a_new_one(session):
    """Mutation: summing them into `new_proposals`. Two brand-new concerts and
    one concert whose closing date moved, so the two counts (2 vs 1) differ --
    equal counts would let the two counters get swapped without this test
    noticing."""
    await _approve(session)
    await _concert(session, "brand-new-a", official_url="https://eplus.jp/a")
    await _concert(session, "brand-new-b", official_url="https://eplus.jp/b")
    await _concert(
        session, "moved", official_url="https://eplus.jp/moved",
        rounds=[("1次先行抽選", OPENS_UTC)],
    )
    fetch, calls = recording_fetch()

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert sorted(calls) == [
        "https://eplus.jp/a", "https://eplus.jp/b", "https://eplus.jp/moved",
    ]
    assert report.new_proposals == 2
    assert report.changed_proposals == 1
    assert report.skipped_held == 0
    rows = await _proposals(session)
    assert len(rows) == 3


async def test_an_unknown_kind_is_stored_as_other_and_the_reason_reported(session):
    """A mechanic this app does not have is not a reason to drop the round --
    it stores as `other` -- but it IS a reason, and the digest is where the
    owner learns not to trust the kind on the review screen. Mutation: logging
    the warning without putting it in `rejections`, which is exactly where
    every other reason goes."""
    await _approve(session)
    await _concert(session, "odd-kind")
    fetch, _calls = recording_fetch()

    report = await run_round_poll(
        session, NOW, fetcher=fetch,
        chat=fake_chat(GOOD_REPLY.replace("kind: lottery_round", "kind: mystery_draw")),
    )

    [proposal] = await _proposals(session)
    assert proposal.kind is RoundKind.OTHER
    assert report.new_proposals == 1
    assert any(
        "odd-kind: " in line and "mystery_draw" in line for line in report.rejections
    )


async def test_a_still_pending_proposal_is_refreshed_not_re_announced(session):
    """The pass re-reads the same page every day, and a proposal nobody has
    reviewed passes BOTH filters -- `new_proposals` only knows rounds the
    concert holds, `dismissed_keys_for` only rounds the owner refused. So the
    row is rewritten, and counting that as new would tell the owner "1 new
    proposal" every day until they act. Mutation: `report.new_proposals += 1`
    unconditionally, which is what the dismissed test below cannot catch
    because it takes the other branch."""
    await _approve(session)
    await _concert(session, "still-pending")
    fetch, _calls = recording_fetch()
    first = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))
    assert (first.new_proposals, first.refreshed) == (1, 0)

    later = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    second = await run_round_poll(session, later, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert second.new_proposals == 0
    assert second.refreshed == 1
    # One row, and it still remembers how long it has been waiting.
    [proposal] = await _proposals(session)
    assert proposal.first_seen_at == NOW
    assert proposal.dismissed_at is None and proposal.applied_at is None


async def test_a_dismissed_proposal_is_not_proposed_again(session):
    """The daily poll re-reads the same page forever, so a dismissal that did
    not stick would come back tomorrow and every day after. Mutation: dropping
    the `dismissed_keys_for` check."""
    await _approve(session)
    await _concert(session, "dismissed")
    fetch, _calls = recording_fetch()
    await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))
    [proposal] = await _proposals(session)
    proposal.dismissed_at = NOW
    await session.flush()

    later = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    report = await run_round_poll(session, later, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert report.new_proposals == 0
    assert report.skipped_dismissed == 1
    rows = await _proposals(session)
    assert len(rows) == 1 and rows[0].dismissed_at == NOW


async def test_the_wall_clock_budget_stops_the_run_and_says_so(session, monkeypatch):
    """Mutation: dropping `budget_exhausted` from the report -- a truncation
    only the journal knows about is the silent degradation this repo keeps
    finding. Mutation: dropping the break, which holds the reminder tick for as
    long as the catalogue takes."""
    await _approve(session)
    await _concert(session, "a", official_url="https://eplus.jp/a")
    await _concert(session, "b", official_url="https://eplus.jp/b")
    fetch, calls = recording_fetch()

    ticks = {"n": 0}

    def fake_monotonic():
        # Once for the deadline, once per concert: the second concert is the
        # one the clock refuses.
        ticks["n"] += 1
        return 0.0 if ticks["n"] <= 2 else 10_000.0

    monkeypatch.setattr(rp, "monotonic", fake_monotonic)

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert report.budget_exhausted is True
    assert report.polled == 1
    assert len(calls) == 1
    # Exactly one concert was attempted, and the other is untouched -- it is
    # still a candidate tomorrow rather than silently marked read.
    concerts = (await session.execute(select(Concert))).scalars().all()
    assert [c.ladder_polled_at_utc for c in concerts].count(NOW) == 1
    assert [c.ladder_polled_at_utc for c in concerts].count(None) == 1


async def test_the_least_recently_polled_concert_is_read_first(session):
    """The pass's cursor IS the stamp it writes. `quiet_ladder_rows` orders by
    `ladder_rechecked_at_utc` -- the OWNER's stamp, which the poll must never
    touch -- so consumed in that order the sequence is identical on every run
    and, the moment the budget bites, the tail is never read at all. Mutation:
    dropping the sort in `_candidates`."""
    await _approve(session)
    await _concert(session, "polled-yesterday", official_url="https://eplus.jp/yesterday",
                   polled_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC))
    await _concert(session, "never-polled", official_url="https://eplus.jp/never")
    await _concert(session, "polled-last-week", official_url="https://eplus.jp/week",
                   polled_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC))
    fetch, calls = recording_fetch()

    await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert calls == [
        "https://eplus.jp/never",
        "https://eplus.jp/week",
        "https://eplus.jp/yesterday",
    ]


async def test_the_pass_pauses_before_every_fetch(session, monkeypatch):
    """Sequential with a pause: parallel requests at a third party is how an IP
    gets blocked. Mutation: deleting the sleep (the recorded list empties), or
    setting the delay to zero (the constant assertion below)."""
    assert REAL_DELAY >= 1.0
    await _approve(session)
    await _concert(session, "a", official_url="https://eplus.jp/a")
    await _concert(session, "b", official_url="https://eplus.jp/b")
    fetch, calls = recording_fetch()
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(rp.asyncio, "sleep", fake_sleep)

    await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert len(calls) == 2
    assert sleeps == [TEST_DELAY] * 2


def test_the_user_agent_names_this_pass_and_keeps_the_host_exceptions():
    """The whole justification for sharing `HOST_USER_AGENTS` with
    draft_completion. Mutation: returning `ROUND_POLL_USER_AGENT`
    unconditionally, which leaves the honest default in place and quietly
    loses the one host the owner's catalogue mostly lives behind."""
    assert "round poll" in rp.ROUND_POLL_USER_AGENT
    assert rp._user_agent_for(URL) == rp.ROUND_POLL_USER_AGENT
    # lovelive-anime.jp answers a non-browser agent with a 403 from a CDN
    # filter; 8 of the owner's 12 concerts sit behind it.
    browser = HOST_USER_AGENTS["www.lovelive-anime.jp"]
    assert browser != rp.ROUND_POLL_USER_AGENT
    assert rp._user_agent_for("https://www.lovelive-anime.jp/event/") == browser
    # Normalized through the same `_normalize_host` the approval policy uses,
    # so a cased or trailing-dot URL cannot miss the table by spelling.
    assert rp._user_agent_for("https://WWW.LoveLive-Anime.JP./event/") == browser
    # A malformed host falls through instead of raising: `fetch_html` is about
    # to refuse it anyway, and the UA lookup is not where that gets decided.
    assert rp._user_agent_for("http://[::bad::]/x") == rp.ROUND_POLL_USER_AGENT


async def test_fetcher_none_binds_the_runs_approved_hosts_to_the_policy(session, monkeypatch):
    """Every other test injects a fetcher, so nothing else exercises the one
    line of production wiring that binds this run's approved-host set to the
    fetch policy. Mutation: passing an empty set, which refuses every page
    while every injected-fetcher test stays green."""
    await _approve(session)
    await _concert(session, "wired")
    seen: list[tuple[str, set[str]]] = []

    async def fake_fetch_page(url, hosts):
        seen.append((url, hosts))
        return PAGE

    monkeypatch.setattr(rp, "_fetch_page", fake_fetch_page)

    report = await run_round_poll(session, NOW, fetcher=None, chat=fake_chat(GOOD_REPLY))

    assert report.polled == 1
    assert seen == [(URL, {HOST})]


# ── Two readings of one round, and a concert that vanishes ───────────────

# The same page with a SECOND closing line on it, so both readings below are
# genuinely grounded and `verify_rounds` accepts each on its own merits -- the
# fold under test must be the reason only one survives, not the evidence rule.
PAGE_TWO_CLOSES = (
    "<html><body><p>1次先行抽選 受付開始 2026年1月5日(月)12:00 "
    "申込締切 2026年1月10日(土)23:59</p>"
    "<p>申込締切 2026年1月20日(火)23:59</p></body></html>"
)

# One reply, two rounds, ONE dedupe key: same label, same opening time,
# different closing time. `classify_proposals` cannot filter this -- it diffs
# against the rounds the concert HOLDS, and it puts each of these in exactly
# one bucket, so the two tests below feed this same reply to a concert holding
# nothing (both readings `fresh`) and to one holding the round (both
# `changed`), which is the only way to reach BOTH `_fold_duplicate_keys` calls.
DOUBLED_REPLY = """\
rounds:
  - label: 1次先行抽選
    kind: lottery_round
    apply_opens_jst: 2026-01-05 12:00
    apply_closes_jst: 2026-01-10 23:59
    evidence:
      apply_opens_jst: "受付開始 2026年1月5日(月)12:00"
      apply_closes_jst: "申込締切 2026年1月10日(土)23:59"
  - label: 1次先行抽選
    kind: lottery_round
    apply_opens_jst: 2026-01-05 12:00
    apply_closes_jst: 2026-01-20 23:59
    evidence:
      apply_opens_jst: "受付開始 2026年1月5日(月)12:00"
      apply_closes_jst: "申込締切 2026年1月20日(火)23:59"
"""


async def test_two_readings_of_one_round_in_one_reply_keep_the_first(session):
    """`upsert_proposal` keys on (concert_id, dedupe_key), so an unfolded
    second reading SELECTs the row the first one just flushed and overwrites
    its closing time and its evidence -- a lost claim, not just a tally one
    too high, since nothing anywhere says a second reading existed.

    Mutation: dropping the `_fold_duplicate_keys` call in `_poll_one`. Then
    `new_proposals` reads 2, `closes_at_utc` is the SECOND reading's, and no
    reason names the collapse.
    """
    await _approve(session)
    await _concert(session, "doubled")

    async def fetch(url):
        return PAGE_TWO_CLOSES

    report = await run_round_poll(
        session, NOW, fetcher=fetch, chat=fake_chat(DOUBLED_REPLY)
    )

    rows = await _proposals(session)
    assert len(rows) == 1
    assert report.new_proposals == 1
    # The FIRST reading owns the row: its closing time survived intact.
    assert rows[0].closes_at_utc == CLOSES_UTC
    assert "2026年1月10日" in rows[0].evidence_yaml
    # And the discard is named, because this module names every discard.
    assert any("second reading of the same round" in r for r in report.rejections)


async def test_two_readings_of_one_CHANGED_round_keep_the_first_too(session):
    """The SECOND fold call, on the `changed` bucket -- and the reason this
    test exists is that the one above cannot reach it.

    `classify_proposals` puts a proposed round in exactly one bucket, and the
    test above seeds a concert holding NO round at all, so both its readings
    land in `fresh` and the `changed` fold beside it is never exercised.
    Deleting `changed = _fold_duplicate_keys(changed, ...)` from `_poll_one`
    left all 22 tests in this file green.

    The concert here HOLDS the round -- same label, same opening minute, no
    closing time -- so both readings are `changed`, and unfolded they do the
    same damage they do in `fresh`: the second SELECTs the row the first just
    flushed and overwrites its closing time and its evidence, with the tally
    reading two.
    """
    await _approve(session)
    await _concert(session, "doubled-changed", rounds=[("1次先行抽選", OPENS_UTC)])

    async def fetch(url):
        return PAGE_TWO_CLOSES

    report = await run_round_poll(
        session, NOW, fetcher=fetch, chat=fake_chat(DOUBLED_REPLY)
    )

    rows = await _proposals(session)
    assert len(rows) == 1
    assert (report.changed_proposals, report.new_proposals) == (1, 0)
    # The FIRST reading owns the row, exactly as in the `fresh` bucket.
    assert rows[0].closes_at_utc == CLOSES_UTC
    assert "2026年1月10日" in rows[0].evidence_yaml
    assert any("second reading of the same round" in r for r in report.rejections)


async def test_a_concert_deleted_mid_run_costs_one_concert_not_the_run(session):
    """`concert_export_yaml` opens with `session.refresh`, which raises
    `InvalidRequestError` -- a SQLAlchemyError -- once the row is gone. The
    blanket re-raise is about a poisoned FLUSH; this is a failed READ and the
    session stays perfectly usable.

    Mutation: dropping the `except InvalidRequestError` in `_poll_one` (or the
    `except ConcertVanished` beside it). The run then aborts on the first
    concert, the second is never polled, and the digest -- the only record the
    run happened at all -- is lost with it.
    """
    from sqlalchemy import text

    await _approve(session)
    # `ladder_polled_at_utc` NULL sorts first, so "gone" is read before "kept".
    await _concert(session, "gone", official_url="https://eplus.jp/gone")
    await _concert(
        session, "kept", official_url="https://eplus.jp/kept",
        polled_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    async def fetch(url):
        if "gone" in url:
            await session.execute(text("DELETE FROM concerts WHERE event_id = 'gone'"))
        return PAGE

    report = await run_round_poll(session, NOW, fetcher=fetch, chat=fake_chat(GOOD_REPLY))

    assert report.polled == 1
    assert report.failed == 1
    assert any("gone" in f and "no longer exists" in f for f in report.failures)
    # The concert behind it was still read, and its proposal still written.
    [proposal] = await _proposals(session)
    kept = (await session.execute(
        select(Concert).where(Concert.event_id == "kept")
    )).scalar_one()
    assert proposal.concert_id == kept.id
    # And the survivor's own stamp was still written -- the vanished one's is
    # skipped, not the whole tail of the run.
    assert kept.ladder_polled_at_utc == NOW
