"""`db/service.py` must keep exporting everything the db layer defines.

CLAUDE.md's architecture rule is that bot and web hold no business logic and
call `db/service.py`. After the 8,063-line original was split, that rule is
kept literally true by a facade -- so the facade going stale is not a tidiness
problem, it is the architecture quietly breaking: a function added to
`db/tags.py` and imported by a route as `from app.db.service import ...` fails
at import time, and only for the code path that happens to import it.

Nothing here re-tests behaviour. It tests that the seam is complete, which is
exactly the property a human maintaining a 238-name re-export list gets wrong.
"""

import ast
import importlib
from pathlib import Path

import pytest

from app.db import service

PKG = Path(__file__).resolve().parents[1] / "src" / "app" / "db"

# The modules the facade fronts. `models`/`session` are NOT here: they are
# imported directly (`from app.db.models import Concert`) everywhere and were
# never part of service's surface.
FEATURE_MODULES = [
    "core", "tags", "drafts", "setup_flow", "rehearsal", "discovery_events",
    "broadcast", "delivery", "calendar_feed", "translation_gaps", "ops_alerts",
    "phrases", "audit", "venues", "tokens",
]


def _defined_in(stem: str) -> set[str]:
    """Top-level names a module DEFINES -- not ones it imported from a sibling.

    Read from the source with ast rather than off the imported module object,
    because `dir()` cannot tell "defined here" from "imported here", and the
    feature modules genuinely import from each other (venues from tags, drafts
    from discovery_events).
    """
    tree = ast.parse((PKG / f"{stem}.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@pytest.mark.parametrize("stem", FEATURE_MODULES)
def test_every_name_a_module_defines_is_reachable_through_the_facade(stem: str) -> None:
    missing = sorted(n for n in _defined_in(stem) if not hasattr(service, n))
    assert missing == [], (
        f"db/{stem}.py defines {missing} but db/service.py does not re-export "
        f"them. Add them to the `from app.db.{stem} import (...)` block and to "
        "__all__, or bot/web cannot reach them."
    )


def test_all_is_complete_and_honest() -> None:
    """__all__ is what makes ruff accept the re-exports, so a name present as an
    attribute but absent from __all__ is one deletion away from being pruned."""
    exported = {n for stem in FEATURE_MODULES for n in _defined_in(stem)}
    assert exported - set(service.__all__) == set(), "names re-exported but missing from __all__"
    assert set(service.__all__) - exported == set(), "__all__ names nothing defines any more"


def test_the_facade_holds_no_logic_of_its_own() -> None:
    """It must stay a re-export list. A definition landing here is how an
    8,000-line module grows back."""
    tree = ast.parse((PKG / "service.py").read_text(encoding="utf-8"))
    defs = [
        n.name for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    assert defs == [], f"db/service.py defines {defs}; it is a facade -- put it in a feature module"


def test_no_feature_module_imports_the_facade() -> None:
    """The property that makes the facade safe.

    The modules import from `core` (and a couple from each other); the facade
    imports from all of them and is imported by none. If a feature module
    imported `app.db.service`, that would be a cycle -- and one that only
    surfaces depending on which module a process happens to import FIRST,
    which is the exact fragility the facade was chosen to avoid.
    """
    offenders = []
    for stem in FEATURE_MODULES:
        tree = ast.parse((PKG / f"{stem}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("db.service"):
                offenders.append(stem)
    assert offenders == [], f"{offenders} import the facade; import app.db.core (or the sibling)"


@pytest.mark.parametrize("stem", FEATURE_MODULES)
def test_each_module_imports_standalone(stem: str) -> None:
    """Importing a feature module FIRST, with no facade in the way, must work.

    This is the concrete failure the facade prevents: had the modules instead
    been imported from the bottom of a still-fat service.py, `import
    app.db.tags` in a fresh interpreter would reach a half-initialised
    `app.db.service` and raise ImportError.
    """
    importlib.import_module(f"app.db.{stem}")


def test_nothing_reaches_through_the_facade_for_an_incidental_import() -> None:
    """`service.<name>` must name something the db layer DEFINES.

    The old single-file module imported ~100 names (`select`, `Concert`,
    `Anchor`, `settings`, ...) and so re-exported every one of them by
    accident. The facade exports only what the layer defines, which is the
    point -- but it means `service.settings` stopped resolving, and exactly
    one caller was relying on it (test_ops_alerts.py, since fixed to read
    `app.config` like the rest of the suite).

    That was found by a full suite run. This finds it at the seam instead, and
    the fix is never to widen the facade: import the name from where it
    actually lives.
    """
    import ast as _ast

    exported = {n for stem in FEATURE_MODULES for n in _defined_in(stem)}
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for base in ("src", "tests"):
        for path in (root / base).rglob("*.py"):
            tree = _ast.parse(path.read_text(encoding="utf-8"))
            for node in _ast.walk(tree):
                if (
                    isinstance(node, _ast.Attribute)
                    and isinstance(node.value, _ast.Name)
                    and node.value.id in {"service", "svc"}
                    and node.attr not in exported
                    and not node.attr.startswith("__")
                ):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno} service.{node.attr}")
    assert offenders == [], (
        f"these read a name off `service` that the db layer does not define: {offenders}"
    )


def test_core_does_not_depend_on_any_feature_module() -> None:
    """The measured property that made the split safe in the first place: the
    engine references nothing in the thirteen modules lifted out of it. Keep it
    -- a core that reaches back into a feature module re-creates the cycle."""
    tree = ast.parse((PKG / "core.py").read_text(encoding="utf-8"))
    siblings = {f"app.db.{s}" for s in FEATURE_MODULES if s != "core"}
    bad = [
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in siblings
    ]
    assert bad == [], f"db/core.py imports {bad}; the dependency must point the other way"
