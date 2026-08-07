"""The web counterpart to the DM outcome buttons: POST /rounds/{id}/outcome.

Every assertion here is about what `record_round_outcome` ACTUALLY does, not
what the route wishes it did -- the route is a thin shell and must not diverge
from `bot/views.py`'s `_handle_outcome_click`, which is the other call site.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    Concert,
    ConcertDay,
    ConcertSubscription,
    LegOptOut,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundOutcome,
    RoundOutcomeDay,
    User,
)
from app.db.service import (
    ensure_user,
    record_round_outcome,
    sync_rule,
    upcoming_deadlines,
)
from app.db.session import get_session
from app.domain.types import (
    Anchor,
    LegResult,
    LotteryOutcome,
    RoundKind,
    SubscriptionState,
)
from app.web import auth
from app.web.app import create_app

USER_A, USER_B = 4242, 9999


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


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def seed_round(db) -> int:
    """One concert with one live day and one open lottery round."""
    now = datetime.now(UTC)
    async with db() as s:
        # PRAGMA foreign_keys=ON is on, so the users these rows point at must
        # exist before them. Logging in would create them too, but the seed
        # runs first.
        s.add_all([
            User(discord_id=USER_A, username="reiji"),
            User(discord_id=USER_B, username="someone-else"),
        ])
        await s.flush()
        concert = Concert(title="Hasunosora 6th", event_id="hasu-6th", created_by=USER_A)
        s.add(concert)
        await s.flush()
        s.add(ConcertDay(
            concert_id=concert.id, label="Day 1", starts_at_utc=now + timedelta(days=60)
        ))
        round_ = Round(
            concert_id=concert.id, label="Lottery 1", kind=RoundKind.LOTTERY_ROUND,
            opens_at_utc=now - timedelta(days=1), closes_at_utc=now + timedelta(days=7),
        )
        s.add(round_)
        await s.flush()
        await s.commit()
        return round_.id


async def outcome_for(db, user_id: int, round_id: int) -> LotteryOutcome | None:
    async with db() as s:
        row = (await s.execute(select(RoundOutcome).where(
            RoundOutcome.user_id == user_id, RoundOutcome.round_id == round_id
        ))).scalar_one_or_none()
        return row.outcome if row else None


def post_outcome(client, round_id: int, outcome: str):
    """Posts as htmx does. Without the HX-Request header the route redirects
    to Home instead of rendering fragments (the JS-disabled fallback), so the
    header is what keeps these tests on the 200-and-a-fragment path they are
    asserting about. The redirect itself is covered in test_home.py."""
    return client.post(
        f"/rounds/{round_id}/outcome",
        data={"outcome": outcome},
        headers={"HX-Request": "true"},
    )


async def test_i_have_applied_records_applied(client):
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    r = post_outcome(client, rid, "applied")
    assert r.status_code == 200
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.APPLIED


async def test_not_applying_records_not_applied(client):
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    assert post_outcome(client, rid, "not_applied").status_code == 200
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.NOT_APPLIED


async def test_paid_is_reachable_from_won(client):
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    post_outcome(client, rid, "won")
    assert post_outcome(client, rid, "paid").status_code == 200
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.PAID


async def test_paid_without_a_prior_won_behaves_as_the_service_defines(client):
    """`record_round_outcome` returns SILENTLY when PAID has no prior WON --
    it is not an error, it is a no-op. The route must not invent a 4xx the
    DM buttons don't produce, so this asserts a 200 with nothing written."""
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    assert post_outcome(client, rid, "paid").status_code == 200
    assert await outcome_for(client.db, USER_A, rid) is None


async def test_requires_login(client):
    rid = await seed_round(client.db)
    r = post_outcome(client, rid, "applied")
    # htmx request: HX-Redirect, not a 303 the XHR would follow and swap in.
    assert r.status_code == 204
    assert r.headers["hx-redirect"] == "/"
    assert await outcome_for(client.db, USER_A, rid) is None


async def test_unknown_round_404s(client):
    """The service returns silently for a missing round, so the route needs
    its own existence check to avoid reporting an honest-looking success."""
    await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    assert post_outcome(client, 987654, "applied").status_code == 404


async def test_bad_outcome_value_422s(client):
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    assert post_outcome(client, rid, "definitely_not_an_outcome").status_code == 422


async def test_outcome_is_scoped_to_the_calling_user(client):
    """Two users on the SAME round keep independent state -- the user comes
    from the session, never from the request body."""
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    post_outcome(client, rid, "applied")
    login_as(client, USER_B, "someone-else")
    post_outcome(client, rid, "won")

    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.APPLIED
    assert await outcome_for(client.db, USER_B, rid) is LotteryOutcome.WON


async def test_upcoming_deadline_carries_round_id_only_for_round_rows(db):
    """A Coming up row has to know which round to post to. Day-derived rows
    (EVENT_START) have no round at all, so the field stays None there."""
    await seed_round(db)
    async with db() as s:
        rows = await upcoming_deadlines(s, limit=50)

    by_anchor = {}
    for row in rows:
        by_anchor.setdefault(row.anchor, []).append(row)

    closes = by_anchor[Anchor.CLOSES][0]
    assert closes.round_id is not None

    start = by_anchor[Anchor.EVENT_START][0]
    assert start.round_id is None


# ── POST /rounds/{id}/day-result ─────────────────────────────────────────
#
# The per-leg sibling of the route above, and the same kind of thin shell:
# every assertion is about what the Task 3 writers actually do
# (`record_round_day_result`, `record_remaining_days_lost`, `set_leg_opt_out`),
# never about a rule the route invented for itself.


async def seed_multi_leg(db) -> tuple[int, int, int]:
    """A two-leg round USER_A is in, whose results are already out -- the one
    shape that has legs left to resolve."""
    now = datetime.now(UTC)
    async with db() as s:
        await ensure_user(s, USER_A, "reiji")
        await ensure_user(s, USER_B, "someone-else")
        concert = Concert(title="Two nights", event_id="two-nights", created_by=USER_A)
        s.add(concert)
        await s.flush()
        d1 = ConcertDay(
            concert_id=concert.id, label="Day 1", starts_at_utc=now + timedelta(days=60)
        )
        d2 = ConcertDay(
            concert_id=concert.id, label="Day 2", starts_at_utc=now + timedelta(days=61)
        )
        s.add_all([d1, d2])
        await s.flush()
        round_ = Round(
            concert_id=concert.id, label="Fan club lottery", kind=RoundKind.LOTTERY_ROUND,
            applies_to=[d1.id, d2.id],
            opens_at_utc=now - timedelta(days=10), closes_at_utc=now - timedelta(days=3),
            results_at_utc=now - timedelta(hours=1),
        )
        s.add(round_)
        await s.flush()
        await record_round_outcome(s, USER_A, round_.id, LotteryOutcome.APPLIED)
        await s.commit()
        return round_.id, d1.id, d2.id


def post_day_result(client, round_id: int, **data):
    """Posts as htmx does, for the same reason post_outcome does."""
    return client.post(
        f"/rounds/{round_id}/day-result", data=data, headers={"HX-Request": "true"}
    )


async def day_results_for(db, user_id: int, round_id: int) -> dict[int, LegResult]:
    async with db() as s:
        return {
            r.day_id: r.result
            for r in (await s.execute(select(RoundOutcomeDay).where(
                RoundOutcomeDay.user_id == user_id, RoundOutcomeDay.round_id == round_id
            ))).scalars()
        }


async def opted_out_days(db, user_id: int) -> set[int]:
    async with db() as s:
        return set((await s.execute(select(LegOptOut.concert_day_id).where(
            LegOptOut.user_id == user_id
        ))).scalars())


async def test_day_result_won_records_day_and_flips_round(client):
    """A win on one leg is a win: the day row lands and the round-level
    outcome follows it to WON, exactly as record_round_day_result defines."""
    rid, d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    assert post_day_result(client, rid, result="won", day_id=d1).status_code == 200
    assert await day_results_for(client.db, USER_A, rid) == {d1: LegResult.WON}
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.WON


async def test_day_result_lost_leaves_the_round_open_while_a_leg_pends(client):
    """One leg lost, the other still unheard: the round is not settled yet."""
    rid, d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    assert post_day_result(client, rid, result="lost", day_id=d1).status_code == 200
    assert await day_results_for(client.db, USER_A, rid) == {d1: LegResult.LOST}
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.APPLIED


async def test_day_result_skip_writes_leg_opt_out(client):
    """"Not going" is not a result -- LegResult has only WON and LOST. It is a
    per-leg opt-out, so it writes a LegOptOut row and NO day row."""
    rid, _d1, d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    assert post_day_result(client, rid, result="skip", day_id=d2).status_code == 200
    assert await opted_out_days(client.db, USER_A) == {d2}
    assert await day_results_for(client.db, USER_A, rid) == {}


async def test_day_result_lost_rest_settles_round(client):
    """Won Saturday, lost the rest: every unresolved leg gets a LOST row and
    the round keeps the WON its ticket earned."""
    rid, d1, d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    post_day_result(client, rid, result="won", day_id=d1)
    assert post_day_result(client, rid, result="lost_rest").status_code == 200
    assert await day_results_for(client.db, USER_A, rid) == {
        d1: LegResult.WON, d2: LegResult.LOST
    }
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.WON


async def test_day_result_bad_result_value_422(client):
    rid, d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    assert post_day_result(
        client, rid, result="definitely_not_a_result", day_id=d1
    ).status_code == 422


async def test_day_result_without_a_day_id_422s_unless_lost_rest(client):
    """Every result but "lost the rest" is ABOUT one leg. Defaulting a missing
    day_id to anything would resolve a leg nobody named."""
    rid, _d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    assert post_day_result(client, rid, result="won").status_code == 422
    assert post_day_result(client, rid, result="skip").status_code == 422
    assert await day_results_for(client.db, USER_A, rid) == {}
    assert await opted_out_days(client.db, USER_A) == set()


async def test_day_result_forged_day_id_is_a_committed_noop_not_500(client):
    """Ids arrive from a form post, so a forged or stale one is re-validated
    server side: the service writes nothing and the route must not invent a
    4xx the DM buttons do not produce."""
    rid, _d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    assert post_day_result(client, rid, result="won", day_id=987654).status_code == 200
    assert await day_results_for(client.db, USER_A, rid) == {}
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.APPLIED


async def test_day_result_forged_day_id_on_a_skip_is_a_noop_too(client):
    """The same forged id down the OTHER branch. `set_leg_opt_out` writes an
    FK-backed row and validates nothing, so an unchecked id here would be an
    IntegrityError at commit -- a 500 where the won/lost branch quietly does
    nothing. One answer for one class of input."""
    rid, _d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    assert post_day_result(client, rid, result="skip", day_id=987654).status_code == 200
    assert await opted_out_days(client.db, USER_A) == set()


async def test_day_result_rejects_a_leg_of_another_concert(client):
    """A real day id, but not one of this round's concert. The url names the
    round; a leg it does not own is not a leg of it."""
    rid, _d1, _d2 = await seed_multi_leg(client.db)
    async with client.db() as s:
        other = Concert(title="Elsewhere", event_id="elsewhere", created_by=USER_A)
        s.add(other)
        await s.flush()
        stray = ConcertDay(
            concert_id=other.id, label="Day 1",
            starts_at_utc=datetime.now(UTC) + timedelta(days=30),
        )
        s.add(stray)
        await s.commit()
        stray_id = stray.id
    login_as(client, USER_A, "reiji")
    assert post_day_result(client, rid, result="skip", day_id=stray_id).status_code == 200
    assert post_day_result(client, rid, result="won", day_id=stray_id).status_code == 200
    assert await opted_out_days(client.db, USER_A) == set()
    assert await day_results_for(client.db, USER_A, rid) == {}


async def test_day_result_skip_of_every_leg_clears_the_queued_reminders(client):
    """The route half of invariant 8. Recording "not going" on the last
    unopted leg leaves a round nobody is going to -- and its reminders must go
    with it, not wait for some unrelated edit to re-plan them. The resync lives
    in `set_leg_opt_out`, so the route adds no call of its own."""
    now = datetime.now(UTC)
    async with client.db() as s:
        await ensure_user(s, USER_A, "reiji")
        concert = Concert(title="Two nights", event_id="two-nights", created_by=USER_A)
        s.add(concert)
        await s.flush()
        d1 = ConcertDay(
            concert_id=concert.id, label="Day 1", starts_at_utc=now + timedelta(days=60)
        )
        d2 = ConcertDay(
            concert_id=concert.id, label="Day 2", starts_at_utc=now + timedelta(days=61)
        )
        s.add_all([d1, d2])
        await s.flush()
        round_ = Round(
            concert_id=concert.id, label="Fan club lottery", kind=RoundKind.LOTTERY_ROUND,
            applies_to=[d1.id, d2.id],
            opens_at_utc=now - timedelta(days=1), closes_at_utc=now + timedelta(days=7),
        )
        s.add(round_)
        await s.flush()
        rule = ReminderRule(
            user_id=USER_A, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=1
        )
        s.add(rule)
        await s.flush()
        await sync_rule(s, rule)
        await s.commit()
        rid, d1_id, d2_id, rule_id = round_.id, d1.id, d2.id, rule.id

    async def queued() -> int:
        async with client.db() as s:
            return len((await s.execute(
                select(ReminderQueue).where(ReminderQueue.rule_id == rule_id)
            )).scalars().all())

    assert await queued() > 0
    login_as(client, USER_A, "reiji")

    post_day_result(client, rid, result="skip", day_id=d1_id)
    assert await queued() > 0  # one leg still on

    post_day_result(client, rid, result="skip", day_id=d2_id)
    assert await queued() == 0


async def test_day_result_unknown_round_404s(client):
    """Same reason the outcome route checks: the service returns silently for
    a missing round, so without this the press reports an honest-looking
    success that wrote nothing."""
    await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    assert post_day_result(client, 987654, result="lost_rest").status_code == 404


async def test_day_result_requires_login(client):
    rid, d1, _d2 = await seed_multi_leg(client.db)
    r = post_day_result(client, rid, result="won", day_id=d1)
    assert r.status_code == 204
    assert r.headers["hx-redirect"] == "/"
    assert await day_results_for(client.db, USER_A, rid) == {}


async def test_day_result_is_scoped_to_the_calling_user(client):
    """The user comes from the SESSION, never the body -- two readers on the
    same round keep entirely separate day rows."""
    rid, d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    post_day_result(client, rid, result="won", day_id=d1)
    login_as(client, USER_B, "someone-else")
    post_day_result(client, rid, result="lost", day_id=d1)

    assert await day_results_for(client.db, USER_A, rid) == {d1: LegResult.WON}
    assert await day_results_for(client.db, USER_B, rid) == {d1: LegResult.LOST}


async def test_day_result_answers_home_with_homes_own_fragments(client):
    """No HX-Current-URL means the press came from Home, so the answer is
    Home's three fragments -- the same surface split the outcome route makes,
    through the same shared helper."""
    rid, d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    r = post_day_result(client, rid, result="won", day_id=d1)
    assert r.status_code == 200
    assert 'id="deadline-rows"' in r.text
    assert 'id="board"' in r.text
    assert 'id="board-summary"' in r.text


async def test_outcome_swap_returns_the_same_block_structure(client):
    """htmx parity. GET / and this swap render the SAME partial, so the
    fragment must come back block-structured too: the outer #deadline-rows
    target intact (an outerHTML swap replaces that very element), a block
    header inside it, and the two out-of-band fragments still riding along.

    The concert is force-subscribed rather than tag-matched because
    `tracked_concert_ids` is what decides whether Coming up has anything in
    it at all, and this file's seeds carry no tags -- an untracked concert
    would render an empty (but structurally valid) fragment and the block
    assertion below would pass for the wrong reason."""
    rid = await seed_round(client.db)
    async with client.db() as s:
        concert_id = (await s.execute(select(Concert.id))).scalars().one()
        s.add(ConcertSubscription(
            user_id=USER_A, concert_id=concert_id, state=SubscriptionState.SUBSCRIBED,
        ))
        await s.commit()
    login_as(client, USER_A, "reiji")

    r = post_outcome(client, rid, "applied")
    assert r.status_code == 200
    # The hx-target element itself, and the block shape inside it.
    assert r.text.lstrip().startswith('<div class="deadline-rows" id="deadline-rows">')
    rows = r.text.split('id="deadline-rows"', 1)[1]
    assert '<div class="cblock">' in rows
    assert 'class="blockhead"' in rows
    assert ">Hasunosora 6th</a>" in rows
    # ...and the two out-of-band fragments the swap has always carried.
    assert 'id="board"' in r.text
    assert 'id="board-summary"' in r.text
    assert 'hx-swap-oob="true"' in r.text


async def test_day_result_without_htmx_returns_where_it_came_from(client):
    """The forms carry a real method/action, so a JS-less browser navigates
    here and would render a bare fragment as the whole document."""
    rid, d1, _d2 = await seed_multi_leg(client.db)
    login_as(client, USER_A, "reiji")
    r = client.post(
        f"/rounds/{rid}/day-result", data={"result": "won", "day_id": d1},
        headers={"Referer": "http://testserver/concerts/two-nights"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/two-nights"
