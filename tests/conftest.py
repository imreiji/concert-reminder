"""Suite-wide fixtures.

Both exist to decouple tests from ambient state -- one from each other, one
from the developer's own machine.

The first: the scheduler keeps its health cadence in a module-level counter,
and several unrelated test files call `tick()`. Without a reset, whether a
given test's tick lands on a multiple of HEALTH_EVERY_N_TICKS depends on how
many ticks ALL the earlier files happened to run -- so a test that never meant
to touch monitoring can suddenly run the real health evaluation (real
disk_usage, a real marker read, real admin ids, real Notification rows)
against its own session. That is a mystery failure waiting to happen; pin the
counter instead.

The second: `settings` is a pydantic-settings object that reads `.env` at
import, so config values leak in from whatever machine the suite runs on. See
`_pin_discord_token`.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_tick_count():
    import app.scheduler.loop as loop_mod

    loop_mod._tick_count = 0
    yield
    loop_mod._tick_count = 0


@pytest.fixture(autouse=True)
def _pin_discord_token(monkeypatch):
    """Force `bot_enabled` off by default instead of inheriting it from `.env`.

    Three places used to assert that `discord_token` "defaults to ''" in
    tests. It does not: `Settings` reads `.env`, and on the owner's machine
    that file holds a real token, so `bot_enabled` was True and any route
    short-circuiting on it instead fell through into live discord.py calls
    (`/me/test-dm` died on `_MissingSentinel.is_set`, since the bot is never
    logged in). CI has no `.env`, so it passed there and failed only locally
    -- the worst shape for a test failure, because the suite disagrees with
    itself depending on who runs it.

    Pin it rather than assume it. Tests that need the bot ON set it
    themselves; test_ops_alerts.py already pins it in both directions and
    explains why plain attribute assignment works on a pydantic model.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "discord_token", "")


class RealNetworkAttempt(BaseException):
    """A test tried to open a real socket. Always a bug in the test.

    BaseException, not AssertionError, and deliberately: this is not an
    application error and no application `except Exception` may swallow it.
    It shipped as AssertionError first and `sweep_one_tag`'s `except Exception`
    -- which is correct and load-bearing, since a `2026年2月30` page raises
    ValueError from the parse -- duly caught it, turning a test that reached
    the real internet into a quiet "the sweep failed" assertion several frames
    away from the cause. Same category as KeyboardInterrupt and SystemExit:
    control flow that application code has no business handling. `pytest.raises`
    accepts BaseException, so the guard's own tests are unaffected, and nothing
    in production changes -- this class exists only under the test fixture."""


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Make a test that would actually hit the internet FAIL, loudly.

    The suite's protection against reaching ramen.events and eventernote.com
    was, until this fixture, every author remembering to stub the fetch. That
    is the worst shape of test safety: forgetting it does not fail, it
    SUCCEEDS -- on any machine with a working connection -- and fails only on
    an offline laptop or a sandboxed CI runner, i.e. it disagrees with itself
    depending on who runs it, exactly like `_pin_discord_token` above. It also
    silently makes a third party's uptime a dependency of `pytest -q`, and
    sends real traffic to sites this project scrapes by permission.

    The seam is `transport`. Every legitimate test either patches the named
    wrapper (`fetch_actor_events`, `fetch_ramen_html`) or passes an explicit
    `httpx.MockTransport`; only a `transport=None` call constructs a real
    connection pool. So the rule is exactly "no client without a transport",
    which needs no allow-list and stays correct as tests are added.

    Guarded in two places, and the second is NOT a second safety net:

    * `httpx.AsyncClient.__init__` is the CHOKEPOINT, and it alone is what
      makes the guard sound -- `fetching.fetch_html` and `web/auth.py`'s two
      OAuth calls all end there, so nothing routes around it, including code
      written later. Remove the walk below and every network path is still
      blocked.
    * The `fetch_html` walk exists for the MESSAGE. The chokepoint can only say
      "someone built a real client"; this says WHICH fetch went unstubbed, with
      the URL, which is the difference between a one-line fix and a hunt.
      `fetch_html` is re-exported by name into `app.discovery` and
      `app.web.routes.imports` (`from app.fetching import fetch_html`), so
      every module holding a reference is found by walking `sys.modules`
      rather than by keeping a list here that would rot the next time it is
      imported somewhere new. Patching `app.fetching` itself is also what makes
      the walk self-healing: a module imported AFTER this fixture runs binds
      the already-guarded function. `test_network_guard.py` pins the message,
      not just the raise, or this half would be untested by construction.
    """
    import sys

    import httpx

    from app import fetching

    real_init = httpx.AsyncClient.__init__
    real_fetch = fetching.fetch_html

    def guarded_init(self, *args, transport=None, **kwargs):
        if transport is None:
            raise RealNetworkAttempt(
                "a test constructed a real httpx.AsyncClient. Pass "
                "transport=httpx.MockTransport(...), or patch the fetch "
                "wrapper (fetch_actor_events / fetch_ramen_html) instead."
            )
        return real_init(self, *args, transport=transport, **kwargs)

    async def guarded_fetch(url, *, transport=None, **kwargs):
        if transport is None:
            raise RealNetworkAttempt(
                f"a test called fetch_html({url!r}) with no transport, which "
                "would hit the real host. Patch the caller's fetch wrapper, or "
                "pass transport=httpx.MockTransport(...)."
            )
        return await real_fetch(url, transport=transport, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", guarded_init)
    for module in list(sys.modules.values()):
        if getattr(module, "fetch_html", None) is real_fetch:
            monkeypatch.setattr(module, "fetch_html", guarded_fetch)
