"""The bearer-token seam.

Two properties matter more than the endpoints themselves. The tier boundary is
the thing most likely to be got wrong, so every endpoint gets an auth matrix.
And "read-only" has to be a property of the routing table rather than a promise
in a docstring, so a sweep asserts it.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import User
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


async def test_whoami_reports_an_editor_who_is_not_an_admin(client, db, monkeypatch):
    """The auth matrix's middle tier: an editor token must read is_editor=True,
    is_admin=False -- distinct from both the admin case above and the plain
    user `api_admin`'s 403 tests exercise elsewhere. Promoted via the DB flag
    (`User.is_editor`), the same path `/promote-editor` writes, rather than
    `settings.editor_whitelist`, so this also proves `api_user` consults the
    DB flag and not only the env whitelist."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN))
    token = await _mint(db, EDITOR, "someone")
    async with db() as s:
        user = await s.get(User, EDITOR)
        user.is_editor = True
        await s.commit()

    body = client.get("/api/v1/whoami", headers=_auth(token)).json()
    assert body["discord_id"] == EDITOR
    assert body["is_editor"] is True
    assert body["is_admin"] is False


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

    This walks `app.routes` and unwraps FastAPI's `_IncludedRouter` wrapper
    down to the real `APIRoute` objects, rather than reading
    `app.openapi()["paths"]`. The schema was tried first and has a hole: a
    route registered `@router.post(..., include_in_schema=False)` never
    enters the OpenAPI schema at all, so a schema-based sweep would pass
    GREEN with a live write endpoint sitting under /api/v1 -- this is the
    branch's single structural guarantee of "read-only by construction", and
    a hidden route is exactly the kind of thing that guarantee exists to
    catch.

    Naively walking `app.routes` directly does not work either: on this
    FastAPI version, `include_router` defers flattening, so `app.routes`
    holds one opaque `_IncludedRouter` per included router rather than its
    individual routes, and a plain `r.path.startswith("/api/")` scan over
    that finds nothing under /api/ at all -- vacuously green even with a POST
    route present. `_IncludedRouter.original_router.routes` is where the real
    `APIRoute` objects (with a true `.methods` set, unaffected by
    `include_in_schema`) actually live, so this walks those instead.

    Verified red-then-green: temporarily adding
    `@router.post("/x", include_in_schema=False)` to `api.py` fails this test
    (and left the openapi-based version green), removed again once confirmed.
    """
    app = create_app()
    offenders: list[tuple[str, list[str]]] = []
    for route in app.routes:
        original = getattr(route, "original_router", None)
        candidates = original.routes if original is not None else [route]
        for candidate in candidates:
            path = getattr(candidate, "path", None)
            methods = getattr(candidate, "methods", None)
            if not path or not methods or not path.startswith("/api/"):
                continue
            extra = sorted(methods - {"GET", "HEAD"})
            if extra:
                offenders.append((path, extra))
    assert offenders == [], f"non-GET routes under /api/: {offenders}"
