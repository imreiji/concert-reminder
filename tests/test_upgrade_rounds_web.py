"""Upgrade rounds on the web surfaces: Discover pills (Task 6), Home rows and
the concert page (Task 7).

The upgrade campaign is a SECOND fact beside the base standing, and every
surface shows it only to a viewer eligible to enter -- a held qualifying
ticket. An ineligible or signed-out viewer sees the base state alone. These
tests pin the exact pill/label text ported from the design concept and the
eligibility gating on every surface.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import (
    Concert,
    ConcertDay,
    ConcertTag,
    Round,
    RoundOutcome,
    RoundQualifier,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import discover_statuses
from app.db.session import get_session
from app.domain.types import LotteryOutcome, RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

USER = 6262
OTHER = 7373
# Relative to the real clock, not an absolute date: the HTTP tests below hit
# real routes (discover.py, service.py's `_now()` default) that read
# `datetime.now(UTC)` directly, so an absolute NOW eventually drifts out of
# the seeded upgrade window and those tests start failing for timing reasons
# having nothing to do with what they are testing.
NOW = datetime.now(UTC)


def dt(offset_days: float) -> datetime:
    return NOW + timedelta(days=offset_days)


@pytest.fixture()
def client(db, monkeypatch):
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


def login(client, discord_id: int = USER, name: str = "reiji"):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


# ── seeding helpers ────────────────────────────────────────────────────────


async def _upgrade_concert(
    s, *, event_id="upgrade", base_outcome=None, up_outcome=None,
    up_opens=-2.0, up_closes=3.0, up_payment=None, user=USER, subscribe=True,
    qualifier=True,
):
    """A concert with one leg, a base lottery round and an upgrade round that
    qualifies on the base. Records the given outcomes for `user`."""
    c = Concert(title="Upgrade tour", event_id=event_id, created_by=USER)
    s.add(c)
    await s.flush()
    if subscribe:
        tag = Tag(name=f"Group {event_id}", kind=TagKind.ARTIST)
        s.add(tag)
        await s.flush()
        s.add(ConcertTag(concert_id=c.id, tag_id=tag.id))
        s.add(TagSubscription(user_id=user, tag_id=tag.id))
    leg = ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=dt(60))
    s.add(leg)
    await s.flush()
    base = Round(
        concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="最速先行",
        opens_at_utc=dt(-30), closes_at_utc=dt(-20), applies_to=[leg.id],
    )
    up = Round(
        concert_id=c.id, kind=RoundKind.UPGRADE, label="Seat upgrade",
        opens_at_utc=dt(up_opens), closes_at_utc=dt(up_closes),
        payment_deadline_at_utc=up_payment, applies_to=[leg.id],
    )
    s.add_all([base, up])
    await s.flush()
    if qualifier:
        s.add(RoundQualifier(upgrade_round_id=up.id, qualifying_round_id=base.id))
    if base_outcome is not None:
        s.add(RoundOutcome(user_id=user, round_id=base.id, outcome=base_outcome))
    if up_outcome is not None:
        s.add(RoundOutcome(user_id=user, round_id=up.id, outcome=up_outcome))
    await s.flush()
    return c, base, up


async def _statuses(s, user_id):
    concerts = list((await s.execute(
        select(Concert).options(
            selectinload(Concert.days), selectinload(Concert.rounds)
        )
    )).scalars())
    return await discover_statuses(s, concerts, user_id, NOW), {c.event_id: c for c in concerts}


async def _ensure_users(s):
    s.add_all([User(discord_id=USER, username="reiji"), User(discord_id=OTHER, username="ohter")])
    await s.flush()


# ── Task 6: Discover pills ─────────────────────────────────────────────────


async def test_secured_base_with_open_upgrade_shows_closes_pill(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(s, base_outcome=LotteryOutcome.PAID)
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Secured"
    assert st.tone == "ok"
    assert st.upgrade_text is not None and st.upgrade_text.startswith("Upgrade · Closes in")
    assert st.upgrade_tone == "accent"


async def test_secured_base_with_applied_upgrade_shows_applied_pill(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.APPLIED
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Secured"
    assert st.upgrade_text == "Upgrade · Applied"
    assert st.upgrade_tone == "accent"


async def test_won_upgrade_collapses_to_a_single_urgent_pill(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.WON,
            up_payment=datetime(2026, 7, 24, 12, tzinfo=UTC),
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    # ONE pill: the urgent standing replaces the base pill, no second pill.
    assert st.text == "Upgrade won — pay by 24 Jul"
    assert st.tone == "danger"
    assert st.upgrade_text is None


async def test_won_upgrade_without_a_deadline_reads_payment_due(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.WON, up_payment=None
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Upgrade won — payment due"
    assert st.upgrade_text is None


async def test_paid_upgrade_shows_secured_alone(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.PAID
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Secured"
    assert st.upgrade_text is None


async def test_lost_upgrade_shows_base_pill_alone(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.LOST
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Secured"
    assert st.upgrade_text is None


async def test_ineligible_user_sees_no_upgrade_pill(db):
    """A user who only APPLIED to the base (no secured ticket) is not eligible
    for the upgrade and sees no upgrade pill -- just their base standing."""
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(s, base_outcome=LotteryOutcome.APPLIED)
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.upgrade_text is None
    assert "Upgrade ·" not in st.text


async def test_signed_out_sees_no_upgrade_pill_and_same_facet(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(s, base_outcome=LotteryOutcome.PAID)
        signed_in, _b = await _statuses(s, USER)
        signed_out, _b2 = await _statuses(s, None)
    assert signed_out[c.id].upgrade_text is None
    # The status FACET is event-only: identical signed in and out.
    assert signed_out[c.id].status == signed_in[c.id].status


async def test_neutral_pill_prefers_a_non_upgrade_open_round(db):
    """A viewer with no standing on a concert with BOTH an open general sale
    and an open upgrade is led with the general sale, not the upgrade they
    cannot act on."""
    async with db() as s:
        await _ensure_users(s)
        c = Concert(title="Both open", event_id="both-open", created_by=USER)
        s.add(c)
        await s.flush()
        leg = ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=dt(60))
        s.add(leg)
        await s.flush()
        s.add_all([
            Round(concert_id=c.id, kind=RoundKind.GENERAL_SALE, label="General",
                  opens_at_utc=dt(-1), closes_at_utc=dt(5), applies_to=[leg.id]),
            Round(concert_id=c.id, kind=RoundKind.UPGRADE, label="Upgrade",
                  opens_at_utc=dt(-1), closes_at_utc=dt(2), applies_to=[leg.id]),
        ])
        await s.flush()
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text.startswith("General sale")
    assert "Upgrade round" not in st.text


async def test_get_discover_renders_the_upgrade_pill_for_an_eligible_user(client):
    async def seed(s):
        await _ensure_users(s)
        await _upgrade_concert(s, base_outcome=LotteryOutcome.PAID)

    async with client.db() as s:
        await seed(s)
        await s.commit()
    login(client)

    html = client.get("/discover").text
    assert "Upgrade · Closes in" in html
    assert "s-up" in html


def test_get_discover_signed_out_still_renders(client):
    r = client.get("/discover")
    assert r.status_code == 200


# ── Task 7: Home "Coming up" rows ──────────────────────────────────────────


async def test_home_coming_up_hides_upgrade_rows_from_ineligible_users(client):
    """A viewer with no secured ticket cannot enter the upgrade, so its Coming
    up row -- whose buttons would be false testimony -- is dropped."""
    async with client.db() as s:
        await _ensure_users(s)
        await _upgrade_concert(s, base_outcome=None)  # ineligible: nothing secured
        await s.commit()
    login(client)

    html = client.get("/").text
    # Positive control first: proves the page rendered with this concert on it,
    # so an empty/error page could not accidentally satisfy the absence check
    # below (this test passed vacuously once already -- see the date-bomb fix).
    assert "Upgrade tour" in html
    # The capture button is unique to the Coming up row (the board carries none).
    assert "Entered upgrade" not in html


async def test_home_coming_up_shows_upgrade_row_once_qualified(client):
    """With the qualifying base ticket secured, the upgrade's Coming up row
    appears and its capture reads the upgrade wording."""
    async with client.db() as s:
        await _ensure_users(s)
        await _upgrade_concert(s, base_outcome=LotteryOutcome.WON)
        await s.commit()
    login(client)

    html = client.get("/").text
    assert "Entered upgrade" in html
    assert "Skipping" in html


async def test_entered_upgrade_posts_applied_through_the_one_write_path(client):
    """Pressing "Entered upgrade" posts APPLIED to the SAME
    /rounds/{id}/outcome route every capture uses -- no new endpoint."""
    async with client.db() as s:
        await _ensure_users(s)
        _c, _base, up = await _upgrade_concert(s, base_outcome=LotteryOutcome.WON)
        up_id = up.id
        await s.commit()
    login(client)

    r = client.post(
        f"/rounds/{up_id}/outcome", data={"outcome": "applied"}, headers={"HX-Request": "true"}
    )
    assert r.status_code == 200
    async with client.db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(
                RoundOutcome.user_id == USER, RoundOutcome.round_id == up_id
            )
        )).scalar_one()
    assert outcome is LotteryOutcome.APPLIED


async def test_global_deadline_list_keeps_upgrade_rows_for_everyone(client):
    """`upcoming_deadlines` is a public fact and is unchanged: the upgrade's
    close still lists on Discover's Coming up soon, even signed out."""
    async with client.db() as s:
        await _ensure_users(s)
        await _upgrade_concert(s, base_outcome=None)
        await s.commit()

    html = client.get("/discover").text  # signed out
    assert "Seat upgrade" in html


# ── Task 7: the concert page ───────────────────────────────────────────────


async def test_concert_page_shows_requirement_line_for_ineligible_user(client):
    async with client.db() as s:
        await _ensure_users(s)
        await _upgrade_concert(s, event_id="np", base_outcome=None)
        await s.commit()
    login(client)

    html = client.get("/concerts/np").text
    assert "Requires a ticket from: 最速先行" in html
    assert "Entered upgrade" not in html


async def test_concert_page_shows_capture_for_eligible_user(client):
    async with client.db() as s:
        await _ensure_users(s)
        await _upgrade_concert(s, event_id="np", base_outcome=LotteryOutcome.PAID)
        await s.commit()
    login(client)

    html = client.get("/concerts/np").text
    assert "Entered upgrade" in html
    assert "Requires a ticket from" not in html


async def test_concert_page_renders_the_upgrade_round_with_dual_times(client):
    async with client.db() as s:
        await _ensure_users(s)
        await _upgrade_concert(s, event_id="np", base_outcome=LotteryOutcome.PAID)
        await s.commit()
    login(client)

    r = client.get("/concerts/np")
    assert r.status_code == 200
    assert "Seat upgrade" in r.text
    assert "JST" in r.text  # times always render dual, JST first
