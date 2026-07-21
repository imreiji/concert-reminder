"""Check the newest Alembic revision against this repo's known migration traps.

Usage (from the repo root):
    uv run python .claude/skills/dekimasen/scripts/check_migration.py [revision.py]

With no argument it checks the most recently modified file in
alembic/versions/. Exit code 0 = clean, 1 = at least one problem.

What it checks, and why (see references/migrations.md for the full story):

1. No ``UTCDateTime`` reference and no ``import app.db.models`` — autogenerate
   emits the TypeDecorator, which must be replaced with ``sa.DateTime()``.
2. ``drop_constraint`` only appears alongside ``naming_convention`` — the live
   DB predates the naming convention, so dropping an anonymous constraint
   passes every metadata-built test and dies on the server.
3. ``alembic.ini`` (and this revision file) are ASCII-only — the owner's
   Windows machine uses a GBK locale; non-ASCII in configs crashes it.

This is a guardrail, not a substitute for reading the revision.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve()
for parent in REPO_ROOT.parents:
    if (parent / "alembic.ini").exists():
        REPO_ROOT = parent
        break
else:
    print("error: could not locate repo root (no alembic.ini found upward)")
    sys.exit(1)

VERSIONS = REPO_ROOT / "alembic" / "versions"


def newest_revision() -> Path | None:
    files = [p for p in VERSIONS.glob("*.py") if p.name != "__init__.py"]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def non_ascii_lines(path: Path) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(path.read_bytes().splitlines(), 1):
        if any(b > 127 for b in line):
            out.append((i, line.decode("utf-8", errors="replace").strip()))
    return out


def main() -> int:
    if len(sys.argv) > 1:
        rev = Path(sys.argv[1])
        if not rev.is_absolute():
            rev = REPO_ROOT / rev
    else:
        rev = newest_revision()
    if rev is None or not rev.exists():
        print("error: no revision file found")
        return 1

    text = rev.read_text(encoding="utf-8")
    problems: list[str] = []

    # 1. UTCDateTime must not survive into a committed revision.
    for i, line in enumerate(text.splitlines(), 1):
        if "UTCDateTime" in line:
            problems.append(
                f"{rev.name}:{i}: UTCDateTime reference — replace with"
                " sa.DateTime() (see references/migrations.md)"
            )
        if re.search(r"^\s*import\s+app\.db\.models", line):
            problems.append(
                f"{rev.name}:{i}: 'import app.db.models' — remove it"
                " (only needed by the UTCDateTime reference above)"
            )

    # 2. drop_constraint without the naming convention passes tests and
    #    fails on the live DB's anonymous legacy constraints.
    if "drop_constraint" in text and "naming_convention" not in text:
        problems.append(
            f"{rev.name}: drop_constraint without naming_convention= in"
            " batch_alter_table — this exact pattern shipped a server-only"
            " crash once. Also add a legacy-DDL fixture test (see"
            " tests/test_migration_legacy_anonymous_constraints.py)."
        )

    # 3. ASCII-only configs (GBK locale on the owner's Windows machine).
    for path in (rev, REPO_ROOT / "alembic.ini"):
        for i, line in enumerate(non_ascii_lines(path), 1):
            problems.append(
                f"{path.name}:{line[0]}: non-ASCII byte(s): {line[1]!r}"
            )

    if problems:
        print(f"checked {rev.name}: {len(problems)} problem(s)\n")
        for p in problems:
            print(f"  FAIL  {p}")
        return 1

    print(f"checked {rev.name}: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
