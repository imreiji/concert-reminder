"""The discovery DM: readable above, copyable below, inside Discord's limit."""

import datetime as dt

from app.domain.discovery_message import (
    DM_CHAR_BUDGET,
    Lead,
    build_discovery_dm,
)


def _lead(n=1, artist="Liyuu", maybe_held=False):
    return Lead(
        event_id=str(400000 + n),
        title=f"Show {n}",
        date=dt.date(2026, 11, n),
        venue="Zepp Haneda",
        artist=artist,
        maybe_held=maybe_held,
    )


def test_it_names_the_artist_and_the_event():
    body = build_discovery_dm([_lead()], total=1)
    assert "Liyuu" in body and "Show 1" in body


def test_it_carries_a_closed_fenced_block():
    """An unclosed fence swallows the rest of the message into a code block --
    invisible to a length check, obvious to a reader."""
    body = build_discovery_dm([_lead()], total=1)
    assert body.count("```") == 2


def test_the_block_names_the_skill_and_the_grouping_rule():
    """Pasting it must be the whole action. Grouping legs into one concert is
    judgment and stays with the agent -- so the prompt has to say so.

    Scoped to the fenced block, like its neighbour
    test_every_listed_lead_appears_in_the_copy_block -- asserting against the
    WHOLE body would still pass if the prompt lived in the prose instead,
    which is exactly the failure this test exists to catch: a maintainer
    pasting bare URLs with no instructions."""
    block = build_discovery_dm([_lead()], total=1).split("```")[1]
    assert "add-concert" in block
    assert "ONE draft" in block


def test_every_listed_lead_appears_in_the_copy_block():
    leads = [_lead(n) for n in range(1, 4)]
    block = build_discovery_dm(leads, total=3).split("```")[1]
    for lead in leads:
        assert f"/events/{lead.event_id}" in block


def test_a_maybe_held_lead_is_marked():
    body = build_discovery_dm([_lead(maybe_held=True)], total=1)
    assert "already have" in body.lower()


def test_a_remainder_is_counted_and_linked():
    body = build_discovery_dm([_lead(n) for n in range(1, 4)], total=40)
    assert "37 more" in body
    assert "/admin/discoveries" in body


def test_it_stays_inside_the_budget_with_many_leads():
    """Long-but-plausible titles/venues, not the earlier short 'Show N' /
    'Zepp Haneda' fixtures -- those fit under budget with or without any
    truncation (1639 chars either way) and prove nothing about this code
    path. These inputs run the untruncated body to 2459 chars, past even
    Discord's real 2000 cap, so staying under DM_CHAR_BUDGET here actually
    depends on the block-truncation loop doing its job."""
    leads = [
        Lead(
            event_id=str(400000 + n),
            title=f"Show {n} at a moderately long venue tour name here",
            date=dt.date(2026, 11, n),
            venue="Zepp Haneda Tokyo Bayside Arena",
            artist="Liyuu",
            maybe_held=False,
        )
        for n in range(1, 11)
    ]
    body = build_discovery_dm(leads, total=200)
    block = body.split("```")[1]
    assert len(body) <= DM_CHAR_BUDGET
    assert "more not shown" in block
    # Partial, not total: the loop drops from the END only as far as it has
    # to. A mutation that empties the whole block outright (skipping the
    # loop and relying on the hard floor to save the day) would still
    # satisfy the two asserts above -- this is what actually pins the loop's
    # own behaviour rather than the floor's.
    assert f"/events/{leads[0].event_id}" in block


def test_an_uncapped_caller_keeps_every_line():
    """The budget belongs to the CHANNEL. /admin/discoveries has none, and it
    is where the DM's "+N more" line points -- so a lead dropped there would be
    reachable from nowhere.

    Uses the same long-field fixtures as the budget test above so the input
    genuinely overruns DM_CHAR_BUDGET; short 'Show N' leads would fit either
    way and would pass with budget=None ignored entirely."""
    leads = [
        Lead(
            event_id=str(400000 + n),
            title=f"Show {n} at a moderately long venue tour name here",
            date=dt.date(2026, 11, n),
            venue="Zepp Haneda Tokyo Bayside Arena",
            artist="Liyuu",
            maybe_held=False,
        )
        for n in range(1, 11)
    ]
    assert len(build_discovery_dm(leads, total=10)) <= DM_CHAR_BUDGET, "the DM still caps"

    body = build_discovery_dm(leads, total=10, budget=None)
    assert len(body) > DM_CHAR_BUDGET, "this input really does overrun the DM budget"
    block = body.split("```")[1]
    for lead in leads:
        assert f"/events/{lead.event_id}" in block
    assert "more not shown" not in block


def test_a_long_venue_is_clipped_in_the_prose():
    """venue is scraped free text off eventernote just like title (see
    domain/eventernote.py's _venue) -- an unclipped one sits ugly in the
    readable list even on an ordinary single-lead message that never comes
    close to the budget, so this can't be caught by a budget assertion; it
    has to check the prose directly."""
    long_venue = "v" * 200
    lead = Lead(
        event_id="1", title="Show", date=dt.date(2026, 11, 1),
        venue=long_venue, artist="Liyuu", maybe_held=False,
    )
    prose = build_discovery_dm([lead], total=1).split("```")[0]
    assert long_venue not in prose


def test_a_long_artist_name_is_clipped_in_the_header():
    """Same rationale as the venue clip, for the artist group header."""
    long_artist = "a" * 200
    lead = Lead(
        event_id="1", title="Show", date=dt.date(2026, 11, 1),
        venue="Zepp Haneda", artist=long_artist, maybe_held=False,
    )
    prose = build_discovery_dm([lead], total=1).split("```")[0]
    assert long_artist not in prose


def test_the_hard_floor_holds_under_absurd_field_lengths():
    """Per-field clipping bounds ONE field at a time; nothing bounds the
    prose half as a WHOLE once enough over-length fields pile up across many
    leads with distinct artists (one header line each). Un-floored, this
    exact input assembles to 2310 chars even with title/venue/artist all
    clipped and the copy block fully emptied -- past Discord's real 2000 cap,
    where discord.py raises and the maintainer gets nothing that day. The
    hard floor must truncate the prose itself rather than let that happen."""
    leads = [
        Lead(
            event_id=str(i),
            title="x" * 300,
            date=dt.date(2026, 11, 1),
            venue="v" * 200,
            artist=f"artist-{i}" * 10,
            maybe_held=False,
        )
        for i in range(10)
    ]
    body = build_discovery_dm(leads, total=10)
    assert len(body) <= DM_CHAR_BUDGET
    assert body.count("```") == 2


def test_dropped_block_lines_are_announced_in_the_block():
    """A DM that lists a lead above but silently omits it from the copy block is
    the quiet kind of wrong. If lines are dropped, the block must say so."""
    long_title = "x" * 300
    leads = [
        Lead(
            event_id=str(i), title=long_title, date=dt.date(2026, 11, 1),
            venue="v", artist="a", maybe_held=False,
        )
        for i in range(10)
    ]
    body = build_discovery_dm(leads, total=10)
    assert len(body) <= DM_CHAR_BUDGET
    block = body.split("```")[1]
    assert "truncated" in block.lower() or "more not shown" in block.lower()


def test_no_leads_produces_no_message():
    """Silence is the correct output for a quiet day: a daily 'nothing found'
    trains the reader to ignore the channel."""
    assert build_discovery_dm([], total=0) == ""
