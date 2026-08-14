"""The round poll's review page: read-only in phase 1, so there is nothing
here but a render -- no button, no form, no route that writes.

The link the digest DM already sends 404s without this page; these tests
pin what should be behind it once it exists.
"""

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import settings
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


NOW = datetime(2026, 8, 13, tzinfo=UTC)


async def _propose(session, concert, *, label, evidence_yaml="", **kw):
    return await upsert_proposal(
        session,
        concert.id,
        label=label,
        kind=kw.pop("kind", RoundKind.LOTTERY_ROUND),
        opens_at_utc=kw.pop("opens_at_utc", None),
        closes_at_utc=kw.pop("closes_at_utc", None),
        evidence_yaml=evidence_yaml,
        source_url=kw.pop("source_url", "https://example.jp/live/tickets"),
        now=kw.pop("now", NOW),
    )


async def test_the_page_renders_for_an_admin(client):
    """Every page needs at least one logged-in GET render test -- a missing
    one shipped a 500 once (template context drift). Seeded with one real
    proposal so the render actually exercises the grouped-table branch, not
    just the empty state.
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s, "bushi", "ブシロード20周年")
        await _propose(s, concert, label="1次先行")
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals")
    assert r.status_code == 200
    assert 'href="/tags"' in r.text  # signed-in nav only renders with "user" in context


async def test_a_non_admin_gets_403(client):
    """Invariant 5: signed in and unauthorized is 403, not 404 (which is
    reserved for ownership checks on another user's own records)."""
    login_as(client, EDITOR_ID, "editor")
    r = client.get("/admin/quiet-ladders/proposals")
    assert r.status_code == 403


async def test_each_proposal_shows_its_quoted_evidence(client):
    """The whole point of the page. Mutation: rendering the label and dates
    without the quote -- the operator then cannot check the claim, which is
    what separates this from a guess.

    Scoped to the ROW: asserts the label and its quote appear close together
    (lazy `.*?` between them, same `<tr>`), not merely somewhere on the page
    -- this repo shipped a test that passed with its feature deleted because
    base.html already contained the asserted string elsewhere on the page.
    Two concerts seeded, each with its own proposal and its own distinct
    quote, so a bug that dropped the concert grouping (e.g. showing only the
    first concert's rows) is visible too.
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert_a = await _concert(s, "live-a", "Live A")
        concert_b = await _concert(s, "live-b", "Live B")
        await _propose(
            s, concert_a, label="1次先行",
            evidence_yaml="apply_opens_jst: 一次先行受付開始 8月1日\n",
        )
        await _propose(
            s, concert_b, label="一般発売",
            evidence_yaml="apply_closes_jst: 一般発売受付終了 9月1日\n",
        )
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals")
    assert r.status_code == 200
    assert re.search(
        r"<tr>\s*<td>1次先行</td>.*?一次先行受付開始 8月1日.*?</tr>", r.text, re.S
    ), "concert A's proposal row must carry its own quoted evidence"
    assert re.search(
        r"<tr>\s*<td>一般発売</td>.*?一般発売受付終了 9月1日.*?</tr>", r.text, re.S
    ), "concert B's proposal row must carry its own quoted evidence"
    # Cross-check: concert A's quote must NOT be the text found beside concert
    # B's label (guards against a mutation that renders every row with the
    # first proposal's evidence, which the two checks above alone could miss
    # if row order happened to match).
    assert not re.search(
        r"<tr>\s*<td>一般発売</td>.*?一次先行受付開始 8月1日.*?</tr>", r.text, re.S
    )


async def test_a_dismissed_proposal_is_not_listed(client):
    """Mutation: listing everything (dropping the dismissed_at filter, i.e.
    querying RoundProposal directly instead of going through
    pending_proposal_groups/pending_proposals).

    Both a pending and a dismissed proposal are seeded on DIFFERENT concerts
    so a broken concert-grouping join could not accidentally hide the
    dismissed one for the wrong reason.
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert_a = await _concert(s, "live-a", "Live A")
        concert_b = await _concert(s, "live-b", "Live B")
        await _propose(s, concert_a, label="1次先行")
        refused = await _propose(s, concert_b, label="2次先行")
        refused.dismissed_at = NOW
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals")
    assert r.status_code == 200
    assert "<td>1次先行</td>" in r.text
    assert "<td>2次先行</td>" not in r.text
    assert "Live B" not in r.text  # the dismissed proposal's concert has nothing else pending
