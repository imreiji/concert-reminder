"""The quiet-ladder DM and copy block. Pure: no DB, no Discord."""

from datetime import date

from app.domain.quiet_ladder_message import (
    QuietEntry,
    build_quiet_ladder_block,
    build_quiet_ladder_dm,
)

BASE = "https://dekimasen.app"


def entry(n: int, **over) -> QuietEntry:
    fields = dict(
        title=f"Concert {n}",
        event_id=f"concert-{n}",
        leg_dates=(date(2026, 12, n),),
        round_labels=("最速先行",),
        official_url=f"https://example.jp/{n}",
        eventernote_url=None,
    )
    fields.update(over)
    return QuietEntry(**fields)


def test_no_entries_is_silence():
    """Running every tick makes this load-bearing: a 'nothing found' message
    at this cadence would be 1,440 DMs a day."""
    assert build_quiet_ladder_dm([], total=0, base_url=BASE) == ""


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
    # down to ~8 entries to land under budget -- so this fixture genuinely
    # exercises build_quiet_ladder_dm's shrink-until-it-fits loop.
    long_title = "Concert {n}: " + "A Very Long Concert Title For Testing " * 5
    entries = [entry(i, title=long_title.format(n=i)) for i in range(1, 21)]
    body = build_quiet_ladder_dm(entries, total=20, base_url=BASE)
    assert "20" in body
    assert len(body) <= 1900


def test_the_block_carries_what_a_re_check_needs():
    block = build_quiet_ladder_block([entry(1)])
    assert "concert-1" in block
    assert "https://example.jp/1" in block
    assert "最速先行" in block


def test_the_block_says_when_a_concert_has_no_rounds_at_all():
    """A concert with a closed 最速先行 reads differently from one with
    nothing -- the agent needs to know which it is."""
    block = build_quiet_ladder_block([entry(1, round_labels=())])
    assert "no rounds" in block.lower()


def test_the_block_says_when_a_concert_has_no_dates():
    block = build_quiet_ladder_block([entry(1, leg_dates=())])
    assert "no dates" in block.lower()
