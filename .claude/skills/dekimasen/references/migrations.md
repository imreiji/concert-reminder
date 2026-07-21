# Migrations (SQLite gotchas — these have bitten before)


- `Base.metadata` has a NAMING_CONVENTION; keep it. SQLite runs migrations
  in batch (table-rebuild) mode which refuses unnamed constraints.
- **The live DB predates that convention, and tests cannot see it.** Tables
  created by older migrations (`concerts`, `tags`) carry anonymous
  constraints -- a bare `FOREIGN KEY(created_by) REFERENCES users(discord_id)`,
  an unnamed `UNIQUE (name)` -- while tables created later (`concert_audit`)
  are named. Every test DB is built from `Base.metadata`, so everything is
  named there and the divergence is invisible to the whole suite. A migration
  calling `drop_constraint` therefore passes locally and dies on the server
  with `ValueError: No such constraint: 'fk_...'` (this shipped once).
  Any migration touching `drop_constraint` must (a) pass
  `naming_convention=NAMING_CONVENTION` into `batch_alter_table` so Alembic
  names anonymous constraints during reflection, and (b) be tested against a
  legacy-shaped fixture, not a metadata-built one -- see
  `tests/test_migration_legacy_anonymous_constraints.py`, which hand-writes
  the real server DDL. Its fixture covers only the four tables that migration
  touched; a migration hitting other legacy tables needs its own DDL.
- After autogenerate, ALWAYS edit the revision: replace
  `app.db.models.UTCDateTime()` with `sa.DateTime()` and remove the
  `import app.db.models` line.
- `alembic.ini` and other config files must stay ASCII-only (the owner's
  Windows machine uses a GBK locale; em-dashes in configs crash it).
- The dedupe index on reminder_queue uses coalesce() because SQLite treats
  NULLs as distinct in unique indexes. Don't "simplify" it.


## The migration ritual

1. `uv run alembic revision --autogenerate -m "msg"`
2. Edit the generated revision:
   - Replace every `app.db.models.UTCDateTime()` with `sa.DateTime()` and
     delete the `import app.db.models` line.
   - If it calls `drop_constraint` anywhere, pass
     `naming_convention=NAMING_CONVENTION` into `batch_alter_table` AND add a
     legacy-shaped DDL fixture test (see
     `tests/test_migration_legacy_anonymous_constraints.py`) — the test DB is
     metadata-built and cannot catch this class of failure.
3. Run the checker: `uv run python .claude/skills/dekimasen/scripts/check_migration.py`
   It verifies the newest revision has no `UTCDateTime` reference, no
   `import app.db.models`, flags unguarded `drop_constraint`, and checks
   `alembic.ini` is still ASCII-only. It is a guardrail, not a replacement
   for reading the revision.
4. `uv run alembic upgrade head`, then `uv run pytest -q`.
