# Subagents

Vendored from [darcyegb/ClaudeCodeAgents](https://github.com/darcyegb/ClaudeCodeAgents)
on 2026-07-21. These are review/verification agents — they check work that
already exists. None of them are implementers.

They live here (project scope, committed) rather than in `~/.claude/agents/`
so the whole repo gets the same set and the local adaptations below travel
with it.

## What is actually here

Seven agents. The README upstream lists six; `ultrathink-debugger` is in
the repo but undocumented there.

| Agent | The question it answers |
|---|---|
| `karen` | Is the thing that was marked done actually done? Runs it and finds the gap. |
| `Jenny` | Does what was built match the written spec? |
| `task-completion-validator` | Is this real, or stubbed/mocked to look finished? |
| `code-quality-pragmatist` | Is this over-engineered for what the project needs? |
| `claude-md-compliance-checker` | Does this change break the project's own documented rules? |
| `ultrathink-debugger` | Deep root-cause analysis on a bug that resisted the obvious fix. |
| `ui-comprehensive-tester` | Does the UI hold up across flows and edge cases? |

## How to use them efficiently

**Pick one verifier, not three.** `karen`, `Jenny`, and
`task-completion-validator` are three framings of one job, and running all
three on the same change buys you three restatements of the same finding at
three times the cost. Choose by what you actually doubt: `Jenny` when a
spec exists and you want conformance to it, `karen` when something is
marked done and you doubt it, `task-completion-validator` when you suspect
the work is deliberately shallow. If you have no spec, `Jenny` has nothing
to check against — that is the common case here, since this repo plans in
`docs/superpowers/specs/` only for substantial features.

**They are reviewers, not a pipeline.** Upstream is explicit that these do
not chain. Fan them out in ONE message so they run concurrently, then read
the reports together. Sequencing them wastes wall-clock for no benefit,
because none of them consumes another's output.

**Scope each one to a diff, not to the repo.** "Review the working diff on
this branch" costs a fraction of "review the codebase" and produces
findings you can act on. An unscoped reviewer on a 1000-test project will
wander, and its report will be padded with restated architecture.

**Tell them to report, not to fix.** None of the seven declares a `tools:`
restriction in its frontmatter, so every one of them inherits the full
tool set including `Edit` and `Write`. That means a "reviewer" can quietly
rewrite your code while you think it is reading. If you want a read-only
pass, say so in the prompt. If you want fixes, say that instead — but then
review the diff it produces, the same as any other change.

**Do not run them before the cheap gates.** `uv run pytest -q` and
`uv run ruff check .` are faster than any agent and catch a different class
of problem. Run those first; send an agent after the suite is green, when
the remaining doubt is about judgement rather than correctness.

## Overlap with what this repo already has

Three of these duplicate tooling that is already installed, and the
installed version is usually the better pick:

- `code-quality-pragmatist` overlaps the `/simplify` skill and the
  `code-simplifier` agent. `/simplify` applies its fixes; the pragmatist
  only reports. Use `/simplify` unless you specifically want the
  over-engineering critique without the edits.
- `ultrathink-debugger` overlaps `superpowers:systematic-debugging`, which
  is a process skill the main loop follows directly. Prefer the skill —
  debugging in a subagent means the findings arrive as a summary, and
  debugging is exactly the work where you want the full trace in front of
  you. Reach for the agent when the investigation is noisy enough (log
  trawls, wide greps) that you want the context kept out of the main
  session.
- `ui-comprehensive-tester` assumes Puppeteer, Playwright, or Mobile MCP.
  None of those are connected here — this project drives Chrome through
  `claude-in-chrome`. As written the agent will look for tools it cannot
  find. Either rewrite its tool selection section against
  `mcp__claude-in-chrome__*` before relying on it, or drive the browser
  from the main loop.

## Local adaptation

`claude-md-compliance-checker` ships knowing only about `CLAUDE.md`. In
this repo CLAUDE.md is deliberately a SUMMARY — the enforceable rules live
in `.claude/skills/dekimasen/references/`. An unmodified compliance checker
therefore validates against the short version and reports clean while a
change violates an invariant spelled out only in the references. It has
been edited to read both. If you re-vendor these agents from upstream,
re-apply that change or the checker silently gets weaker.
