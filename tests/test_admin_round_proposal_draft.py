"""The round poll's per-concert draft page: `GET
/admin/quiet-ladders/proposals/{event_id}`.

Task 4 of the round-poll phase-2 plan. Every proposal renders as a real form,
pre-filled with the model's own values, its quoted source line beside every
field that has one -- and, for a proposal whose round the concert already
holds under another date, the stored value beside the proposed one with no
Approve control at all (phase 2's write path is creates-only). No POST route
exists yet: every button here is `disabled`.
"""

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import ConcertDay, Round
from app.db.service import ensure_user, upsert_proposal
from app.db.session import get_session
from app.domain.types import RoundKind
from app.web import auth
from app.web.app import create_app

ADMIN_ID, EDITOR_ID = 42, 77


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False)
    c.db = db
    c.monkeypatch = monkeypatch
    return c


def login_as(client, discord_id, name):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def _concert(session, event_id, title):
    from app.db.models import Concert

    concert = Concert(title=title, event_id=event_id, created_by=ADMIN_ID)
    session.add(concert)
    await session.flush()
    return concert


async def _leg(session, concert, label, starts_at_utc, *, cancelled=False):
    day = ConcertDay(
        concert_id=concert.id, label=label, starts_at_utc=starts_at_utc, cancelled=cancelled,
    )
    session.add(day)
    await session.flush()
    return day


async def _held_round(session, concert, *, label, opens_at_utc, **kw):
    round_ = Round(
        concert_id=concert.id,
        kind=kw.pop("kind", RoundKind.LOTTERY_ROUND),
        label=label,
        opens_at_utc=opens_at_utc,
        closes_at_utc=kw.pop("closes_at_utc", None),
        results_at_utc=kw.pop("results_at_utc", None),
        payment_deadline_at_utc=kw.pop("payment_deadline_at_utc", None),
    )
    session.add(round_)
    await session.flush()
    return round_


NOW = datetime(2026, 8, 13, tzinfo=UTC)


async def _propose(session, concert, *, label, evidence_yaml="", **kw):
    return await upsert_proposal(
        session,
        concert.id,
        label=label,
        kind=kw.pop("kind", RoundKind.LOTTERY_ROUND),
        opens_at_utc=kw.pop("opens_at_utc", None),
        closes_at_utc=kw.pop("closes_at_utc", None),
        results_at_utc=kw.pop("results_at_utc", None),
        payment_deadline_at_utc=kw.pop("payment_deadline_at_utc", None),
        applies_to_labels=kw.pop("applies_to_labels", []),
        evidence_yaml=evidence_yaml,
        source_url=kw.pop("source_url", "https://example.jp/live/tickets"),
        now=kw.pop("now", NOW),
    )


def _proposal_block(html: str, proposal_id: int) -> str:
    """Everything from THIS proposal's own card onward.

    Every test in this file seeds exactly one proposal per page, so slicing
    to the end of the document is a tight enough scope -- and it still fails
    loudly (`marker not found`) if the row itself were deleted, rather than
    silently falling back to a page-wide search that base.html's nav or
    footer could satisfy by accident.
    """
    marker = f'id="proposal-{proposal_id}"'
    idx = html.find(marker)
    assert idx != -1, f"no proposal card for id {proposal_id}"
    return html[idx:]


# ── The basics ─────────────────────────────────────────────────────────────


async def test_the_draft_page_renders_for_an_admin(client):
    """Every page needs one logged-in GET render test -- a missing one
    shipped a 500 on this repo once, from template context drift."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s, "bushi", "ブシロード20周年")
        await _propose(s, concert, label="1次先行")
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/bushi")
    assert r.status_code == 200
    assert 'href="/tags"' in r.text  # signed-in nav only renders with "user" in context
    assert "1次先行" in r.text


async def test_a_non_admin_gets_403(client):
    """Invariant 5. Signed in as a real non-admin (the whitelisted editor,
    not merely signed out) -- a signed-out redirect would pass this test
    even with the admin check missing entirely."""
    login_as(client, EDITOR_ID, "editor")
    r = client.get("/admin/quiet-ladders/proposals/whatever")
    assert r.status_code == 403


async def test_a_concert_with_no_pending_proposals_renders_an_empty_state(client):
    """A link in a days-old digest DM must land somewhere sensible even after
    every proposal on that concert has already been handled. Mutation:
    404ing instead of rendering an empty state."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        await _concert(s, "quiet-now", "静かな公演")
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/quiet-now")
    assert r.status_code == 200
    assert "Nothing is waiting on a review" in r.text


# ── NEW: pre-filled fields and their evidence ────────────────────────────

OPENS = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)     # JST 12:00
CLOSES = datetime(2026, 9, 8, 5, 30, tzinfo=UTC)   # JST 14:30
RESULTS = datetime(2026, 9, 15, 0, 15, tzinfo=UTC)  # JST 09:15
PAYMENT = datetime(2026, 9, 22, 5, 45, tzinfo=UTC)  # JST 14:45


async def test_each_field_is_pre_filled_with_the_models_value(client):
    """Mutation: rendering empty inputs. Scoped to THIS proposal's own card,
    not the page -- base.html's nav and tab bar have made a page-wide
    assertion pass on this repo with the whole feature deleted."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s, "live-a", "Live A")
        proposal = await _propose(
            s, concert, label="1次先行",
            opens_at_utc=OPENS, closes_at_utc=CLOSES,
            results_at_utc=RESULTS, payment_deadline_at_utc=PAYMENT,
        )
        proposal_id = proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/live-a")
    assert r.status_code == 200
    block = _proposal_block(r.text, proposal_id)

    for name, expected in (
        ("round_opens_at", "2026-09-01T12:00"),
        ("round_closes_at", "2026-09-08T14:30"),
        ("round_results_at", "2026-09-15T09:15"),
        ("round_payment_at", "2026-09-22T14:45"),
    ):
        match = re.search(rf'name="{name}"\s+value="([^"]*)"', block)
        assert match, f"no input named {name} in the proposal's card"
        assert match.group(1) == expected, f"{name} was not pre-filled with the model's value"


async def test_every_field_shows_its_quoted_source_line(client):
    """The reason the page exists: without the quote an operator cannot
    check the claim. Mutation: rendering values without evidence."""
    evidence_yaml = (
        "apply_opens_jst: 一次先行受付開始 9月1日\n"
        "apply_closes_jst: 一次先行受付終了 9月8日\n"
        "results_jst: 当落発表 9月15日\n"
        "payment_deadline_jst: 入金期限 9月22日\n"
    )
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s, "live-a", "Live A")
        proposal = await _propose(
            s, concert, label="1次先行",
            opens_at_utc=OPENS, closes_at_utc=CLOSES,
            results_at_utc=RESULTS, payment_deadline_at_utc=PAYMENT,
            evidence_yaml=evidence_yaml,
        )
        proposal_id = proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/live-a")
    assert r.status_code == 200
    block = _proposal_block(r.text, proposal_id)

    for quote in (
        "一次先行受付開始 9月1日", "一次先行受付終了 9月8日",
        "当落発表 9月15日", "入金期限 9月22日",
    ):
        assert quote in block, f"missing quoted evidence: {quote!r}"


# ── CHANGED: stored beside proposed, and no Approve ──────────────────────


async def test_a_CHANGED_proposal_shows_stored_beside_proposed_and_no_apply(client):
    """Mutation: rendering it like a new one. Assert BOTH that the stored
    value appears AND that no apply control does -- either alone passes
    while the other half is wrong.

    The held round and the proposal share the same label and opening minute
    (the dedupe key) but disagree on the closing time -- exactly the shape
    `classify_stored_proposal` reports as "changed"."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s, "live-a", "Live A")
        await _held_round(
            s, concert, label="1次先行", opens_at_utc=OPENS,
            closes_at_utc=datetime(2026, 9, 8, 5, 0, tzinfo=UTC),  # JST 14:00
        )
        proposal = await _propose(
            s, concert, label="1次先行", opens_at_utc=OPENS,
            closes_at_utc=datetime(2026, 9, 10, 9, 0, tzinfo=UTC),  # JST 18:00
        )
        proposal_id = proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/live-a")
    assert r.status_code == 200
    block = _proposal_block(r.text, proposal_id)

    assert "14:00 JST" in block, "the stored (held) closing time must still be shown"
    assert "18:00 JST" in block, "the proposed closing time must be shown beside it"
    assert "Approve" not in block, "a CHANGED proposal must offer no Approve control"


async def test_a_proposal_matching_the_held_round_exactly_is_not_shown(client):
    """The "resolves itself" case: an operator who already applied the exact
    change by hand leaves the proposal row pending, but there is nothing left
    to review. Mutation: classifying it as CHANGED (or NEW) instead of
    filtering it out -- either would show a stale, already-resolved row.
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s, "live-a", "Live A")
        await _held_round(
            s, concert, label="1次先行", opens_at_utc=OPENS, closes_at_utc=CLOSES,
        )
        proposal = await _propose(
            s, concert, label="1次先行", opens_at_utc=OPENS, closes_at_utc=CLOSES,
        )
        proposal_id = proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/live-a")
    assert r.status_code == 200
    assert f'id="proposal-{proposal_id}"' not in r.text
    assert "Nothing is waiting on a review" in r.text


# ── Legs: empty/no-match means ALL, and unmatched is never dropped ───────


async def test_every_leg_box_is_ticked_when_the_model_named_none(client):
    """Empty means ALL. Mutation: rendering none ticked, which reads as a
    round applying to nothing. Two legs seeded so "all" and "the first"
    differ."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s, "live-a", "Live A")
        day1 = await _leg(s, concert, "Day 1", datetime(2026, 9, 1, 10, 0, tzinfo=UTC))
        day2 = await _leg(s, concert, "Day 2", datetime(2026, 9, 2, 10, 0, tzinfo=UTC))
        proposal = await _propose(s, concert, label="1次先行", applies_to_labels=[])
        proposal_id, day1_id, day2_id = proposal.id, day1.id, day2.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/live-a")
    assert r.status_code == 200
    block = _proposal_block(r.text, proposal_id)

    for day_id in (day1_id, day2_id):
        match = re.search(rf'value="{day_id}"[^>]*>', block)
        assert match, f"no checkbox for leg {day_id}"
        assert "checked" in match.group(0), (
            f"leg {day_id}'s box must be ticked when none were named"
        )


async def test_a_leg_label_matching_nothing_is_shown_as_unmatched(client):
    """Mutation: dropping it silently -- the operator then cannot tell the
    model read a leg this concert does not have."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s, "live-a", "Live A")
        await _leg(s, concert, "Day 1", datetime(2026, 9, 1, 10, 0, tzinfo=UTC))
        proposal = await _propose(
            s, concert, label="1次先行", applies_to_labels=["Day 9 (does not exist)"],
        )
        proposal_id = proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/live-a")
    assert r.status_code == 200
    block = _proposal_block(r.text, proposal_id)
    assert "Day 9 (does not exist)" in block
