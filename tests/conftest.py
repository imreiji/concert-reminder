"""Suite-wide fixtures.

The only one here exists to decouple test files from each other: the scheduler
keeps its health cadence in a module-level counter, and several unrelated test
files call `tick()`. Without a reset, whether a given test's tick lands on a
multiple of HEALTH_EVERY_N_TICKS depends on how many ticks ALL the earlier
files happened to run -- so a test that never meant to touch monitoring can
suddenly run the real health evaluation (real disk_usage, a real marker read,
real admin ids, real Notification rows) against its own session. That is a
mystery failure waiting to happen; pin the counter instead.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_tick_count():
    import app.scheduler.loop as loop_mod

    loop_mod._tick_count = 0
    yield
    loop_mod._tick_count = 0
