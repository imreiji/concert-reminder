# Triage phase 3: the `triage-leads` skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A skill that takes the discovery sweep's output and produces the two
files phases 1 and 2 consume — a prune list and a multi-draft batch — so the
443-lead backlog becomes two reviewed imports instead of hundreds of manual
steps.

**Architecture:** `.claude/skills/triage-leads/SKILL.md` plus two reference
examples, each PINNED to the parser that reads it by a test. The skill CALLS
`add-concert` for drafts rather than duplicating its schema.

**Tech Stack:** Markdown + YAML. One test module. No app code changes except
the skill-distribution zip.

**Spec:** `docs/superpowers/specs/2026-08-02-triage-leads-design.md`, Phase 3.

## Global Constraints

- **The skill proposes; it never writes.** Both outputs are files the owner
  imports through a surface that plans before it commits. The skill must never
  be described as dismissing leads or creating concerts itself.
- **`import_commit` is the only write path into `concerts`.** Say so in the
  skill, so a future reader does not invent a shortcut.
- The scope ruling of 2026-08-02 governs classification: **catalogue ticketed
  concerts/tours and radio/talk/番組イベント; everything else is a dismissal.**
- `docs/discovery-lead-taxonomy-2026-08-01.md` is the source for the classes,
  their signals, and the collapse finding. Do not restate its full contents in
  the skill — reference it and carry only the operational rules.
- Both example files MUST parse clean against the real parsers, pinned by test,
  the same guarantee `.claude/skills/add-concert/references/example-draft.yaml`
  already has (see `tests/test_yaml_import.py::test_skill_example_draft_parses_clean`).
- The skill is written for an agent with **no login** to dekimasen.app. It
  cannot read the Tags page or the discoveries page directly; its input is
  pasted text.
- `uv run --isolated pytest -q` must pass and `uv run --isolated ruff check .`
  must be clean. Always `--isolated` — an external process holds a `.venv` lock.

---

### Task 1: The two examples, pinned to their parsers

**Files:**
- Create: `.claude/skills/triage-leads/references/example-prune-list.yaml`
- Create: `.claude/skills/triage-leads/references/example-batch.yaml`
- Create: `tests/test_skill_triage_leads.py`

**Interfaces:**
- Consumes: `app.domain.prune_list.parse_prune_list` and
  `app.domain.yaml_import.parse_drafts`.
- Produces: two example files Task 2's prose describes.

**Why examples before prose:** the skill's whole value is that its output
parses. Writing the example first and pinning it means the prose describes
something proven, rather than something plausible. This is the order
`add-concert` was built in and the reason its example has never drifted.

- [ ] **Step 1: Write the failing tests**

```python
"""The triage-leads skill's examples must parse against the REAL parsers.

A skill that emits a format nothing reads is a proposal, not a workflow, and
the drift is silent: the skill keeps producing files that stopped importing.
Same guarantee test_skill_example_draft_parses_clean gives add-concert.
"""
import pathlib

from app.domain.prune_list import parse_prune_list
from app.domain.types import DismissReason
from app.domain.yaml_import import parse_drafts

SKILL = pathlib.Path(".claude/skills/triage-leads/references")


def test_example_prune_list_parses_clean():
    got = parse_prune_list((SKILL / "example-prune-list.yaml").read_text(encoding="utf-8"))
    assert got.entries, "the example must actually name some leads"
    assert got.warnings == (), "a warning means the example teaches a bad habit"


def test_example_prune_list_shows_more_than_one_reason():
    """An example with a single reason would let an agent infer the file takes
    one class at a time, which is exactly wrong -- the whole point is one file
    covering the whole backlog."""
    got = parse_prune_list((SKILL / "example-prune-list.yaml").read_text(encoding="utf-8"))
    assert len({e.reason for e in got.entries}) >= 3


def test_example_prune_list_only_uses_dismissible_classes():
    """The scope ruling catalogues concerts and talk shows. An example that
    dismissed either would teach the opposite of the ruling."""
    got = parse_prune_list((SKILL / "example-prune-list.yaml").read_text(encoding="utf-8"))
    assert DismissReason.TALK not in {e.reason for e in got.entries}


def test_example_batch_parses_into_several_drafts():
    batch = parse_drafts((SKILL / "example-batch.yaml").read_text(encoding="utf-8"))
    assert len(batch.drafts) >= 2, "a batch example with one draft teaches nothing"
    assert batch.errors == ()


def test_example_batch_drafts_are_each_complete():
    """Each document must stand alone -- it is stored verbatim and re-parsed
    later, so a draft that only makes sense in context would break on review."""
    batch = parse_drafts((SKILL / "example-batch.yaml").read_text(encoding="utf-8"))
    for d in batch.drafts:
        assert d.parsed.title and d.parsed.title_en and d.parsed.title_zh
        assert d.parsed.days and d.parsed.rounds


def test_example_batch_shows_a_multi_leg_concert():
    """The collapse rule is the skill's hardest judgment: a tour is ONE concert
    with several legs. An example of only single-leg concerts would not teach
    it."""
    batch = parse_drafts((SKILL / "example-batch.yaml").read_text(encoding="utf-8"))
    assert any(len(d.parsed.days) >= 2 for d in batch.drafts)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --isolated pytest tests/test_skill_triage_leads.py -q`
Expected: FAIL — the reference files do not exist.

- [ ] **Step 3: Write the two examples**

`example-prune-list.yaml` — realistic leads drawn from
`docs/discovery-lead-taxonomy-2026-08-01.md`, using REAL Eventernote ids and a
trailing `#` comment naming each so a human reading the file can tell what they
are about to prune. At least three reasons, none of them `talk`.

`example-batch.yaml` — at least two complete drafts separated by `---`, one of
them multi-leg (a tour). Build them from
`.claude/skills/add-concert/references/example-draft.yaml`'s shape; that file is
itself pinned, so it is the current truth for the schema.

- [ ] **Step 4: Run the tests**

Run: `uv run --isolated pytest tests/test_skill_triage_leads.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/triage-leads tests/test_skill_triage_leads.py
git commit -m "feat: triage-leads examples, pinned to the parsers that read them"
```

---

### Task 2: The skill itself

**Files:**
- Create: `.claude/skills/triage-leads/SKILL.md`
- Modify: `tests/test_skill_triage_leads.py`

**Interfaces:** consumes the examples from Task 1.

**Read `.claude/skills/add-concert/SKILL.md` first and match its shape** —
frontmatter with `name` and a `description` naming the trigger phrases, numbered
sections, tables where a table is clearer than prose, and a final "emit and hand
off" section telling the owner exactly where to paste.

**The three passes, cheapest first, and the order IS the design:**

1. **Collapse by title stem.** No network. The largest single reduction: 443
   leads is roughly 120-150 productions. **Two different mechanisms produce
   repeated titles and they want opposite treatment** — this is the trap and the
   skill must state it plainly:
   - 学園アイドルマスター LIVE TOUR is ONE concert with eight legs.
   - 『Liella!と結ぶプロジェクト』お渡し会 is eleven events because each member got
     her own slot at one venue on one day — one event, or none.
   Grouping purely on title stem gets the first right and the second wrong.

2. **Classify against the scope ruling.** No network. Keep ticketed
   concerts/tours and radio/talk/番組イベント; everything else is a dismissal with
   a reason. Output: the prune list. Two rules the taxonomy earned:
   - The `!_` venue prefix (`!_東京都内某所`, Eventernote's undisclosed-venue
     placeholder) is the strongest single signal for release events. A SIGNAL,
     not a rule — some cruises and fan events use it too.
   - 【当選者限定】 means a lottery HAPPENED. A blanket dismiss-on-keyword loses
     exactly the leads in that class that mattered.

3. **Research the survivors.** The only pass needing a ticket page. Emits the
   batch file, in batches rather than all at once. **Calls `add-concert`** for
   each draft rather than restating its schema.

**Where each output goes:**
- prune list → `https://dekimasen.app/admin/discoveries/prune` (admin-only)
- batch → `https://dekimasen.app/concerts/import` (the batch paste box)

- [ ] **Step 1: Write the failing tests**

```python
def test_skill_exists_and_has_frontmatter():
    text = (pathlib.Path(".claude/skills/triage-leads/SKILL.md")).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: triage-leads" in text


def test_the_skill_names_both_destinations():
    """An agent that produces the files but cannot say where they go has not
    closed the loop."""
    text = ...
    assert "/admin/discoveries/prune" in text
    assert "/concerts/import" in text


def test_the_skill_delegates_drafts_to_add_concert():
    """It must not restate the draft schema -- that one is owned by
    add-concert and pinned to the parser by its own test."""
    text = ...
    assert "add-concert" in text


def test_the_skill_states_the_scope_ruling():
    text = ...
    assert "番組イベント" in text


def test_the_skill_warns_about_the_collapse_trap():
    """The per-member split is the mistake most likely to produce a wrong
    import, and it is not guessable."""
    text = ...
    assert "お渡し会" in text
```

- [ ] **Step 2: Run and watch them fail**
- [ ] **Step 3: Write `SKILL.md`**
- [ ] **Step 4: Run the tests**
- [ ] **Step 5: Full suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/triage-leads tests/test_skill_triage_leads.py
git commit -m "feat: the triage-leads skill"
```
