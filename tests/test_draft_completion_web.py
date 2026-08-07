"""The completion button, its gate, and what the pending list says about it.

Same fixture shape as tests/test_admin_discoveries.py (one `client` fixture,
a `login_as` helper) and tests/test_venue_rollup.py's `editor_client` (a
client pre-logged-in, its `.db()` reachable for direct session access) --
this suite has no shared conftest fixture for either, so each file that
needs an HTTP client builds its own from the same TestClient + OAuth-stub
pattern. `admin_client`/`editor_client` here are that same client, already
signed in as the corresponding identity.
"""

import re
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import TriageRun
from app.db.service import note_fetch_domain, request_triage
from app.db.session import get_session
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


@pytest.fixture()
def admin_client(client):
    login_as(client, ADMIN_ID, "admin")
    return client


@pytest.fixture()
def editor_client(client):
    login_as(client, EDITOR_ID, "editor")
    return client


@pytest_asyncio.fixture()
async def session(db):
    async with db() as s:
        yield s


def _completion_form(body: str) -> str:
    """The `<form>...</form>` block posting to the completion route, so a
    test can check `disabled` scoped to THAT button rather than anywhere on
    the page (the discard buttons render their own `disabled`-free forms)."""
    m = re.search(
        r'<form[^>]*action="/concerts/import/pending/complete".*?</form>',
        body, re.DOTALL,
    )
    assert m, "no completion form on the page"
    return m.group(0)


async def test_the_button_is_absent_when_the_flag_is_off(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", False)
    body = admin_client.get("/concerts/import/pending").text
    assert "/concerts/import/pending/complete" not in body


async def test_the_button_is_present_for_an_admin_when_the_flag_is_on(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    body = admin_client.get("/concerts/import/pending").text
    assert "/concerts/import/pending/complete" in body


async def test_a_plain_editor_never_sees_the_button(editor_client, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    body = editor_client.get("/concerts/import/pending").text
    assert "/concerts/import/pending/complete" not in body


async def test_a_plain_editor_pressing_it_anyway_gets_403(editor_client, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    r = editor_client.post("/concerts/import/pending/complete")
    assert r.status_code == 403


async def test_a_plain_editor_pressing_it_with_the_flag_off_still_gets_403(
    editor_client, monkeypatch
):
    """Both gates refuse independently, so this combination is not a real
    gap -- but it is the cheap check that neither gate quietly leans on the
    other. `require_admin` is a route dependency, evaluated before the
    handler body ever reads `settings.triage_enabled`, so a non-admin is
    turned away on IDENTITY alone and never learns whether the flag is even
    on."""
    monkeypatch.setattr(settings, "triage_enabled", False)
    r = editor_client.post("/concerts/import/pending/complete")
    assert r.status_code == 403


async def test_pressing_it_when_the_flag_is_off_404s(admin_client, monkeypatch):
    """The route itself must refuse, not just the template hiding the
    button -- an admin who bookmarks the URL on a deploy that has not
    opted in must not be able to spend the key it doesn't have."""
    monkeypatch.setattr(settings, "triage_enabled", False)
    r = admin_client.post("/concerts/import/pending/complete")
    assert r.status_code == 404


async def test_pressing_it_queues_one_completion_run(admin_client, session, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    r = admin_client.post("/concerts/import/pending/complete")
    assert r.status_code == 303
    runs = (await session.execute(select(TriageRun))).scalars().all()
    assert [run.kind for run in runs] == ["complete"]


async def test_pressing_it_twice_queues_one_run(admin_client, session, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    admin_client.post("/concerts/import/pending/complete")
    admin_client.post("/concerts/import/pending/complete")
    runs = (await session.execute(select(TriageRun))).scalars().all()
    assert len(runs) == 1


async def test_the_button_disables_once_a_completion_run_is_pending(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    before = _completion_form(admin_client.get("/concerts/import/pending").text)
    assert "disabled" not in before

    admin_client.post("/concerts/import/pending/complete")

    after = _completion_form(admin_client.get("/concerts/import/pending").text)
    assert "disabled" in after


async def test_a_pending_classify_run_does_not_block_the_completion_press(
    admin_client, session, monkeypatch
):
    """The button's disabled state must read the COMPLETION kind's own
    pending run, not the classify button's -- getting this backwards means
    pressing one button greys out the other. A classify run sitting
    "requested" must not stop a completion press from queuing its own row."""
    monkeypatch.setattr(settings, "triage_enabled", True)
    await request_triage(session, datetime.now(UTC), ADMIN_ID, kind="classify")
    await session.commit()

    # The pending list must not grey the button out over the OTHER kind's run.
    body = admin_client.get("/concerts/import/pending").text
    assert "disabled" not in _completion_form(body)

    r = admin_client.post("/concerts/import/pending/complete")
    assert r.status_code == 303
    runs = (await session.execute(select(TriageRun))).scalars().all()
    assert sorted(run.kind for run in runs) == ["classify", "complete"]


async def test_waiting_domains_are_called_out_on_the_list(admin_client, session, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", datetime.now(UTC))
    await session.commit()
    body = admin_client.get("/concerts/import/pending").text
    assert "/admin/fetch-domains" in body


async def test_no_waiting_domains_means_no_callout(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    body = admin_client.get("/concerts/import/pending").text
    assert "/admin/fetch-domains" not in body
