"""The parallel default must stay in `pyproject.toml`, with its plugin beside it.

The suite takes 591s serially and 100s across 16 workers (both measured
back-to-back; the full table is in `pyproject.toml`'s own comment). 591s is
past the 600s ceiling of the agent harness that runs verification here, so a
serial run does not fail -- it gets BACKGROUNDED, and stops being something
anyone waits for. That failure has a history: the convention "run the suite in
the foreground with an explicit timeout" was written down, re-stated and
re-confirmed three times, and went on being missed, because prose does not
survive habit. `addopts = "-n auto"` is that convention made mechanical.

WHAT THESE TESTS PROVE, exactly: that the configuration still ASKS for
parallelism, and that the plugin it names is still a declared dependency. They
do NOT prove the suite parallelises -- a test cannot honestly assert that from
inside the run it is asserting about, since `-n0` is a supported way to invoke
it and any check on PYTEST_XDIST_WORKER would then fail for a legitimate
caller. The mutation each survives is named per test below; the evidence that
parallel execution is CORRECT here (3,023 passing at 4, 8 and 16 workers) is a
measurement, recorded in `pyproject.toml` and WISHLIST's Shipped entry.
"""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_pytest_defaults_to_parallel_workers():
    """Survives: someone deleting `addopts`, or setting it to a serial `-n0`.

    Does not survive being read as proof that workers ran -- see the module
    docstring.
    """
    addopts = _pyproject()["tool"]["pytest"]["ini_options"].get("addopts", "")

    match = re.search(r"-n\s*(auto|logical|\d+)", addopts)
    assert match, (
        "pyproject's [tool.pytest.ini_options] addopts no longer requests xdist "
        f"workers (addopts={addopts!r}). A serial run of this suite measures "
        "591s, past the 600s foreground ceiling, so it gets backgrounded "
        "instead of waited on."
    )
    assert match.group(1) != "0", "-n0 is the opt-out, not a default"


def test_the_xdist_plugin_is_a_declared_dev_dependency():
    """Survives: the default surviving while the plugin that serves it is dropped.

    Those two halves live in one file but nothing else couples them, and the
    failure is not a slow suite -- pytest exits with a usage error on an
    unrecognised `-n`, so a fresh checkout cannot run its tests at all.
    """
    dev = _pyproject()["dependency-groups"]["dev"]

    assert any(spec.startswith("pytest-xdist") for spec in dev), (
        f"addopts asks for xdist workers but pytest-xdist is not in the dev group: {dev}"
    )
