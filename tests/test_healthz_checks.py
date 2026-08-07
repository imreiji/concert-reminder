"""The `ok` regression test is the important one here: UptimeRobot keyword-
matches '"ok":true', so that field has an external consumer and changing its
meaning silently would defeat the point of this work.

The second theme is the split introduced when `checks` moved behind admin:
liveness is public, DIAGNOSTICS are not. The detail strings are infrastructure
facts about the host (free disk, last-backup time, and a filesystem path
whenever the marker will not parse), which an anonymous caller has no business
reading. `check_dms` already withheld its user-derived count for exactly this
reason -- see test_ops_checks.py -- and the other three now match it.
"""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID = 4242
PLAIN_ID = 99


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
    c.monkeypatch = monkeypatch
    return c


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def test_healthz_reports_every_check_to_an_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/healthz").json()
    assert set(body["checks"]) == {"backup", "disk", "scheduler", "dms"}
    assert set(body["checks"]["backup"]) == {"ok", "detail"}


async def test_healthz_withholds_check_detail_from_anonymous(client):
    """The endpoint stays reachable and still answers `ok` -- only the
    diagnostics are gone."""
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "checks" not in body
    assert set(body) == {"ok", "bot_enabled", "scheduler_ok", "scheduler_last_tick"}


async def test_healthz_withholds_check_detail_from_a_signed_in_non_admin(client, monkeypatch):
    """Signed in is not the bar; ADMIN is. A plain user has no more business
    reading the host's free disk than a stranger does."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, PLAIN_ID, "someone")
    body = client.get("/healthz").json()
    assert "checks" not in body
    assert body["ok"] is not None  # still answered, not 403'd


async def test_anonymous_healthz_does_not_run_the_checks_at_all(client, monkeypatch):
    """Not merely filtered from the response -- never executed.

    Each check does a disk stat, a file read and a COUNT(*) over the users
    table. Running them for every unauthenticated poll would make the one
    endpoint that must always answer into a free amplification primitive, and
    `ok` comes from the in-memory heartbeat, so their results are not needed
    to produce it.
    """
    from app.web import app as app_mod

    calls = []

    async def spy(session):
        calls.append(1)
        return []

    monkeypatch.setattr(app_mod, "run_checks", spy)
    client.get("/healthz")
    assert calls == [], "run_checks ran for an anonymous caller"


async def test_healthz_ok_still_tracks_scheduler_only(client, monkeypatch):
    """A failing backup must NOT flip `ok`, or the existing uptime monitor
    starts meaning something different without anyone deciding that."""
    from app import ops

    monkeypatch.setattr(ops.settings, "backup_marker_path", "/nonexistent/marker")
    # Past the startup grace, or a missing marker reports healthy by design.
    monkeypatch.setattr(ops, "_started_at", datetime.now(UTC) - timedelta(hours=40))
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/healthz").json()
    assert body["checks"]["backup"]["ok"] is False
    assert body["ok"] == body["scheduler_ok"]


async def test_healthz_survives_a_check_that_raises(client, monkeypatch):
    """/healthz must stay answerable even when a check is broken -- a 500 here
    would read to the uptime monitor as a full outage."""
    from app import ops

    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ops, "REGISTRY", [ops.RegistryEntry("disk", boom, alerting=True)])
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["checks"]["disk"]["ok"] is False
