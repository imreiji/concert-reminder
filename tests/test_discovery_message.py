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
    judgment and stays with the agent -- so the prompt has to say so."""
    body = build_discovery_dm([_lead()], total=1)
    assert "add-concert" in body
    assert "ONE draft" in body


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
    body = build_discovery_dm([_lead(n) for n in range(1, 11)], total=200)
    assert len(body) <= DM_CHAR_BUDGET


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
