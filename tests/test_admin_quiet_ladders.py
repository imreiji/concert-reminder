"""The round-watch worklist: admin-only, and it writes only the recheck stamp."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert, ConcertDay, Round
from app.db.service import ensure_user
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


async def test_the_page_renders_for_an_admin(client):
    """Every page needs a logged-in GET render test: a missing one shipped a
    500 once, from template context drift.

    Asserts on the row's Checked-button form action rather than a bare
    "bushi" substring: `build_quiet_ladder_block` also emits
    "- bushi: <title>" into `copy_text`, rendered in the <pre> below the
    table, so a bare substring check passes even with the whole <tbody> loop
    deleted -- it did, in review round 1. The form action only the row
    rendering produces. `'href="/tags"'` is a signed-in-chrome marker: it
    only renders when `user` reaches base.html's nav (review round 1 also
    found the route's context missing `"user"`, which silently drops the
    whole signed-in header on this admin-only page).
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        s.add(Concert(title="ブシロード20周年", event_id="bushi", created_by=ADMIN_ID))
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders")
    assert r.status_code == 200
    assert '/admin/quiet-ladders/bushi/checked' in r.text
    assert 'href="/tags"' in r.text


async def test_a_concert_with_no_legs_shows_no_dates_announced(client):
    """The canonical case for this whole feature: a zero-leg skeleton import.

    `| unique` alone (without `| list`) is a generator, which Jinja treats as
    always truthy, so the "no dates announced" branch never rendered -- this
    is the test that would have caught it, and did not exist before review
    round 1.

    Asserts the exact `<span class="dim">no dates announced</span>` markup
    the Dates CELL renders, not a bare substring: `_dates()` in
    domain/quiet_ladder_message.py emits the same plain-text phrase into
    `copy_text`, rendered unconditionally in the `<pre>` below the table --
    a bare `"no dates announced" in r.text` check passed even with the cell's
    generator bug reintroduced, because the `<pre>` block supplied the string
    on its own.
    """
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        s.add(Concert(title="Skeleton", event_id="skeleton", created_by=ADMIN_ID))
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders")
    assert r.status_code == 200
    assert '<span class="dim">no dates announced</span>' in r.text


async def test_a_concert_with_a_leg_and_a_past_round_shows_both(client):
    """A quiet concert is not always dateless or round-less: the ladder can
    hold a future leg and a fully-resolved round (nothing left announced),
    which is exactly the "went quiet" case this page exists for. Exercises
    the Dates and Rounds cells -- the only display logic on the page -- with
    real data, which no test did before review round 1.

    Round-1 review flagged this test as the same proxy shape as finding 3:
    `assert "最速先行 Round 1" in r.text` is satisfied by `copy_text` alone
    (`_rounds()` in domain/quiet_ladder_message.py emits
    "  rounds held: 最速先行 Round 1" into the `<pre>`, rendered
    unconditionally), and the two negative assertions hold trivially with an
    empty table. Verified: `{% for row in rows %}` -> `{% for row in [] %}`
    left the old version passing.

    Two row-only discriminators replace it:

    - The date is asserted UN-PADDED (`3 Mar`, from the template's
      `{{ d.day }} {{ d.strftime('%b') }}`) with a `\\b` word-boundary regex,
      not a bare substring: `copy_text`'s `_dates()` uses `%d %b` and renders
      the PADDED `03 Mar`, which contains the literal characters "3 Mar" as a
      substring -- `"3 Mar" in r.text` would still pass from the `<pre>`
      alone. `\\b3 Mar\\b` does not match inside "03 Mar" (no boundary
      between the two digits), only where "3 Mar" is not preceded by another
      digit, i.e. only the cell's own un-padded rendering. The seeded leg
      date's day is deliberately single-digit (2035-03-03) -- with a
      two-digit day the padded and un-padded forms are identical strings and
      this whole discriminator collapses.
    - The round label is asserted inside its own `<td>...</td>`, not as a
      bare substring, so it cannot be satisfied by "rounds held: <label>" in
      the `<pre>`.
    """
    now = datetime.now(UTC)
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        c = Concert(title="Legged Show", event_id="legged", created_by=ADMIN_ID)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(
            concert_id=c.id, label="Day 1",
            starts_at_utc=datetime(2035, 3, 3, 12, 0, tzinfo=UTC),
        ))
        s.add(Round(
            concert_id=c.id,
            kind=RoundKind.LOTTERY_ROUND,
            label="最速先行 Round 1",
            closes_at_utc=now - timedelta(days=10),
        ))
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders")
    assert r.status_code == 200
    assert '/admin/quiet-ladders/legged/checked' in r.text
    assert re.search(r"\b3 Mar\b", r.text)
    assert re.search(r"<td>\s*最速先行 Round 1\s*</td>", r.text)


async def test_an_editor_is_forbidden(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/quiet-ladders").status_code == 403


async def test_signed_out_is_redirected_not_an_error(client):
    r = client.get("/admin/quiet-ladders")
    assert r.status_code == 303


async def test_an_editor_is_forbidden_from_checking(client):
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        s.add(Concert(title="Quiet", event_id="quiet", created_by=ADMIN_ID))
        await s.commit()

    login_as(client, EDITOR_ID, "editor")
    assert client.post("/admin/quiet-ladders/quiet/checked").status_code == 403


async def test_signed_out_checking_is_redirected_not_an_error(client):
    r = client.post("/admin/quiet-ladders/quiet/checked")
    assert r.status_code == 303


async def test_checked_stamps_and_redirects(client):
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        s.add(Concert(title="Quiet", event_id="quiet", created_by=ADMIN_ID))
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/quiet-ladders/quiet/checked")
    assert r.status_code == 303

    async with client.db() as s:
        c = (await s.execute(
            select(Concert).where(Concert.event_id == "quiet")
        )).scalar_one()
        assert c.ladder_rechecked_at_utc is not None


async def test_checking_an_unknown_concert_is_404(client):
    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/quiet-ladders/nope/checked")
    assert r.status_code == 404
    assert r.json()["detail"] == "no such concert"


async def test_preferences_links_an_admin_to_the_page(client):
    """The page is reachable: nothing in the site nav points at it, so the
    Preferences "Admin tools" block is the only way in.

    Both halves are load-bearing, and the brief's one-line
    `"/admin/quiet-ladders" in r.text` had neither:

    - The ANCHOR markup, not the bare path. The path alone would also be
      satisfied by a comment, a `data-` attribute or a form action, none of
      which is a link a human can follow.
    - The EDITOR half first. `test_an_editor_is_forbidden` pins that the route
      answers 403; this pins that a non-admin is not offered a link into a 403.
      Without it the test's own name ("an admin") is unpinned -- an
      unconditional link outside the `{% if user.is_admin %}` block would pass
      the positive half alone.

    Checked by deleting the two template lines: the positive assertion fails.
    Checked by moving them outside the admin gate: the editor assertion fails.
    """
    login_as(client, EDITOR_ID, "editor")
    editor_page = client.get("/preferences")
    assert editor_page.status_code == 200
    assert '<a href="/admin/quiet-ladders">' not in editor_page.text

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert '<a href="/admin/quiet-ladders">' in r.text
