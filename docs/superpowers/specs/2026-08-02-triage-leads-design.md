# Triage leads: prune in bulk, import in bulk, and the skill that feeds both

Discovery produces leads. 443 of them are open, and the loop does not close: a
lead becomes a tracked concert only when somebody finds the official ticket page,
extracts the rounds, groups the legs and writes the trilingual titles.

The owner's flow, in his words and in order:

1. Every lead goes to the agent. He does not prune by hand — 443 is too many.
2. The agent works out which are not supported.
3. **A list he imports back to mass-prune the inapplicable leads.**
4. The survivors run through the creation process and get their YAML.
5. **That must handle many concerts without copy-pasting each one.**

Three pieces, in that order. The skill is last because it produces the artifacts
the first two consume, and a skill that emits a format nothing reads is a
proposal, not a workflow.

## What is already settled

- **The scope ruling (2026-08-02).** Only ticketed concerts/tours and
  radio/talk/番組イベント get catalogued. Everything else is a dismissal. The
  classify pass is binary, not a judgment per class.
- **The taxonomy** (`docs/discovery-lead-taxonomy-2026-08-01.md`) — seven
  classes read off all 443 leads, with the collapse finding: a nine-performance
  run is nine leads, so grouping by title stem takes 443 to roughly 120-150
  productions, and the scope ruling takes that to something like fifty.
- **`DismissReason`** shipped 2026-08-01 with a value per class.
- **`import_commit` remains the only write path into `concerts`.** Nothing here
  changes that, and no phase below writes a concert without a human looking at a
  preview first.

## Phase 1: prune by imported list

`GET /admin/discoveries/prune` (paste form), `POST .../prune` (plan),
`POST .../prune/apply` (commit). Admin-only, English-only, in
`routes/discoveries.py` beside the surface it serves.

### The format, and why it keys on the Eventernote id

```yaml
dismiss:
  stage:
    - 481833   # ミュージカル信長 9/21
    - 481832
  release:
    - 466181
  free:
    - 481300
```

A mapping of `DismissReason` value to a list of **Eventernote event ids** —
never internal lead ids. The agent's input is the DM copy block, which carries
`https://www.eventernote.com/events/486243`; the internal id appears nowhere it
can see. `discovered_events.eventernote_event_id` is unique, so the join is
exact. A comment after an id is ignored by the parser and is there so a human
reading the file can tell what he is about to prune.

Reasons outside the eight are a parse error naming the bad key, not a silent
skip: this file's entire purpose is to write a column whose value is that every
row in it is a real judgment.

### Plan before write, following `/admin/import/tags`

The plan screen shows, per reason, what will be dismissed, and names three
classes of problem rather than swallowing them:

- **Unknown ids** — in the file, not in the queue. Named and counted. Usually a
  stale file or a typo'd id.
- **Already dismissed** — named, skipped, and NOT re-stamped. `dismiss_lead`
  already returns False for these; the plan surfaces it instead of reporting a
  write that did not happen.
- **Ids appearing under two reasons** — a contradiction in the file, refused
  outright rather than resolved by ordering. Last-one-wins would make the result
  depend on dict iteration order, which is exactly the kind of silent behaviour
  the tags importer was built to avoid.

`/apply` **re-parses and re-plans from the pasted text**, exactly as
`import_tags_apply` does. The browser sends the file back and a confirmation,
never a list of ids to dismiss — nothing the client says can name a lead the
file did not.

### What it deliberately does not do

- **It never un-dismisses.** There is no reverse operation and no `restore` key.
  A lead dismissed in error is recoverable only by the sweep never raising it —
  which is the existing, accepted semantics, not something this phase worsens.
- **It never creates a concert.** Same rule as the surface it lives on.
- **It writes through `dismiss_lead`**, once per lead, not a bulk UPDATE. A
  second writer would drift from the single-writer rule the same way a second
  `record_round_outcome` would, and the loop is 300 rows once, not a hot path.

## Phase 2: many drafts, one paste

### The format is YAML's own

A multi-concert file is several YAML documents separated by `---`. No new
format, no wrapper key, and `domain/yaml_import.py`'s `parse_draft` is untouched:

```python
def parse_drafts(text: str) -> list[ParsedDraftResult]:
    """Split a multi-document paste and parse each. One bad document is
    skipped and NAMED, never fatal -- the house rule the whole draft
    vocabulary follows, and at fifty concerts a single typo must not cost
    the other forty-nine."""
```

`yaml.safe_load_all` only, like `safe_load` before it — a draft is pasted text
from outside the app.

### Persistence, and the one place this adds state

**A new table, `PendingDraft`.** This is the deliberate exception to the app's
no-step-state habit, and it needs its reason stated rather than assumed.

`/setup` holds no state because each screen renders current DB truth, which is
what makes it re-runnable and tamper-safe. This is not that. It is a **work
batch**: fifty to a hundred concerts, each needing a human to read a preview,
which is not one sitting. Carrying the remainder in a hidden form field would
make a closed tab lose the batch, and re-pasting a hundred drafts is the exact
thing the owner asked to stop doing.

```
PendingDraft
  id
  draft_text        the single document, verbatim as pasted
  title             parsed out for the list, so rendering needs no re-parse
  created_by        FK users.discord_id
  created_at
  committed_at      NULL until it becomes a concert
  concert_id        FK concerts.id, ON DELETE SET NULL -- what it became
  discarded_at      NULL unless waved off without importing
```

A row is done when `committed_at` or `discarded_at` is set. Nothing cleans up
automatically: a committed batch is a record of what was imported and when, and
deleting it would throw away the only trace linking a draft to the concert it
produced.

### The flow

`GET /concerts/import/pending` lists uncommitted drafts — title, leg count,
round count, and the tags that did not match. "Review" opens the EXISTING
preview (`import_preview.html`), prefilled by the existing path, with the pending
row's id carried through. Committing runs the existing `import_commit`, stamps
`committed_at`/`concert_id`, and returns to the list with that row gone.

**Every concert still passes through one human-reviewed preview.** The feature
removes the copy-paste, not the review — the review is the work, and at a hundred
concerts it is the only thing standing between a bad draft and the catalogue.

## Phase 3: the `triage-leads` skill

`.claude/skills/triage-leads/SKILL.md`, invoked with the DM's copy block (or
`/admin/discoveries`) pasted as arguments. It CALLS `add-concert` for the drafts
rather than duplicating it — that skill owns the schema and is pinned to the
parser by a test.

Three passes, cheapest first, and the order is the design:

**1. Collapse by title stem.** No network. The largest single reduction
available. Two mechanisms produce repeated titles and they want opposite
treatment, which is the trap this pass exists to avoid:
- 学園アイドルマスター LIVE TOUR is ONE concert with eight legs.
- 『Liella!と結ぶプロジェクト』お渡し会 is eleven events because each member got
  her own slot at one venue on one day — one event, or none.

**2. Classify against the scope ruling.** No network. Keep ticketed
concerts/tours and radio/talk/番組イベント; everything else is a dismissal with a
reason. Output: the Phase 1 prune file.

Two rules the taxonomy earned and the skill must carry:
- The `!_` venue prefix (`!_東京都内某所`) is the strongest single signal for
  release events, and it is a signal, not a rule — a few cruises and fan events
  use it too.
- 【当選者限定】 means a lottery HAPPENED. A blanket dismiss-on-keyword loses
  exactly the leads in that class that mattered.

**3. Research the survivors.** The only pass that needs a ticket page. Emits the
Phase 2 multi-document file, batched rather than all at once.

The skill proposes; it never writes. Both artifacts are files the owner imports
through a surface that plans before it commits.

## Testing

Per phase, the properties that matter rather than a count:

- **Phase 1:** an unknown id is named and does not stop the rest; an
  already-dismissed lead is skipped without re-stamping; the same id under two
  reasons is refused; `/apply` ignores anything the browser adds beyond the file;
  an invalid reason key is a parse error naming the key.
- **Phase 2:** one malformed document does not lose the others; a committed row
  leaves the list and records its `concert_id`; a draft pasted twice does not
  produce two concerts silently (the `event_id` 409 already covers this — the
  test pins that it still fires through the pending path).
- **Phase 3:** the skill's example prune file parses against Phase 1's parser,
  and its example multi-draft parses against `parse_drafts`, both pinned by
  tests — the same guarantee `add-concert`'s example already has.

## Not in scope

- Un-dismissing. No reverse operation exists and none is added.
- Auto-committing concerts. Every one passes a human-reviewed preview.
- A classifier living in the app. The skill classifies; the app records what a
  human accepted. Scoring the two against each other becomes possible once
  `dismiss_reason` has real data in it, and is its own work.
- Anything for the five out-of-scope classes beyond dismissing them
  (WISHLIST #8), and the A/B cast gap (WISHLIST #9).
