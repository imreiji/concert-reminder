# AGENTS.md

Guidance for coding agents (Codex and anything else that reads `AGENTS.md`)
working in this repository.

This file is deliberately SHORT. It used to be a full copy of `CLAUDE.md`,
and the copy went stale within a single feature — two 500-line prose files
describing the same rules will always drift, and the one you are reading is
the one that gets forgotten. So the rules live in exactly one place now:

- **`CLAUDE.md`** — the summary: architecture map, commands, the eight
  invariants as one-liners, owner context. Read this first, in full. It is
  ~100 lines.
- **`.claude/skills/dekimasen/references/*.md`** — the depth. Plain markdown;
  you do not need Claude's skill machinery to read them. Every rule in there
  exists because breaking it shipped a real bug, and several describe
  failures the test suite structurally cannot catch.

## Which reference to read

Read the one matching what you are about to touch, in full, BEFORE you write
code. Most tasks hit two or three.

| You are about to… | Read |
|---|---|
| Touch an area covered by an invariant one-liner in CLAUDE.md (timezones, queue sync, group tags, notifications, auth/sessions, event_id, injection boundaries, subscriptions) | `references/invariants.md` |
| Add/move/edit modules or routes, or wonder where logic belongs | `references/architecture.md` |
| Create or edit an Alembic migration, or change `db/models.py` | `references/migrations.md`, then run `.claude/skills/dekimasen/scripts/check_migration.py` on the revision |
| Add, edit, or delete ANY user-visible string; touch `.po` files or locale logic | `references/i18n.md` |
| Edit templates, `style.css`, page structure, or copy | `references/ui-conventions.md` |
| Write or modify tests | `references/testing.md` |
| Plan, scope, or ship a feature; deploy | `references/workflows.md` |

## Non-negotiables regardless of what you are doing

- `uv run pytest -q` and `uv run ruff check .` MUST both pass before any
  commit. No exceptions.
- The DB stores aware UTC only; JST at the form boundary; display is dual.
- Bot and web layers never contain business logic — it lives in
  `db/service.py`.
- Never commit `.env`.
- The owner is technically comfortable but rusty at coding, on Windows
  PowerShell 5.1: no `&&` chaining in commands you hand him (use `;` or
  separate lines), explain the why behind non-obvious changes, and on UX
  questions ask rather than assume.

## If you change a rule

Update the reference file, not this one. If the change is significant enough
that the summary is now wrong, update `CLAUDE.md` too. Do not restore prose
to this file — that is the failure mode it was rewritten to prevent.
