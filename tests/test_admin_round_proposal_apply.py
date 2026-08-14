"""Applying and dismissing a round proposal -- the round poll's ONE write path.

  POST /admin/quiet-ladders/proposals/{event_id}/{proposal_id}/apply
  POST /admin/quiet-ladders/proposals/{event_id}/{proposal_id}/dismiss

Task 5 of the round-poll phase-2 plan, and the riskiest thing in it: every
task before this one writes rows nobody sees, while this one writes a `Round`
onto a concert real people already hold reminders for.

Its worst failure is silent and looks exactly like success. `reminder_queue`
is a MATERIALIZED outbox (invariant 2), so a `Round` created without
`sync_concert` leaves it untouched: the row exists, the page says applied, the
concert stops being quiet and leaves the worklist -- and nobody is ever
reminded of that deadline, which is the precise failure this whole feature was
built to prevent. That is why the queue assertion below, not the row
assertion, is the load-bearing test in this file: a test asserting only that a
`Round` exists passes straight through the bug.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert, ConcertDay, ReminderQueue, ReminderRule, Round, RoundProposal
from app.db.service import ensure_user, upsert_proposal
from app.db.session import get_session
from app.domain.timezones import jst_to_utc
from app.domain.types import Anchor, RoundKind
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


NOW = datetime(2026, 8, 13, tzinfo=UTC)
# JST 12:00 on 2026-09-01 -- the proposal's opening minute, and therefore half
# of its dedupe key, so a held round sharing it is the "changed" shape.
OPENS = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
# Deliberately FAR in the future. A reminder planned for a moment already past
# is skipped by `plan_for_rule`, so a nearer deadline would leave the queue
# empty for a reason that has nothing to do with `sync_concert` -- and the one
# assertion this file exists for would pass with the feature deleted.
CLOSES_JST = "2099-06-25T23:59"
CLOSES_UTC = jst_to_utc(datetime(2099, 6, 25, 23, 59))


async def _concert(session, event_id="live-a", title="Live A"):
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


async def _propose(session, concert, *, label="1次先行", **kw):
    return await upsert_proposal(
        session,
        concert.id,
        label=label,
        kind=kw.pop("kind", RoundKind.LOTTERY_ROUND),
        opens_at_utc=kw.pop("opens_at_utc", OPENS),
        closes_at_utc=kw.pop("closes_at_utc", CLOSES_UTC),
        results_at_utc=kw.pop("results_at_utc", None),
        payment_deadline_at_utc=kw.pop("payment_deadline_at_utc", None),
        applies_to_labels=kw.pop("applies_to_labels", []),
        evidence_yaml=kw.pop("evidence_yaml", ""),
        source_url=kw.pop("source_url", "https://example.jp/live/tickets"),
        now=kw.pop("now", NOW),
    )


def _apply_url(event_id, proposal_id):
    return f"/admin/quiet-ladders/proposals/{event_id}/{proposal_id}/apply"


def _dismiss_url(event_id, proposal_id):
    return f"/admin/quiet-ladders/proposals/{event_id}/{proposal_id}/dismiss"


def _form(**overrides):
    """What the draft page's own form submits, with the model's values
    pre-filled -- the four `round_*` names `_TIME_FIELDS` renders."""
    data = {
        "round_opens_at": "2026-09-01T12:00",
        "round_closes_at": CLOSES_JST,
        "round_results_at": "",
        "round_payment_at": "",
    }
    data.update(overrides)
    return data


async def _rounds(session, concert_id):
    return list((await session.execute(
        select(Round).where(Round.concert_id == concert_id)
    )).scalars().all())


# ── The happy path ───────────────────────────────────────────────────────


async def test_applying_a_proposal_creates_the_round(client):
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post(_apply_url("live-a", proposal_id), data=_form())
    assert r.status_code == 303

    async with client.db() as s:
        rounds = await _rounds(s, concert_id)
        assert len(rounds) == 1, "applying must create exactly one Round"
        assert rounds[0].label == "1次先行"
        assert rounds[0].kind is RoundKind.LOTTERY_ROUND
        assert rounds[0].closes_at_utc == CLOSES_UTC


async def test_applying_a_proposal_populates_the_reminder_queue(client):
    """THE most important check in this plan.

    Invariant 2: `reminder_queue` is a materialized outbox, so a `Round`
    written without `sync_concert` schedules NOTHING while every surface says
    the deadline is tracked.

    Mutation: delete the `await sync_concert(session, concert.id)` line in the
    apply route. Every other test in this file stays green -- the Round row is
    still created, still carries the form's closing time, the proposal is
    still stamped -- and only the `no reminder was scheduled` assertion below
    fails. That asymmetry IS the point of this test.

    The rule is seeded directly rather than through `POST /concerts/.../rules`
    because it must EXIST BEFORE the round does: this is the real-world shape
    (a user already following a concert whose ladder then grows), and it is
    the only shape in which the sync is what schedules the reminder.
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        s.add(ReminderRule(
            user_id=ADMIN_ID, concert_id=concert.id, anchor=Anchor.CLOSES, offset_days=-3,
        ))
        await s.flush()
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    assert client.post(_apply_url("live-a", proposal_id), data=_form()).status_code == 303

    async with client.db() as s:
        rounds = await _rounds(s, concert_id)
        assert len(rounds) == 1
        queued = list((await s.execute(select(ReminderQueue))).scalars().all())
        assert queued, "no reminder was scheduled for the round that was just applied"
        assert len(queued) == 1
        assert queued[0].round_id == rounds[0].id
        assert queued[0].anchor is Anchor.CLOSES
        # Three days before the closing time the FORM submitted.
        assert queued[0].fire_at_utc == jst_to_utc(datetime(2099, 6, 22, 23, 59))


async def test_applying_stamps_applied_at_and_the_proposal_leaves_pending(client):
    """Mutation: creating the round without stamping `applied_at`. The
    proposal then reappears on the draft page forever, and a second press
    creates a duplicate round on a concert people hold reminders for."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    assert client.post(_apply_url("live-a", proposal_id), data=_form()).status_code == 303

    async with client.db() as s:
        from app.db.service import pending_proposals_for

        stored = await s.get(RoundProposal, proposal_id)
        assert stored.applied_at is not None, "an applied proposal must be stamped"
        assert stored.dismissed_at is None, "applying is not dismissing"
        assert await pending_proposals_for(s, concert_id) == [], (
            "an applied proposal must leave the pending queue"
        )


async def test_a_second_apply_is_refused_and_creates_no_duplicate_round(client):
    """The other half of the stamp: a stale page, a double-click or a back
    button must not put the same round on the concert twice. Mutation:
    dropping the already-handled guard in the route."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    assert client.post(_apply_url("live-a", proposal_id), data=_form()).status_code == 303
    second = client.post(_apply_url("live-a", proposal_id), data=_form())
    assert second.status_code == 409

    async with client.db() as s:
        assert len(await _rounds(s, concert_id)) == 1, "the second press duplicated the round"


# ── Legs: every box ticked means ALL, a subset means exactly that ────────


async def test_every_leg_ticked_is_stored_as_EMPTY_applies_to(client):
    """`Round.applies_to`'s empty-means-ALL convention.

    Mutation: storing the explicit id list the form posted. Both readings
    agree TODAY and disagree the moment a leg is added -- a third leg falls
    outside a frozen `[day1, day2]` array, so the round silently stops
    applying to it and nobody is reminded for that leg. Asserting the value
    is FALSY, not that it equals both ids, is what makes this test able to
    fail at all."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        day1 = await _leg(s, concert, "Day 1", datetime(2099, 9, 1, 10, 0, tzinfo=UTC))
        day2 = await _leg(s, concert, "Day 2", datetime(2099, 9, 2, 10, 0, tzinfo=UTC))
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        day_ids = [str(day1.id), str(day2.id)]
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        _apply_url("live-a", proposal_id), data=_form(applies_to_days=day_ids),
    )
    assert r.status_code == 303

    async with client.db() as s:
        rounds = await _rounds(s, concert_id)
        assert len(rounds) == 1
        assert not rounds[0].applies_to, (
            "every leg ticked must normalise back to empty (all), not freeze today's ids"
        )


async def test_a_subset_of_legs_is_stored_verbatim(client):
    """The other half of the rule above -- without it, an implementation that
    always stored empty would pass `test_every_leg_ticked...` outright, and a
    round meant for Saturday only would remind Sunday's holders too."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        day1 = await _leg(s, concert, "Day 1", datetime(2099, 9, 1, 10, 0, tzinfo=UTC))
        await _leg(s, concert, "Day 2", datetime(2099, 9, 2, 10, 0, tzinfo=UTC))
        proposal = await _propose(s, concert)
        concert_id, proposal_id, day1_id = concert.id, proposal.id, day1.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        _apply_url("live-a", proposal_id), data=_form(applies_to_days=[str(day1_id)]),
    )
    assert r.status_code == 303

    async with client.db() as s:
        rounds = await _rounds(s, concert_id)
        assert rounds[0].applies_to == [day1_id], (
            "a subset of legs must be stored exactly as ticked"
        )


# ── CHANGED is refused in the ROUTE, not merely hidden ──────────────────


async def test_applying_a_CHANGED_proposal_is_REFUSED_by_the_route(client):
    """Phase 2's write path is creates-only (owner ruling, 2026-08-14), and a
    hidden button is not an authorisation check.

    Mutation: relying on the template's `{% if row.status == "new" %}` alone
    and letting the route apply whatever it is POSTed. This test never renders
    the page -- it POSTs the URL directly, exactly as a stale tab or a curl
    would -- and asserts BOTH halves: the refusal, and that no second `Round`
    appeared beside the one the concert already holds.
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        # Same label and same opening minute -> the same dedupe key; a
        # different closing time -> `classify_stored_proposal` says "changed".
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="1次先行",
            opens_at_utc=OPENS, closes_at_utc=datetime(2099, 6, 20, 14, 59, tzinfo=UTC),
        ))
        await s.flush()
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post(_apply_url("live-a", proposal_id), data=_form())
    assert r.status_code == 409, "a CHANGED proposal must be refused by the route itself"

    async with client.db() as s:
        assert len(await _rounds(s, concert_id)) == 1, (
            "the refusal must leave the concert's rounds untouched"
        )
        assert (await s.get(RoundProposal, proposal_id)).applied_at is None


# ── Invariant 5 ──────────────────────────────────────────────────────────


async def test_a_non_admin_cannot_apply(client):
    """Invariant 5, on the one route in this feature that writes. Signed in as
    a real non-admin (the whitelisted EDITOR), never merely signed out -- a
    signed-out request redirects, so it would pass with the admin check gone.
    The proposal is fully seeded, so a missing guard really would write."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, EDITOR_ID, "editor")
    r = client.post(_apply_url("live-a", proposal_id), data=_form())
    assert r.status_code == 403

    async with client.db() as s:
        assert await _rounds(s, concert_id) == [], "a non-admin must write no Round"


async def test_a_non_admin_cannot_dismiss(client):
    """The same guard on the other write route -- `require_admin` on one of
    two POSTs is the shape this test exists to catch."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        proposal_id = proposal.id
        await s.commit()

    login_as(client, EDITOR_ID, "editor")
    assert client.post(_dismiss_url("live-a", proposal_id)).status_code == 403

    async with client.db() as s:
        assert (await s.get(RoundProposal, proposal_id)).dismissed_at is None


# ── Dismiss ──────────────────────────────────────────────────────────────


async def test_dismissing_sets_dismissed_at_and_writes_no_round(client):
    """Mutation: dismiss falling through to the apply path. Assert BOTH
    halves -- the stamp alone would stay green while a round was quietly
    written, and "no round" alone would stay green while nothing was
    recorded and the poll re-proposed it tomorrow."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post(_dismiss_url("live-a", proposal_id))
    assert r.status_code == 303

    async with client.db() as s:
        stored = await s.get(RoundProposal, proposal_id)
        assert stored.dismissed_at is not None, "a dismissal must stick, or the poll re-proposes"
        assert stored.applied_at is None
        assert await _rounds(s, concert_id) == [], "dismissing must write no Round"


async def test_a_dismissed_proposal_cannot_then_be_applied(client):
    """The one thing `classify_stored_proposal` can NEVER catch: a dismissed
    proposal is still "new" against the concert's rounds, because dismissing
    wrote none. So without `_pending_proposal`'s already-handled guard, a
    stale tab (or a back button) applies a round the operator explicitly
    refused -- and the refusal was the whole point of pressing Dismiss.

    Mutation: dropping that clause. The double-APPLY test above survives it,
    since the round the first press created makes the second read "resolved";
    only this ordering is left uncovered."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    assert client.post(_dismiss_url("live-a", proposal_id)).status_code == 303
    assert client.post(_apply_url("live-a", proposal_id), data=_form()).status_code == 409

    async with client.db() as s:
        assert await _rounds(s, concert_id) == [], (
            "a refused proposal must not be applicable afterwards"
        )
        assert (await s.get(RoundProposal, proposal_id)).applied_at is None


async def test_a_CHANGED_proposal_can_still_be_dismissed(client):
    """The creates-only refusal belongs to APPLY alone. Dismiss is the only
    action a CHANGED row has -- refusing it too would leave those proposals
    pending forever, re-proposed by the poll every single day."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="1次先行",
            opens_at_utc=OPENS, closes_at_utc=datetime(2099, 6, 20, 14, 59, tzinfo=UTC),
        ))
        await s.flush()
        proposal = await _propose(s, concert)
        proposal_id = proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    assert client.post(_dismiss_url("live-a", proposal_id)).status_code == 303

    async with client.db() as s:
        assert (await s.get(RoundProposal, proposal_id)).dismissed_at is not None


# ── The form, not the stored proposal, is what gets written ─────────────


async def test_an_edited_value_is_what_gets_written(client):
    """The whole reason every field on the draft page is an INPUT rather than
    text: the model misreads a date, and an admin corrects it before applying.

    Mutation: reading `proposal.closes_at_utc` off the row instead of the
    submitted `round_closes_at`. The two are deliberately DIFFERENT here, and
    the assertion names both -- so a route that silently ignored the operator's
    correction and wrote the model's original cannot pass.
    """
    corrected_jst = "2099-06-30T21:00"
    corrected_utc = jst_to_utc(datetime(2099, 6, 30, 21, 0))
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        _apply_url("live-a", proposal_id),
        data=_form(round_closes_at=corrected_jst, round_results_at="2099-07-05T18:00"),
    )
    assert r.status_code == 303

    async with client.db() as s:
        rounds = await _rounds(s, concert_id)
        assert rounds[0].closes_at_utc == corrected_utc, (
            "the FORM's corrected closing time must win, not the proposal's stored one"
        )
        assert rounds[0].closes_at_utc != CLOSES_UTC
        # A field the model never filled at all, typed in by the operator.
        assert rounds[0].results_at_utc == jst_to_utc(datetime(2099, 7, 5, 18, 0))


# ── The page is actually WIRED to the two routes ────────────────────────


async def test_the_draft_pages_controls_submit_to_these_routes(client):
    """Both routes could be perfect and the feature still shipped dead: Task 4
    rendered every control `disabled` with no `action`/`method` anywhere, and
    every test above POSTs its URL directly, so none of them would notice.

    Mutation: leaving the buttons `disabled` (or the form without its
    `action`). Asserts the form points at THIS proposal's apply URL, that
    Approve reaches that form by `form=` (it renders outside it), and that
    neither control is disabled.
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        proposal = await _propose(s, concert)
        proposal_id = proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/live-a")
    assert r.status_code == 200
    block = r.text[r.text.index(f'id="proposal-{proposal_id}"'):]

    assert f'action="{_apply_url("live-a", proposal_id)}"' in block
    assert f'action="{_dismiss_url("live-a", proposal_id)}"' in block
    assert f'<button type="submit" class="act" form="proposal-form-{proposal_id}">Approve' in block
    assert "disabled" not in block, "the controls must be live, not Task 4's inert placeholders"


async def test_a_CHANGED_rows_one_control_is_a_live_dismiss(client):
    """The complement of Task 4's "no Approve on a CHANGED row": that
    assertion stays green if the row has no working control AT ALL, which
    would strand those proposals pending and re-proposed daily forever."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="1次先行",
            opens_at_utc=OPENS, closes_at_utc=datetime(2099, 6, 20, 14, 59, tzinfo=UTC),
        ))
        await s.flush()
        proposal = await _propose(s, concert)
        proposal_id = proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders/proposals/live-a")
    assert r.status_code == 200
    block = r.text[r.text.index(f'id="proposal-{proposal_id}"'):]

    assert f'action="{_dismiss_url("live-a", proposal_id)}"' in block
    assert _apply_url("live-a", proposal_id) not in block
    assert "disabled" not in block


async def test_the_unknown_concert_and_proposal_are_404(client):
    """Invariant 6: keyed by `event_id`. A proposal id belonging to ANOTHER
    concert must not be applicable through this concert's URL -- without the
    ownership check the route would happily write the wrong concert's round."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        concert = await _concert(s)
        other = await _concert(s, event_id="live-b", title="Live B")
        proposal = await _propose(s, other)
        concert_id, proposal_id = concert.id, proposal.id
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    assert client.post(_apply_url("nope", proposal_id), data=_form()).status_code == 404
    assert client.post(_apply_url("live-a", proposal_id), data=_form()).status_code == 404

    async with client.db() as s:
        assert await _rounds(s, concert_id) == []
