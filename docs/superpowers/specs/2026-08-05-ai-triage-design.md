# AI triage of discovery leads (DeepSeek V4 Flash) — design (phase 1)

Date: 2026-08-05. Status: approved by owner (this session), pre-implementation.

**Phasing (owner, 2026-08-05):** this spec is PHASE 1 of the AI pipeline —
classify + skeleton drafts, rounds never emitted. PHASE 2 is AI drafting
FROM a skeleton draft: completing a pending skeleton into a full draft,
rounds included. Phase 2 gets its own brainstorm and spec once phase 1 has
shipped and been calibrated against real leads; its one hard design
question is already known and recorded here so it is not rediscovered: how
the official ticket page reaches the model. Owner-supplied (a URL or pasted
page text on the pending-draft review) keeps the human vouching for the
source and the fetch surface narrow; server-side search would mean an
arbitrary-host fetch surface and puts an LLM's judgment where the app's
"a deadline it names is real" promise lives. That choice is phase 2's to
make, not this build's.

## Why now, and which wishlist entry this is

Wishlist #6 ("In-app LLM extraction behind the same draft seam") has been
blocked on API budget since 2026-07-22 — the owner had no allowance for
per-import LLM calls, which is why the import path is agent-side. That budget
question changed: the owner purchased DeepSeek V4 Flash credits
(~$0.088/M input, ~$0.176/M output, 1M context). A full pass over the
~400-lead discovery backlog costs on the order of $0.20–0.40; a daily
incremental run costs pennies.

The owner chose a different first target than the entry as written: not
extraction on the import page, but **auto-drafting discovery leads** — the
sweep produces a steady lead stream whose triage (the `triage-leads` skill's
three passes) is today a manual agent workflow. This build moves the first
two-thirds of that pipeline server-side, behind an admin button.

## What it does

One button on `/admin/discoveries` runs an AI triage of the open leads:

1. **Classify** — DeepSeek collapses raw leads into productions (pass 1 of
   the triage skill) and classifies each against the 2026-08-02 scope ruling
   (pass 2), producing a proposed prune-list YAML.
2. **Draft** — for surviving productions, DeepSeek authors *skeleton* concert
   drafts from their Eventernote event pages: trilingual titles and leg
   labels, legs (date/venue), cast tags — and an **empty `rounds:` list,
   always**.

Both outputs land on review surfaces that already exist. Nothing is
committed, dismissed, or DM'd to users by this feature; the owner reviews and
applies exactly as today.

## What it deliberately does NOT do

- **No round extraction — in this phase.** Pass 3 of the triage skill
  (round times from the production's official ticket page) stays with the
  agent skills for now; it becomes PHASE 2 (see Phasing above), designed
  separately. Round research needs the official page plus judgment, and it
  sits exactly where an LLM hallucinating a deadline would break the app's
  core promise ("a deadline it names is real"). Phase 1's skeleton drafts
  are honest by construction: rounds cannot be invented because rounds are
  not emitted — and are stripped in code even if the model emits them
  anyway.
- **No auto-apply.** The prune plan is proposed, never applied; drafts go to
  the pending queue, never to `concerts`. `import_commit` remains the only
  write path into `concerts` (unchanged), and lead dismissal still happens
  only through the plan screen's two-step paste → plan → apply.
- **No new fetch surface.** Only `eventernote.com` (already the pinned host
  of the sweep) and the DeepSeek API host are contacted. Official ticket
  pages are never fetched server-side.
- **No sweep-time automation.** The run is admin-initiated only. The runner's
  shape (a request stamp picked up by the tick) does not preclude wiring it
  to the sweep later, but that is not designed in and would be its own
  decision.
- **No per-lead verdict UI.** Approach B (structured JSON verdicts with
  inline accept/reject on `/admin/discoveries`) was considered and set aside:
  it builds a third review surface and a second validation vocabulary for the
  same outcome. Nothing here blocks building it later if volume justifies it.

## Architecture

The load-bearing idea: **the LLM speaks the two text formats the app already
parses.** The classify phase emits the prune-list YAML that
`domain/prune_list.py:parse_prune_list` reads (the same parser behind the
paste box); the draft phase emits the `add-concert` YAML vocabulary that
`domain/yaml_import.py:parse_drafts` reads (the same parser behind the batch
paste). Malformed model output fails at the same boundary a bad agent draft
does — warnings over failures, per-document isolation — and no second
validation vocabulary exists to drift from the first.

### New pieces

- **Config** (`app/config.py`) — four settings, same shape as
  `discovery_enabled`:
  - `triage_enabled: bool = False` — gates the scheduler pickup; keeps
    tests, dev runs and misconfigured deploys off the network and off the
    owner's credit balance.
  - `deepseek_api_key: str = ""`
  - `deepseek_base_url: str = "https://api.deepseek.com"`
  - `deepseek_model: str = ""` — **no model id is hardcoded**; the owner
    sets the exact V4 Flash id in `.env`. Baking in a guess at a
    third-party's current alias is how a config rots silently.
- **`app/llm.py`** — the DeepSeek client. Top-level beside `fetching.py`
  for the same reason (it does I/O, so it cannot live in `domain/`; only
  the runner imports it today, but it is provider-plumbing, not triage
  logic). A hand-rolled httpx POST to the chat-completions endpoint:
  timeout, no redirects, `raise_for_status`, raises its own `LlmError`.
  No OpenAI SDK dependency — the codebase hand-rolls `ics_read` rather
  than take a dependency for four fields per VEVENT, and this is one JSON
  POST.
- **`domain/triage_prompts.py`** — pure prompt builders and response
  extraction (fence-stripping), no I/O. The classify prompt embeds the
  pass-1/pass-2 rules from `.claude/skills/triage-leads/SKILL.md` (collapse
  by title stem, the three repeated-title mechanisms, the scope ruling's
  keep/dismiss table, the `!_` venue-prefix signal, the 【当選者限定】 rule,
  leave-unclear-leads-out). The draft prompt embeds the skeleton-draft
  vocabulary and the `rounds: []` instruction. Prompts are code constants;
  they change by commit, like the calendar roster.
- **`app/triage.py`** — the runner, same layer and discipline as
  `discovery.py` (imports `domain/`, `app/llm.py`, `app/fetching.py`,
  `db.service`; nothing in `db/` imports it).
- **`TriageRun`** (`db/models.py`) — one row per run: requested/started/
  finished timestamps, status, the proposed prune YAML, per-phase counts
  (leads seen, productions, dismissals proposed, drafts created, failures
  skipped), and tokens in/out so the owner can see spend. **The request
  stamp IS the row**: the button inserts a `status="requested"` row (unlike
  the sweep, which stamps the `DiscoveryState` singleton — triage wants
  per-run history, so the request and the record are one thing). The tick
  picks up the oldest requested row; the two-halves failure pattern then
  means the loop handler marks that row failed on the cleaned transaction
  after a rollback, or the rollback would restore it to `requested` and the
  run would re-fire every 60 seconds — the same trap `stamp_discovery_run`
  documents, in row form.
- **Routes** (`routes/discoveries.py`) — `POST /admin/discoveries/triage`
  (writes the request stamp, 303 back, button disabled while one is
  pending — the `sweep_now` pattern verbatim, because a run is minutes long
  and no HTTP request may hold it) and a status strip on
  `/admin/discoveries` (run in progress / last run's counts + spend, a
  "Review prune plan" link, a pointer at the pending-drafts queue).
  `GET /admin/discoveries/prune` gains an optional `?triage_run=<id>` that
  prefills the textarea from the stored YAML — the owner still walks
  paste → plan → apply unchanged.

### The run, in order

1. Tick sees `triage_requested_at` set and `triage_enabled` true.
2. Load all open (undismissed) leads.
3. **Classify**: one DeepSeek call (the backlog's lead lines fit trivially
   in a 1M context) returns the prune YAML plus the survivor productions,
   each with its merged lead ids and representative Eventernote event id.
   Prune YAML is validated by `parse_prune_list` and stored on the run row.
   One edge is handled in code: `parse_prune_list` deliberately raises on a
   list with zero entries, so a run where the model proposes no dismissals
   stores no prune YAML (an empty proposal is absence, not an error) rather
   than failing the run.
   Calendar-sourced survivors (namespaced ids, no Eventernote page behind
   them) are counted and left for the agent workflow — there is nothing to
   fetch, and the triage skill already calls them the weaker starting point.
4. **Draft**: for up to `TRIAGE_DRAFT_CAP` (25) survivors, oldest first:
   fetch the Eventernote event page via `app/fetching.py` (host-pinned,
   sequential, 1s politeness pause — 25 parallel requests at a third party
   is how an IP gets blocked), one DeepSeek call per production, validate
   through `parse_drafts`, strip any rounds, store via
   `create_pending_drafts`. A production whose fetch or call or parse fails
   is skipped and counted; the run continues.
5. Finish: write counts and token totals, queue ONE admin `Notification`
   through the outbox (invariant 4; an ordinary notice, NOT in
   `UNREPORTED_NOTE_KINDS` — it reports on a triage run, not on
   deliveries), stamp the run done.

The cap bounds both spend and wall-clock (~7–8 min a press at 25). Pressing
again works through the next chunk; the classify phase re-running is
harmless because it reads only still-open leads, and drafts for productions
already sitting in the pending queue are the one duplication risk — the
runner therefore skips a survivor whose representative Eventernote event id
already appears in an uncommitted `PendingDraft`'s text (cheap containment
check, not a schema change).

### The two scheduler rules that apply here (both learned the hard way)

- **Beat the heartbeat inside the per-production loop.** A capped run still
  occupies the tick for minutes; without a beat per production, `/healthz`
  pages the owner about a healthy app. Same rule, same reason as the sweep.
- **The completion stamp follows the `stamp_discovery_run` two-halves
  pattern.** The runner's own stamp only flushes; `scheduler/loop.py`'s
  handler re-stamps and commits on the cleaned transaction after a
  rollback. Without the second half, a run that dies leaves the request
  stamp set and re-runs — 25 fetches and 26 LLM calls — every 60 seconds
  forever, and tests stay green because they never roll back.

## Safety rails (summary)

| Rail | Enforcement |
|---|---|
| No invented deadlines | Drafts emitted with `rounds: []`; runner strips rounds in code regardless of what the model returns; pinned by test |
| No new concert writes | Drafts land as `PendingDraft`; `import_commit` unchanged as sole writer |
| No un-reviewed dismissals | Prune YAML is stored text; only the existing plan screen's apply step dismisses |
| Spend bounded | `triage_enabled` gate + admin-initiated only + `TRIAGE_DRAFT_CAP` per press + tokens logged per run |
| No SSRF widening | `app/fetching.py` with `eventernote.com`; DeepSeek host only via `app/llm.py` |
| Degradation | Per-production skip-and-count; malformed output dies at the existing parser boundary |

## Testing

Fake LLM client (canned responses) and fake fetch; no network in tests, as
everywhere. Pins:

- rounds present in a model response are stripped before storage (the
  safety property, asserted on the stored `PendingDraft` text);
- `triage_enabled=False` means the tick never picks up a stamp and the
  route still 303s (gated, not guarded — mirror the rehearsal reasoning);
- request-stamp pickup and the button-disabled-while-pending render;
- the re-stamp-after-rollback path (a runner that raises mid-run leaves the
  request stamp cleared — the poisoned-session test, mirroring discovery's);
- classify-phase prune YAML round-trips through `parse_prune_list`; a
  malformed prune response fails the run loudly rather than storing junk;
- a draft response that fails `parse_drafts` is skipped and counted;
- the duplicate-survivor containment check;
- logged-in GET render test for the status strip and the prefilled prune
  form (every page has one).

Prompt quality is NOT tested in CI (no network, and quality is a judgment).
Calibration is operational: the first real run is one capped batch, read
critically — the prune plan before applying, the zh/en translations in the
preview — at a cost of cents. If V4 Flash's Japanese-domain judgment is not
good enough, the feature has a natural fallback: classify-only (skip the
draft phase) still cuts the queue by the largest factor.

## Deploy notes

New `.env` keys on the server (`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`,
`TRIAGE_ENABLED=true`); one Alembic migration (`TriageRun` + the request
stamp column; UTCDateTime swap per the migration checklist). No Caddy
change. The API key lives only on the server and in the owner's local copy,
like every secret.
