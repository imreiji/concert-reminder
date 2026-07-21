---
name: dekimasen
description: Deep project knowledge for the dekimasen.app (concert-reminder) codebase — the full invariants, architecture warnings, migration/i18n/UI/testing rules, and shipping rituals that CLAUDE.md only summarizes. Use this skill for ANY non-trivial work in this repo. ALWAYS consult the matching reference before writing code that touches migrations or the DB schema, translatable strings or locales, templates/CSS/UI copy, the reminder queue or outcomes, tags/subscriptions, auth/routes, or tests — and before planning, estimating, or discussing any feature. Every rule in the references exists because breaking it shipped a real bug.
---

# dekimasen — project skill

CLAUDE.md gives you the map: architecture, commands, invariant one-liners,
owner context. This skill holds the depth. The contract is simple: **before
you touch an area, read its reference in full.** The references are not
background reading — they are the difference between changes that ship and
changes that pass every test locally and then break production (several
rules in there exist precisely because the test suite structurally cannot
catch the failure they prevent).

## Routing table

| You are about to… | Read first |
|---|---|
| Touch anything in an area listed by an invariant one-liner in CLAUDE.md (timezones, queue sync, group tags, notifications, auth/sessions, event_id, injection boundaries, subscriptions) | `references/invariants.md` — the full text of that invariant |
| Add/move/edit modules, routes, or wonder "where does this logic go" | `references/architecture.md` |
| Create or edit an Alembic migration, or change `db/models.py` schema | `references/migrations.md`, then run `scripts/check_migration.py` on the generated revision |
| Add, edit, or delete ANY user-visible string; touch `.po` files, `i18n.py`, or locale logic | `references/i18n.md` |
| Edit templates, `style.css`, page structure, copy, or anything the user sees | `references/ui-conventions.md` (and the concept demos it names) |
| Write or modify tests | `references/testing.md` |
| Plan, scope, or discuss a feature; ship one; deploy | `references/workflows.md` (feature ritual, WISHLIST.md maintenance, deploy) |

Multiple rows usually apply to one task — a typical feature touches
architecture + invariants + UI + i18n + testing. Read all that apply, but
only those: the point of this layout is that you never pay for the
migration gotchas while editing CSS.

## Non-negotiables that apply to every change

- `uv run pytest -q` and `uv run ruff check .` must pass before any commit.
- The DB stores aware UTC only; JST at the form boundary; dual display.
- Bot and web layers never contain business logic — it lives in
  `db/service.py`.
- The owner is technically comfortable but rusty at coding, on Windows
  PowerShell 5.1: no `&&` in commands you give him (use `;` or separate
  lines), explain the why behind non-obvious changes, and on UX questions
  ask rather than assume.
