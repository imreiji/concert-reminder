# Agent read API (`/api/v1`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an agent read-only HTTP access to the catalogue, tag vocabulary, discovery leads and its own pending drafts, so the agent loop stops running through the owner's clipboard.

**Architecture:** One new `GET`-only router at `/api/v1`, authenticated by a bearer token stored as a SHA-256 hash on `User` (mirroring `calendar_token_hash`). The token resolves to the same `SessionUser` the cookie path produces, so the existing three-tier auth applies unchanged. Each endpoint's query lives in the `db/` module that already owns that data — `core.py` for concerts, `tags.py`, `discovery_events.py`, `drafts.py` — and is re-exported through the `db/service.py` facade. Concert content rides as the existing draft YAML rather than a new JSON schema.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, aiosqlite, Alembic, pytest + pytest-asyncio (auto mode).

**Spec:** `docs/superpowers/specs/2026-08-08-agent-read-api-design.md` — read it before Task 1.

## Global Constraints

- `uv run pytest -q` AND `uv run ruff check .` must both pass before every commit. These are the same two gates CI runs. Never commit with either failing.
- Ruff: `line-length = 100`, rules `E, F, I, UP, B`. Imports are sorted by `I`; run `uv run ruff check --fix .` to sort them rather than hand-ordering.
- Python `>=3.11`. Use `X | None`, not `Optional[X]`.
- **Timezones (invariant 1):** the DB stores aware UTC only; the `UTCDateTime` TypeDecorator REJECTS naive datetimes. Never construct a naive `datetime` in a test fixture — always `datetime(..., tzinfo=UTC)`.
- **Tests use `tests/conftest.py`'s shared `db` / `session` fixtures.** Do not write a new engine fixture. `db` yields an `async_sessionmaker`; `session` yields one open `AsyncSession` on the same database. Override locally only to ADD seeding, deriving from `db`.
- **Every name added to a `db/` module must also be added to `db/service.py`'s import list AND its `__all__`.** `tests/test_service_facade.py` fails if they disagree. A `db/` feature module must NEVER import `app.db.service` — that is a cycle. Import `app.db.core` or the sibling directly.
- Config files (`alembic.ini`, etc.) stay ASCII-only — the owner's Windows machine uses a GBK locale and em-dashes in configs crash it.
- The API is English-only and NOT wrapped in `_()`, like `/admin/deliveries` and `/admin/discoveries`. Its consumer is a program.
- Owner is on Windows PowerShell 5.1: no `&&` chaining in any command given to him. Commands in this plan are for the agent's shell (bash) and may chain.

## File Structure

| File | Responsibility |
|---|---|
| `src/app/db/tokens.py` | NEW. Secret tokens at rest: `hash_token`, `generate_api_token`, `get_user_by_api_token`. One responsibility, no sibling imports. |
| `src/app/db/calendar_feed.py` | MODIFY. Drop its private `_hash_token`, import the shared `hash_token`. Removes a duplicate hash implementation. |
| `src/app/db/models.py` | MODIFY. `User.api_token_hash`. |
| `alembic/versions/<rev>_api_token.py` | NEW. One nullable unique column. |
| `src/app/web/paging.py` | NEW. `PageParams` dependency + `page_envelope`. HTTP-boundary concern, beside `web/forms.py` which is the same kind of module. |
| `src/app/web/routes/api.py` | NEW. The router: six GET endpoints and the bearer-token dependency. |
| `src/app/web/app.py` | MODIFY. Register the router; carve `/api/` out of `_wants_html`. |
| `src/app/db/core.py` | MODIFY. `api_concert_rows`, `api_concert_detail`. |
| `src/app/db/tags.py` | MODIFY. `api_tag_rows`. |
| `src/app/db/discovery_events.py` | MODIFY. `api_lead_rows`. |
| `src/app/db/drafts.py` | MODIFY. `api_draft_rows`, `api_draft_detail`. |
| `src/app/db/service.py` | MODIFY. Re-export every new name above. |
| `src/app/web/routes/preferences.py` + `preferences.html` | MODIFY. `POST /me/api-token` and the mint UI. |
| `tests/test_api_tokens.py`, `tests/test_api_auth.py`, `tests/test_api_paging.py`, `tests/test_api_reads.py` | NEW. |

---

### Task 1: Token storage and the shared hash

**Files:**
- Create: `src/app/db/tokens.py`
- Create: `alembic/versions/<generated>_api_token.py`
- Modify: `src/app/db/models.py` (in `class User`, beside `calendar_token_hash`)
- Modify: `src/app/db/calendar_feed.py` (delete `_hash_token`, import `hash_token`)
- Modify: `src/app/db/service.py`
- Test: `tests/test_api_tokens.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `hash_token(token: str) -> str`
  - `async generate_api_token(session: AsyncSession, user_id: int) -> str`
  - `async get_user_by_api_token(session: AsyncSession, token: str) -> User | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_tokens.py`:

```python
"""Agent API tokens: minted once, stored only as a hash.

Same shape as the calendar feed token, which invariant 5 names as the pattern
every future personal-secret-link feature should reuse. The properties worth
pinning are the ones that make a leak survivable: the raw value is never
persisted, and minting again invalidates whatever was issued before.
"""

from app.db.models import User
from app.db.service import (
    ensure_user,
    generate_api_token,
    get_user_by_api_token,
    hash_token,
)

USER = 4242


async def test_mint_stores_only_the_hash(session):
    await ensure_user(session, USER, "reiji")
    token = await generate_api_token(session, USER)

    row = await session.get(User, USER)
    assert row.api_token_hash == hash_token(token)
    assert row.api_token_hash != token
    assert token not in (row.api_token_hash or "")


async def test_lookup_finds_the_user(session):
    await ensure_user(session, USER, "reiji")
    token = await generate_api_token(session, USER)
    found = await get_user_by_api_token(session, token)
    assert found is not None
    assert found.discord_id == USER


async def test_unknown_token_finds_nobody(session):
    await ensure_user(session, USER, "reiji")
    await generate_api_token(session, USER)
    assert await get_user_by_api_token(session, "not-a-real-token") is None


async def test_minting_again_invalidates_the_old_token(session):
    """Recovery is 'generate a new one', never 'look up the old one' -- so the
    previous value must stop matching the moment a new one is issued."""
    await ensure_user(session, USER, "reiji")
    first = await generate_api_token(session, USER)
    second = await generate_api_token(session, USER)

    assert first != second
    assert await get_user_by_api_token(session, first) is None
    assert (await get_user_by_api_token(session, second)).discord_id == USER


async def test_tokens_are_long_enough_to_be_unguessable(session):
    await ensure_user(session, USER, "reiji")
    token = await generate_api_token(session, USER)
    assert len(token) >= 32


async def test_the_calendar_feed_uses_the_same_hash(session):
    """calendar_feed.py must not keep a second hash implementation -- two would
    be one refactor away from disagreeing, and the failure is silent (a token
    that simply never matches)."""
    from app.db import calendar_feed

    assert not hasattr(calendar_feed, "_hash_token")
    assert calendar_feed.hash_token is hash_token
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_tokens.py -q`
Expected: FAIL — `ImportError: cannot import name 'generate_api_token'`.

- [ ] **Step 3: Add the column**

In `src/app/db/models.py`, inside `class User`, directly after `calendar_token_hash`:

```python
    # Agent read-API token, hashed at rest -- same pattern as
    # calendar_token_hash and WebSession.token_hash (invariant 5 names this as
    # the shape for any personal-secret-link feature). Sent as an
    # Authorization: Bearer header rather than in a URL, which the calendar
    # feed cannot do because calendar clients send no headers.
    api_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
```

- [ ] **Step 4: Create `src/app/db/tokens.py`**

```python
"""Secret tokens at rest.

One hash implementation for every personal-secret-link feature. This module
exists because there were about to be two: `calendar_feed.py` had a private
`_hash_token`, and a second copy for the API token would be one refactor away
from disagreeing -- with a failure mode that is silent rather than loud, since
a mismatched hash just means a token that never matches anything.

Invariant 5's rule, applied here: `secrets.token_urlsafe`, only the SHA-256
stored, the raw value returned once and never recoverable. Recovery is
"generate a new one", which is why every generator overwrites in place.
"""

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def generate_api_token(session: AsyncSession, user_id: int) -> str:
    """(Re)generate the user's agent API token, returning the RAW value once.

    Overwriting invalidates any previously-issued token, because only the hash
    is stored and the old raw value stops matching. Fetched with session.get
    rather than ensure_user for the same reason generate_calendar_token is:
    callers are behind require_user, so the row already exists, and ensure_user
    would overwrite the username with a placeholder.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"no such user: {user_id}")
    token = secrets.token_urlsafe(32)
    user.api_token_hash = hash_token(token)
    await session.flush()
    return token


async def get_user_by_api_token(session: AsyncSession, token: str) -> User | None:
    """None for an unknown token. The caller must answer 401 identically for
    this and for a malformed header, so a probe cannot learn whether a given
    token exists."""
    if not token:
        return None
    res = await session.execute(
        select(User).where(User.api_token_hash == hash_token(token))
    )
    return res.scalar_one_or_none()
```

- [ ] **Step 5: De-duplicate the calendar feed's hash**

In `src/app/db/calendar_feed.py`: delete the `def _hash_token(...)` function, add `from app.db.tokens import hash_token` to the imports, and replace both `_hash_token(` call sites with `hash_token(`. Remove the now-unused `import hashlib` if nothing else in the file uses it (`uv run ruff check --fix` will tell you).

- [ ] **Step 6: Export through the facade**

In `src/app/db/service.py`, add a `from app.db.tokens import (...)` block importing `generate_api_token`, `get_user_by_api_token`, `hash_token`, and add those three names to `__all__` (it is sorted; keep it sorted).

- [ ] **Step 7: Generate and fix the migration**

```bash
uv run alembic revision --autogenerate -m "api token hash"
```

Then EDIT the generated file: remove any `import app.db.models` line and replace `app.db.models.UTCDateTime()` with `sa.DateTime()` if present (not expected for a `String(64)` column, but check — this is a standing rule). Confirm the upgrade is a single `add_column` with a unique constraint and that `down_revision` points at the current head. It touches no existing constraint, so it needs neither the `naming_convention` reflection workaround nor a legacy-DDL fixture.

```bash
uv run alembic upgrade head
```

- [ ] **Step 8: Run tests and lint**

Run: `uv run pytest tests/test_api_tokens.py tests/test_service_facade.py tests/test_calendar_feed.py -q`
Expected: PASS.
Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 9: Full suite, then commit**

```bash
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat(api): store an agent API token hash on User

One hash implementation, in a new db/tokens.py, rather than a second copy
beside calendar_feed.py's private one -- two would be one refactor away from
disagreeing, and a mismatched hash fails silently as a token that never
matches. Follows invariant 5's stated pattern: token_urlsafe, only the
SHA-256 stored, raw value returned once, minting again invalidates the old."
```

---

### Task 2: Bearer auth, the router, `whoami`, and JSON errors

**Files:**
- Create: `src/app/web/routes/api.py`
- Modify: `src/app/web/app.py`
- Test: `tests/test_api_auth.py`

**Interfaces:**
- Consumes: `get_user_by_api_token` (Task 1).
- Produces:
  - `router` (APIRouter, prefix `/api/v1`)
  - `async api_user(...) -> SessionUser` — FastAPI dependency, raises 401
  - `async api_admin(...) -> SessionUser` — raises 403 for a non-admin
  - `API_PREFIX = "/api/v1"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_auth.py`:

```python
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
    """Read-only as a property of the routing table, not a docstring promise."""
    app = create_app()
    offenders = [
        (r.path, sorted(r.methods - {"HEAD", "OPTIONS"}))
        for r in app.routes
        if getattr(r, "path", "").startswith("/api/")
        and (getattr(r, "methods", set()) - {"GET", "HEAD", "OPTIONS"})
    ]
    assert offenders == [], f"non-GET routes under /api/: {offenders}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_auth.py -q`
Expected: FAIL — every request 404s because the router does not exist.

- [ ] **Step 3: Create `src/app/web/routes/api.py`**

```python
"""The agent read API: `/api/v1`, GET only.

Its own module because a router registers whole, and this is an unrelated
concern from the pages beside it -- the same reason `discoveries.py` and
`fetch_domains.py` are their own files.

English-only and NOT wrapped in `_()`, like /admin/deliveries: the consumer is
a program.

READ-ONLY BY CONSTRUCTION. Only `@router.get` appears here, and
`tests/test_api_auth.py::test_every_api_route_is_read_only` sweeps the routing
table for anything else. `import_commit` remains the only write path into
`concerts`; nothing in this module writes at all.

The token acts AS its minting user: `api_user` returns the same `SessionUser`
the cookie path builds, so `is_editor`/`is_admin` mean exactly what they mean
everywhere else and there is no second permission model to drift from the
first.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.service import get_user_by_api_token
from app.db.session import get_session
from app.web.auth import SessionUser

API_PREFIX = "/api/v1"

router = APIRouter(prefix=API_PREFIX)


async def api_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> SessionUser:
    """Resolve `Authorization: Bearer <token>` to a SessionUser, or 401.

    Every failure answers the SAME 401 body -- absent header, wrong scheme,
    unparseable, unknown token. Distinguishing them would let a prober learn
    which tokens exist, and the caller can do nothing different about any of
    them anyway.
    """
    unauthorized = HTTPException(status_code=401, detail="invalid or missing API token")
    if not authorization:
        raise unauthorized
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        raise unauthorized

    user = await get_user_by_api_token(session, raw.strip())
    if user is None:
        raise unauthorized

    # Same resolution current_user() does, from the same inputs: env whitelist,
    # admin whitelist, or the DB flag.
    is_editor = (
        settings.is_editor(user.discord_id)
        or settings.is_admin(user.discord_id)
        or user.is_editor
    )
    return SessionUser(
        id=user.discord_id,
        username=user.username,
        avatar=None,
        is_editor=is_editor,
        dm_blocked=user.dm_blocked_since is not None,
    )


async def api_admin(user: SessionUser = Depends(api_user)) -> SessionUser:
    """403, not 404: the caller authenticated fine and simply lacks the tier.
    Same split web/auth.py draws -- signed out is 401, signed in and
    unauthorized is 403."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


@router.get("/whoami")
async def whoami(user: SessionUser = Depends(api_user)) -> dict:
    """The first call to make when auth misbehaves: it turns 'my token does not
    work' into one request that says which account it resolved to."""
    return {
        "discord_id": user.id,
        "username": user.username,
        "is_editor": user.is_editor,
        "is_admin": user.is_admin,
    }
```

- [ ] **Step 4: Register the router and carve out the error handlers**

In `src/app/web/app.py`, import the module alongside the other route imports (`from app.web.routes import api as api_routes`) and register it beside the others:

```python
    app.include_router(api_routes.router)
```

Then, in `_wants_html`, add the API carve-out as the FIRST check:

```python
    def _wants_html(request: Request) -> bool:
        # The agent API always answers JSON, including its errors. Its requests
        # can carry a browser-like Accept header, so without this an agent's
        # 401 arrives as a styled HTML error page it cannot parse. Checked
        # first because it is unconditional -- no header may override it.
        if request.url.path.startswith("/api/"):
            return False
        if request.headers.get("hx-request"):
            return False
        return "text/html" in request.headers.get("accept", "")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_api_auth.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat(api): bearer-token auth, the /api/v1 router and whoami

The token acts as its minting user, returning the same SessionUser the cookie
path builds, so the existing three-tier auth applies unchanged and there is no
second permission model. Every auth failure answers one identical 401 so a
probe cannot learn which tokens exist. _wants_html gets an unconditional
/api/ carve-out, checked first: an agent request can carry a browser-like
Accept header, and without it a 401 arrives as an HTML page."
```

---

### Task 3: Paging

**Files:**
- Create: `src/app/web/paging.py`
- Test: `tests/test_api_paging.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class PageParams` with fields `limit: int`, `offset: int`
  - `async page_params(limit: int = 200, offset: int = 0) -> PageParams` — FastAPI dependency, raises 422
  - `page_envelope(items: list, total: int, params: PageParams) -> dict`
  - `MAX_LIMIT = 500`, `DEFAULT_LIMIT = 200`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_paging.py`:

```python
"""Paging parameters and the response envelope.

The cap is a 422 rather than a silent clamp on purpose: an agent that asked for
5000 and quietly received 500 would page as though it had the whole set.
"""

import pytest
from fastapi import HTTPException

from app.web.paging import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    PageParams,
    page_envelope,
    page_params,
)


async def test_defaults():
    p = await page_params()
    assert p.limit == DEFAULT_LIMIT
    assert p.offset == 0


async def test_accepts_the_cap_exactly():
    assert (await page_params(limit=MAX_LIMIT)).limit == MAX_LIMIT


@pytest.mark.parametrize("limit", [MAX_LIMIT + 1, 0, -1])
async def test_bad_limit_is_422_not_a_clamp(limit):
    with pytest.raises(HTTPException) as e:
        await page_params(limit=limit)
    assert e.value.status_code == 422


async def test_negative_offset_is_422():
    with pytest.raises(HTTPException) as e:
        await page_params(offset=-1)
    assert e.value.status_code == 422


def test_envelope_shape():
    env = page_envelope([1, 2], 47, PageParams(limit=2, offset=4))
    assert env == {"items": [1, 2], "total": 47, "limit": 2, "offset": 4}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_paging.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.web.paging'`.

- [ ] **Step 3: Create `src/app/web/paging.py`**

```python
"""Offset paging for the agent API.

Its own module beside `web/forms.py`, which is the same kind of thing: a small
HTTP-boundary helper several routers import.

THE RULE THAT MAKES THIS CORRECT LIVES IN THE QUERIES, NOT HERE. Offset paging
over a non-unique sort key is broken even when nothing is being inserted --
SQLite may order ties differently between the two queries, so a row repeats on
page 2 while another vanishes. Every paged query must sort on a TOTALLY ORDERED
key, with a unique column (normally `id`) as the final tiebreaker. This module
cannot enforce that; `tests/test_api_reads.py` asserts it per endpoint by
checking that the union of pages equals the whole set.
"""

from dataclasses import dataclass

from fastapi import HTTPException

DEFAULT_LIMIT = 200
MAX_LIMIT = 500


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


async def page_params(limit: int = DEFAULT_LIMIT, offset: int = 0) -> PageParams:
    """422 rather than a silent clamp.

    Clamping is the friendlier-looking choice and the wrong one: an agent that
    asked for 5000, received 500 and was told nothing would conclude it had
    read the whole set and stop paging.
    """
    if limit < 1 or limit > MAX_LIMIT:
        raise HTTPException(
            status_code=422, detail=f"limit must be between 1 and {MAX_LIMIT}"
        )
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be 0 or greater")
    return PageParams(limit=limit, offset=offset)


def page_envelope(items: list, total: int, params: PageParams) -> dict:
    """`total` is the count BEFORE limit/offset, which is what lets a caller
    know when to stop instead of paging until it gets a short page."""
    return {
        "items": items,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
uv run pytest tests/test_api_paging.py -q && uv run ruff check .
git add -A
git commit -m "feat(api): offset paging params and envelope

Over-cap limit is a 422, not a clamp: an agent that asked for 5000 and
silently got 500 would page believing it had the whole set. The totally-ordered
sort requirement belongs to each query and is asserted per endpoint."
```

---

### Task 4: `/concerts` list and detail

**Files:**
- Modify: `src/app/db/core.py` (append to the `# ── Discover status ──` section, which already owns catalogue queries)
- Modify: `src/app/db/service.py`
- Modify: `src/app/web/routes/api.py`
- Test: `tests/test_api_reads.py` (create)

**Interfaces:**
- Consumes: `PageParams`, `page_envelope`, `api_user` (Tasks 2-3).
- Produces:
  - `async api_concert_rows(session, *, q="", tag_handles=(), since=None, until=None, limit, offset) -> tuple[list[dict], int]`
  - `async api_concert_detail(session, event_id: str) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_reads.py` with the concert cases (later tasks append to this file):

```python
"""The read endpoints, end to end through the router.

The paging assertion is the load-bearing one and is deliberately not "limit=N
returns N rows": that passes with a non-deterministic sort. Asserting that the
UNION of the pages equals the whole set with no repeats is what catches a
missing `id` tiebreaker.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import Concert, ConcertDay
from app.db.service import ensure_user, generate_api_token
from app.db.session import get_session
from app.web.app import create_app

ADMIN = 4242
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client(db):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, follow_redirects=False)


async def _mint(db, discord_id=ADMIN, name="reiji") -> str:
    async with db() as s:
        await ensure_user(s, discord_id, name)
        token = await generate_api_token(s, discord_id)
        await s.commit()
        return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_concerts(db, n: int) -> None:
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        for i in range(n):
            c = Concert(title=f"Live {i}", title_en=f"Live {i}",
                        event_id=f"live-{i}", created_by=ADMIN)
            s.add(c)
            await s.flush()
            s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW))
        await s.commit()


async def test_concerts_list_returns_an_envelope(client, db):
    token = await _mint(db)
    await _seed_concerts(db, 3)
    body = client.get("/api/v1/concerts", headers=_auth(token)).json()
    assert body["total"] == 3
    assert body["limit"] == 200
    assert body["offset"] == 0
    assert {r["event_id"] for r in body["items"]} == {"live-0", "live-1", "live-2"}


async def test_paging_covers_every_row_exactly_once(client, db):
    """The assertion that catches a missing id tiebreaker. Every seeded concert
    shares one leg date, so the sort key ties on all five rows -- without a
    unique tiebreaker the two pages may overlap or drop a row."""
    token = await _mint(db)
    await _seed_concerts(db, 5)

    first = client.get("/api/v1/concerts?limit=2&offset=0", headers=_auth(token)).json()
    second = client.get("/api/v1/concerts?limit=2&offset=2", headers=_auth(token)).json()
    third = client.get("/api/v1/concerts?limit=2&offset=4", headers=_auth(token)).json()

    seen = [r["event_id"] for r in first["items"] + second["items"] + third["items"]]
    assert len(seen) == 5
    assert len(set(seen)) == 5, f"pages overlapped or dropped rows: {seen}"
    assert first["total"] == 5


async def test_limit_over_the_cap_is_422(client, db):
    token = await _mint(db)
    assert client.get("/api/v1/concerts?limit=501", headers=_auth(token)).status_code == 422


async def test_search_matches_title(client, db):
    token = await _mint(db)
    await _seed_concerts(db, 3)
    body = client.get("/api/v1/concerts?q=live%202", headers=_auth(token)).json()
    assert [r["event_id"] for r in body["items"]] == ["live-2"]
    assert body["total"] == 1


async def test_concert_detail_carries_the_draft_yaml(client, db):
    token = await _mint(db)
    await _seed_concerts(db, 1)
    body = client.get("/api/v1/concerts/live-0", headers=_auth(token)).json()
    assert body["event_id"] == "live-0"
    assert "draft_yaml" in body
    assert "title" in body["draft_yaml"]


async def test_unknown_event_id_is_404_json(client, db):
    token = await _mint(db)
    r = client.get("/api/v1/concerts/nope", headers=_auth(token))
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


async def test_concerts_require_a_token(client, db):
    await _seed_concerts(db, 1)
    assert client.get("/api/v1/concerts").status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_reads.py -q`
Expected: FAIL — 404 on `/api/v1/concerts`.

- [ ] **Step 3: Move `concert_search_text` to the db layer**

The API and `/discover` must match on the SAME definition — two would drift. Cut `concert_search_text` from `src/app/web/routes/discover.py` (currently around line 119), paste it into `src/app/db/core.py` in the `# ── Discover status ──` section, add it to `service.py`'s exports, and in `discover.py` replace the definition with an import from `app.db.service`. Its body is unchanged.

- [ ] **Step 4: Add the queries to `src/app/db/core.py`**

Append to the `# ── Discover status ──` section:

```python
async def api_concert_rows(
    session: AsyncSession,
    *,
    q: str = "",
    tag_handles: Sequence[str] = (),
    since: date | None = None,
    until: date | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Compact catalogue rows for the agent API, plus the pre-paging total.

    Filtered in PYTHON over the eager-loaded set, exactly as /discover does,
    not in SQL. `q` matches localized tag names, which a plain LIKE cannot
    reach without joining tags three times per locale, and the catalogue is
    dozens of rows. If it ever reaches thousands this becomes a real query;
    the envelope's shape does not change when it does.

    SORT IS TOTALLY ORDERED -- earliest live leg, then event_id, which is
    unique. Offset paging over a tie-prone key silently repeats and drops rows
    (see web/paging.py), and every concert seeded on one date is exactly that
    case.
    """
    res = await session.execute(
        select(Concert)
        .where(discoverable_concert_criterion())
        .options(selectinload(Concert.tags), selectinload(Concert.days),
                 selectinload(Concert.rounds))
    )
    concerts = list(res.scalars().unique())

    needle = q.strip().lower()
    wanted = {h for h in tag_handles if h}

    def _keep(c: Concert) -> bool:
        if needle and needle not in concert_search_text(c):
            return False
        if wanted and not wanted <= {t.slug for t in c.tags}:
            return False
        live = [d for d in c.days if not d.cancelled and d.starts_at_utc]
        if since and not any(d.starts_at_utc.date() >= since for d in live):
            return False
        if until and not any(d.starts_at_utc.date() <= until for d in live):
            return False
        return True

    kept = [c for c in concerts if _keep(c)]
    kept.sort(key=lambda c: (_first_leg_sort_key(c), c.event_id))
    total = len(kept)
    return [_api_concert_row(c) for c in kept[offset:offset + limit]], total


def _first_leg_sort_key(concert: Concert) -> tuple[int, float]:
    """Earliest live leg. A concert with no dated leg sorts LAST (the leading 1),
    matching /discover, where a dateless draft is still listed but never first."""
    live = [d.starts_at_utc for d in concert.days if not d.cancelled and d.starts_at_utc]
    return (0, min(live).timestamp()) if live else (1, 0.0)


def _api_concert_row(concert: Concert) -> dict:
    """One catalogue row. Datetimes are ISO-8601 UTC (invariant 1); plain dates
    carry no zone, because a performance date is a fact about the world rather
    than an instant to act by."""
    live = [d for d in concert.days if not d.cancelled]
    return {
        "event_id": concert.event_id,
        "title": concert.title,
        "title_en": concert.title_en,
        "leg_dates": [
            d.starts_at_utc.date().isoformat() for d in sorted(
                (d for d in live if d.starts_at_utc), key=lambda d: d.starts_at_utc
            )
        ],
        "tag_handles": sorted(t.slug for t in concert.tags),
        "venue_handles": sorted(
            {d.venue_tag.slug for d in live if d.venue_tag_id and d.venue_tag}
        ),
        "round_count": len(concert.rounds),
        "next_anchor_at": _next_anchor_iso(concert),
    }


def _next_anchor_iso(concert: Concert) -> str | None:
    """CATALOGUE-LEVEL, not per-viewer -- the earliest future moment among live
    rounds.

    Deliberately NOT concert_next_moment/_needs_you, which consult this user's
    outcomes and leg opt-outs: routed through those, an admin's token and an
    editor's token would report different facts about the same concert. None
    means the ladder holds no future anchor at all.
    """
    now = _now()
    cancelled = {d.id for d in concert.days if d.cancelled}
    moments: list[datetime] = []
    for r in concert.rounds:
        if is_round_cancelled(r, cancelled):
            continue
        for at in (r.opens_at_utc, r.closes_at_utc, r.results_at_utc, r.payment_due_utc):
            if at is not None and at > now:
                moments.append(at)
    return min(moments).isoformat() if moments else None


async def api_concert_detail(session: AsyncSession, event_id: str) -> dict | None:
    """The compact row plus `draft_yaml`.

    The YAML is the existing export verbatim -- the vocabulary the add-concert
    skill already writes and `parse_draft` already reads back. NOTE it carries
    JST timestamps because it is the AUTHORING format, while every other field
    here is UTC. That split is stated in the spec and is why the field is named
    `draft_yaml` (a document) rather than anything suggesting parsed data.
    """
    res = await session.execute(
        select(Concert)
        .where(Concert.event_id == event_id)
        .options(selectinload(Concert.tags), selectinload(Concert.days),
                 selectinload(Concert.rounds))
    )
    concert = res.scalars().unique().one_or_none()
    if concert is None:
        return None
    row = _api_concert_row(concert)
    row["draft_yaml"] = await concert_export_yaml(session, concert)
    return row
```

`concert_export_yaml` lives in `db/tags.py`. `core.py` must NOT import a sibling — that would reverse the dependency direction. Instead, have the ROUTE call `concert_export_yaml` and pass the text in: change the signature to `api_concert_detail(session, event_id, *, draft_yaml_for)` where the route supplies an awaitable, OR simpler and preferred — return the `Concert` too and let the route assemble. Use this signature instead:

```python
async def api_concert_detail(session: AsyncSession, event_id: str) -> tuple[dict, Concert] | None:
    """... returns (row, concert) so the CALLER can attach draft_yaml.

    core.py must not import db/tags.py -- feature modules import core, never the
    reverse. concert_export_yaml lives in tags.py, so the route composes the two
    rather than core reaching sideways.
    """
```
and drop the `row["draft_yaml"] = ...` line from it.

- [ ] **Step 5: Export through the facade**

Add `api_concert_rows`, `api_concert_detail`, `concert_search_text` to `service.py`'s core import block and to `__all__`.

- [ ] **Step 6: Add the endpoints to `src/app/web/routes/api.py`**

```python
@router.get("/concerts")
async def list_concerts(
    q: str = "",
    tag: list[str] = Query(default=[]),
    since: date | None = None,
    until: date | None = None,
    page: PageParams = Depends(page_params),
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The catalogue, for answering "do I already have this?".

    Any valid token: /discover is already public, so no tier is required.
    `tag` filters by HANDLE (Tag.slug), never by name -- invariant 3, names are
    not unique. `since`/`until` filter on LEG DATES.
    """
    rows, total = await api_concert_rows(
        session, q=q, tag_handles=tag, since=since, until=until,
        limit=page.limit, offset=page.offset,
    )
    return page_envelope(rows, total, page)


@router.get("/concerts/{event_id}")
async def get_concert(
    event_id: str,
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    found = await api_concert_detail(session, event_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such concert")
    row, concert = found
    row["draft_yaml"] = await concert_export_yaml(session, concert)
    return row
```

Add the needed imports at the top of `api.py`: `from datetime import date`, `from fastapi import Query`, `from app.db.service import api_concert_detail, api_concert_rows, concert_export_yaml`, `from app.web.paging import PageParams, page_envelope, page_params`.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_api_reads.py tests/test_discover.py -q`
Expected: PASS. `test_discover.py` must still pass — it exercises the `concert_search_text` you moved.

- [ ] **Step 8: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat(api): GET /api/v1/concerts list and detail

Sort is totally ordered (earliest live leg, then the unique event_id) because
offset paging over a tie-prone key silently repeats and drops rows. next_anchor
is catalogue-level and deliberately NOT concert_next_moment, which is per-user
and would make two tokens report different facts about one concert.
concert_search_text moved to the db layer so /discover and the API cannot
drift apart."
```

---

### Task 5: `/tags`

**Files:**
- Modify: `src/app/db/tags.py`, `src/app/db/service.py`, `src/app/web/routes/api.py`
- Test: `tests/test_api_reads.py` (append)

**Interfaces:**
- Consumes: `PageParams`, `page_envelope`, `api_user`.
- Produces: `async api_tag_rows(session, *, kind=None, limit, offset) -> tuple[list[dict], int]`

- [ ] **Step 1: Append the failing tests to `tests/test_api_reads.py`**

```python
async def test_tags_expose_the_vocabulary(client, db):
    from app.db.models import Tag
    from app.domain.types import TagKind

    token = await _mint(db)
    async with db() as s:
        s.add(Tag(name="ラブライブ！", name_en="Love Live!", kind=TagKind.FRANCHISE,
                  slug="love-live"))
        await s.commit()

    body = client.get("/api/v1/tags", headers=_auth(token)).json()
    row = next(r for r in body["items"] if r["handle"] == "love-live")
    assert row["name"] == "ラブライブ！"
    assert row["name_en"] == "Love Live!"
    assert row["kind"] == "franchise"
    assert body["total"] >= 1


async def test_tags_filter_by_kind(client, db):
    from app.db.models import Tag
    from app.domain.types import TagKind

    token = await _mint(db)
    async with db() as s:
        s.add(Tag(name="A", kind=TagKind.ARTIST, slug="a"))
        s.add(Tag(name="V", kind=TagKind.VENUE, slug="v"))
        await s.commit()

    body = client.get("/api/v1/tags?kind=venue", headers=_auth(token)).json()
    assert [r["handle"] for r in body["items"]] == ["v"]


async def test_tags_require_a_token(client):
    assert client.get("/api/v1/tags").status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_reads.py -k tags -q`
Expected: FAIL — 404.

- [ ] **Step 3: Add the query to `src/app/db/tags.py`**

```python
async def api_tag_rows(
    session: AsyncSession,
    *,
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """The tag vocabulary as JSON rows, plus the pre-paging total.

    Built from `current_tag_exports`, the ONE builder of the catalogue
    snapshot, so the API, the zip export and the differ all describe the same
    thing. Ordering comes from there too -- (kind, slug) -- and slug is unique,
    so the sort is already totally ordered and safe to page.

    Handles, never ids or names: invariant 3. This is what stops an agent
    inventing tag names that match nothing.
    """
    exports = await current_tag_exports(session)
    if kind:
        exports = [e for e in exports if e.kind == kind]
    total = len(exports)
    window = exports[offset:offset + limit]
    return [
        {
            "handle": e.handle,
            "name": e.name,
            "name_en": e.name_en,
            "name_zh": e.name_zh,
            "kind": e.kind,
            "parent": e.parent,
            "voiced_by": e.voiced_by,
            "members": list(e.members),
            "region": e.region,
            "city": e.city,
            "city_en": e.city_en,
            "city_zh": e.city_zh,
            "address": e.address,
            "location_url": e.location_url,
            "eventernote_url": e.eventernote_url,
        }
        for e in window
    ], total
```

- [ ] **Step 4: Export it and add the endpoint**

Add `api_tag_rows` to `service.py` (tags block + `__all__`). In `api.py`:

```python
@router.get("/tags")
async def list_tags(
    kind: str | None = None,
    page: PageParams = Depends(page_params),
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The vocabulary, served. Any valid token."""
    rows, total = await api_tag_rows(
        session, kind=kind, limit=page.limit, offset=page.offset
    )
    return page_envelope(rows, total, page)
```

- [ ] **Step 5: Run, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat(api): GET /api/v1/tags

Built from current_tag_exports, the one builder of the catalogue snapshot, so
the API cannot describe a different vocabulary from the zip export or the
differ. Handles, never names -- invariant 3."
```

---

### Task 6: `/leads` (admin)

**Files:**
- Modify: `src/app/db/discovery_events.py`, `src/app/db/service.py`, `src/app/web/routes/api.py`
- Test: `tests/test_api_reads.py` (append)

**Interfaces:**
- Consumes: `api_admin`, `PageParams`, `page_envelope`.
- Produces: `async api_lead_rows(session, *, limit, offset) -> tuple[list[dict], int]`

- [ ] **Step 1: Append the failing tests**

```python
async def test_leads_are_admin_only(client, db, monkeypatch):
    """The tier boundary, which is the thing most likely to be got wrong."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN))
    editor_token = await _mint(db, 99, "someone")
    assert client.get("/api/v1/leads", headers=_auth(editor_token)).status_code == 403

    admin_token = await _mint(db, ADMIN, "reiji")
    assert client.get("/api/v1/leads", headers=_auth(admin_token)).status_code == 200


async def test_leads_carry_date_is_deadline(client, db, monkeypatch):
    """An agent treating a 申込締切 as a performance date would file the wrong
    thing, so the flag must ride along."""
    from datetime import date

    from app.db.models import DiscoveredEvent

    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN))
    token = await _mint(db, ADMIN, "reiji")
    async with db() as s:
        s.add(DiscoveredEvent(
            source_event_id="imas:abc", source="imas", date_is_deadline=True,
            title="申込", event_date=date(2026, 10, 1), venue="",
        ))
        await s.commit()

    body = client.get("/api/v1/leads", headers=_auth(token)).json()
    row = next(r for r in body["items"] if r["source_event_id"] == "imas:abc")
    assert row["date_is_deadline"] is True
    assert row["source"] == "imas"
    assert row["event_date"] == "2026-10-01"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_reads.py -k leads -q`
Expected: FAIL — 404.

- [ ] **Step 3: Add the query to `src/app/db/discovery_events.py`**

```python
async def api_lead_rows(
    session: AsyncSession, *, limit: int = 200, offset: int = 0
) -> tuple[list[dict], int]:
    """Open leads as JSON rows, plus the pre-paging total.

    Built on `open_leads`, so the API and /admin/discoveries agree on what
    "open" means -- not dismissed, not bound to a concert, and deliberately NOT
    filtered on announced_at (announced is not triaged). Its sort is already
    (event_date DESC, id DESC), which is totally ordered and safe to page.

    `date_is_deadline` is not optional decoration: the imas feed's DTSTART is
    an application deadline, and an agent reading it as a performance date
    would file the wrong thing.
    """
    leads = await open_leads(session)
    total = len(leads)
    return [
        {
            "id": r.id,
            "source": r.source,
            "source_event_id": r.source_event_id,
            "title": r.title,
            "event_date": r.event_date.isoformat(),
            "date_is_deadline": r.date_is_deadline,
            "venue": r.venue,
            "first_seen_via_tag_id": r.first_seen_via_tag_id,
            "first_seen_at": r.first_seen_at.isoformat(),
            "announced_at": r.announced_at.isoformat() if r.announced_at else None,
        }
        for r in leads[offset:offset + limit]
    ], total
```

- [ ] **Step 4: Export it and add the endpoint**

Add `api_lead_rows` to `service.py`. In `api.py`:

```python
@router.get("/leads")
async def list_leads(
    page: PageParams = Depends(page_params),
    user: SessionUser = Depends(api_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The open discovery queue. ADMIN only -- same audience as
    /admin/discoveries, which is where these are triaged."""
    rows, total = await api_lead_rows(session, limit=page.limit, offset=page.offset)
    return page_envelope(rows, total, page)
```

- [ ] **Step 5: Run, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat(api): GET /api/v1/leads, admin only

Built on open_leads so the API and /admin/discoveries agree on what open
means. date_is_deadline rides along because an agent reading a 申込締切 as a
performance date would file the wrong thing."
```

---

### Task 7: `/drafts` (own)

**Files:**
- Modify: `src/app/db/drafts.py`, `src/app/db/service.py`, `src/app/web/routes/api.py`
- Test: `tests/test_api_reads.py` (append)

**Interfaces:**
- Consumes: `api_user`, `PageParams`, `page_envelope`.
- Produces:
  - `async api_draft_rows(session, user_id, *, limit, offset) -> tuple[list[dict], int]`
  - `async api_draft_detail(session, draft_id: int, user_id: int) -> dict | None`

- [ ] **Step 1: Append the failing tests**

```python
async def _seed_draft(db, user_id, text="title: x\nrounds: []\n", title="x"):
    from app.db.models import PendingDraft

    async with db() as s:
        await ensure_user(s, user_id, "someone")
        row = PendingDraft(draft_text=text, title=title, created_by=user_id)
        s.add(row)
        await s.commit()
        return row.id


async def test_draft_detail_carries_text_and_completion(client, db):
    token = await _mint(db)
    draft_id = await _seed_draft(db, ADMIN)
    body = client.get(f"/api/v1/drafts/{draft_id}", headers=_auth(token)).json()
    assert body["draft_text"].startswith("title:")
    assert body["completion_yaml"] == ""


async def test_another_users_draft_is_404_not_403(client, db):
    """Invariant 5: ownership checks 404. A 403 would confirm the row exists."""
    token = await _mint(db, ADMIN, "reiji")
    other = await _seed_draft(db, 777)
    assert client.get(f"/api/v1/drafts/{other}", headers=_auth(token)).status_code == 404


async def test_another_users_draft_is_not_listed(client, db):
    token = await _mint(db, ADMIN, "reiji")
    await _seed_draft(db, 777)
    mine = await _seed_draft(db, ADMIN)
    body = client.get("/api/v1/drafts", headers=_auth(token)).json()
    assert [r["id"] for r in body["items"]] == [mine]
    assert body["total"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_reads.py -k draft -q`
Expected: FAIL — 404.

- [ ] **Step 3: Add the queries to `src/app/db/drafts.py`**

```python
async def api_draft_rows(
    session: AsyncSession, user_id: int, *, limit: int = 200, offset: int = 0
) -> tuple[list[dict], int]:
    """This user's open drafts, plus the pre-paging total.

    Built on `pending_drafts`, which is already scoped to the pasting user and
    already ordered by id -- unique, so totally ordered and safe to page.
    """
    rows = await pending_drafts(session, user_id)
    total = len(rows)
    return [
        {
            "id": r.id,
            "title": r.title,
            "created_at": r.created_at.isoformat(),
            "has_rounds": "rounds: []" not in r.draft_text,
            "has_completion": bool(r.completion_yaml),
        }
        for r in rows[offset:offset + limit]
    ], total


async def api_draft_detail(
    session: AsyncSession, draft_id: int, user_id: int
) -> dict | None:
    """One draft's full text AND its completion evidence.

    Both together is the point: this is the iteration loop, where an agent
    reads its own draft alongside the evidence/rejection result rather than
    having a human relay either.

    None for another user's draft, which the caller renders as 404 -- invariant
    5's ownership rule. A 403 would confirm the row exists.
    """
    row = await session.get(PendingDraft, draft_id)
    if row is None or row.created_by != user_id:
        return None
    return {
        "id": row.id,
        "title": row.title,
        "created_at": row.created_at.isoformat(),
        "committed_at": row.committed_at.isoformat() if row.committed_at else None,
        "discarded_at": row.discarded_at.isoformat() if row.discarded_at else None,
        "draft_text": row.draft_text,
        "completion_yaml": row.completion_yaml,
    }
```

- [ ] **Step 4: Export them and add the endpoints**

Add both to `service.py`. In `api.py`:

```python
@router.get("/drafts")
async def list_drafts(
    page: PageParams = Depends(page_params),
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The token holder's OWN open drafts. Two editors triaging their own
    batches at once is the expected case, not an exotic one."""
    rows, total = await api_draft_rows(
        session, user.id, limit=page.limit, offset=page.offset
    )
    return page_envelope(rows, total, page)


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: int,
    user: SessionUser = Depends(api_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await api_draft_detail(session, draft_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    return row
```

- [ ] **Step 5: Run, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat(api): GET /api/v1/drafts list and detail

Detail returns draft_text AND completion_yaml together -- that pairing is the
iteration loop, letting an agent read its own draft beside the evidence result
without a human relaying either. Another user's draft answers 404, not 403,
per invariant 5's ownership rule."
```

---

### Task 8: Minting the token from Preferences

**Files:**
- Modify: `src/app/web/routes/preferences.py`
- Modify: `src/app/web/templates/preferences.html`
- Test: `tests/test_api_tokens.py` (append)

**Interfaces:**
- Consumes: `generate_api_token` (Task 1).
- Produces: `POST /me/api-token`.

- [ ] **Step 1: Append the failing tests to `tests/test_api_tokens.py`**

```python
async def test_mint_route_shows_the_token_once(client_pref, db):
    """The raw value is displayed exactly once and is unrecoverable after.
    Recovery is 'mint a new one', which is the whole point of storing a hash."""
    r = client_pref.post("/me/api-token", data={})
    assert r.status_code in (200, 303)
    page = client_pref.get("/preferences").text
    assert "api_token=" not in page  # never sticky in the URL or re-rendered


async def test_mint_route_requires_a_session(client_pref_anon):
    r = client_pref_anon.post("/me/api-token", data={})
    assert r.status_code in (303, 401, 403)
```

Build `client_pref` / `client_pref_anon` with the same `client` + `login_as` pattern used in `tests/test_preferences_page.py` — copy that file's fixtures rather than inventing new ones.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_tokens.py -k mint -q`
Expected: FAIL — 405/404 on `/me/api-token`.

- [ ] **Step 3: Add the route to `src/app/web/routes/preferences.py`**

Mirror `POST /me/calendar-feed` in `routes/calendar.py`: `require_user`, call `generate_api_token`, `await session.commit()`, then redirect back to `/preferences` with the raw token in a one-shot query parameter, exactly as the calendar mint does with `feed_token`. Use the parameter name `api_token`.

- [ ] **Step 4: Render it in `preferences.html`**

Beside the existing calendar-feed block (around line 346), add a section with a `<form method="post" action="/me/api-token">` and a "Generate API token" button; when `api_token` is present in the request query, show the raw value once with the existing copy-button markup from `_feed_links.html`, plus a line stating it will not be shown again.

Wrap all visible copy in `_()` — Preferences IS translated, unlike the API itself — and add the new msgids to BOTH `src/app/translations/ja/LC_MESSAGES/messages.po` and `.../zh/...`. `tests/test_i18n_catalogues.py` fails on any untranslated msgid.

- [ ] **Step 5: Run, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat(api): mint the agent token from Preferences

Shown exactly once, as the calendar feed's token is: only the hash is stored,
so recovery is minting a new one rather than looking the old one up."
```

---

### Task 9: Document the API

**Files:**
- Create: `docs/agent-api.md`
- Modify: `CLAUDE.md` (Layout section, the `src/app/web/` bullet)

- [ ] **Step 1: Write `docs/agent-api.md`**

One page: how to mint a token, the `Authorization: Bearer` header, every endpoint with its parameters and an example response, the paging envelope, the error table, and — stated loudly — that the JSON envelope is UTC while `draft_yaml` is JST.

- [ ] **Step 2: Add one line to CLAUDE.md's Layout**

Under the `src/app/web/` bullet: `routes/api.py` is the read-only agent API at `/api/v1`, bearer-token authenticated, GET only; point at `docs/agent-api.md` and the spec.

- [ ] **Step 3: Commit**

```bash
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "docs: the agent read API"
```

---

## Self-Review

**Spec coverage:** whoami → T2. concerts list/detail → T4. tags → T5. leads → T6. drafts → T7. Bearer auth + token-acts-as-user → T1/T2. Read-only sweep → T2. JSON errors → T2. Paging + tiebreaker rule → T3, asserted in T4. Migration → T1. Mint UI → T8. delivery_log exclusion → satisfied by omission; no task exposes it. Times (UTC envelope / JST `draft_yaml`) → T4 and T9.

**Known deviation, called out rather than hidden:** Task 4's first draft of `api_concert_detail` had `core.py` calling `concert_export_yaml` from `tags.py`, which reverses the layer's dependency direction (feature modules import `core`, never the reverse — `tests/test_service_facade.py::test_core_does_not_depend_on_any_feature_module` would fail). Step 4 corrects it to return `(row, concert)` so the route composes the two. Implement the corrected signature.

**Types:** `api_*_rows` all return `tuple[list[dict], int]`; `api_concert_detail` returns `tuple[dict, Concert] | None`; `api_draft_detail` returns `dict | None`. `PageParams(limit, offset)` is used identically in every endpoint.
