"""Phase 10: presets, one-click apply, and the notify-and-apply pipeline.

The scenario tests mirror the intended real-world flow: a subscriber sets up
a preset once, an editor tags a new event, and everything else is automatic.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import (
    Notification,
    ReminderQueue,
    ReminderRule,
    TagSubscription,
)
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID, FAN_ID = 42, 777


@pytest.fixture()
def client(db, monkeypatch):
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


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def build_standard_preset(client) -> None:
    """Preset #1: 3d before closes + 7d before event day."""
    client.post("/presets", data={"name": "standard"})
    client.post("/presets/1/items", data={"anchor": "closes", "days": 3, "direction": "before"})
    client.post(
        "/presets/1/items", data={"anchor": "event_start", "days": 7, "direction": "before"}
    )


def build_concert_with_deadlines(client, **extra_tags) -> None:
    """Concert #1 with one lottery round and one day (as the editor). The
    rich creation form takes days/rounds/tags all in one atomic POST now --
    no more separate add_round/add_day calls."""
    client.post(
        "/concerts",
        data={"title_en": "Hasunosora 6th", "title_zh": "Hasunosora 6th",
            "title": "Hasunosora 6th", "event_id": "hasunosora-6th",
            "round_label": ["最速先行"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["最速先行"],
            "round_label_zh": ["最速先行"], "round_url": [""], "round_notes": [""],
            "round_leg": [""],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            **extra_tags,
        },
    )


async def attach_tag_to_concert(client, concert_id: int, tag_id: int) -> None:
    """Attach an existing tag to an already-created concert -- the rich
    edit page's tag picker is what does this in the real UI now; tests that
    only care about the notify-and-apply pipeline reacting to a newly
    attached tag go straight through the same service functions the edit
    route itself calls (attach_tag + handle_newly_tagged)."""
    from app.db.models import Concert, Tag
    from app.db.service import attach_tag, handle_newly_tagged

    async with client.db() as s:
        concert = await s.get(Concert, concert_id)
        tag = await s.get(Tag, tag_id)
        added = await attach_tag(s, concert_id, tag)
        await handle_newly_tagged(s, concert, added)
        await s.commit()


async def _all(db, model):
    async with db() as s:
        return list((await s.execute(select(model))).scalars())


# ── Presets & one-click apply ────────────────────────────────────────────


async def test_apply_preset_creates_rules_and_queues(client):
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    build_standard_preset(client)

    r = client.post("/concerts/hasunosora-6th/presets/1/apply")
    assert r.status_code == 200

    rules = await _all(client.db, ReminderRule)
    assert len(rules) == 2  # one per preset item
    queue = await _all(client.db, ReminderQueue)
    assert len(queue) == 2  # round-close reminder + day reminder


async def test_apply_is_idempotent(client):
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    build_standard_preset(client)
    client.post("/concerts/hasunosora-6th/presets/1/apply")
    client.post("/concerts/hasunosora-6th/presets/1/apply")
    client.post("/concerts/hasunosora-6th/presets/1/apply")
    assert len(await _all(client.db, ReminderRule)) == 2  # clicks are harmless


def test_cannot_apply_someone_elses_preset(client):
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    build_standard_preset(client)
    login_as(client, FAN_ID, "fan")
    assert client.post("/concerts/hasunosora-6th/presets/1/apply").status_code == 404


# ── The notify-and-apply pipeline ────────────────────────────────────────


async def test_new_tagged_event_auto_applies_and_notifies(client):
    """The end-state flow: fan subscribes once; editor tags an event;
    fan's reminders exist and a DM notice is queued — untouched by the fan."""
    # editor creates the tag universe first
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Hasunosora", "name_zh": "Hasunosora", "name": "Hasunosora", "kind": "group",
    })
    client.post("/tags", data={
        "name_en": "Kozue", "name_zh": "Kozue", "name": "Kozue", "kind": "artist",
    })
    client.post("/tags/1/members", data={"member_tag_id": 2})

    # fan sets up: preset + subscription to the ARTIST with notify+preset
    login_as(client, FAN_ID, "fan")
    build_standard_preset(client)
    client.post("/subscriptions", data={"tag_id": 2, "preset_id": 1, "notify": "true"})

    # editor creates the event and tags the GROUP (expansion adds the artist)
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    await attach_tag_to_concert(client, 1, 1)

    rules = await _all(client.db, ReminderRule)
    fan_rules = [r for r in rules if r.user_id == FAN_ID]
    assert len(fan_rules) == 2  # preset auto-applied via the materialized artist tag

    notes = await _all(client.db, Notification)
    assert len(notes) == 1
    assert notes[0].user_id == FAN_ID
    assert "Hasunosora 6th" in notes[0].body
    assert "standard" in notes[0].body  # names the applied preset


async def test_subscriber_without_preset_gets_notification_only(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Gakumas", "name_zh": "Gakumas", "name": "Gakumas", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    client.post("/subscriptions", data={"tag_id": 1, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "Gakumas 3rd", "title_zh": "Gakumas 3rd", "title": "Gakumas 3rd",
        "event_id": "gakumas-3rd",
    })
    await attach_tag_to_concert(client, 1, 1)

    assert [r for r in await _all(client.db, ReminderRule) if r.user_id == FAN_ID] == []
    notes = await _all(client.db, Notification)
    assert len(notes) == 1 and "no preset linked" in notes[0].body


async def test_subscribe_default_redirect_is_unchanged(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Gakumas", "name_zh": "Gakumas", "name": "Gakumas", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1})
    assert r.headers["location"] == "/preferences"


async def test_subscribe_honors_next_param(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Gakumas", "name_zh": "Gakumas", "name": "Gakumas", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1, "next": "/welcome"})
    assert r.headers["location"] == "/welcome"


async def test_subscribe_rejects_an_unrecognized_next_value(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Gakumas", "name_zh": "Gakumas", "name": "Gakumas", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1, "next": "https://evil.example"})
    assert r.headers["location"] == "/preferences"


async def test_unsubscribe_by_subscription_id_deletes_the_row(client):
    """POST /subscriptions/{sub_id}/delete -- the ID-KEYED unfollow, which is
    what Preferences' Following rows, Preferences' tag picker and the welcome
    wizard post to. (The /tags chips use the tag-keyed /subscriptions/unfollow
    instead, because that page renders one tag more than once and a subscription
    id in a chip goes stale -- see tests/test_tags_follow_htmx.py.)

    Mutation this must fail against: DELETING THE ROUTE. Until this test
    existed, the only test that ever posted this URL was one that got
    repointed at the tag-keyed route, so removing `unsubscribe` outright left
    the whole suite green while 405-ing three live surfaces.
    """
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Gakumas", "name_zh": "Gakumas", "name": "Gakumas", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    client.post("/subscriptions", data={"tag_id": 1, "notify": "true"})
    subs = await _all(client.db, TagSubscription)
    assert len(subs) == 1

    r = client.post(f"/subscriptions/{subs[0].id}/delete", data={"next": "/welcome"})
    assert r.status_code == 303
    assert r.headers["location"] == "/welcome", "and it still honours `next`"
    assert await _all(client.db, TagSubscription) == []


async def test_unsubscribe_by_id_404s_on_someone_elses_subscription(client):
    """Ownership, checked as a 404 rather than a 403 -- the house rule for
    another user's row (invariant 5). Mutation: dropping the `user_id` check,
    which lets any signed-in user unfollow anyone else's tag by guessing a
    small integer."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Gakumas", "name_zh": "Gakumas", "name": "Gakumas", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    client.post("/subscriptions", data={"tag_id": 1, "notify": "true"})
    subs = await _all(client.db, TagSubscription)
    assert len(subs) == 1 and subs[0].user_id == FAN_ID

    login_as(client, EDITOR_ID, "reiji")
    r = client.post(f"/subscriptions/{subs[0].id}/delete")
    assert r.status_code == 404
    assert len(await _all(client.db, TagSubscription)) == 1, "and the row survives"

    # The same request from the OWNER must succeed. Without this half, a 404
    # from a route that does not exist at all reads exactly like a 404 from the
    # ownership check -- so deleting the route would leave this test green.
    login_as(client, FAN_ID, "fan")
    r = client.post(f"/subscriptions/{subs[0].id}/delete")
    assert r.status_code == 303
    assert await _all(client.db, TagSubscription) == []


async def test_create_preset_honors_next_param(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/presets", data={"name": "standard", "next": "/welcome"})
    assert r.headers["location"] == "/welcome"


async def test_set_timezone_honors_next_param(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/me/timezone", data={"timezone": "Asia/Tokyo", "next": "/welcome"})
    assert r.headers["location"] == "/welcome"


async def test_user_with_existing_rules_is_skipped(client):
    """Second matching tag on the same concert must not double-apply or re-notify."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Hasunosora", "name_zh": "Hasunosora", "name": "Hasunosora", "kind": "franchise",
    })
    client.post("/tags", data={
        "name_en": "Kozue", "name_zh": "Kozue", "name": "Kozue", "kind": "artist",
    })
    login_as(client, FAN_ID, "fan")
    build_standard_preset(client)
    client.post("/subscriptions", data={"tag_id": 1, "preset_id": 1, "notify": "true"})
    client.post("/subscriptions", data={"tag_id": 2, "preset_id": 1, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    await attach_tag_to_concert(client, 1, 1)
    await attach_tag_to_concert(client, 1, 2)  # second tag later

    fan_rules = [r for r in await _all(client.db, ReminderRule) if r.user_id == FAN_ID]
    assert len(fan_rules) == 2  # still just the one application
    assert len(await _all(client.db, Notification)) == 1  # and the one notice


async def test_scheduler_delivers_notifications(client):
    """The outbox drains through the same tick as reminders."""
    from app.scheduler.loop import tick

    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Hasunosora", "name_zh": "Hasunosora", "name": "Hasunosora", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    client.post("/subscriptions", data={"tag_id": 1, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "6th", "title_zh": "6th", "title": "6th", "event_id": "6th",
    })
    await attach_tag_to_concert(client, 1, 1)

    sent = []

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            sent.append(embed.title if embed is not None else body)

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    # point the scheduler's session factory at the test DB
    import app.scheduler.loop as loop_mod

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)
    delivered = await tick(FakeBot())

    assert delivered == 1
    assert len(sent) == 1 and "6th" in sent[0]  # embed title carries the concert
    notes = await _all(client.db, Notification)
    assert notes[0].sent_at_utc is not None


async def test_scheduler_delivers_leg_cancelled_notice_with_reinstate_button(client):
    """A leg_cancelled notification drains through the same tick as a
    new_event one, but renders the cancellation embed + reinstate button."""
    from app.db.models import Concert, Notification
    from app.scheduler.loop import tick

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "Cancelled Tour", "title_zh": "Cancelled Tour", "title": "Cancelled Tour",
        "event_id": "cancelled-tour",
    })
    login_as(client, FAN_ID, "fan")  # creates the User row the Notification FK needs
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
        s.add(Notification(
            user_id=FAN_ID, body="fallback text", concert_id=concert.id, kind="leg_cancelled",
        ))
        await s.commit()

    sent = []

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            sent.append((embed.title if embed is not None else body, view))

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    import app.scheduler.loop as loop_mod

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)
    delivered = await tick(FakeBot())

    assert delivered == 1
    title, view = sent[0]
    assert "Cancelled Tour" in title
    # discord.ui.DynamicItem only proxies custom_id (not .label) -- checking
    # custom_id is also the more precise assertion, since it identifies
    # exactly which button this is, not just its display text.
    custom_ids = [getattr(item, "custom_id", None) for item in view.children]
    assert any(cid and cid.startswith("dk:reinstate:") for cid in custom_ids)
    notes = await _all(client.db, Notification)
    assert notes[0].sent_at_utc is not None


async def _seed_due_reminders(client, n: int, *, past) -> list[int]:
    """N distinct users/concerts/rounds, each with one reminder rule and a
    queue row already due -- built directly at the DB layer (not through
    the web form) since these just need to exist, not be created realistically."""
    from app.db.models import Concert, ReminderQueue, ReminderRule, Round, User
    from app.domain.types import Anchor, RoundKind

    uids = [9000 + i for i in range(n)]
    async with client.db() as s:
        for uid in uids:
            s.add(User(discord_id=uid, username=f"fan{uid}", timezone="UTC"))
            concert = Concert(
                title=f"Concurrent {uid}", event_id=f"concurrent-{uid}", created_by=uid
            )
            s.add(concert)
            await s.flush()
            round_ = Round(
                concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="R1", closes_at_utc=past,
            )
            s.add(round_)
            await s.flush()
            rule = ReminderRule(
                user_id=uid, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=0
            )
            s.add(rule)
            await s.flush()
            s.add(ReminderQueue(
                rule_id=rule.id, round_id=round_.id, anchor=Anchor.CLOSES, fire_at_utc=past
            ))
        await s.commit()
    return uids


async def test_tick_delivers_multiple_due_reminders_concurrently(client):
    """Regression guard for the concurrency fix: N sends that each take a
    fixed amount of (simulated) network time must finish in roughly one
    slice, not N serialized slices -- proves real concurrency is
    happening, not just a relabeled sequential loop."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    import app.scheduler.loop as loop_mod
    from app.scheduler.loop import tick

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)

    n = 5
    past = datetime.now(UTC) - timedelta(seconds=5)
    uids = await _seed_due_reminders(client, n, past=past)

    sent_order = []
    in_flight = 0
    peak_in_flight = 0

    class FakeUser:
        def __init__(self, uid):
            self.uid = uid

        async def send(self, body=None, *, embed=None, view=None):
            nonlocal in_flight, peak_in_flight
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
            await asyncio.sleep(0.05)  # simulated network latency
            in_flight -= 1
            sent_order.append(self.uid)

    class FakeBot:
        def get_user(self, uid):
            return FakeUser(uid)

    start = asyncio.get_running_loop().time()
    delivered = await tick(FakeBot())
    elapsed = asyncio.get_running_loop().time() - start

    assert delivered == n
    assert set(sent_order) == set(uids)

    # Concurrency is asserted STRUCTURALLY, not by wall clock. The old
    # `elapsed < 0.2` budget was 0.05s of real work against a 0.2s threshold,
    # so a busy CI runner failed it while the code was perfectly correct
    # (0.253s on one such run) -- a flaky test that cries wolf about the thing
    # it is guarding teaches you to ignore it.
    assert peak_in_flight > 1, "sends were serialized, not concurrent"
    assert peak_in_flight == min(n, loop_mod.SEND_CONCURRENCY)

    # Kept only as a coarse guard against reintroducing the old 1s-inter-send
    # gap, which would put this at >= 5s. Deliberately loose: the structural
    # assertion above is what actually proves concurrency.
    assert elapsed < 2.0

    async with client.db() as s:
        rows = (await s.execute(select(ReminderQueue))).scalars().all()
        assert all(r.sent_at_utc is not None for r in rows)


async def test_tick_forbidden_send_does_not_affect_others(client):
    """One recipient with DMs closed must not block or corrupt delivery to
    the others in the same concurrent batch."""
    from datetime import UTC, datetime, timedelta

    import discord

    import app.scheduler.loop as loop_mod
    from app.scheduler.loop import tick

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)

    past = datetime.now(UTC) - timedelta(seconds=5)
    uids = await _seed_due_reminders(client, 3, past=past)
    forbidden_uid = uids[1]

    sent = []

    class FakeResponse:
        status = 403
        reason = "Forbidden"

    class FakeUser:
        def __init__(self, uid):
            self.uid = uid

        async def send(self, body=None, *, embed=None, view=None):
            if self.uid == forbidden_uid:
                raise discord.Forbidden(FakeResponse(), "missing access")
            sent.append(self.uid)

    class FakeBot:
        def get_user(self, uid):
            return FakeUser(uid)

    delivered = await tick(FakeBot())

    assert delivered == 3  # Forbidden counts as "delivered" (permanent drop)
    assert set(sent) == {uids[0], uids[2]}

    async with client.db() as s:
        rows = (await s.execute(select(ReminderQueue))).scalars().all()
        # every row marked sent, including the Forbidden one (dead, not retried)
        assert all(r.sent_at_utc is not None for r in rows)


async def test_tick_forbidden_send_sets_dm_blocked(client):
    from datetime import UTC, datetime, timedelta

    import discord

    import app.scheduler.loop as loop_mod
    from app.db.models import User
    from app.scheduler.loop import tick

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)

    past = datetime.now(UTC) - timedelta(seconds=5)
    uids = await _seed_due_reminders(client, 1, past=past)

    class FakeResponse:
        status = 403
        reason = "Forbidden"

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            raise discord.Forbidden(FakeResponse(), "missing access")

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    await tick(FakeBot())

    async with client.db() as s:
        user = await s.get(User, uids[0])
        assert user.dm_blocked_since is not None


async def test_tick_successful_send_clears_dm_blocked(client):
    from datetime import UTC, datetime, timedelta

    import app.scheduler.loop as loop_mod
    from app.db.models import User
    from app.db.service import record_dm_outcome
    from app.scheduler.loop import tick

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)

    past = datetime.now(UTC) - timedelta(seconds=5)
    uids = await _seed_due_reminders(client, 1, past=past)
    async with client.db() as s:
        await record_dm_outcome(s, uids[0], blocked=True)
        await s.commit()

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            pass

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    await tick(FakeBot())

    async with client.db() as s:
        user = await s.get(User, uids[0])
        assert user.dm_blocked_since is None


async def test_tick_transient_failure_leaves_dm_blocked_unchanged(client):
    from datetime import UTC, datetime, timedelta

    import discord

    import app.scheduler.loop as loop_mod
    from app.db.models import ReminderQueue, User
    from app.db.service import record_dm_outcome
    from app.scheduler.loop import tick

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)

    past = datetime.now(UTC) - timedelta(seconds=5)
    uids = await _seed_due_reminders(client, 1, past=past)
    async with client.db() as s:
        await record_dm_outcome(s, uids[0], blocked=True)
        await s.commit()
        user = await s.get(User, uids[0])
        blocked_since_before = user.dm_blocked_since
    assert blocked_since_before is not None

    class FakeResponse:
        status = 500
        reason = "Internal Server Error"

    class FakeUser:
        async def send(self, body=None, *, embed=None, view=None):
            raise discord.HTTPException(FakeResponse(), "temporary blip")

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    delivered = await tick(FakeBot())

    assert delivered == 0
    async with client.db() as s:
        user = await s.get(User, uids[0])
        # unchanged, not just coincidentally None -- a regression that calls
        # record_dm_outcome(..., blocked=False) on transient failure would
        # wrongly clear this to None and fail this assertion
        assert user.dm_blocked_since == blocked_since_before
        rows = (await s.execute(select(ReminderQueue))).scalars().all()
        assert all(r.sent_at_utc is None for r in rows)  # left unsent, retries next tick


# ── Full preset editability (rename, in-place edit, create-with-item) ────


async def test_new_preset_is_born_with_its_first_item(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/presets", data={
        "name": "standard", "anchor": "closes", "days": 5, "hours": 2, "direction": "before",
    })
    assert r.status_code == 303
    from app.db.models import PresetItem

    (item,) = await _all(client.db, PresetItem)
    assert (item.offset_days, item.offset_hours) == (-5, -2)


async def test_rename_preset(client):
    login_as(client, FAN_ID, "fan")
    client.post("/presets", data={"name": "standrad", "anchor": "closes", "days": 3})
    client.post("/presets/1/rename", data={"name": "standard"})
    from app.db.models import ReminderPreset

    (p,) = await _all(client.db, ReminderPreset)
    assert p.name == "standard"


async def test_edit_item_in_place_every_field(client):
    login_as(client, FAN_ID, "fan")
    client.post("/presets", data={"name": "s", "anchor": "closes", "days": 3})
    client.post("/presets/1/items/1/edit", data={
        "anchor": "event_start", "days": 7, "hours": 12, "direction": "after",
    })
    from app.db.models import PresetItem
    from app.domain.types import Anchor

    (item,) = await _all(client.db, PresetItem)
    assert item.anchor is Anchor.EVENT_START
    assert (item.offset_days, item.offset_hours) == (7, 12)  # after -> positive


def test_cannot_edit_items_of_someone_elses_preset(client):
    login_as(client, FAN_ID, "fan")
    client.post("/presets", data={"name": "s", "anchor": "closes", "days": 3})
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/presets/1/items/1/edit", data={"anchor": "closes", "days": 1})
    assert r.status_code == 404


# ── Phase 12: default presets, button actions, snooze ────────────────────


async def test_default_preset_is_exclusive(client):
    from app.db.models import ReminderPreset

    login_as(client, FAN_ID, "fan")
    client.post("/presets", data={"name": "a", "anchor": "closes", "days": 3})
    client.post("/presets", data={"name": "b", "anchor": "opens", "days": 1})
    client.post("/presets/1/default")
    client.post("/presets/2/default")  # crown moves
    presets = await _all(client.db, ReminderPreset)
    assert [(p.name, p.is_default) for p in presets] == [("a", False), ("b", True)]


async def test_apply_default_button_logic(client):
    from app.db.service import apply_default_preset

    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    login_as(client, FAN_ID, "fan")

    async with client.db() as s:
        assert await apply_default_preset(s, FAN_ID, 1) == ("no_default", 0)

    build_standard_preset(client)
    client.post("/presets/1/default")
    async with client.db() as s:
        status, n = await apply_default_preset(s, FAN_ID, 1)
        await s.commit()
        assert (status, n) == ("applied", 2)
        assert await apply_default_preset(s, FAN_ID, 1) == ("already_covered", 0)


async def test_remove_rules_button_logic(client):
    from app.db.service import remove_user_rules

    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    build_standard_preset(client)
    client.post("/concerts/hasunosora-6th/presets/1/apply")

    async with client.db() as s:
        assert await remove_user_rules(s, EDITOR_ID, 1) == 2
        await s.commit()
    assert await _all(client.db, ReminderRule) == []
    assert await _all(client.db, ReminderQueue) == []  # queue cascaded


async def test_snooze_rearms_with_deadline_cap(client):
    from datetime import UTC, datetime, timedelta

    from app.db.service import snooze_reminder

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["R1"],
            "round_label_zh": ["R1"], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post("/concerts/c/rules", data={"anchor": "closes", "days_before": 3})

    async with client.db() as s:
        (row,) = await _all(client.db, ReminderQueue)
        # deadline far away -> snooze re-arms
        assert await snooze_reminder(s, row.id, EDITOR_ID) == "snoozed"
        await s.commit()
    (row,) = await _all(client.db, ReminderQueue)
    assert row.sent_at_utc is None
    assert row.fire_at_utc > datetime.now(UTC) + timedelta(hours=23)

    async with client.db() as s:
        assert await snooze_reminder(s, row.id, FAN_ID) == "not_yours"
        assert await snooze_reminder(s, 999, EDITOR_ID) == "gone"


async def test_snooze_refuses_within_24h_of_deadline(client):
    from datetime import UTC, datetime, timedelta

    from app.db.models import ReminderRule as RR
    from app.db.models import Round
    from app.db.service import snooze_reminder, sync_rule
    from app.domain.types import Anchor, RoundKind

    async with client.db() as s:
        from app.db.service import ensure_user

        await ensure_user(s, FAN_ID, "fan")
        from app.db.models import Concert

        c = Concert(title="Soon", event_id="soon", created_by=FAN_ID)
        s.add(c)
        await s.flush()
        round_ = Round(concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="R1",
                        closes_at_utc=datetime.now(UTC) + timedelta(hours=10))
        s.add(round_)
        rule = RR(user_id=FAN_ID, concert_id=c.id, anchor=Anchor.CLOSES,
                  offset_days=0, offset_hours=-9)
        s.add(rule)
        await s.flush()
        await sync_rule(s, rule)
        await s.commit()

    async with client.db() as s:
        (row,) = await _all(client.db, ReminderQueue)
        assert await snooze_reminder(s, row.id, FAN_ID) == "too_close"


async def test_snooze_reminder_accepts_custom_day_count(client):
    from datetime import UTC, datetime, timedelta

    from app.db.service import snooze_reminder

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C2", "title_zh": "C2",
            "title": "C2", "event_id": "c2",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["R1"],
            "round_label_zh": ["R1"], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post("/concerts/c2/rules", data={"anchor": "closes", "days_before": 3})

    async with client.db() as s:
        (row,) = await _all(client.db, ReminderQueue)
        assert await snooze_reminder(s, row.id, EDITOR_ID, days=10) == "snoozed"
        await s.commit()
    (row,) = await _all(client.db, ReminderQueue)
    assert row.fire_at_utc > datetime.now(UTC) + timedelta(days=9)


async def test_snooze_reminder_default_days_matches_existing_behavior(client):
    from datetime import UTC, datetime, timedelta

    from app.db.service import snooze_reminder

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C3", "title_zh": "C3",
            "title": "C3", "event_id": "c3",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["R1"],
            "round_label_zh": ["R1"], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post("/concerts/c3/rules", data={"anchor": "closes", "days_before": 3})

    async with client.db() as s:
        (row,) = await _all(client.db, ReminderQueue)
        assert await snooze_reminder(s, row.id, EDITOR_ID) == "snoozed"
        await s.commit()
    (row,) = await _all(client.db, ReminderQueue)
    assert row.fire_at_utc > datetime.now(UTC) + timedelta(hours=23)
    assert row.fire_at_utc < datetime.now(UTC) + timedelta(hours=25)


async def test_snooze_reminder_custom_days_still_capped_at_deadline(client):
    from datetime import UTC, datetime, timedelta

    from app.db.models import Concert, Round
    from app.db.models import ReminderRule as RR
    from app.db.service import ensure_user, snooze_reminder, sync_rule
    from app.domain.types import Anchor, RoundKind

    async with client.db() as s:
        await ensure_user(s, FAN_ID, "fan")
        c = Concert(title="Soon2", event_id="soon2", created_by=FAN_ID)
        s.add(c)
        await s.flush()
        round_ = Round(concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="R1",
                        closes_at_utc=datetime.now(UTC) + timedelta(hours=10))
        s.add(round_)
        rule = RR(user_id=FAN_ID, concert_id=c.id, anchor=Anchor.CLOSES,
                  offset_days=0, offset_hours=-9)
        s.add(rule)
        await s.flush()
        await sync_rule(s, rule)
        await s.commit()

    async with client.db() as s:
        (row,) = await _all(client.db, ReminderQueue)
        # deadline is only 10h away; a 5-day custom snooze would sleep past it
        assert await snooze_reminder(s, row.id, FAN_ID, days=5) == "too_close"


async def test_notifications_carry_structured_payload(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Hasunosora", "name_zh": "Hasunosora", "name": "Hasunosora", "kind": "franchise",
    })
    login_as(client, FAN_ID, "fan")
    client.post("/subscriptions", data={"tag_id": 1, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "6th", "title_zh": "6th", "title": "6th", "event_id": "6th",
    })
    await attach_tag_to_concert(client, 1, 1)

    (note,) = await _all(client.db, Notification)
    assert note.concert_id == 1 and note.kind == "new_event"
    assert note.body  # fallback text still present


# ── A dead concert announces nothing (cleanup batch §C) ──────────────────


async def _run_tag_attach(db, *, cancelled: bool):
    """One follower with a notify+preset subscription, one concert whose only
    leg is (or is not) cancelled, run through the REAL attach + notify-and-
    apply path the edit route uses. The caller then reads the DB."""
    from datetime import UTC, datetime, timedelta

    from app.db.models import (
        Concert,
        ConcertDay,
        PresetItem,
        ReminderPreset,
        Round,
        Tag,
        TagSubscription,
    )
    from app.db.service import attach_tag, ensure_user, handle_newly_tagged
    from app.domain.types import Anchor, RoundKind, TagKind

    async with db() as s:
        await ensure_user(s, FAN_ID, "fan")
        tag = Tag(name="Hasunosora", kind=TagKind.FRANCHISE)
        preset = ReminderPreset(user_id=FAN_ID, name="standard")
        s.add_all([tag, preset])
        await s.flush()
        s.add(PresetItem(preset_id=preset.id, anchor=Anchor.CLOSES, offset_days=-3))
        s.add(TagSubscription(
            user_id=FAN_ID, tag_id=tag.id, preset_id=preset.id, notify=True
        ))
        concert = Concert(title="6th", event_id="6th", created_by=FAN_ID)
        s.add(concert)
        await s.flush()
        s.add(ConcertDay(
            concert_id=concert.id, label="Day 1", cancelled=cancelled,
            starts_at_utc=datetime.now(UTC) + timedelta(days=30),
        ))
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="R1",
            closes_at_utc=datetime.now(UTC) + timedelta(days=10),
        ))
        await s.flush()
        added = await attach_tag(s, concert.id, tag)
        processed = await handle_newly_tagged(s, concert, added)
        await s.commit()
        return processed


async def test_attaching_a_tag_to_a_dead_concert_notifies_nobody(db):
    """handle_newly_tagged never asked whether the show was still happening,
    so tagging a concert whose every leg is cancelled DM'd every follower a
    🆕 notice with an "Apply here" button and quietly applied their preset.
    Owner ruling (2026-07-28): a dead concert queues no notification and
    applies no preset. This only SKIPS -- no send path is added or rerouted
    (invariant 4)."""
    assert await _run_tag_attach(db, cancelled=True) == 0
    assert await _all(db, Notification) == []
    assert await _all(db, ReminderRule) == []


async def test_attaching_a_tag_to_a_live_concert_is_unchanged(db):
    """The regression half, and it matters more than the fix half: the
    ordinary attach still notifies and still auto-applies."""
    assert await _run_tag_attach(db, cancelled=False) == 1
    (note,) = await _all(db, Notification)
    assert note.kind == "new_event"
    assert len(await _all(db, ReminderRule)) == 1


async def test_a_dateless_draft_is_not_dead_when_tagged(db):
    """all_legs_cancelled exempts a concert with no legs at all -- a draft is
    not a cancellation -- and the suppression must inherit that exemption
    rather than restate it. Every create path attaches its tags BEFORE the
    legs exist, so getting this wrong would silence the pipeline outright."""
    from app.db.models import Concert, Tag, TagSubscription
    from app.db.service import attach_tag, ensure_user, handle_newly_tagged
    from app.domain.types import TagKind

    async with db() as s:
        await ensure_user(s, FAN_ID, "fan")
        tag = Tag(name="Hasunosora", kind=TagKind.FRANCHISE)
        s.add(tag)
        await s.flush()
        s.add(TagSubscription(user_id=FAN_ID, tag_id=tag.id, notify=True))
        concert = Concert(title="6th", event_id="6th", created_by=FAN_ID)
        s.add(concert)
        await s.flush()
        added = await attach_tag(s, concert.id, tag)
        assert await handle_newly_tagged(s, concert, added) == 1
        await s.commit()

    assert len(await _all(db, Notification)) == 1


# ── A born-dead concert announces nothing, through create AND import ─────
#
# The block above pins the rule on the EDIT route, where it shipped. It did
# not hold on create or import: create_concert_row called
# handle_newly_tagged straight after flushing the Concert, before any leg
# existed, so all_legs_cancelled read a dateless draft (correctly -- see
# test_a_dateless_draft_is_not_dead_when_tagged) and notified. A concert
# whose only leg was submitted cancelled DM'd every franchise/group/artist
# follower a 🆕 "Apply here" for a show that is off, while every VENUE
# follower on the same request was correctly skipped by the rollup. No
# un-send, and no re-announce if the leg is later un-cancelled.


async def _follower_with_a_notifying_subscription(db) -> int:
    """One FRANCHISE tag, one fan following it with notify on and a preset
    linked -- so a notice AND an auto-apply are both owed if the pipeline
    runs. Returns the tag id, ready to pass as `franchise_tags`."""
    from app.db.models import PresetItem, ReminderPreset, Tag, TagSubscription
    from app.db.service import ensure_user
    from app.domain.types import Anchor, TagKind

    async with db() as s:
        await ensure_user(s, FAN_ID, "fan")
        tag = Tag(name="Hasunosora", kind=TagKind.FRANCHISE)
        preset = ReminderPreset(user_id=FAN_ID, name="standard")
        s.add_all([tag, preset])
        await s.flush()
        s.add(PresetItem(preset_id=preset.id, anchor=Anchor.CLOSES, offset_days=-3))
        s.add(TagSubscription(
            user_id=FAN_ID, tag_id=tag.id, preset_id=preset.id, notify=True
        ))
        await s.commit()
        return tag.id


def _create_payload(tag_id: int, *, cancelled: list[str], labels: list[str]) -> dict:
    """A create/import submit with `labels` legs, each flagged per
    `cancelled`, attached to one followed franchise tag. One round, so there
    is a deadline for a preset to attach a rule to."""
    return {
        "title": "6th", "title_en": "6th", "title_zh": "6th",
        "event_id": "sixth",
        "franchise_tags": [tag_id],
        "day_label": labels,
        "day_label_en": labels, "day_label_zh": labels,
        "day_starts_at": ["2099-08-01T18:00"] * len(labels),
        "day_doors_at": [""] * len(labels),
        "day_cancelled": cancelled,
        "round_label": ["R1"], "round_label_en": ["R1"], "round_label_zh": ["R1"],
        "round_kind": ["lottery_round"],
        "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
        "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""],
    }


async def test_creating_a_concert_with_its_only_leg_cancelled_notifies_nobody(client):
    tag_id = await _follower_with_a_notifying_subscription(client.db)
    login_as(client, EDITOR_ID, "reiji")

    r = client.post("/concerts", data=_create_payload(
        tag_id, cancelled=["true"], labels=["Day 1"]
    ))
    assert r.status_code == 303  # the concert is still created -- only the notice is suppressed

    assert await _all(client.db, Notification) == []
    assert await _all(client.db, ReminderRule) == []


async def test_importing_a_concert_with_its_only_leg_cancelled_notifies_nobody(client):
    """The same suppression on the import commit, which is a create boundary
    too and accepts day_cancelled from its preview form."""
    tag_id = await _follower_with_a_notifying_subscription(client.db)
    login_as(client, EDITOR_ID, "reiji")

    payload = _create_payload(tag_id, cancelled=["true"], labels=["Day 1"])
    del payload["event_id"]  # import has no such field; generate_event_id mints it
    r = client.post("/concerts/import/commit", data=payload)
    assert r.status_code == 303

    assert await _all(client.db, Notification) == []
    assert await _all(client.db, ReminderRule) == []


async def test_creating_a_live_concert_still_notifies_and_applies(client):
    """The regression half, and it matters more than the fix half: moving the
    handle_newly_tagged call later must not silence the ordinary create."""
    tag_id = await _follower_with_a_notifying_subscription(client.db)
    login_as(client, EDITOR_ID, "reiji")

    r = client.post("/concerts", data=_create_payload(
        tag_id, cancelled=["false"], labels=["Day 1"]
    ))
    assert r.status_code == 303

    (note,) = await _all(client.db, Notification)
    assert note.kind == "new_event"
    assert len(await _all(client.db, ReminderRule)) == 1


async def test_importing_a_live_concert_still_notifies_and_applies(client):
    """The import's own regression half -- it gained a call it never had, so
    its live path is newly exercised rather than merely preserved."""
    tag_id = await _follower_with_a_notifying_subscription(client.db)
    login_as(client, EDITOR_ID, "reiji")

    payload = _create_payload(tag_id, cancelled=["false"], labels=["Day 1"])
    del payload["event_id"]
    assert client.post("/concerts/import/commit", data=payload).status_code == 303

    (note,) = await _all(client.db, Notification)
    assert note.kind == "new_event"
    assert len(await _all(client.db, ReminderRule)) == 1


async def test_one_cancelled_leg_of_two_still_notifies(client):
    """A concert is dead only when EVERY leg is cancelled. A tour that loses
    one date is still happening, and its followers are still owed the notice
    -- the boundary the whole fix has to land on the right side of."""
    tag_id = await _follower_with_a_notifying_subscription(client.db)
    login_as(client, EDITOR_ID, "reiji")

    r = client.post("/concerts", data=_create_payload(
        tag_id, cancelled=["true", "false"], labels=["Day 1", "Day 2"]
    ))
    assert r.status_code == 303

    (note,) = await _all(client.db, Notification)
    assert note.kind == "new_event"


async def test_duplicating_a_concert_still_notifies_though_the_clone_is_legless(client):
    """duplicate_concert does NOT go through create_concert_row and creates no
    legs at all, so its clone is a genuine dateless draft -- the one create
    path where notifying a legless concert is right. Pins the exemption this
    fix leaves in place rather than removing."""
    tag_id = await _follower_with_a_notifying_subscription(client.db)
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/concerts", data=_create_payload(
        tag_id, cancelled=["true"], labels=["Day 1"]
    )).status_code == 303
    assert await _all(client.db, Notification) == []  # the born-dead source, suppressed

    assert client.post("/concerts/sixth/duplicate").status_code == 303

    (note,) = await _all(client.db, Notification)
    assert note.kind == "new_event"


async def test_notice_context_state_awareness(client):
    from app.db.service import notice_context

    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    build_standard_preset(client)

    async with client.db() as s:
        ctx = await notice_context(s, 1, EDITOR_ID)
        assert ctx.title == "Hasunosora 6th"
        assert ctx.first_deadline_label == "最速先行"
        assert not ctx.user_has_rules  # -> would render [Set my reminders]

    client.post("/concerts/hasunosora-6th/presets/1/apply")
    async with client.db() as s:
        ctx = await notice_context(s, 1, EDITOR_ID)
        assert ctx.user_has_rules  # -> would render [Remove these reminders]


# ── Preferences page: inline confirm() handler injection ─────────────────


def test_preset_delete_confirm_does_not_execute_a_hostile_preset_name(client):
    """Self-XSS only (a preset renders on its own owner's page), but the
    pattern is identical to the Tags page's, so it is closed the same way:
    the browser HTML-decodes an on* attribute before parsing it as JS, so
    Jinja's &#39; escaping does not protect confirm()'s argument."""
    import re

    login_as(client, FAN_ID, "fan")
    assert client.post("/presets", data={"name": "'); alert(1); //"}).status_code == 303
    html = client.get("/preferences").text
    for attr in re.findall(r"\son[a-z]+\s*=\s*\"([^\"]*)\"", html):
        assert "alert(1)" not in attr, f"payload sits raw in an inline handler: {attr}"


def test_preset_delete_confirm_still_names_the_preset_and_deletes_it(client):
    login_as(client, FAN_ID, "fan")
    client.post("/presets", data={"name": "standard"})
    html = client.get("/preferences").text
    assert 'data-preset-name="standard"' in html  # the confirm() reads this
    assert client.post("/presets/1/delete").status_code == 303
    assert 'data-preset-name="standard"' not in client.get("/preferences").text
