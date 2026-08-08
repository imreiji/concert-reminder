"""The bearer-token seam.

Two properties matter more than the endpoints themselves. The tier boundary is
the thing most likely to be got wrong, so every endpoint gets an auth matrix.
And "read-only" has to be a property of the routing table rather than a promise
in a docstring, so a sweep asserts it.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.service import ensure_user, generate_api_token
from app.db.session import get_session
from app.web.app import create_app

ADMIN = 4242
EDITOR = 99


@pytest.fixture()
def client(db):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, follow_redirects=False)


async def _mint(db, discord_id: int, name: str) -> str:
    async with db() as s:
        await ensure_user(s, discord_id, name)
        token = await generate_api_token(s, discord_id)
        await s.commit()
        return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_whoami_reports_the_minting_user(client, db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN))
    token = await _mint(db, ADMIN, "reiji")
    body = client.get("/api/v1/whoami", headers=_auth(token)).json()
    assert body["discord_id"] == ADMIN
    assert body["username"] == "reiji"
    assert body["is_admin"] is True


async def test_no_token_is_401(client):
    r = client.get("/api/v1/whoami")
    assert r.status_code == 401


async def test_malformed_header_is_401(client, db):
    await _mint(db, ADMIN, "reiji")
    for header in ({"Authorization": "Bearer"}, {"Authorization": "Basic xyz"},
                   {"Authorization": "xyz"}):
        assert client.get("/api/v1/whoami", headers=header).status_code == 401


async def test_unknown_token_is_401_and_says_nothing_extra(client, db):
    """An unknown token and a malformed header must be indistinguishable, or a
    probe learns which tokens exist."""
    await _mint(db, ADMIN, "reiji")
    unknown = client.get("/api/v1/whoami", headers=_auth("nope"))
    malformed = client.get("/api/v1/whoami", headers={"Authorization": "xyz"})
    assert unknown.status_code == malformed.status_code == 401
    assert unknown.json() == malformed.json()


async def test_errors_stay_json_even_for_a_browser_like_request(client):
    """web/app.py returns the HTML error page for anything that looks like a
    navigation, and an agent's request can look exactly like one. The API must
    opt out or its 401 arrives as a styled web page."""
    r = client.get("/api/v1/whoami", headers={"Accept": "text/html"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")
    assert "detail" in r.json()


async def test_a_404_under_the_api_prefix_is_json(client):
    r = client.get("/api/v1/nope", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_every_api_route_is_read_only():
    """Read-only as a property of the routing table, not a docstring promise.

    This reads `app.openapi()["paths"]` rather than walking `app.routes`
    directly. On this FastAPI version, `include_router` defers flattening --
    `app.routes` holds an opaque `_IncludedRouter` wrapper per included
    router, not the individual `APIRoute` objects, so a naive walk of
    `app.routes` finds nothing under `/api/` at all and this assertion would
    pass VACUOUSLY even with a POST route present (verified locally: adding
    one and asserting `r.path.startswith("/api/")` over `app.routes` stayed
    green). The OpenAPI schema is generated from the same effective routing
    table the app actually matches against, so it is what this sweep must
    read to mean anything.
    """
    app = create_app()
    schema = app.openapi()
    offenders = [
        (path, sorted(m.upper() for m in methods))
        for path, methods in schema["paths"].items()
        if path.startswith("/api/") and set(methods) - {"get"}
    ]
    assert offenders == [], f"non-GET routes under /api/: {offenders}"
