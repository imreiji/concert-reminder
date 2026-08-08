"""Paging parameters and the response envelope.

The cap is a 422 rather than a silent clamp on purpose: an agent that asked for
5000 and quietly received 500 would page as though it had the whole set.

`page_params` is written as a FastAPI dependency (`Depends(page_params)`), but
it is also just a plain `async def` with no `Request`/`Depends` machinery of
its own inside it -- it only branches on its own `limit`/`offset` arguments
and raises a plain `HTTPException`. Calling it directly, unmediated by
FastAPI's dependency resolution, therefore executes exactly the same code a
real request would and is a meaningful test on its own; it was verified by
also driving the same cases through a real route with `TestClient` below,
which additionally confirms FastAPI actually turns a dependency's raised
`HTTPException` into a 422 HTTP response (the direct-call tests cannot show
that, since they never go through FastAPI's exception handling at all).
"""

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

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


# --- Driven through a real route, not called directly -----------------------
#
# The tests above call `page_params` as a bare coroutine function, which never
# touches FastAPI's dependency-injection or exception-handling machinery. That
# is enough to prove the function's own branching is correct, but not enough
# to prove a real `GET` with a bad `limit` actually comes back as an HTTP 422
# -- FastAPI has to (a) resolve `page_params` as a dependency of a route and
# (b) catch the `HTTPException` it raises and turn it into a response. Neither
# is exercised above, so it is confirmed here with a throwaway probe route.


def _probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(params: PageParams = Depends(page_params)) -> dict:
        return {"limit": params.limit, "offset": params.offset}

    return app


@pytest.fixture()
def client():
    return TestClient(_probe_app())


def test_defaults_through_a_real_request(client):
    r = client.get("/probe")
    assert r.status_code == 200
    assert r.json() == {"limit": DEFAULT_LIMIT, "offset": 0}


def test_accepts_the_cap_exactly_through_a_real_request(client):
    r = client.get("/probe", params={"limit": MAX_LIMIT})
    assert r.status_code == 200
    assert r.json()["limit"] == MAX_LIMIT


@pytest.mark.parametrize("limit", [MAX_LIMIT + 1, 0, -1])
def test_bad_limit_is_422_through_a_real_request(client, limit):
    r = client.get("/probe", params={"limit": limit})
    assert r.status_code == 422


def test_negative_offset_is_422_through_a_real_request(client):
    r = client.get("/probe", params={"offset": -1})
    assert r.status_code == 422
