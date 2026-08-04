# Onboarding-skip fix + dialog drag-close fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship WISHLIST #1 (a bot-first user never sees onboarding) and #8 (the venue quick-create dialog closes on a drag from inside to outside) on one branch, `fix-onboarding-skip-and-dialog-drag`.

**Architecture:** #1 adds one honest column, `User.welcomed_at`, stamped when the wizard completes and checked by the OAuth callback instead of row absence; a hand-written migration backfills every existing row as done. #8 deletes two local naive backdrop-click handlers that bypass base.html's drag-safe global guard, and pins the deletion with a template sweep test.

**Tech Stack:** SQLAlchemy async + Alembic (SQLite batch mode), FastAPI, pytest-asyncio auto mode, vanilla JS in Jinja templates.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` MUST pass before any commit.
- Migration files stay ASCII-only (owner's GBK-locale Windows machine).
- In migrations, use `sa.DateTime()`, never `app.db.models.UTCDateTime()` (CLAUDE.md Migrations section).
- The DB stores aware UTC only (invariant 1): Python-side writes use `datetime.now(UTC)`.
- No new translatable strings are introduced anywhere in this plan — no catalogue work.
- Commit messages end with the project's standard trailers (see session instructions).

## Root causes (verified against the tree 2026-08-03 — do not re-derive)

**#8:** Commit `e23943d` (2026-07-30) made backdrop-click-close drag-safe *globally* in `base.html:146-157` (a `pointerdown` capture listener records `pressedOn`; the click handler closes a dialog only when `pressedOn === e.target`). Its commit message claims it covered "every dialog in the app", but it only touched base.html. Two dialogs carry their own LOCAL naive handlers that predate it and fire regardless of the global guard:
- `src/app/web/templates/_venue_create_dialog.html:79-83`
- `src/app/web/templates/_tag_create_dialog.html:129-132`

Both are `dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); })`. A click event's target is the nearest common ancestor of the mousedown and mouseup targets, so a drag that starts in an input (selecting text) and releases on the backdrop reports the `<dialog>` itself — indistinguishable, to this handler, from a real backdrop click. Owner repro: desktop, venue quick-create, drag from inside to outside closes the dialog. The fix is deletion: the global guard already handles both dialogs correctly (it keys off `e.target instanceof HTMLDialogElement`, no class or id required). `_round_phrase_dialog.html:145` is the precedent — it has no local handler and a comment saying backdrop-click is handled globally.

**#1:** `web/auth.py:175` decides "brand-new" by row absence (`await db.get(User, user_id) is None`), but `ensure_user` (`db/service.py:97`) creates bare rows from the bot's three slash-command call sites (`bot/cogs/reminders.py:36,83,127`). A bot-first user therefore logs into the web as "existing" and never sees `/welcome`; an admin `delete_user` + re-login is the same hole. `User.onboarding_step` CANNOT serve as the check: its migration (`e8a1c9d2f7b5`) backfilled existing rows to ZERO, so a pre-wizard real web user and a bot-first bare row are both step 0 — indistinguishable. Hence the new column and the backfill-everyone-as-done ruling (recorded in the WISHLIST entry).

**One subtlety the WISHLIST entry does not mention:** `is_new_user` in the callback gates TWO things — the `/welcome` redirect AND the language-cookie seeding at `auth.py:179`. The seeding must STAY keyed on row absence (its own comment explains why: only the moment before the row exists is safe). Only the redirect decision moves to `welcomed_at`.

**Behaviour change to state plainly:** today, only the FIRST login redirects to `/welcome`; after this change, every login of a user whose `welcomed_at` is NULL redirects there until they finish or skip the wizard (skip-all is one click, and `GET /welcome` already renders whatever step they left off at). That is intended — an unfinished onboarding is unfinished — and existing rows are backfilled as done so no current user is re-wizarded.

---

### Task 1: Delete the two local backdrop-close handlers (#8)

**Files:**
- Modify: `src/app/web/templates/_venue_create_dialog.html:79-83`
- Modify: `src/app/web/templates/_tag_create_dialog.html:129-132`
- Test: `tests/test_theme_and_tokens.py` (beside `test_backdrop_close_requires_press_and_release_to_agree`, the drag-guard script-text test around line 220)

**Interfaces:**
- Consumes: base.html's global drag-safe handler (already shipped, `e23943d`) — closes any open `HTMLDialogElement` when press and release both land on it.
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Write the failing sweep test**

Add to `tests/test_theme_and_tokens.py` (it already imports what it needs; add `from pathlib import Path` at the top if not present):

```python
def test_no_template_hand_rolls_a_naive_backdrop_close():
    """base.html's global backdrop-close guard is drag-safe: it closes a dialog
    only when pointerdown and click agree on the target (see the test above).
    A LOCAL `if (e.target === dlg) dlg.close()` handler on a dialog bypasses
    that guard entirely -- a drag that starts in an input and releases on the
    backdrop reports the dialog as the click target and closes it, discarding
    what was typed. e23943d fixed this globally but two dialogs kept local
    handlers and shipped the bug anyway; this sweep keeps a third from
    reintroducing it. Rely on the global handler; do not hand-roll one.
    """
    tpl_dir = Path(__file__).resolve().parents[1] / "src" / "app" / "web" / "templates"
    offenders = [
        p.name for p in sorted(tpl_dir.glob("*.html"))
        if "e.target === dlg" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"{offenders} hand-roll a backdrop-close click handler; delete it -- "
        "base.html's global drag-safe handler already closes on backdrop click"
    )
```

- [ ] **Step 2: Run it to verify it fails naming exactly the two templates**

Run: `uv run pytest tests/test_theme_and_tokens.py::test_no_template_hand_rolls_a_naive_backdrop_close -q`
Expected: FAIL with `['_tag_create_dialog.html', '_venue_create_dialog.html']` in the message. If any OTHER template appears, stop and investigate before proceeding.

- [ ] **Step 3: Delete both local handlers**

In `_venue_create_dialog.html`, replace lines 79-83:

```
    // Backdrop-click closes, exactly like .picker. Esc is the <dialog>
    // default and needs no handler.
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg) dlg.close();
    });
```

with:

```
    // Backdrop-click is handled globally in base.html (drag-safe: press and
    // release must both land on the backdrop); Esc is the <dialog> default.
    // A local `e.target === dlg` handler here is exactly the bug e23943d
    // fixed -- it closes on a drag out of a field. Don't reintroduce one.
```

In `_tag_create_dialog.html`, replace lines 129-132:

```
    // Backdrop-click closes, like .picker. Esc is the <dialog> default.
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg) dlg.close();
    });
```

with the same four-line replacement comment as above.

Do NOT touch the `[data-venue-cancel]` / `[data-tag-cancel]` listeners on the adjacent lines — the × buttons keep their handlers.

- [ ] **Step 4: Run the sweep test and both files' render tests**

Run: `uv run pytest tests/test_theme_and_tokens.py -q` then `uv run pytest -q -k "venue or tag_create or picker"`
Expected: all PASS.

- [ ] **Step 5: Full gates, then commit**

Run: `uv run pytest -q` and `uv run ruff check .`
Expected: both clean.

```bash
git add tests/test_theme_and_tokens.py src/app/web/templates/_venue_create_dialog.html src/app/web/templates/_tag_create_dialog.html
git commit -m "fix: dragging out of the venue/tag quick-create dialogs no longer closes them"
```

(Include in the commit body: the local handlers bypassed e23943d's global drag-safe guard; the sweep test keeps a third dialog from reintroducing one.)

---

### Task 2: `User.welcomed_at` — the callback stops trusting row absence (#1)

**Files:**
- Modify: `src/app/db/models.py:116` vicinity (User model)
- Create: `alembic/versions/<generated>_user_welcomed_at.py` (hand-written body)
- Modify: `src/app/web/auth.py:175-192` (callback)
- Modify: `src/app/web/routes/welcome.py` (module docstring, `advance`, `skip_all`)
- Modify: `src/app/db/service.py:154-155` (`delete_user` docstring rot)
- Create: `tests/test_migration_welcomed_at.py`
- Modify: `tests/test_auth.py`, `tests/test_welcome.py`

**Interfaces:**
- Consumes: `ensure_user(session, discord_id, username)` (`db/service.py:97`); `TOTAL_STEPS = 5` (`routes/welcome.py:38`).
- Produces: `User.welcomed_at: datetime | None` (aware UTC, NULL = wizard never finished). Task 3's doc edits describe this exact semantic.

- [ ] **Step 1: Add the column to the model**

In `src/app/db/models.py`, next to `onboarding_step` (line 116), following the file's existing nullable-UTCDateTime pattern (copy the style of an existing `Mapped[datetime | None]` column such as `dismissed_at` or `send_after_utc`):

```python
    # NULL until the welcome wizard completes (advance past the last step, or
    # skip-all). The OAuth callback checks THIS, not row absence, to decide who
    # gets /welcome: the bot's ensure_user creates bare rows, so "row exists"
    # never meant "was onboarded". onboarding_step cannot serve here -- its
    # migration backfilled existing rows to 0, so a pre-wizard web user and a
    # bot-first bare row are indistinguishable by step.
    welcomed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

- [ ] **Step 2: Write the migration test (failing)**

Create `tests/test_migration_welcomed_at.py`, modelled directly on `tests/test_migration_onboarding_step.py` (same fixture shape: build the pre-migration schema, insert a user row, run `alembic upgrade head`, inspect):

```python
"""Migration test: the welcomed_at column on users.

Backfill rule: every PRE-EXISTING row is stamped as already welcomed (from
its created_at), because at migration time a real pre-wizard web user and a
bot-first bare row are indistinguishable (both onboarding_step 0) and
re-wizarding a long-time user is the worse failure. Only rows created AFTER
this migration can be NULL, which is what makes NULL mean "the wizard has
genuinely never finished for this account".
"""
```

Two assertions, in the same style as the onboarding_step test:
1. after upgrade, `welcomed_at` exists on `users`;
2. the pre-inserted row's `welcomed_at` is non-NULL and equals its `created_at`.

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_migration_welcomed_at.py -q`
Expected: FAIL (column absent — no migration yet).

- [ ] **Step 4: Write the migration**

Run `uv run alembic revision -m "user welcomed_at"` (plain, NOT autogenerate — the body is two statements and hand-writing avoids the UTCDateTime cleanup ritual). Fill in:

```python
def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("welcomed_at", sa.DateTime(), nullable=True))
    # Backfill every existing row as already welcomed, from created_at:
    # copying a column the same TypeDecorator wrote sidesteps every datetime
    # string-format question, and at migration time "existing row" cannot be
    # split into onboarded-web-user vs bot-first bare row anyway (both are
    # onboarding_step 0), so everyone is grandfathered as done.
    op.execute("UPDATE users SET welcomed_at = created_at")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("welcomed_at")
```

ASCII only in this file. No `drop_constraint` is involved, so the legacy anonymous-constraint fixture is not needed (adding a column in batch mode never reflects constraints).

- [ ] **Step 5: Run the migration test**

Run: `uv run pytest tests/test_migration_welcomed_at.py -q` — expected: PASS.
Then: `uv run alembic upgrade head` against the dev DB so the local app keeps working.

- [ ] **Step 6: Write the failing behaviour tests**

In `tests/test_auth.py` (reuse the file's existing login helpers — see `test_callback_redirects_new_user_to_welcome:101` for the login dance):

```python
async def test_bot_first_user_is_still_sent_to_the_wizard(client):
    """The bot's ensure_user creates a bare row; that row's owner has never
    seen onboarding, and 'a row exists' must not read as 'was onboarded'."""
    # Create the bare row exactly as the bot does, BEFORE any web login.
    # (Use the test session factory + service.ensure_user with the same
    # discord id the fake OAuth identity returns.)
    ...
    # First WEB login:
    assert location == "/welcome"


async def test_returning_unwelcomed_user_is_sent_back_to_the_wizard(client):
    """Logging in twice without finishing the wizard lands on /welcome twice --
    an unfinished onboarding is unfinished, not 'seen it, too late'."""


async def test_welcomed_user_goes_to_index(client):
    """Login, finish the wizard (POST /welcome/skip-all), log out, log in
    again -> / (this is the OLD returning-user test made honest)."""


async def test_deleted_then_recreated_user_is_rewizarded(client):
    """The owner's original repro: delete_user, then log in again -> /welcome."""
```

In `tests/test_welcome.py` (reuse `_onboarding_step`-style helpers):

```python
async def test_advancing_past_the_last_step_stamps_welcomed_at(client):
    ...

async def test_skip_all_stamps_welcomed_at(client):
    ...
```

Check `tests/test_auth.py::test_callback_redirects_returning_user_to_index` (line ~108): if it logs in twice with no wizard completion, its expectation flips under the new rule — rewrite it as the welcomed-user test above rather than leaving a stale twin. Sweep the rest of test_auth.py (`_login_with_next` family) for second-login assumptions and fix any the same way (completing the wizard via `POST /welcome/skip-all` is the one-line way to mark a test user welcomed).

- [ ] **Step 7: Run them to verify they fail**

Run: `uv run pytest tests/test_auth.py tests/test_welcome.py -q`
Expected: the new tests FAIL (redirect still keyed on row absence; no stamping).

- [ ] **Step 8: Implement**

`src/app/web/auth.py` (callback, lines 175-192): keep `is_new_user` and the language seeding EXACTLY as they are (seeding stays creation-only). Change only the redirect decision and its comment:

```python
    # The wizard is owed to anyone it has never finished for -- which is
    # welcomed_at IS NULL, not "row did not exist yet": the bot's slash
    # commands ensure_user bare rows, and an admin delete_user + re-login
    # re-creates one, so row absence never meant "was onboarded". Someone
    # who has not picked a single tag is not served by landing on the page
    # that bounced them here, so the wizard also wins over ?next=.
    response = RedirectResponse("/welcome" if db_user.welcomed_at is None else (destination or "/"))
```

`src/app/web/routes/welcome.py`:
- `advance()` (line 227): after the increment, before commit:

```python
    if db_user.onboarding_step >= TOTAL_STEPS and db_user.welcomed_at is None:
        # Crossing into done is the moment the wizard completes; the OAuth
        # callback keys the /welcome redirect off this stamp.
        db_user.welcomed_at = datetime.now(UTC)
```

- `skip_all()` (line 243): same stamp after setting `onboarding_step = TOTAL_STEPS`.
- Module docstring line 1-2: "offered once at first login (see auth.py's callback -- a brand-new row redirects here instead of /)" → "offered until completed (see auth.py's callback -- a login with welcomed_at still NULL redirects here instead of /)".

`src/app/db/service.py:154-155`: replace the stale sentence in `delete_user`'s docstring:

```
    No route or UI calls this: erasure is a manual, owner-initiated
    operation for now.
```

with:

```
    POST /me/delete (web/routes/preferences.py) calls this, scoped to the
    caller behind require_user and a heavy client-side confirmation; it
    also remains available as a manual owner operation. A re-created row
    after erasure starts with welcomed_at NULL, so the next login is
    onboarded afresh -- by design, not accident.
```

- [ ] **Step 9: Run the behaviour tests, then the full gates**

Run: `uv run pytest tests/test_auth.py tests/test_welcome.py tests/test_migration_welcomed_at.py -q` — expected: PASS.
Then `uv run pytest -q` (2008+ tests) and `uv run ruff check .` — expected: clean. Any OTHER failing test almost certainly assumed "second login → /": fix it by welcoming the user (skip-all) rather than weakening the assertion.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "fix: decide who gets onboarding by welcomed_at, not row existence"
```

(Include in the body: bot-first rows and delete+re-login both skipped /welcome silently; backfill grandfathers every existing row; language seeding stays keyed on row absence deliberately.)

---

### Task 3: Record what shipped (WISHLIST, CLAUDE.md)

**Files:**
- Modify: `WISHLIST.md` (move #1 and #8 to Shipped; full revision pass per the file's own rules)
- Modify: `CLAUDE.md` (two lines, below)

**Interfaces:**
- Consumes: the semantics shipped in Tasks 1-2, as described in their commit messages and this plan's root-cause section.
- Produces: nothing — documentation only.

- [ ] **Step 1: CLAUDE.md edits**

Invariant 5's sentence "and a brand-new account still goes to `/welcome` regardless" → "and an account whose wizard was never finished (`User.welcomed_at` NULL — row existence proves nothing, the bot's `ensure_user` mints bare rows) still goes to `/welcome` regardless".

UI conventions, picker sentence ("Pickers are native <dialog> white cards ... backdrop-click and Esc close"): append "— backdrop-close comes ONLY from base.html's global drag-safe handler; never add a local `e.target === dlg` click handler to a dialog (that shipped the drag-out-closes-the-dialog bug twice; a sweep test in `test_theme_and_tokens.py` now forbids it)."

- [ ] **Step 2: WISHLIST.md — move #1 and #8 to Shipped with a dated pass paragraph, renumber Proposed, run the revision pass over every remaining entry (the pass paragraph at the top of the file records what moved and why; #8's Shipped entry must record that the symptom sentence — "dragging from inside to outside closes it" — re-routed the diagnosis from the backdrop CSS the entry suspected to the close handler, and that the fix was deletion)**

- [ ] **Step 3: Commit**

```bash
git add WISHLIST.md CLAUDE.md docs/superpowers/plans/2026-08-03-onboarding-skip-and-dialog-drag.md
git commit -m "docs: wishlist and CLAUDE.md for the onboarding-skip and dialog-drag fixes"
```
