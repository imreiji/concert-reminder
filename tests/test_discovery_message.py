"""The discovery DM: readable above, copyable below, inside Discord's limit."""

import datetime as dt

from app.domain.discovery_message import (
    DM_CHAR_BUDGET,
    DM_LIST_LIMIT,
    Lead,
    build_discovery_dm,
)

_EVENT_PREFIX = "https://www.eventernote.com/events/"


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
    depends on the shrink loop doing its job."""
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
    # to. A mutation that lists nothing at all (skipping the loop and relying
    # on the "+N more" skeleton to come in under budget) would still satisfy
    # the two asserts above -- this is what actually pins the loop's own
    # behaviour rather than the floor's.
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


def test_the_hard_floor_holds_when_not_even_one_lead_fits():
    """The last resort. Both halves now shrink together, so a message naming
    ANY lead is short enough for Discord long before the floor matters -- what
    is left below it is the "+N more" skeleton, measured at 262 chars for this
    input. A budget under that has nothing listable in it at all, and the floor
    has to truncate the prose rather than emit an over-budget message: past
    Discord's real 2000 cap discord.py raises and the maintainer hears nothing
    at all that day, which is the one outcome this code path exists to prevent.

    budget=200 is deliberate: below the 262-char skeleton so the floor is
    genuinely entered, above the 175-char fenced footer so truncating can
    actually reach the bound."""
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
    assert len(build_discovery_dm(leads, total=10, budget=None)) > 200, (
        "the input really does overrun this budget"
    )
    body = build_discovery_dm(leads, total=10, budget=200)
    assert len(body) <= 200
    assert body.count("```") == 2


def _prose_and_block_counts(body: str) -> tuple[int, int]:
    """How many leads each half names."""
    prose, block = body.split("```")[0], body.split("```")[1]
    return (
        sum(1 for line in prose.splitlines() if line.startswith("· ")),
        sum(1 for line in block.splitlines() if line.startswith(_EVENT_PREFIX)),
    )


def test_the_two_halves_name_exactly_the_same_leads():
    """The copy block IS the deliverable -- "paste this to an agent" -- and the
    prose above it is duplicate content. Trimming only the block inverted that:
    measured against a real Hasunosora-length title and a real venue, ten leads
    in prose left THREE in the block, so 70% of the point of the message was
    crowded out by its own restatement. Both halves now name the same leads.

    This input genuinely forces the shrink (an untruncated ten runs past the
    budget), which is what makes the equality assertion about the sync rule
    rather than about a message that happened to fit whole."""
    title = (
        "ラブライブ！蓮ノ空女学院スクールアイドルクラブ "
        "3rd Live Tour Blooming with 105 Day.1 昼公演"
    )
    leads = [
        Lead(
            event_id=str(460000 + n),
            title=title,
            date=dt.date(2026, 11, n + 1),
            venue="バンテリンドーム ナゴヤ",
            artist="蓮ノ空女学院スクールアイドルクラブ",
            maybe_held=False,
        )
        for n in range(10)
    ]
    assert len(build_discovery_dm(leads, total=10, budget=None)) > DM_CHAR_BUDGET, (
        "this input really does need truncating"
    )

    body = build_discovery_dm(leads, total=10)
    assert len(body) <= DM_CHAR_BUDGET
    listed, blocked = _prose_and_block_counts(body)
    assert listed == blocked, "the two halves disagree about today's leads"
    assert 0 < listed < 10, "the message really was shrunk, not merely rendered whole"
    # And the counts stay honest about the backlog that is NOT in the message.
    assert f"+{10 - listed} more" in body
    assert f"# {10 - listed} more not shown" in body


def test_the_dm_never_names_more_than_the_list_limit():
    """DM_LIST_LIMIT is a real cap now, not a comment in a module nothing
    imported: past ten a digest stops being scannable whatever the character
    budget allows. Applied inside the builder because the caller cannot know
    how many will fit, and the two halves have to agree on the answer.

    An enormous budget so nothing else can be doing the capping."""
    leads = [_lead(n) for n in range(1, 26)]
    body = build_discovery_dm(leads, total=25, budget=100_000)
    listed, blocked = _prose_and_block_counts(body)
    assert listed == blocked == DM_LIST_LIMIT
    assert "+15 more" in body


def test_an_uncapped_caller_is_not_held_to_the_list_limit():
    """The control for the cap: /admin/discoveries passes budget=None and gets
    every lead, so the assertion above is about DM_LIST_LIMIT and not about the
    builder refusing to render more than ten of anything."""
    leads = [_lead(n) for n in range(1, 26)]
    listed, blocked = _prose_and_block_counts(
        build_discovery_dm(leads, total=25, budget=None)
    )
    assert listed == blocked == 25


def test_dropped_leads_are_announced_in_the_block():
    """The block is what gets pasted, so it has to say when it is partial --
    an agent handed a silently short list catalogues a silently short backlog.
    The prose carries the same count in its "+N more" line; this pins the
    block's own copy, which lives inside the fence where no link survives."""
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


# ── Calendar leads: the deadline marker and the feed-label grouping ────────


def _calendar_lead(n=1, source="ll-main", artist="LL-Fans メイン", deadline=False):
    return Lead(
        event_id=f"{source}:uid{n}",
        title=f"Calendar Show {n}",
        date=dt.date(2026, 11, n),
        venue="Zepp Haneda",
        artist=artist,
        maybe_held=False,
        deadline=deadline,
        source=source,
    )


def test_a_deadline_lead_carries_the_marker_in_both_halves():
    """申込締切 is ADDITIVE on the date -- both the readable prose line and the
    copyable block line must carry it, or a triage agent reading only one half
    reads a deadline as a performance date."""
    lead = _calendar_lead(deadline=True)
    body = build_discovery_dm([lead], total=1)
    prose, block = body.split("```")[0], body.split("```")[1]
    assert "申込締切" in prose
    assert "申込締切" in block


def test_a_non_deadline_calendar_lead_carries_no_marker():
    """The control: a plain event-dated calendar lead (e.g. LL-Fans' main
    feed) must not be marked as a deadline it is not."""
    lead = _calendar_lead(deadline=False)
    body = build_discovery_dm([lead], total=1)
    assert "申込締切" not in body


def test_a_calendar_lead_groups_under_its_feed_label():
    """`_lead` resolves a calendar row's display source into the SAME
    grouping slot an Eventernote lead's artist name uses -- so the digest
    should show a feed-labelled group beside an artist-labelled one with no
    renderer changes beyond the marker."""
    leads = [
        _lead(1, artist="Liyuu"),
        _calendar_lead(2, artist="LL-Fans メイン"),
    ]
    body = build_discovery_dm(leads, total=2)
    assert "**Liyuu**" in body
    assert "**LL-Fans メイン**" in body


def test_a_calendar_lead_never_builds_an_eventernote_link():
    """A calendar lead's event_id is a namespaced "<feed key>:<uid>", not an
    Eventernote numeric id -- EVENT_URL.format(event_id=...) on it would build
    a URL that resolves nowhere real. Neither half may build one."""
    lead = _calendar_lead()
    body = build_discovery_dm([lead], total=1)
    assert "eventernote.com" not in body
    block = body.split("```")[1]
    # The id itself must still be present in the block -- just bare, not
    # wrapped in a broken Eventernote URL.
    assert lead.event_id in block


def test_an_eventernote_lead_still_links_as_before():
    """The control for the fix above: an ordinary Eventernote-sourced lead
    (the default `source`) must keep building its real link."""
    lead = _lead()
    body = build_discovery_dm([lead], total=1)
    block = body.split("```")[1]
    assert f"{_EVENT_PREFIX}{lead.event_id}" in block
