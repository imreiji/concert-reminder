"""The quiet-ladder DM and copy block. Pure: no DB, no Discord."""

from datetime import UTC, date, datetime

from app.domain.quiet_ladder_message import (
    QuietEntry,
    QuietRoundInfo,
    build_quiet_ladder_block,
    build_quiet_ladder_dm,
)

BASE = "https://dekimasen.app"


def round_info(label: str = "最速先行", **over) -> QuietRoundInfo:
    fields = dict(
        label=label,
        opens_at_utc=None,
        closes_at_utc=None,
        results_at_utc=None,
        payment_deadline_at_utc=None,
    )
    fields.update(over)
    return QuietRoundInfo(**fields)


def entry(n: int, **over) -> QuietEntry:
    fields = dict(
        title=f"Concert {n}",
        title_en=None,
        event_id=f"concert-{n}",
        leg_dates=(date(2026, 12, n),),
        rounds=(round_info(),),
        official_url=f"https://example.jp/{n}",
        eventernote_url=None,
        source_url=None,
    )
    fields.update(over)
    return QuietEntry(**fields)


def test_no_entries_is_silence():
    """Running every tick makes this load-bearing: a 'nothing found' message
    at this cadence would be 1,440 DMs a day."""
    assert build_quiet_ladder_dm([], total=0, base_url=BASE) == ""
    # The spec requires BOTH functions to answer "" on empty input -- the DM
    # and the block are two different render paths over the same dataclass,
    # so silence on one proves nothing about the other.
    assert build_quiet_ladder_block([]) == ""


def test_the_dm_names_the_concerts_and_links_the_page():
    body = build_quiet_ladder_dm([entry(1)], total=1, base_url=BASE)
    assert "Concert 1" in body
    assert f"{BASE}/admin/quiet-ladders" in body


def test_the_dm_reports_the_real_total_when_it_cannot_name_them_all():
    # Titles are deliberately long here, not the bare "Concert N" the entry()
    # default gives. Measured: with short titles, 20 entries (capped to
    # DM_LIST_LIMIT=10 before any shrinking) render at ~386 chars -- nowhere
    # near the 1900 budget, so the shrink loop never runs and the len()
    # assertion below would pass trivially. With titles this long, the
    # unshrunk 10-entry body measures ~2306 chars, and the shrink loop pops
    # down to 8 entries to land under budget -- so this fixture genuinely
    # exercises build_quiet_ladder_dm's shrink-until-it-fits loop.
    long_title = "Concert {n}: " + "A Very Long Concert Title For Testing " * 5
    entries = [entry(i, title=long_title.format(n=i)) for i in range(1, 21)]
    body = build_quiet_ladder_dm(entries, total=20, base_url=BASE)
    assert "20" in body
    assert len(body) <= 1900
    # "20" in body is satisfied by the head line's total alone, whatever the
    # drop-count logic does -- it does not pin the "...and N more." line.
    # Measured: 20 entries shrink to 8 kept, so the honest gap is 20 - 8 = 12.
    # Assert that exact line, not a loose substring, so a wrong dropped-count
    # expression (e.g. against len(entries) instead of total, or a stray
    # off-by-one) fails this test instead of hiding behind the "20" check.
    assert "…and 12 more." in body


def test_the_block_carries_what_a_re_check_needs():
    block = build_quiet_ladder_block([entry(1)])
    assert "concert-1" in block
    assert "https://example.jp/1" in block
    assert "最速先行" in block


def test_the_block_says_when_a_concert_has_no_rounds_at_all():
    """A concert with a closed 最速先行 reads differently from one with
    nothing -- the agent needs to know which it is."""
    block = build_quiet_ladder_block([entry(1, rounds=())])
    assert "no rounds" in block.lower()


def test_the_block_says_when_a_concert_has_no_dates():
    block = build_quiet_ladder_block([entry(1, leg_dates=())])
    assert "no dates" in block.lower()


def test_the_block_carries_a_rounds_moments():
    """The load-bearing part per the design spec (2026-08-11-round-watch-
    design.md:236-240): without the moments, an agent re-proposing this
    ladder cannot tell a round that closed months ago from one that never
    got a date -- both would otherwise read as the same bare label."""
    r = round_info(
        "最速先行",
        opens_at_utc=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        closes_at_utc=datetime(2026, 7, 8, 23, 59, tzinfo=UTC),
    )
    block = build_quiet_ladder_block([entry(1, rounds=(r,))])
    # JST, not UTC (+9h): 2026-07-01 10:00 UTC -> 19:00 JST same day;
    # 2026-07-08 23:59 UTC -> 08:59 JST the FOLLOWING day. yaml_export._jst_str
    # writes the identical "%Y-%m-%d %H:%M" shape into an agent's draft
    # apply_closes_jst/results_jst/payment_deadline_jst fields -- an
    # unlabelled UTC string here would be indistinguishable from that JST one
    # and a transcribing agent would silently move the deadline nine hours
    # earlier into the database. The explicit "JST" suffix is what a
    # regression to bare UTC (a plausible "simplification" of _moment) would
    # drop, so it is asserted here rather than left implicit.
    assert "2026-07-01 19:00 JST" in block
    assert "2026-07-09 08:59 JST" in block
    assert "opens" in block
    assert "closes" in block


def test_a_round_with_no_moments_says_so():
    """Distinguishes 'known, nothing scheduled' from 'unknown entirely' --
    the whole reason round_labels alone was not enough."""
    block = build_quiet_ladder_block([entry(1, rounds=(round_info("一般発売"),))])
    assert "一般発売" in block
    assert "no moments" in block.lower()


def test_the_block_includes_the_source_url_when_present():
    block = build_quiet_ladder_block([entry(1, source_url="https://source.example/1")])
    assert "https://source.example/1" in block


def test_the_block_omits_the_source_line_when_absent():
    block = build_quiet_ladder_block([entry(1, source_url=None)])
    assert "source:" not in block


def test_the_block_shows_title_en_beside_title_when_different():
    block = build_quiet_ladder_block([entry(1, title="邦題", title_en="English Title")])
    assert "邦題 / English Title" in block


def test_the_block_does_not_repeat_a_title_en_identical_to_title():
    block = build_quiet_ladder_block([entry(1, title="Same", title_en="Same")])
    assert block.count("Same") == 1


def test_the_dm_stays_unchanged_by_the_new_fields():
    """The DM is a nudge and must stay short -- rounds/title_en/source_url
    feed the block only. Two entries differing ONLY in those fields must
    render an identical DM body."""
    plain = entry(1)
    rich = entry(
        1,
        title_en="Rich Title EN",
        rounds=(round_info(
            "最速先行",
            opens_at_utc=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            closes_at_utc=datetime(2026, 7, 8, 23, 59, tzinfo=UTC),
        ),),
        source_url="https://source.example/1",
    )
    assert build_quiet_ladder_dm([plain], total=1, base_url=BASE) == (
        build_quiet_ladder_dm([rich], total=1, base_url=BASE)
    )
