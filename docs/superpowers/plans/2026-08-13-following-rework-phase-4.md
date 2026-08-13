# Following Rework — Phase 4: Preferences reduces to a count and a default

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shrink Preferences' Following section to a fixed height forever, make a new follow inherit your default preset, and give you one explicit way to apply that default to what you already follow — without ever overwriting a choice you made on purpose.

**Architecture:** No new page and **no migration** — the whole rework stays schema-free. `ReminderPreset.is_default` already exists with a route and a UI; this phase widens what it means and deletes the surface it replaces.

**Tech Stack:** FastAPI, Jinja2, plain CSS, SQLAlchemy async, pytest, Babel.

**Spec:** `docs/superpowers/specs/2026-08-12-following-rework-design.md`, §Preferences → Following — sequencing step 4, the last. Phases 1-3 are merged (PRs #151, #152, #153).

**This completes the rework.** Task 5 moves the WISHLIST entry to Shipped.

## An owner ruling that changes the spec

**The standing default is preset-only. There is no notify default.** (Owner, 2026-08-13.)

The spec says the standing default covers "which preset new follows get, **and whether they notify**". The notify half would need a new `User` column — the first migration in four phases, for one boolean. The owner chose to drop it: **new follows always notify**, and you turn notify off per tag in `/following`'s dialog, which already does exactly that.

Do not add the column. Do not add a notify control to Preferences.

## Global Constraints

- Run everything from the repo root `E:\click clack clan\concert-reminder`.
- **Always `uv run --isolated`** — an external process holds a lock on `.venv`; never `uv sync`.
- **Run test commands in the FOREGROUND with `timeout: 900000`.** Backgrounding has stalled implementers here twice.
- **Baseline: 2880 passing**, `uv run --isolated ruff check .` clean, before any commit.
- **Sentence case everywhere.** Radiuses 3px / 999px chips / 4px overlay cards / 50% circles — never 6px or 8px, there is a sweep test.
- **Two callout shapes and no third**: `.edgecard` (ongoing state), `.banner` (needs attention).
- **New user-visible strings need BOTH catalogues** filled by hand. Editing existing English copy must keep the msgid byte-identical or update both. `tests/test_i18n_catalogues.py` fails otherwise.
- **Never interpolate user-controlled text into an inline `on*` handler** (invariant 7). Preset names are user-controlled. There is now a repo-wide sweep in `tests/test_xss_escaping.py`.
- **Invariant 8**: following stays derived, `tracked_concert_ids` is the single derivation, and any write to a subscription re-syncs that user's rules through the existing machinery. Do not add a second derivation.
- **Ownership checks 404, never 403** (invariant 5). `owned_preset` and `_owned_subscription` already exist in `routes/preferences.py`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NMLgHrqvFFmFatWBeJuCGH
  ```

## What already exists — do not rebuild it

| Thing | Where |
|---|---|
| `ReminderPreset.is_default`, plus `set_default_preset` and `POST /presets/{id}/default` | `db/models.py:650`, `routes/preferences.py:244` |
| `get_default_preset(session, user_id)` | `db/core.py:4117` |
| `owned_preset`, `_owned_subscription` | `routes/preferences.py` |
| `/following` and its per-tag dialog | shipped in phase 3 |
| The "Default" pill and "Make default" button | `preferences.html:205-207` |

---

### Task 1: three coverage gaps phase 3's final review left open

**Files:**
- Test: `tests/test_tags_follow_htmx.py`, `tests/test_following_page.py` (or wherever the chip attributes are asserted — grep), `tests/test_xss_escaping.py`

**Interfaces:** none. Fully independent; could ship alone.

All three are **test-only**. No feature code is wrong.

**A. The `(user_id, tag_id)` pair is only half pinned.** `unfollow_tag` finds its row with both conditions, but the test seeds the presser with **one** tag — so "the presser's row for tag X" and "the presser's only row" are the same row, and **dropping `TagSubscription.tag_id == tag_id` leaves every test green**.

Real consequence: unfollowing Aqours could delete your 蓮ノ空 row. `scalar_one_or_none` raises on multiple matches so a real user gets a 500 rather than silent loss — unless someone also softens it to `.first()`, which is the plausible-cleanup pair.

Fix: seed a **second** tag for the presser and assert only the pressed one is gone.

**B. The chip's keydown handler is unpinned.** `following.html:211` has a capture-phase Enter/Space listener that opens the dialog. The chip's `role="button" tabindex="0"` **is** pinned; the listener is not. Delete it and the chips stay Tab-reachable and announce as buttons while doing nothing on Enter — which the template's own comment calls worse than not being focusable.

Fix: a source-text assertion that the listener exists. This suite has no JS runtime, so this pins **presence, not behaviour** — say so in the docstring rather than implying more.

**C. The inline-`on*` sweep misses mixed case.** `_ON_ATTR = re.compile(r"""\bon[a-z]+\s*=\s*(["'])(.*?)\1""", re.S)` has no `re.I`, so `onClick="…"` — valid HTML, case-insensitive attribute names — passes unseen.

Fix: add `re.I`. Add a mixed-case offender to `SCANNER_SAMPLE` so the sanity test covers it.

**Also document, do not fix:** the sweep still cannot see an **unquoted** handler (`onclick={{ x }}`), because the pattern requires a quote after `=`. An optional-quote regex must terminate on whitespace or `>` and over-matches easily. Record it as a known, deliberate limit in the module docstring — a sweep that names what it does not cover is honest; one that implies total coverage is not.

- [ ] **Step 1: Write the three failing tests / assertions**
- [ ] **Step 2: Run them; confirm A and B fail, and that C's sample offender is missed**
- [ ] **Step 3: Apply the fixes**
- [ ] **Step 4: Verify each by its own mutation** — drop `tag_id ==` (A must fail); delete the keydown listener (B must fail); write `onClick="{{ t.name }}"` into a template (C must fail and name it). Revert cleanly **between** mutations — a report on the previous branch misattributed a result by running two in one tree.
- [ ] **Step 5: Full suite, lint, commit**

---

### Task 2: a new follow inherits your default preset

**Files:**
- Modify: `src/app/web/routes/preferences.py` — `subscribe`
- Test: `tests/test_presets.py` or `tests/test_tags_follow.py` (grep for where `POST /subscriptions` is tested)

**Interfaces:**
- Produces: `POST /subscriptions` applies `get_default_preset()` when the form supplies no preset. Task 3's fill and Task 4's copy both describe this behaviour.

**Why:** the spec's standing default is "which preset new follows get". Today that does not happen — `subscribe` writes `preset_id=preset_id or None`, and the chip forms send no `preset_id`, so **every follow from `/tags` links no preset at all.**

**The rule:** `preset_id` absent or `0` means *"I did not choose"* → apply the viewer's default preset (or `None` if they have none). There is no need for an "explicitly no preset" value at follow time: `/following`'s dialog has a real "none" option and writes through `/subscriptions/{id}/settings`, a different route.

**A second defect to fix in the same place.** `subscribe`'s re-submit branch currently does `sub.preset_id = preset_id or None` — so **re-following an already-followed tag clears a preset you deliberately set.** Phase 3's review flagged this as pre-existing and narrow (a stale tab or a second window), but this phase makes per-tag presets first-class, which turns it into silent data loss. Re-submitting must **not** clear a set preset.

- [ ] **Step 1: Write the failing tests**

Three, each naming its mutation:

```python
async def test_a_new_follow_inherits_the_default_preset(client):
    """The standing default. Mutation: reverting to `preset_id or None`,
    which links no preset at all -- what shipped before this phase."""

async def test_a_new_follow_links_nothing_when_there_is_no_default(client):
    """A user with no presets must not get a bogus preset_id. Mutation: a
    fallback that invents one."""

async def test_re_following_does_not_clear_a_deliberately_set_preset(client):
    """Silent data loss. Mutation: the old `sub.preset_id = preset_id or None`
    on the re-submit branch."""
```

- [ ] **Step 2: Run to verify they fail**
- [ ] **Step 3: Implement** — read `subscribe` first and keep `owned_preset`'s guard on an explicitly-supplied preset.
- [ ] **Step 4: Check the other callers.** `POST /subscriptions` is also posted by the welcome wizard (`welcome.html` sends an explicit `preset_id=0`) and by Preferences' picker (about to be deleted in Task 4). Under the new rule the wizard's `0` now means "apply my default" — **judge whether that is right and say so.** A brand-new user in the wizard has usually just created a preset, so inheriting it is arguably the intent; but it is a behaviour change to an explicit value and must be deliberate, not accidental.
- [ ] **Step 5: Verify all three mutations. Full suite, lint, commit.**

---

### Task 3: "Apply my default preset to all followed tags"

**Files:**
- Modify: `src/app/web/routes/preferences.py` (a new POST), `src/app/web/templates/preferences.html`
- Test: `tests/test_presets.py` or a new `tests/test_preset_fill.py`

**Interfaces:**
- Consumes: Task 2's default-preset behaviour.
- Produces: `POST /presets/apply-to-following` (name it as you judge best — say what you chose).

**This is an action, not a setting**, and the distinction is the whole design. The spec is explicit:

> **The standing default** governs future follows only. Changing it never touches an existing subscription — a setting that rewrites rows when you change it is the surprise this design must not ship.

So: a **button beside the setting**, which writes the default into every subscription whose `preset_id` is **NULL**, leaves every subscription that already carries its own preset **exactly as it is**, and then **reports both counts**: how many were filled, and how many were left alone because they had their own.

**The report is the point, not a courtesy.** Without it the action is indistinguishable from one that overwrote everything, and the user has no way to tell which happened.

This is the same shape as the catalogue tag import — CLAUDE.md: *"a blank on the DB side is a FILL applied automatically (writing into emptiness cannot lose anything)… two differing values are a CONFLICT somebody resolves."*

- [ ] **Step 1: Write the failing tests**

The assertions that matter, each naming its mutation:

- a NULL `preset_id` is filled
- **a set `preset_id` is left exactly as it was** — this is the one that matters most; its mutation is a blanket `UPDATE`
- the report names **both** counts, and the skipped count is correct
- a user with no default preset gets a no-op and is told so, not a crash
- **another user's subscriptions are untouched** — seed a second user with a NULL preset and assert their row survives. Phase 3 shipped a route whose user-scoping was unpinned across the entire suite; do not repeat it
- the redirect target is pinned (`location`, not just the 303)

**`/preferences` is already in `_ALLOWED_NEXT`** — confirm rather than assume.

- [ ] **Step 2-4: Run red, implement, run green**
- [ ] **Step 5: Verify the overwrite mutation specifically.** Make the action write the default over every row regardless, and confirm the "left alone" test fails. This is the failure that would silently destroy a user's per-tag tuning.
- [ ] **Step 6: Both catalogues** for the button and the report copy. The report is a count sentence — use `ngettext` for both numbers, not a bare format string.
- [ ] **Step 7: Full suite, lint, commit**

---

### Task 4: the Preferences reduction

**Files:**
- Modify: `src/app/web/templates/preferences.html`, `src/app/web/routes/preferences.py`
- Test: `tests/test_preferences_page.py`, `tests/test_preferences_following.py` (grep for others)

**What the section becomes** — fixed height regardless of how many tags are followed:

- **the count**, with a **"Manage →"** link to `/following`
- **the standing default**: which preset new follows get. That is the existing `is_default` flag — **widen its copy**, do not add a control. `preferences.html:205-207`'s pill and button currently say *"The Discord 'Set my reminders' button applies this preset"*, which is now only half of what it does. Editing that copy means both catalogues.
- **Task 3's apply-to-all button**, beside it
- **the existing skipped-events list**, unchanged — it is concert-level opt-outs, the visible half of invariant 8's overrides, and has no home on a tag catalogue

**What goes:** the picker (the whole `<details>` fold and its `sub-defaults` control), and the per-tag `.subrow`s with their Notify / Auto-apply / Unfollow toggles.

**Consequences to handle deliberately, not by accident:**

- `POST /subscriptions/{id}/notify` and `POST /subscriptions/{id}/auto-apply` may lose their only callers. **Grep before deleting anything.** If a route becomes uncalled, decide: delete it, or keep it with a comment saying why. Either is defensible; silently leaving dead routes is not. Note `/subscriptions/{sub_id}/delete` is still used by the welcome wizard.
- The route's context loses whatever the deleted markup consumed. Remove it — templates are invisible to ruff, so nothing will flag dead context.
- Existing tests will break. For **each** one, judge whether its subject is genuinely gone (delete it, and say so per test) or whether it moved to `/following` (point it there). Never delete a test whose behaviour survives.

- [ ] **Step 1: Write the failing tests** — the section renders the count, a link to `/following`, the default-preset control and the skipped list, and does **not** render the picker or any `.subrow` toggle. **Scope every assertion**: `base.html` renders a Tags nav link and a tab bar, and an earlier task on this branch shipped a test that passed with its whole feature deleted because the chrome already contained the string it asserted.
- [ ] **Step 2-4: Run red, implement, run green**
- [ ] **Step 5: Account for every test you touched**, per test, in the report.
- [ ] **Step 6: Browser check** — the section at a realistic follow count, and at zero. Confirm it is fixed height and that "Manage →" reaches `/following`.
- [ ] **Step 7: Full suite, lint, commit**

---

### Task 5: bookkeeping, and the entry finally ships

**Files:** `docs/architecture.md`, `WISHLIST.md`, `docs/superpowers/specs/2026-08-12-following-rework-design.md`

- [ ] **Step 1: architecture.md** — entries for: the standing default being `is_default` widened rather than a new column, and that the notify half was deliberately dropped (owner, 2026-08-13) so no migration was needed in four phases; the fill-never-overwrite rule and why the report is load-bearing; and what Preferences now owns versus `/following`.

- [ ] **Step 2: Correct the spec.** Its §Preferences says the standing default covers notify. The owner dropped that half. Mark it as a **dated correction**, matching how this branch corrected the same document's sequencing reason — do not silently rewrite.

- [ ] **Step 3: WISHLIST — move the entry to Shipped.** This completes the rework, so unlike phases 1-3 the entry **does** move now, dated, with the four phases named and what each delivered. Then do the **full revision pass** CLAUDE.md requires: re-rank what remains, reconsider what is still useful, and write the revision-pass narrative note.

  Record what the entry itself could not have known when it was filed unranked: that the surface was 878 chips and a 65-query N+1; that 681 of 735 tags were unfindable by the name they displayed; that 318 characters could not be followed at all; and that the follow press went 923ms → 10.7ms.

- [ ] **Step 4: Full suite, lint, commit**

---

## Self-review notes

**Spec coverage.** Implements §Preferences in full, minus the notify half the owner dropped (recorded as a correction, not an omission). Task 1 clears phase 3's residue. Nothing else in the spec remains.

**No migration, four phases running.** Worth stating in the PR: `TagSubscription.preset_id`/`notify` and `ReminderPreset.is_default` carried the whole design, and the one place a column was tempting is the one place the owner chose not to.

**The riskiest task is 3, not 4.** Task 4 deletes markup and will fail loudly. Task 3 writes to every subscription a user owns, and its failure mode — overwriting a deliberately-set preset — is silent, irreversible for the user, and would look exactly like success. Its overwrite mutation is the single most important check in this plan.

**Task ordering.** 1 is independent. 2 → 3 (the fill applies the default that Task 2 makes meaningful). 4 after 3, since it renders 3's button. 5 last.
