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
