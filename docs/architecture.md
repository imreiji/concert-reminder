# Architecture notes

The per-module detail that used to live in CLAUDE.md's Layout section. It was
moved here on 2026-08-07: CLAUDE.md is loaded into every session, this is read
when you are about to touch the module it describes, and at ~50 KB it was the
single largest claim on that budget.

**Nothing here was rewritten or summarised** -- the text is the original,
verbatim. CLAUDE.md keeps the module MAP and every hard rule; this keeps the
reasoning behind them, which is what stops a later pass from "simplifying"
code that is deliberate.

Read the entry for a module before changing it. The recurring shape is a
measurement or an incident that a reasonable-looking edit would undo.

## Layout

- `src/app/domain/` — pure logic, NO I/O, no discord/fastapi/sqlalchemy
  imports. Reminder math in `reminders.py`, JST↔UTC conversion in
  `timezones.py`, ramen.events HTML parsing in `ingest.py` (takes an HTML
  string, returns a draft — no httpx call itself), `.ics`/YAML export
  formatting in `ics_export.py`/`yaml_export.py`, and editor-supplied URL
  scheme validation in `urls.py` (`clean_url` normalizes an http(s) URL or
  raises `UnsafeURLError`; see invariant 7). `urls.py` also holds
  `safe_next`, the open-redirect guard on the post-login return path --
  same family, opposite direction (a same-origin PATH or None, never an
  absolute URL), and it returns None rather than raising, since a bad
  `next` is a stale link, not an editor mistake worth a 422.
  `tags_yaml.py` is the TAGS vocabulary and holds BOTH halves --
  `tags_to_yaml` and `parse_tags` in one module, deliberately. Splitting a
  format's serializer from its parser is how the catalogue round-trip hole
  opened: the concert export looked complete until something had to read it
  back, and only then did it turn out a tag had no identity to key on. Keep
  them together, and add fields to both at once. Its parser follows
  `parse_draft`'s philosophy -- warnings over failures, one bad row skipped and
  named, only an unusable file raises -- and `RESTORE_NOTES`, the text written
  into every export, lives here too because it documents the format.
  `eventernote.py` parses an actor's events page the way `ingest.py` parses a
  ramen.events page -- HTML string in, rows out, no httpx -- and skips-and-counts
  an unreadable row rather than raising, so a site redesign degrades to "found
  nothing" instead of crashing a scheduler tick every day. Its `future_events`
  is a TAKE-WHILE, not a filter, and that is the whole economy of discovery:
  rows are strictly newest-first (measured, and pinned by a test), so stopping
  at the first past row means ONE fetch per artist -- ~86 a sweep instead of the
  ~1,548 that reading all 18 pages of every artist would cost. A filter would
  be correct and eighteen times as expensive, so don't "simplify" it into one.
  An event dated TODAY counts as future. `actor_events_url` builds
  `/actors/<name>/<id>/events` from OUR name because **the name segment is
  DECORATIVE** -- `/actors/x/5847` resolves the same as the site's own path
  (verified against the live site) -- so only the id is identity, which is also
  why `actor_id_from_url` reads nothing else out of a stored URL. The site's own
  slug sometimes disagrees with the displayed name; that is fine and expected.
  `discovery_message.py` composes the discovery DM and is its own module for the
  reason `tags_yaml`/`tags_diff` are two: that one is about READING a source,
  this is about COMPOSING a message. The message is deliberately the same content
  TWICE -- a readable markdown list, then a fenced block carrying the same leads
  as a paste-ready agent prompt -- because Discord does not linkify inside a
  fence, so one half stays clickable and the other stays copyable. The 2000-char
  limit is a hard budget and **the block yields first**, saying so on its last
  line when lines are dropped; every free-text field is clipped and there is a
  final prose truncation floor, because past Discord's real cap discord.py
  raises and the WHOLE DM is lost rather than trimmed. `build_discovery_dm` takes
  `budget=None` for `/admin/discoveries`, which has no character limit and is
  where the DM's "+N more" points. Its `Lead.deadline` renders as an ADDITIVE
  `申込締切 ` prefix on the date and never reorders the line, because the
  `triage-leads` skill reads the copy block by field position; `Lead.source`
  gates the Eventernote LINK, since a calendar lead has no page behind it.
  `ics_read.py` is the RFC 5545 half of calendar discovery -- text in,
  `IcsEvent` rows out, no httpx, exactly as `eventernote.py` takes HTML.
  Hand-rolled rather than a new dependency (the feeds are Google Calendar
  exports and the app wants four fields per VEVENT), warnings over failures
  like every parser here: a VEVENT missing UID/SUMMARY/DTSTART is skipped and
  COUNTED, and only a body with no `BEGIN:VEVENT` structure at all raises
  `IcsError` -- so a feed that rots degrades to "found nothing" on the status
  line instead of crashing a scheduler tick every day. **It keeps only the
  DATE half of a DTSTART**: inventing a midnight instant would put a
  deadline-shaped fake into an aware-UTC schema (invariant 1), and a lead's
  date is a pointer, not a deadline -- which is also why the ≤1-day skew of a
  UTC-suffixed stamp at the JST boundary is accepted rather than corrected.
- `src/app/db/` — models, session, and the business logic that touches the DB
  (discord-free so it's testable). **`service.py` is a FACADE, not a module
  with logic in it**: it re-exports every name the layer defines, so
  `from app.db.service import X` still reaches everything and bot/web code
  needs no knowledge of the split. ADD a name to a module below and you must
  add it to `service.py`'s import list too — `tests/test_service_facade.py`
  fails if the two disagree. The work lives in:
  - `core.py` — the engine, and the ONE file still worth its size: queue
    sync, retrieval for the scheduler, the personal board, the concert page's
    rounds-by-leg, Discover status, DM button actions, presets/subscriptions,
    users, and the ORM→domain adapters. Those nine sections are MUTUALLY
    recursive (measured: one strongly-connected component in the call graph),
    so no cut through them produces modules that import in one direction.
    Splitting it needs a design change, not a file move — see WISHLIST.md.
  - `tags.py` — the tag catalogue, membership and slug minting
    (`create_tag_row`/`assign_tag_slug`, invariant 3's single construction
    path). `venues.py` — the legs→concert VENUE rollup, and the one module
    that depends on `tags.py`.
  - `drafts.py` (`PendingDraft` rows) and `discovery_events.py`
    (`DiscoveredEvent` leads) — named that, NOT `discovery.py`, because
    `app/discovery.py` is the sweep RUNNER and imports this layer.
  - `setup_flow.py` (the `/setup` capture flow), `calendar_feed.py` (the
    personal `.ics`), `rehearsal.py` (the flag-gated harness's data layer).
  - `delivery.py` (`delivery_log` + the per-tick digest), `broadcast.py`,
    `ops_alerts.py` (health checks → admin DMs).
  - `audit.py` (`ConcertAudit`), `phrases.py` (the round-label library),
    `translation_gaps.py` (the edit pages' "what's missing" notice).
  - `quiet_ladders.py` — round watch: which catalogue concerts hold no future
    deadline, the two stamps that track them, and the per-tick pass that DMs
    the admins once when one newly goes quiet. Its own entry below.
  - `tokens.py` — secret tokens at rest: one `hash_token`/`generate_*_token`
    shape shared by the calendar feed and the agent read API's
    `api_token_hash`, so the two never carry two hash implementations that
    could drift apart silently (a mismatched hash just means a token that
    stops matching, with no error anywhere).
  Dependencies point ONE way: feature modules import `core`, `core` imports
  none of them, and the facade imports everything and is imported by nothing.
  A feature module must never `from app.db.service import ...` — that is a
  cycle, and one that surfaces or not depending on which module a process
  imports first. Import `app.db.core` (or the sibling) directly.
- **Round watch (`db/quiet_ladders.py`)** -- shipped 2026-08-11, design in
  `docs/superpowers/specs/2026-08-11-round-watch-design.md`. Discovery answers
  "what exists that you are not tracking"; this answers "what changed about
  what you already track". A round announced AFTER a concert was imported is
  otherwise invisible: nothing re-visits a concert's own pages, and the
  reminder machinery can only plan from rounds it was given. That failure is
  silent and it is the app's core promise failing -- a user who followed the
  right artist, got the new-event DM, and still missed the lottery.
  **The predicate lives here and only here:**

      not all_legs_cancelled(days)
      and (a live dated leg is in the future  or  no live leg is dated at all)
      and next_anchor_at(concert, now) is None

  - **`next_anchor_at` is REUSED, never restated.** `db/core.py` already
    computed "the earliest future moment among live rounds" for the agent read
    API (`_next_anchor_iso`); it was PROMOTED to
    `next_anchor_at(concert, now) -> datetime | None` and the ISO version is now
    a one-line wrapper over it. Two definitions of "future anchor" free to
    drift apart is the defect this prevents: the page and `/api/v1` answer
    identically by construction, and the predicate test that pins it is the one
    asserting a concert WITH a future anchor is absent from the list. Do not
    re-derive the anchor here, and do not transliterate the predicate into SQL:
    candidates come from ONE unfiltered `select(Concert)` and every clause runs
    in Python against the loaded rows, because `is_round_cancelled` is Python.
    The catalogue is ~157 productions, so a full scan is cheaper than a second
    copy of the rule that would then be free to disagree with the first.
  - **"Dateless" means ZERO LEGS, not an undated leg.** `ConcertDay.starts_at_utc`
    compiles to `DATETIME NOT NULL`, so a leg always carries a date and a
    concert cannot hold a mix of dated and undated ones. The first draft of the
    spec ruled on "dated legs decide when a concert has both" -- a state the
    schema forbids, whose test would have asserted something impossible. What
    the leg clause actually distinguishes is a concert with no `ConcertDay`
    rows AT ALL (a skeleton import such as ブシロード20周年記念ライブ, imported with no
    dates because its page says 出演日程やチケットの詳細は後日発表, and every
    `duplicate_concert` clone) from one whose legs have all been performed. The
    LATEST live leg decides, so a tour whose first night has passed and whose
    last has not is still on the list. Past concerts fall off by themselves --
    the list drains and never accumulates, so nothing needs to expire it.
  - **The pass runs EVERY TICK, with NO cadence clock**, which is the one place
    it departs from the discovery sweep it otherwise copies. The sweep's
    24-hour clock protects 86 third-party fetches ending in a DM: expensive,
    rude to repeat, not idempotent. This is a query and a diff over the local
    catalogue, and `reconcile_quiet_ladders` is SELF-IDEMPOTENT -- once a
    newcomer is stamped it is no longer a newcomer, so a re-run announces
    nothing. A clock would therefore protect nothing and would delay a notice
    by up to a day; it would also make `quiet_since_utc` mean "N days since the
    pass noticed" instead of a real measurement. Adding one is the edit to
    refuse, and the test that must fail if someone does is "an immediate second
    run announces nothing".
  - **The stamps and the queued notice must commit in ONE transaction.** That
    pairing is the whole of the notice's exactly-once property: commit the
    stamps first and a crash loses the DM forever (a stamped concert is never
    a newcomer again); commit the notice first and a crash repeats it. So
    `reconcile_quiet_ladders`/`run_quiet_ladder_pass` only FLUSH, and
    `scheduler/loop.py`'s round-watch block owns the single commit. Do not add
    a commit inside the db layer here.
  - **Both stamps clear together.** `quiet_since_utc` is system-owned (set on
    entry, cleared on exit) and `ladder_rechecked_at_utc` is the admin's
    Checked button; when a concert LEAVES the list the pass clears both,
    because both belong to the CURRENT quiet spell. A concert that goes quiet,
    is checked, recovers a round and later goes quiet again must arrive
    unchecked -- the earlier check answered a different question.
    `quiet_since_utc` is named "first observed quiet", not "went quiet",
    because the migration (`0671edabe2ac`) stamps it on EVERY concert with a
    blanket `UPDATE`, not on the quiet ones. That is deliberate and is the
    reason to leave it alone: a predicate backfill would mean transliterating
    `next_anchor_at`/`is_round_cancelled` into SQL, where the copy is free to
    disagree with the real one -- the same drift the promotion of
    `next_anchor_at` exists to prevent. A non-quiet concert's stamp is simply
    cleared by the first pass; what the blanket buys is that NO concert is a
    newcomer on that pass, so the first tick after deploy DMs nothing instead
    of announcing the entire back catalogue. Under the name "went quiet" every
    one of those stamps would be a lie.
  - **Never wrap this in `session.no_autoflush`.** `_all_concerts_for_quiet_scan` loads with
    `execution_options(populate_existing=True)` -- needed because
    `SessionMaker` sets `expire_on_commit=False`, so a session that outlives a
    commit keeps its identity map and with it a stale `days`/`rounds`
    collection that a fresh SELECT would not otherwise replace. But
    `populate_existing` overwrites in-memory attributes even when dirty, so
    with autoflush suppressed a stamp set just before the call is silently
    discarded rather than written.
  - **The page derives membership live on every load** (`quiet_ladder_rows`),
    so a scheduler failure can never make it wrong; the pass owns only the two
    things a query cannot, the entry stamp and the DM. That is also why there
    is no "run now" button -- unlike the sweep, there is nothing to run. Rows
    sort never-checked first, then longest-since-checked, then longest-quiet,
    and a checked row DIMS but is never hidden: the stamp answers "have I
    looked at this", and hiding would silently promote it to "is this
    resolved", which it cannot answer. A concert checked in March genuinely
    does grow a 一般発売 in July.
  - The notice is `kind="quiet_ladder"`, `concert_id=NULL` (a digest naming
    several concerts is nobody's embed, and NULL already makes
    `record_deliveries` skip the title lookup), one row per
    `sorted(settings.admin_ids)`, with `ensure_user` called ONLY when
    `session.get(User, admin_id)` returns None -- unconditional would overwrite
    a real admin's username with the numeric placeholder every single tick. It
    is deliberately NOT in `UNREPORTED_NOTE_KINDS`: that set is for notices
    that REPORT ON deliveries, and this one reports on the catalogue. No
    newcomers means NO DM, and at a per-minute cadence that is load-bearing
    rather than tasteful -- a "nothing found" note here would be 1,440 DMs a
    day. Silence is the pass's normal output.
- **Round poll (`app/round_poll.py`, `web/routes/quiet_ladders.py`)** -- phase 1
  shipped 2026-08-13 (design in
  `docs/superpowers/specs/2026-08-13-round-poll-design.md`), phase 2 shipped
  2026-08-14 (design in
  `docs/superpowers/specs/2026-08-14-round-poll-phase-2-design.md`), flag-gated
  by `settings.round_poll_enabled` (off by default). Round watch above answers
  "which tracked concerts hold no future deadline"; this is what actually
  RE-VISITS one, once a day, and records what its own official page now says
  as a `RoundProposal` -- a claim, never a `Round` until an operator approves
  it. Read the module docstring before touching `round_poll.py` and the module
  docstring of `web/routes/quiet_ladders.py` before touching the apply/dismiss
  routes -- both are unusually thorough and most of what follows here is a
  shorter pointer into them. The phase-1 bullets below (stamping, the dedupe
  key, the wall-clock budget, the digest) are unchanged by phase 2 in their
  REASONING; the `_fold_duplicate_keys` bullet is the one exception and says so
  in place, because phase 2 gave it a second bucket to fold. What follows them
  is phase 2's own set.
  - **`Concert.ladder_polled_at_utc` is a THIRD stamp, deliberately not a reuse
    of `ladder_rechecked_at_utc`.** The latter is the OWNER's "I looked at
    this" and is what orders `/admin/quiet-ladders`, the worklist a human
    reads. Writing it from the poll -- even to "help" by marking a freshly
    re-read concert attended -- would silently sink every polled concert to
    the bottom of that list and tell the owner a human had reviewed something
    only a model had glanced at. `quiet_ladder_rows` must keep sorting by
    `ladder_rechecked_at_utc` alone; the poll reads and writes only its own
    column.
  - **The resume order is a re-sort the poll does itself, and it is load-
    bearing -- the plan that specified this pass first assumed it was free and
    was wrong.** `quiet_ladder_rows` sorts by `ladder_rechecked_at_utc`, the
    human's stamp, which never moves on the poll's account. Consumed in that
    order the candidate sequence is byte-identical every run: the moment the
    240s wall clock bites (see below), the same head of the list is re-read
    every day and the tail is never reached at all -- the exact starvation
    `discovery.py`'s own budget comment records ("a fixed start point would
    have been worse than no budget"). `_candidates` in `round_poll.py`
    re-sorts the SAME candidate set by `ladder_polled_at_utc` (never-polled
    first, then oldest poll, stable so `quiet_ladder_rows`' own order survives
    as the tiebreak) before walking it, which is what makes tomorrow's run
    resume where today's stopped. Removing that re-sort on the reasoning that
    "the cursor is already handled" reopens the starvation with no test to
    catch it, since a short test run never spends the whole budget.
  - **A concert is stamped when an attempt is SPENT, success or failure
    alike** -- `record_ladder_polled` runs after `_poll_one` whether it raised
    or not, with two named exceptions. `SQLAlchemyError` poisons the session
    and is re-raised to abandon the run. `ConcertVanished` is the carve-out
    OUT of that rule: `concert_export_yaml` opens with `session.refresh`,
    which raises `InvalidRequestError` -- a `SQLAlchemyError` -- when the
    concert has gone since `_candidates` loaded it, and that is a failed READ
    on a perfectly usable session, so it costs ONE concert (counted and named,
    exactly as `_candidates` treats the same race one step earlier) rather
    than the run and its digest. That one is deliberately NOT stamped either,
    and the hazard behind that is real but was first written down with the
    wrong cause. With the row still in the identity map,
    `record_ladder_polled` issues an UPDATE matching zero rows and raises
    `StaleDataError` at flush -- the same abandoned run by the back door. What
    PREVENTS that is the `expunge`, not the skip: measured 2026-08-14, either
    alone suffices and only removing BOTH raises. Both are kept anyway -- the
    expunge so nothing later reads the phantom back out of the map, the skip
    because a deleted concert has nothing to rotate -- and the skip cannot
    reopen starvation, since `quiet_ladder_rows` never returns a deleted row.
    Stamping only successes, however, WOULD: it would let one
    permanently-broken page -- a 403, a host that redirects off the approved
    set -- hold the head of the queue forever and starve everything behind it,
    reintroducing the same starvation through the sympathetic-looking rule. A
    concert that cost NO attempt (no URL, an unapproved host) is left
    unstamped on purpose: it consumed no budget, so it starves nothing, and
    stamping it would make "when did the poll last actually read this page" a
    lie.
  - **`dedupe_key` (`domain/round_proposals.py`) is keyed on the label AND
    `opens_at_utc`, truncated to MINUTE precision, and both halves matter.**
    Truncation exists because the two sides of the diff disagree below a
    minute by construction: a proposed round's open time is always parsed from
    the draft vocabulary's `"%Y-%m-%d %H:%M"` text and lands on `:00`, while a
    `Round.opens_at_utc` a human or an AI draft seeded can carry seconds
    (`yaml_import._dt` accepts a YAML timestamp with seconds verbatim). Skip
    the truncation and the same real-world round produces two different keys
    depending on which side of the diff reads it, and is re-proposed every
    single day forever. Keying on the label ALONE, to "simplify" the key,
    would instead swallow a moved date: a round whose opening was pushed from
    Sept 3 to Sept 10 shares the label with the one already on file, so it
    would read as the SAME round rather than a new offering, and an owner who
    dismissed "opens Sept 3" would never be shown "opens Sept 10" at all --
    silently, since a key collision looks identical to no proposal.
  - **`_fold_duplicate_keys` collapses two readings of ONE key inside a single
    reply, and it is not the same de-dupe `classify_proposals` does.**
    `classify_proposals` (`domain/round_proposals.py`) diffs the proposed
    rounds against the ones the concert HOLDS; two candidates that differ only
    in `apply_closes_jst` or in which legs they name are neither held, so both
    survive it, both reach `upsert_proposal`, and the second's SELECT finds the
    row the first just flushed. That is CONTENT LOST, not merely a tally one
    too high: the second reading silently overwrites the first's closing time
    and evidence quotes, and both are counted, because the row still carries
    today's `first_seen_at`. First sighting wins the row and the collapse is
    named in the digest, because this module names every discard. It runs
    BEFORE the dismissed check, or a duplicate of a refused key would count as
    two dismissals of one round. Phase 2 made it run TWICE, once per bucket
    (`fresh`, then `changed`): `classify_proposals` puts a proposed round in
    exactly one bucket, so the two can never share a key and each folds against
    itself alone -- and a doubled reading of a round the concert already holds
    does exactly the same damage as a doubled new one. The `changed` call is
    the one nothing defended for a while: every fold test seeded a concert
    holding no round, so both readings landed in `fresh` and deleting the
    second call left the file green (`test_round_poll.py`'s
    `test_two_readings_of_one_CHANGED_round_keep_the_first_too` is what pins it
    now). NOTE for anything reading an older copy of this bullet: the pure diff
    was called `new_proposals` until commit `6258683` renamed and re-shaped it
    into `classify_proposals`; the surviving `new_proposals` is
    `PollReport.new_proposals`, a COUNTER, which diffs nothing.
  - **The run is bounded by a WALL CLOCK (`ROUND_POLL_BUDGET_SECONDS`, 240s),
    never by a count, and AI triage's 511-lead classify failure does not
    transfer here -- the two failures are shaped differently, not merely
    smaller.** Triage's classify pass put N leads into ONE prompt and outgrew
    DeepSeek's output cap; this pass is one prompt PER CONCERT, so a page that
    is too long or a reply that is too large is a failure of that one
    concert, counted in `PollReport.failed` and named in `failures`, never a
    failure of the run. What a per-item cap cannot bound here is TIME: the run
    sits inline in the 60s reminder tick, `httpx`'s own timeout is per READ so
    a server dripping bytes under the size cap can hold a connection open with
    no deadline at all (which is why `FETCH_DEADLINE_SECONDS` exists on top),
    and `heartbeat.beat()` fires per concert so a run reading fifteen pages
    does not page the owner about a perfectly healthy app. Do not add a
    per-run concert cap in place of the wall clock on the classify precedent;
    the failure mode it would guard against does not exist here.
  - **The pass reuses `domain/round_completion.py:completion_prompt` and
    `domain/round_evidence.py:verify_rounds` VERBATIM rather than owning a
    second prompt or a second safety rule.** `completion_prompt` already asks
    a model to fill in a draft's `rounds:` from one page of text, and a
    concert rendered by `concert_export_yaml` IS such a draft, so the only
    difference between this pass and AI triage's own phase 2, `draft_completion`,
    is which candidate list each one walks. A second prompt here would be a second
    thing to keep in step with `verify_rounds`'s evidence-grounding rule (see
    the `draft_completion.py` entry above for what that rule guards and why
    it is shaped the way it is), and the day the two drifted the symptom would
    be fabricated deadlines, not an error anyone would see.
  - **`new_proposals` counts ROWS CREATED, not sightings**, and this is not
    an incidental choice: the pass re-reads the same page every day, so a
    proposal nobody has reviewed yet is re-diffed and re-`upsert_proposal`'d
    on every run. Counting sightings would report "1 new proposal" every
    single morning until the owner acts on it, which trains them to stop
    reading the one message that also carries the rejection reasons.
    `upsert_proposal` deliberately never refreshes `first_seen_at` on a
    re-sighting, which is the only thing that lets `_poll_one` tell "created
    today" from "seen again" apart.
  - **`changed_proposals` is its own counter and must never be summed into
    `new_proposals`** (phase 2). The two lead to different work: a NEW
    proposal has an Approve button on the draft page, a CHANGED one has only
    Dismiss and a link into the concert editor, because phase 2's write path is
    creates-only (owner ruling, 2026-08-14). Summed, the digest would send the
    owner looking for a button that is not there on some fraction of what it
    counted. It takes its insert-vs-resighting split from the same
    `first_seen_at` test `new_proposals` uses -- a re-sighting increments
    `refreshed` whichever bucket it came from -- so the two counters mean the
    same thing about rows and differ only in which verdict produced them.
  - **Every reason line the digest embeds is clipped to `MAX_REASON_CHARS`
    (200), separately from `DIGEST_LIST_LIMIT`'s cap on the LIST.** A
    rejection reason carries model-supplied text verbatim
    (`round_evidence.py` mints `f"round {label!r}: {reason}"`), so a single
    label the model invented long enough produces a digest body past
    Discord's 2,000-character limit even after every list is trimmed down to
    its last entry -- the shrink loop has nothing left to drop once each list
    is at one line. Past that limit `discord.py` raises `HTTPException`
    instead of truncating, `_send_notification` reports `TRANSIENT_FAILURE`,
    the row is never marked sent, and the digest retries every 60 seconds
    forever without ever being delivered. Removing the per-line clip on the
    reasoning that the list cap already bounds the message reopens exactly
    this: the list cap bounds COUNT, not the length of one entry.
  - **An empty run still queues a digest**, deliberately unlike
    `build_discovery_dm`/`build_quiet_ladder_dm`, which both return `""` on
    nothing found. Those two are worklists, where an empty one is genuinely
    nothing to do; this is a RUN REPORT for a pass that spends a paid key
    against third-party pages once a day and keeps no other record of having
    run (`RoundPollState` carries only `last_run_at`). Its ABSENCE is the
    signal a broken pass is distinguishable from a quiet one at all -- make
    this suppress on an empty result and a dead pass looks identical to a
    healthy quiet day, which is the exact silence this whole feature exists to
    remove.
  - **A proposal's status (NEW/CHANGED/`"resolved"`) is DERIVED at render
    time, by `classify_stored_proposal` (`db/round_proposals.py`), and stored
    nowhere.** A proposal whose round an operator later fixes by hand -- on
    the ordinary concert edit page, not through this feature at all -- simply
    stops matching `anchors_differ` the next time a reader classifies it, and
    BOTH readers drop it with no second write anywhere to keep in step --
    `_draft_row` (`web/routes/quiet_ladders.py`) on the per-concert draft page,
    and `pending_proposal_groups` (`db/round_proposals.py`) on the review
    queue, which only joined it in `5d2c471` (see the bullet below).
    Storing a `changed`/`resolved` flag on the row
    instead would need a second writer to clear it the moment a human's own
    edit made it stale, and a flag with no writer for one of its own
    transitions is exactly the kind of drift this file keeps finding
    elsewhere.
  - **`pending_proposal_groups` (`db/round_proposals.py`), the review QUEUE
    the digest DM links to, did NOT run this filter for all of round 1 of
    this feature's own docs review -- only `_draft_row`, the PER-CONCERT
    page, did.** A proposal an operator resolved by hand kept listing on the
    queue with a phantom pending count; drilling into the concert was the
    only way to discover there was nothing left to do, which is exactly the
    silent worklist-that-never-empties failure phase 1's own gaps entry
    named for this same page. Fixed by reusing `classify_stored_proposal` --
    never a second comparison -- inside `pending_proposal_groups` itself, so
    a concert whose every pending proposal now resolves leaves the queue
    entirely rather than showing an empty-looking group. That function loads
    `Concert.rounds` with `selectinload`, one query for the whole batch:
    the relationship carries no `lazy="raise"` guard the way `venue_tag`
    does, so a bare `concert.rounds` read during async template rendering
    would be a `MissingGreenlet` 500, not a clean failure, and a per-concert
    load inside the grouping loop would turn one review-queue render into
    N+1 queries.
  - **The queue's concert HEADING is the only link to the draft page anywhere
    in the app, and phase 2 shipped without it.** The chain is digest DM ->
    `GET /admin/quiet-ladders/proposals` -> `.../proposals/{event_id}`; there
    is no nav entry to either page by design (an admin-only operational
    surface), so the heading is not a convenience, it is the sole route in.
    Every phase-2 route, form and button existed and was reachable only by
    hand-typing a URL containing an `event_id` -- the heading still pointed at
    `/concerts/{event_id}`, the PUBLIC page, which phase 1 had rightly given it
    when there was nothing deeper to open. The public page survives as the
    secondary link under the heading; the action lives on the heading. Pinned
    by `test_admin_round_proposals.py`'s
    `test_each_concert_heading_links_to_its_own_draft_page` (scoped to one
    concert's group block, since a page-wide assertion has passed on this repo
    with a whole feature deleted) and by its sibling, which reads the href off
    the rendered queue and fetches it.
  - **`anchors_differ` (`domain/round_proposals.py`) is the field list TWO
    different comparisons share, on purpose, and the sharing is the fix for a
    real incident, not a style preference.** `_differs` diffs a poll's
    freshly-parsed `ProposedRound` (JST text, via `proposed_stamp_utc`)
    against a concert's held rounds; `classify_stored_proposal` diffs an
    already-stored `RoundProposal` row (typed UTC columns) against the same
    held rounds -- two different shapes, one shared predicate deciding
    `closes_at_utc`/`results_at_utc`/`payment_deadline_at_utc` (never
    `opens_at_utc`, already spent on the dedupe key). Before the sharing, a
    version with two separately-written copies of this comparison survived 37
    tests after two of the three compared fields were deleted from the second
    one, because nothing forced the two definitions to agree. What that drift
    permits is silent: a results-only or a payment-only postponement reads as
    `"resolved"` and is FILTERED OFF the draft page, with no error and no
    digest line -- the exact class of silent miss this whole feature exists to
    remove, reintroduced inside its own review page. The proof the sharing is
    real: deleting any one field from `anchors_differ`'s list breaks
    assertions in `test_round_proposals_domain.py` (which exercises
    `classify_proposals` / `_differs`) AND in `test_round_proposals_db.py`
    (whose three `test_a_moved_*_alone_is_CHANGED_on_the_stored_side` cases
    exercise `classify_stored_proposal`, one per field) in the same run -- one
    edit, two files, which is the whole proof. Those two, and deliberately not
    `test_admin_round_proposal_draft.py`: that file's only CHANGED fixture
    differs on `closes_at_utc` alone, so dropping the results or payment clause
    leaves it green. It renders the PAGE, and per-field fixtures duplicated
    through a full page render would buy no coverage the db file does not
    already hold. Do not re-inline either
    caller's copy of this comparison "for locality" -- that is the exact edit
    that shipped the incident once.
  - **`POST .../apply` (`apply_round_proposal`) refuses a duplicate TWICE, on
    two different questions, and neither refusal subsumes the other.** The
    write path is creates-only (owner ruling, 2026-08-14): the draft page
    hides Approve on a CHANGED row, but a hidden button is not an
    authorisation check, so the route re-asks `classify_stored_proposal`
    against the concert's LIVE rounds when the POST arrives -- the STORED
    refusal, "was this proposal a change when it was stored". That refusal is
    not enough by itself, and the gap it leaves is on the feature's HAPPY
    path: it keys off the stored proposal, while the round that is actually
    about to be written is built from the FORM, and `opens_at_utc` is both
    half the dedupe key and an editable field. A model that misreads an
    opening date produces a proposal that classifies `"new"` against a round
    the concert already holds; an admin correcting that box back to the real
    time -- exactly what the editable fields are for -- would, without a
    second check, sail past the first refusal and create a second, identical
    round, with `sync_concert` dutifully arming reminders for both. The
    SUBMITTED refusal exists for exactly that: it computes
    `dedupe_key(round_.label, round_.opens_at_utc)` off the round `build_round`
    just constructed from the submission and checks THAT against the
    concert's held rounds. Do not delete either refusal on the reasoning that
    the other already covers it -- each is pinned by a test the other
    survives.
  - **The submitted-side refusal sits after `build_round` and before
    `session.add`, and that ordering is load-bearing.** `Round.concert_id` is
    a plain FK column set through the constructor
    (`Round(concert_id=concert_id)`); `apply_round_fields` never touches
    `Round.concert` or appends to a relationship. But `Concert.rounds`
    (`db/models.py`) carries `cascade="all, delete-orphan"`, so a `Round`
    that WAS associated through the relationship would ride into the session
    on the next flush regardless of any later refusal. Building the round
    first and only adding it to the session after the submitted-side check
    passes is what makes a refusal here leave no pending row behind; wiring
    the round through the relationship at construction time -- even to "make
    the object graph consistent early" -- would make this refusal advisory
    rather than real.
  - **Every leg ticked on the draft page normalises back to EMPTY
    `applies_to`, in `apply_round_proposal`, the same convention
    `Round.applies_to` already carries everywhere else.** Empty means ALL; a
    literal list of every currently-live leg id means exactly the same thing
    today and a DIFFERENT, narrower thing the day a leg is added, since a leg
    added later falls outside a frozen array. Storing what the operator MEANT
    rather than the ids that happened to exist at submit time is the whole
    point, and the leg set that seeds this comparison must come from
    `_live_legs(concert)` -- LIVE legs only, the same list `_leg_selection`
    drew checkboxes for -- never from a bare `concert.days`, which still
    carries a cancelled leg (invariant 2) and would freeze the "all" reading
    the moment one exists.
  - **The apply route is the FOURTH caller of `build_round`
    (`web/routes/concerts.py`), and `sync_concert` after the round is
    flushed is what makes the deadline real.** `build_round`'s own docstring
    names three: "the rich creation form, the edit page's new rows, and the
    URL-import commit route". Routing the apply path through the SAME
    constructor, handed FORM strings exactly as those three do, is what keeps
    the JST parse, the at-least-one-bound 422 and the empty-means-all
    convention from drifting a fourth way. But constructing and flushing the
    `Round` is not the end of the write: `reminder_queue` is a MATERIALIZED
    outbox (invariant 2), and a `Round` written without `sync_concert` after
    it looks exactly like success -- the row exists, the page says applied,
    the concert leaves the quiet-ladder worklist -- while nobody is ever
    reminded of the deadline it names. That is this feature's own failure
    mode, reintroduced by the fix meant to close it, so `sync_concert` runs
    LAST, after `mark_proposal_applied`, inside the same transaction as
    everything above it, with nothing but the `session.commit()` that makes the
    whole thing durable after it.
- **Outcome correction (`clear_round_outcome`)** -- shipped 2026-08-11, design
  in `docs/superpowers/specs/2026-08-11-outcome-correction-design.md`. The
  un-answer: one idea on two surfaces, and **no new answer button anywhere**. A
  correction returns a round (or one leg of it) to the state it was in before
  anyone pressed anything, and the existing capture buttons -- which already
  encode which answer is offerable when -- take over from there. It closes
  WISHLIST's 2026-08-04 entry about an irreversible APPLIED press as a side
  effect rather than as a second feature.
  - **It is the ONLY path that deletes a `RoundOutcome` or a
    `RoundOutcomeDay`**, and `record_round_outcome`'s sibling in every other
    respect (invariant 2): a missing round returns silently, a day the round
    does not cover writes nothing, and it calls `reinstate_user_rules` for the
    concert ITSELF, so no call site can forget the resync. A second deletion
    path desyncs the materialized queue exactly as a second
    `record_round_outcome` would. `day_id=None` clears the whole round --
    outcome row and every day row -- and needs no re-derivation and **no reader
    change anywhere**, because the round returns to "no row", the common case
    the entire model is already built around.
  - **`_rederive_round_from_days` is the per-leg branch's whole tail**, and the
    order of its three cases is the point. A surviving WON row leaves the round
    alone: WON stays WON and **PAID stays PAID**, because demoting PAID re-arms
    a payment reminder for a ticket already paid for (the trap
    `record_round_day_result` guards). No WON row but a covered leg still
    unresolved means APPLIED -- the honest word, since a reader with a per-leg
    result was in the draw, and it is exactly the state the won/lost buttons
    re-open from. Nothing left to wait on means LOST. It deliberately does NOT
    auto-arm the next round on that last branch, unlike `record_round_outcome`:
    a correction is not a new loss, and the arm a genuine loss made is still
    there.
  - **The per-leg clear is offered only for a leg that owns its own
    `RoundOutcomeDay` row, and `row.has_day_results` is the half of that test a
    reader drops.** `row.leg_result is not None` alone is NOT "this leg has a
    row": `_leg_result_for` carries the no-rows-means-all convention, so a
    WON/PAID/LOST round with ZERO day rows DERIVES a leg result for every leg it
    covers -- the inherited pill, which is the round's answer wearing the leg's
    badge. Offer a per-leg clear there and the commonest shape in the app (a
    single-leg WON round, no day rows) posts a `day_id`, `clear_round_outcome`
    deletes nothing, and `_rederive_round_from_days` then lands the round on
    APPLIED instead of unrecorded -- a correction that silently answers for the
    reader. The same rule is what lets the service skip materialization
    entirely: day rows already exist whenever a per-leg clear runs, so the
    convention is already off and `_materialize_implicit_won_rows`' LOST-side
    twin -- which does not exist, and which a naive design would have had to
    invent -- never came up. If a materialization step ever looks necessary in
    there, the caller is offering a per-leg clear where it should be offering a
    whole-round one. `_capture_actions.html` resolves the condition ONCE at the
    top of the macro (`clear_day`) rather than per branch, because **what a
    clear posts is not a property of the branch**: a fully resolved multi-leg
    round -- won Saturday, lost Sunday -- has nothing unresolved, so
    `capture_days` is empty and it never reaches the per-day branch at all, yet
    both legs plainly own their answers and Sunday's press must not throw
    Saturday's ticket away.
  - **The correction follows the capture buttons: wherever a card lets you
    record, it lets you un-record** (owner ruling, 2026-08-11). A `leg_off`
    parameter briefly withheld the clear on a cancelled or opted-out leg; the
    reasoning was fine and the rule was still wrong, because it was STRICTER
    THAN THE CAPTURE RULE BESIDE IT -- and that mismatch, not the correction,
    was the surprise. `capture_gates` takes its `cancelled` input from
    `all_legs_cancelled`, which is CONCERT-level, so a round whose `applies_to`
    names only dead legs on an otherwise live concert keeps both gates open and
    renders under that dead leg alone: measured, the page went from one clear
    form to zero while the same card still offered "I won" / "I lost".
    Writable but not un-writable, with no live sibling to correct from and no
    reader-reversible un-cancel. The opted-out side makes the same point from
    the other direction -- invariant 8 says an opt-out forfeits the reminder and
    never the record, so the record is still theirs to correct. Do not re-derive
    a suppression here; the only gate is `clear_day`, which says what a clear
    POSTS, never whether it appears.
  - **`correctable` defaults to False, which keeps Home's markup
    byte-identical.** `_round_rows.html` is the one caller passing True, and
    Home's abstention is not mere tidiness: it DROPS LOST and NOT_APPLIED
    rounds from Coming up (the planner suppresses their anchors), so a
    correction offered there would be unreachable for exactly the rounds that
    need it, and a destructive action does not belong in its one-tap flow. The
    catch-up dialog abstains for the reason it passes no `only_day` -- it is the
    unfiltered whole-round caller, and a correction wants the leg in front of
    you. In the terminal branch the affordance REPLACES "Nothing to do" rather
    than sitting beside it: every row reaching that sentence has a recorded
    outcome and no offerable answer, which is precisely the set a correction
    serves, so the sentence became false the moment this shipped. That branch is
    wider than the design first said -- it also holds an APPLIED round whose
    results are still ahead, which is the same mis-press with more at stake, and
    it is why the affordance must survive the results moment rather than
    vanishing into the per-day branch.
  - **The confirmation gates with `stopPropagation`, not `preventDefault`
    alone** (`concert_detail.html`). Verified against the vendored htmx 2.0.4:
    its submit handler is bound to the FORM in the BUBBLE phase and never
    consults `defaultPrevented`, so `preventDefault()` by itself stops only the
    native navigation -- the AJAX delete flies anyway and the dialog asks
    permission for a write that has already landed. The listener is delegated on
    `document.body` and registered in the CAPTURE phase to win that race, and
    delegated at all because an htmx-swapped form is a NEW element a per-form
    binding would never see. There are TWO head/body copies, chosen by whether
    the pressed form carries a hidden `day_id` input -- the presence of that
    input IS the scope -- because a whole-round clear pressed under a leg that
    owns no answer really does throw a sibling leg's ticket away, and leg-scoped
    copy would be a lie exactly there. Only forms carrying `data-clear-confirm`
    are gated: LOST, NOT_APPLIED and APPLIED forfeit nothing and a confirmation
    there would be theatre. The copy must not borrow `_following_toggle.html`'s
    wording, which promises "does not remove that mark"; this is the first thing
    in the app that genuinely removes the record.
  - **The DM backtrack clears the WHOLE round, deliberately.** "Change my
    answer" (`dk:clear:`, `bot/views.py`) rides on every REPLY to an outcome
    press -- including the terminal states that used to return `view=None`, and
    including the refusals, because those are exactly the messages a reader is
    looking at when they realise they pressed the wrong thing. Because it lives
    on the reply and never on the reminder,
    `domain/rehearsal.py:expected_buttons` -- the oracle for what a REMINDER
    carries -- needs no entry for it, and must not grow one. A DM reply is one
    moment about one press; per-leg surgery needs to see every leg at once,
    which is the concert page, one tap away on the "Open on dekimasen.app"
    button every reminder already carries. It re-derives state and never trusts
    the message it was pressed on (a persistent button outlives the state it was
    built for), and it asks first when the round is WON or PAID, naming how many
    legs go with it, because a DM has no dialog and the question has to be a
    second press.
  - **The flat `WonButton`/`LostButton` pair now refuses a settled round**
    (`refuse_if_secured`) -- the round-level twin of the guard `_apply_press`
    gives the all-legs shortcuts, for the same reason: those buttons are
    persistent, so a months-old DM can be pressed against a round paid for on
    the site since, where WON demotes PAID and re-arms its payment reminder
    while LOST wipes the ticket outright. The refusal REPLIES with the backtrack
    instead of no-opping silently, so the guard is a signpost rather than a dead
    end and corrections live in one vocabulary. It is deliberately NOT set on
    the other three presses: `record_round_outcome` already refuses their
    damaging cases at the write, and guarding "Paid" would make it permanently
    inert, since the state it needs is exactly the state the guard refuses.
  - `POST /rounds/{id}/outcome/clear` (`web/routes/outcomes.py`) adds no logic
    of its own and reuses `_outcome_response` VERBATIM -- the same surface
    split, the same out-of-band `#board`/`#board-summary` fragments, the same
    JS-less 303, the same `open_round_id` fold reopening. Its `cleared` toast
    key joins `TOAST_MSGS` in `base.html` without being a `LotteryOutcome`
    value, which is fine: that map is a lookup rather than an exhaustive switch,
    and an unmapped key already yields no toast instead of a wrong one.
- **Venues live on the LEG, as a tag.** `ConcertDay.venue_tag_id` (FK ->
  `tags.id`, ON DELETE SET NULL, indexed) is the structured venue and the ONLY
  one anything reads for display; SET NULL rather than CASCADE because a VENUE
  tag is shared taxonomy and deleting one must never take performances down
  with it. It replaced a case-insensitive free-text NAME match, which left a
  re-pointed leg rendering its previous venue forever -- the old
  `find_venue_tag` helper is gone, don't reintroduce that shape. The tag
  carries `city`/`city_en`/`city_zh` and `address`: a venue is always in one
  city, so the city is a property of the VENUE, not of each leg visiting it
  (`address` deliberately has NO locale variants -- its job is to be pasted
  into a map, and `location_url` already covers the maps link). A concert's
  VENUE tags are DERIVED, never typed: `sync_concert_venue_tags`
  (`db/service.py`) rewrites them as the union of its legs' venues, and the
  create route, the edit route and `import_commit` all call it. It returns the
  tags it NEWLY attached and every caller MUST feed those to
  `handle_newly_tagged` -- VENUE tags are subscribable, so someone following
  "Zepp Haneda" is owed the same DM notice a concert-level attach gives them
  (invariant 4). It touches VENUE rows only; franchise/group/artist attachment
  is deliberate and materialized (invariant 3) and must survive untouched.
  Discover's region filter is unchanged by all of this -- it still reads
  `concert_tags` client-side off each tile's `data-tags`, and this rollup is
  exactly what keeps that current while venues live on legs.
  `ConcertDay.venue_tag` is `lazy="raise"` ON PURPOSE: a lazy load during async
  template rendering is a `MissingGreenlet` 500, which this project has shipped
  once, and raising at the seam turns that into a loud test failure instead.
  Every path handing legs to a template must `selectinload` it, or load the
  tags separately by id the way `concert_rounds_context`
  (`web/routes/concerts.py`) does.
- **The legacy free-text venue columns are GONE (venue-to-tags is complete).**
  `ConcertDay.city`/`venue`/`venue_address` and `Concert.venue`/`venue_en`/
  `venue_zh` were dropped by migration `ce43bfcfcae3` once every venue lived on
  a leg's VENUE tag. They existed through phases 1-4 as recovery data (a leg
  whose free-text venue did not match a tag during the `789bbcc95bc3` backfill
  stayed recoverable); the owner confirmed zero unmatched legs in production
  before the drop. Do not reintroduce them or the old `apply_day_fields`
  preserve-on-empty rule -- a leg's venue is a VENUE tag and nothing else. A
  concert with no leg venue tag simply has no venue anywhere. NOTE the drop
  migration reversed the deploy order (restart on new code BEFORE
  `alembic upgrade head`) so the old process could not SELECT the dropped
  columns mid-deploy; any future column-DROP migration needs the same order.
- **A CHARACTER is a tag, and `Tag.voiced_by_tag_id` says who plays her**
  (migration `bb9780f0ad82`, 2026-08-01). `TagKind.CHARACTER` is a fifth kind
  beside franchise/artist/venue/group, because an idolm@ster bill credits
  如月千早 and never mentions 今井麻美 -- a user following the performer missed
  the show entirely, which is this app's worst failure. `voiced_by_tag_id` is a
  nullable self-FK to the ARTIST who voices her, `ON DELETE SET NULL` for the
  same reason `ConcertDay.venue_tag_id` is: deleting a performer's tag must not
  take the character down with it. A recast is this ONE value re-pointed --
  there is deliberately no history model, and the owner ruled recasting rare
  enough that there never should be.
  **It is NOT `parent_id`, and that was a decision, not an oversight.**
  `parent_id` means "the broader thing I belong to" and is what the Tags page
  renders its hierarchy from; a seiyuu is not broader than a character, and
  spending the column on her would leave 如月千早 unable to say she belongs to
  idolm@ster at all -- which Discover's franchise filtering reads.
  Only an ARTIST may voice a character, checked at BOTH write boundaries
  (`resolve_seiyuu` in `web/routes/tags.py`, 422; `apply_tag_import` in
  `db/service.py`, warn-and-leave-unvoiced). That check also refuses
  SELF-voicing for free, and the failure it prevents is silent rather than
  loud: a character pointed at herself lands in `performer_clusters`'
  `paired_seiyuu` set and is filtered out of `entries`, so she VANISHES from
  the Performing panel instead of erroring. Pointing it at a VENUE was the
  other reachable trap -- `attach_tag` materialises whatever it names onto the
  concert, so the venue would render as a performer and `handle_newly_tagged`
  would DM its followers.
  A tag's KIND stays immutable -- no route accepts it and the importer refuses
  a mismatch outright -- and the im@s reformat needs no exception: seiyuu stay
  ARTIST tags, characters are NEW tags, and a group's member list swaps
  handles. Don't add a kind-change path for this.
- `src/app/bot/` — thin shell: cogs, embed builders (`messages.py`),
  persistent buttons (`views.py` — whose `custom_id` namespace is documented in
  its module docstring; the `dk:clear`/`dk:clearok`/`dk:keep` trio and the flat
  pair's `refuse_if_secured` guard are the outcome-correction entry above).
- `src/app/web/` — thin shell: routes, templates, static. `routes/imports.py`
  (the ramen.events importer, fetches the URL then delegates parsing to
  `domain/ingest.py`) MUST be registered before `routes/concerts.py` in
  `web/app.py` — otherwise `GET /concerts/import` gets swallowed by the
  `GET /concerts/{event_id}` route, since FastAPI matches path templates
  before literal segments. Its fetch is SSRF-guarded three ways: https +
  `ramen.events` host only, the same check re-run on every redirect hop via
  an httpx response hook, and the body streamed under a byte cap — don't
  loosen any of them. That guard is no longer local to this route: it lives in
  `app/fetching.py` and is SHARED with the Eventernote sweep (see below), so
  `fetch_ramen_html` is now a thin wrapper that translates the shared errors
  into this route's 400/502. Its preview (`import_preview.html`) is built in the
  same day-card/round-card/leg-chip vocabulary as `concert_new.html`/
  `concert_edit.html`, and `import_commit` binds a parsed round's
  `applies_to` to legs via the same `round_legs`/`day_key`/
  `parse_round_legs`/`key_to_day_id` path `create_concert` uses -- before
  this, the flat import form could not express a round spanning more than
  one leg.
  The same preview has a second producer: `POST /concerts/import/draft`
  takes a pasted YAML draft (the `domain/yaml_export.py` vocabulary made
  two-way -- `domain/yaml_import.py` parses it, warnings over failures,
  `yaml.safe_load` only) and renders `import_preview.html` fully prefilled:
  trilingual titles/labels, all four round anchors, tag/venue NAMES resolved
  to picker pre-selections via `match_tag_ids_by_name` /
  `match_venue_tag_id` (never ids in the draft; unmatched names render as
  hints, never dropped). The producer is normally an agent following
  `.claude/skills/add-concert/SKILL.md`, whose example draft is pinned to
  the parser by a test. import_commit stays the only write path.
  A THIRD producer takes MANY drafts at once: `POST /concerts/import/batch`
  splits a multi-document paste (`---` separated, plain YAML, no wrapper key)
  and persists each document verbatim as a `PendingDraft` row, which
  `/concerts/import/pending` then walks one preview at a time. It is
  deliberately NOT all-or-nothing -- `domain/yaml_import.py`'s `parse_drafts`
  names the documents that failed and keeps the rest, because at fifty
  concerts one typo must not cost the other forty-nine. Boundaries come from
  `yaml.scan()` rather than `text.split("---")` (a `---` inside a block scalar
  would cut a draft in half) and rather than `safe_load_all`/`compose_all`
  (both abort their generator on the FIRST bad document, silently losing every
  one after it); a paste that breaks the scanner itself falls back to a
  line-based split, so a scanner-level typo costs one oddly-split fragment
  instead of the batch. `PendingDraft` is the ONE place this app keeps step
  state, and the reason is that it is not step state: it is a work batch of
  fifty-to-a-hundred concerts each needing a human-read preview, which is not
  one sitting, and a hidden form field would lose it to a closed tab. A
  resubmitted pending commit (back button, refresh) answers 409 rather than
  minting a second concert -- agent drafts carry no `event_id`, so
  `generate_event_id` would de-dupe to `alpha-2` instead of colliding, and
  nothing would link the duplicate.
  **Starlette hard-caps every `Form(...)` field at 1MB**, whatever an
  app-level constant says, so `MAX_BATCH_CHARS` is 300k rather than the
  millions a paste of a hundred drafts might suggest. This applies to EVERY
  form field in the codebase, not just this route -- any future large-paste
  feature hits the same wall, and hits it as an opaque failure well before
  its own limit.
  Reminder-rule add/delete lives in
  `routes/reminders.py` (split out of `concerts.py`; renders via
  `concerts.render_rules_fragment`), and the `/me/timezone*` routes live in
  `routes/preferences.py` with the other per-user preference routes.
  `web/forms.py` holds the HTTP-boundary wrappers around domain validators
  (currently `form_url`) -- its own module so routes/concerts.py,
  routes/tags.py and routes/imports.py can all import it cheaply.
  A venue can be created without leaving the editor: `POST /tags/venue/quick`
  (`routes/tags.py`, editor-only, JSON) plus `_venue_create_dialog.html`,
  included by both `concert_new.html` and `concert_edit.html`. It answers 409
  on a duplicate name specifically, so the dialog can say "that venue already
  exists" instead of the generic 422 everything else gets. The concert-level
  venue picker was REMOVED from both forms -- the leg is the single place a
  venue is entered, and `create_concert_row` sets `venue=None`.
  Franchise/group/artist tags get the same treatment in the IMPORT PREVIEW:
  each unmatched draft name renders as a per-name create chip
  (`data-new-tag` + `data-tag-name`/`data-tag-kind`) opening
  `_tag_create_dialog.html`, backed by `POST /tags/quick` (editor-only,
  kind-aware, groups take a `parent_id`; its kind-scoped 409 returns the
  existing tag's id+name so the dialog offers one-click select-existing).
  A group created there is EMPTY on purpose -- expansion stays
  attach-time-only (invariant 3) -- and creation fires no notification
  (creation is not attachment, invariant 4). The created tag joins the
  picker via `_tag_picker_script.html`'s `pickerAddAndSelect`.
  `routes/discover.py` is the public catalogue and `routes/outcomes.py` is
  the web half of lottery-outcome capture (`POST /rounds/{id}/outcome`) --
  it shares `record_round_outcome` with the DM buttons rather than writing
  its own path (a second writer would desync the queue, invariant 2) and
  returns THREE top-level fragments: the deadline rows as the hx-target,
  plus `#board` and `#board-summary` out-of-band, since one recorded outcome
  changes all three. Don't wrap that response -- htmx only honours OOB
  elements at the top level. Its third route,
  `POST /rounds/{id}/outcome/clear`, is the un-answer and reuses that same
  `_outcome_response` verbatim; see the outcome-correction entry above before
  touching it, the `clear_day` gate in `_capture_actions.html`, or the
  confirmation script in `concert_detail.html`.
  `routes/api.py` is the read-only agent API at `/api/v1`, bearer-token
  authenticated (`User.api_token_hash`, minted at `POST /me/api-token` in
  `routes/preferences.py`), GET only and swept by a test that no route under
  the prefix ever declares another method. English-only and NOT wrapped in
  `_()`, like `/admin/deliveries` -- its consumer is a program. See
  `docs/agent-api.md` for the endpoints and
  `docs/superpowers/specs/2026-08-08-agent-read-api-design.md` for the
  design. `web/paging.py` is the offset-paging helper it and no other router
  currently uses: `limit`/`offset` parsing plus the `{items, total, limit,
  offset}` envelope, with `limit` over its cap answering 422 rather than a
  silent clamp -- the module's own docstring states why (an agent that asked
  for 5000 and silently got 500 back would conclude it had read everything).
- **Tag search: `search_key` + `filterChips`'s two passes** -- shipped
  2026-08-12, phase 1 of WISHLIST's "Following is due a rework".
  - **`search_key(obj)` (`web/app.py`) is the `data-name` value, and it DROPS
    empties.** It joins `name`/`name_en`/`name_zh` lowercased, because the chip
    beside the hook renders `loc(obj, "name")` and keying the hook on `name`
    alone meant an English viewer could not find a tag by the name they were
    looking at (681 of 735 live tags have a `name_en` that differs from
    `name`). `name_en`/`name_zh` are NULLABLE -- 109 live tags have no
    `name_zh` -- and Jinja renders None as the literal `"None"`, so a naive
    `" ".join(...)` makes every one of them match a search for "none". The
    display rule beside it is unchanged and still narrower: form values and
    URLs keep the original field; display and this search key do not.
  - **`filterChips` (`base.html`) runs TWO passes, and pass 2 reads pass 1's
    result, not the query.** Pass 1 shows or hides each `[data-name]` chip;
    pass 2 hides any container that now holds no visible chip, so a search
    returns a result list instead of the page skeleton with one chip left in
    it. Containers opt IN via `data-filter-container` -- a selector list would
    have to be edited every time a template adds a level. Pass 2 tests each
    chip's own `style.display`, which is what makes NESTED containers
    order-independent: an inner container already hidden by an earlier
    iteration still reports its chips' real per-chip state. An
    `offsetParent`/`visibility` test looks equivalent and is not -- both are
    also falsy inside an already-hidden ancestor, which reintroduces the
    ordering dependency. It cannot be CSS either: `:empty` does not match,
    because template indentation leaves whitespace text nodes inside these
    elements (the same trap documented on `.chiprow:empty` in
    `concert_detail.html`). On `tags.html` the opt-in must reach `.fam` as well
    as `.tsec`: `.fam` carries its own padding and bottom border, so an
    unmarked one leaves an empty bordered region row per region under a
    one-hit venue search. `.famhead` is deliberately NOT a container -- keeping
    a franchise chip visible as context while only its groups match is a
    design call.
  - **`members_by_group` (`db/tags.py`) gives EVERY requested id an entry.** It
    replaced a per-group `group_members` loop at four sites -- `/tags`,
    `/preferences`, `/welcome` and `tag_picker_context` (which is on
    `GET /concerts/new`, `GET /concerts/{id}/edit` and the import preview) --
    each ~65 round trips on the live catalogue. A group with no members must
    yield `[]`, not a missing key, because every caller indexes the map per
    group; building the dict only from the returned rows silently drops
    memberless groups, which is a different bug in each caller. `group_members`
    itself stays and is still right for ONE group -- `attach_tag`'s expansion
    step and `routes/concerts.py`'s performer rollup both iterate one concert's
    tags, not the catalogue, so neither is an N+1 to convert.
- **`/tags` is the follow surface** -- shipped 2026-08-12, phase 2 of WISHLIST's
  "Following is due a rework", design in
  `docs/superpowers/specs/2026-08-12-following-rework-design.md`. Before it the
  page's only follow control (`_tag_follow_bell.html`) rendered solely inside
  the editor-only table view, so a non-editor was shipped 735 hidden rows of
  follow buttons they could not reveal and, in the view they could see, 878
  inert `<span>`s. Now every chip follows, for everyone, and the editor's
  edit-on-click came back as a MODE rather than as a second meaning for the
  same click. Phases 3-4 (`/following`, the Preferences reduction) are not in
  this; the Preferences picker is still the other way to follow.
  - **Every chip is a real `<form>`, and that is the feature, not the
    implementation.** `tag_chip`/`follow_half` (`tags.html`) each emit a POST to
    `/subscriptions` or `/subscriptions/{id}/delete` with a hidden `next=/tags`
    -- the shape `_tag_follow_bell.html` established, which is why that partial
    was DELETED with the table rather than kept: this macro is the pattern now,
    and unlike the partial it is pinned by tests -- so following works with
    JavaScript off. A chip rewritten as a `<button onclick>` renders identically
    (`.chipform` is `display: inline`, so the wrapper contributes no box of its
    own) and does nothing without JS, which is the one constraint the WISHLIST
    entry named as most likely to be dropped by accident in this rework. The `next` field only reaches `/tags` because `_ALLOWED_NEXT`
    (`routes/preferences.py`) lists it: `_safe_next` there is a CLOSED internal
    allowlist, not the open-redirect guard (that is `domain/urls.py:safe_next`),
    and until this build the set held only `/preferences`/`/welcome` -- so the
    bell had been bouncing every follow to Preferences since it shipped. Trim
    `/tags` back out and every chip on the page silently does that again.
  - **A press swaps ITS OWN chip, and the markup for it lives in
    `_tag_chip.html` for that reason** (2026-08-12, owner report: "the loading
    time after clicking each follow and unfollow is incredibly slow", and every
    press scrolled the reader back to the top). The plain forms above were
    correct and unusable: a follow 303'd back to `/tags`, which re-rendered
    every chip on the page plus an editor's `<dialog>` per tag. Measured on a
    735-tag seed of the live shape (1,053 chip forms, 6.98 MB of HTML), one
    press, both figures from the same run: the redirect path costs **923 ms and
    6.98 MB**, the htmx press **10.7 ms and 518 bytes** -- the same POST,
    answered with one chip instead of the whole directory. `tag_chip`/`follow_half` therefore MOVED out of `tags.html`
    (which keeps two one-line wrappers holding the `sub_by_tag` lookup) and take
    their subscription row as a parameter, because a route cannot render a macro
    that closes over a page's context. Three things hold it together and each
    one is silent when broken: the forms keep `method`/`action` BESIDE
    `hx-post`/`hx-target="this"`/`hx-swap="outerHTML"`, so JS-off following
    still works (`_capture_actions.html`'s idiom, deliberately copied rather
    than reinvented); the routes answer a fragment ONLY to `HX-Request` and
    otherwise still 303, since htmx FOLLOWS a redirect and would swap the whole
    page into a chip-sized hole; and the fragment comes from the same partial
    the page renders, byte for byte, or a swapped chip quietly loses `data-name`
    (unfindable by the search box), `data-tag-id` (inert in Edit mode) or its
    `unused` marking. The hidden `chip` input is how the route knows which of
    the four shapes to send back, and the event count is re-queried server-side
    rather than trusted from the form -- a member chip must not grow a number it
    has never had. `tests/test_tags_follow_htmx.py` pins all of it, including
    the byte-identity in both directions.
  - **The fragment's SHAPE is supplied by the CLIENT, and a server-derived one
    is impossible rather than merely expensive.** The same tag renders in two
    different shapes on one page -- a seiyuu is a plain member chip in one group
    row and the `cv` half of a split pill in another -- so shape is a function
    of the chip's POSITION, not of the tag, and no amount of querying the tag
    can recover which of its chips was pressed. Hence the hidden `chip` field.
    Two INDEPENDENT layers make that safe, and each was tested by breaking the
    other (nine crafted values in review, 2026-08-12: template injection,
    attribute breakout, 2,500 characters): `_PILL_HALVES`
    (`routes/preferences.py`) is a WHITELIST -- `{"cn", "cv"}`, the only two
    values that reach an attribute, since a half's shape doubles as its CSS
    class -- and everything else falls to the plain chip, where Jinja's
    autoescaping is the second layer. Do not "simplify" the whitelist into a
    blacklist (`chip not in {"count", "plain"}` -> render a half): every other
    test in that file survives it, so
    `test_a_hostile_chip_value_cannot_escape_the_whitelist` is the only thing
    naming the defence. An unknown value lands on the plain chip rather than
    raising -- the value is only ever written by the partial itself, and a
    cosmetic surprise beats a 500 on a follow press.
  - **BOTH directions are keyed by TAG, because this page renders the same tag
    more than once.** A performer in two groups gets a chip in each row; a
    seiyuu can be a direct member chip AND the `cv` half of a pill at the same
    time. Measured on the live catalogue: **318 of 735 tags render more than
    once**, so this is the common case, not an edge. A full-page 303 re-rendered
    every copy in step, and a one-chip swap
    cannot -- so `POST /subscriptions/{sub_id}/delete` in a chip's action meant
    that unfollowing via one copy left the other pointing at a deleted row:
    pressing it answered 404, htmx does not swap a 4xx, and the reader got
    NOTHING until a full reload (found in review, 2026-08-12, one day after the
    swap shipped). Chips post to **`POST /subscriptions/unfollow`** with
    `tag_id` instead: no row is nothing to delete, not an error, and the answer
    is the follow chip either way -- matching the idempotence `POST
    /subscriptions` already had, since it upserts by (user, tag). It is a
    SECOND route, not a widened one: `/subscriptions/{sub_id}/delete` still
    serves Preferences and the welcome wizard, which render a tag once and
    reload wholly, and one route resolving by id OR by tag depending on which
    field the form sent is two identity schemes wearing one URL. Never point a
    chip back at the id-keyed route -- `test_every_chip_form_on_the_page_can_swap_itself`
    forbids `/delete` in any chip form, and it FOLLOWS half its seeded tags
    before fetching the page, because seeded with none it saw only unfollowed
    forms and both of its load-bearing assertions were vacuous (caught in
    review, 2026-08-12; the id-keyed URL could be put back in either followed
    branch with that whole file still green). The id-keyed route keeps its own
    two tests in `test_presets.py`, beside the other `/subscriptions` route
    tests: deleting it outright used to leave the suite green while 405-ing
    three live surfaces.
    Note what did NOT change: the copies still go stale visually (the other
    chip still shows ✓ until something re-renders it). Making a press update
    every copy would need an out-of-band swap keyed on the tag, which is a
    bigger mechanism than this page has earned. **That trade is acceptable
    ONLY BECAUSE BOTH DIRECTIONS ARE IDEMPOTENT** -- a stale copy's press is
    merely redundant, never destructive and never an error. Add a third,
    NON-idempotent action to these chips (anything where pressing twice differs
    from pressing once, or where a press depends on the state the chip is
    displaying) and the trade reopens: that action needs the out-of-band sync,
    or a confirmation, or it must not live on a duplicated chip at all.
  - **Subunit de-dup is `/tags`-ONLY, and the 2026-08-01 ruling it appears to
    contradict still stands where it was made.** `tag_directory_context`'s
    `group_rows` subtracts every member of a group's subunits from the parent's
    own row (`subunit_member_ids`, which carries its OWN `seen` set rather than
    sharing the walk's `walked` -- it runs before the walk reaches those
    children, and sharing would make a parent's de-dup depend on visit order;
    it also needs its own cycle guard, since parent rows predate
    `would_create_tag_cycle`). The 2026-08-01 character/seiyuu/subunit spec
    ruled "repetition kept" for the CONCERT page, because a bill must be a
    truthful lineup and de-duping would make what it displays depend on which
    OTHER tags happen to be attached -- two shows with identical lineups
    rendering differently. A catalogue has no "attached" and asserts no lineup,
    so that objection does not transfer and the reversal is scoped to this page.
    Do not "unify" the two. Measured on the live catalogue: 485 member chips ->
    343, and 6 parent rows go empty and still render (empty rows render
    silently; there are no per-group folds, and search is how you reach a name
    in a large group). "Silently" is what the TEMPLATE has to be told:
    `group_row`'s `{% else %}` fallback is gated on `counts[g.id].members == 0`
    -- the RAW `TagMember` count -- because a de-duped empty list means two
    different things and only one of them is "no members yet". Ungated, SideM
    (49 members) and Shiny Colors (28) captioned themselves "no members yet"
    directly above the subunit rows listing every one of them. The count in the
    dialog's stat block is the same raw number, so the two agree by
    construction.
  - **`seiyuu_of` exists because `Tag.voiced_by` is not a loaded relationship.**
    `tag_directory_context` resolves each CHARACTER's performer off the tag list
    it has ALREADY loaded and hands the template a `{character_id: Tag | None}`
    map. `Tag` has no `voiced_by` relationship at all -- only the
    `voiced_by_tag_id` column -- so the hazard is structurally absent rather
    than merely avoided, and adding the obvious convenience relationship is what
    would open it: resolving a seiyuu during async template rendering is a
    `MissingGreenlet` 500. Unset, or a seiyuu tag deleted (the FK is
    `ON DELETE SET NULL`), maps to None and renders as a plain chip -- the
    concert page's conditional-merge rule, unchanged.
  - **The split pill's box does NOT match the plain chip beside it, and that is
    filed, not fixed.** `.mchip .half button` measures 28.72px against `.tchip`'s
    29.52px, because `.tchip` differs from the `.chip`/`.mchip` family on BOTH
    axes -- font-size (`.8rem` vs `.82rem`) AND line-height (inherited 1.6 vs an
    explicit 1.5). Closing it with line-height alone would OVERSHOOT to
    28.24px, which is the fix to refuse. **28.24 is COMPUTED, not measured** --
    it is what the box works out to under a change nobody made, and a change
    nobody made cannot be measured. 28.72 and 29.52 are browser readings of the
    shipped page. Anyone acting on the 28.24 must make the change and re-measure
    both boxes first (the standing rule at the top of "UI conventions"); do not
    read it as a third measurement.
    The real argument for leaving it is that `.memb` is `align-items: center`,
    so 0.78px splits to 0.39px above and below and is invisible. Two things
    around it ARE deliberate and measured: `.half { display: contents }` removes
    the form from the box tree so the button becomes `.mchip`'s flex child (but
    `.mchip > *` still matches the now-boxless FORM, by DOM parentage, which is
    why the halves' padding/line-height is set on the button directly), and at
    <=700px the button gets `min-height: 32px`, not 34px -- `.tchip`'s 34px
    floor includes its own 2px border, while `.mchip`'s border sits on the pill
    outside the button, so 34 here would ship a 36px pill.
  - **`.tagtable`'s CSS outlives its `/tags` markup.** The table view is gone
    from this page, but six admin templates still use the class
    (`admin_broadcast`, `admin_deliveries`, `admin_discoveries`,
    `admin_fetch_domains`, `admin_quiet_ladders`, `rehearsal`). Deleting the
    rule as dead alongside the markup silently flattens six admin pages, which
    is why a test pins the selector's continued existence in `style.css`.
  - **Edit mode's chip interceptor listens in the CAPTURE phase and calls both
    `preventDefault` and `stopPropagation` -- and the reason is ORDERING, not
    cancellation.** `preventDefault` is what cancels the submit, from any phase:
    for a native form submit the submission is the submit button's ACTIVATION
    BEHAVIOUR, which runs only if the click event's canceled flag is unset, and
    that flag is a property of the EVENT, not of the phase in which it was set.
    Capture, at-target and bubble cancel alike. (The htmx habit this codebase
    reaches for here was a DIFFERENT problem: htmx binds on bubble and fires
    without consulting `defaultPrevented`, so that lesson is about which
    listener runs first, not about whether cancelling works.) **Capture** is
    used because this listener must run before anything else can see or swallow
    the press: a bubble-phase listener can be silently nullified by any earlier
    `stopPropagation` between the button and the document, and that failure
    presents as "the dialog just doesn't open", with nothing in the console.
    **`stopPropagation`** is used because nothing else should act on the press
    at all. The codebase's own precedent says exactly this in one line --
    `_variant_guard.html`: *"Capture phase, so the submit is stopped before any
    other handler acts on it."* Do NOT record this as "preventDefault does not
    work from the bubble phase"; it does, and writing that down would leave the
    next reader with a false model of the DOM. One traced consequence, benign:
    stopping propagation does skip `base.html`'s backdrop-close reset of
    `pressedOn` -- harmless, because its pointerdown listener is itself capture
    and overwrites the stale value on the very next press, and the guard it
    feeds requires an `HTMLDialogElement` target, which a chip button can never
    be. The mode itself DOES NOT PERSIST (a plain closure variable; no
    localStorage, no cookie), matching the Chips/Table toggle it replaced: a
    remembered Edit mode means an editor returning tomorrow silently cannot
    follow anything by clicking and has no memory of why. In Edit, chips drop
    their ticks and their followed ground and their title swaps -- a chip
    claiming "following" while its click opens an editor is lying about the
    click -- but the KIND grounds are restored at higher specificity, because a
    tag's kind is a fact about the tag and survives the mode.
- **`/following` is the subscription surface** -- shipped 2026-08-12, phase 3 of
  WISHLIST's "Following is due a rework", design in
  `docs/superpowers/specs/2026-08-12-following-rework-design.md`. `/tags` owns
  the catalogue (everything you COULD follow, where a chip press follows);
  `/following` owns your subscriptions (what you DO follow, where a chip press
  opens that subscription's config dialog). The owner's reasoning for two pages
  rather than a filter on one: a chip cannot mean "follow" on one surface and
  "configure" on the other. Phase 4 (the Preferences reduction, the standing
  default and its retroactive fill) is NOT in this.
  - **It reads `TagSubscription` rows DIRECTLY, and that is not a second
    derivation of invariant 8.** `followed_tag_families` (`db/tags.py`) selects
    the viewer's subscription rows and joins their tags, full stop. Invariant 8
    governs a different question -- which CONCERTS a follow reaches, with
    `ConcertSubscription`/`LegOptOut` overrides on top -- and
    `tracked_concert_ids` remains the single place THAT lives, untouched by this
    page. A subscription row is not a derivation of anything: it is the explicit
    user edit itself, one per followed tag, which is exactly what a page called
    "Following" is a list of. Do not "unify" the two by routing this page
    through `tracked_concert_ids`; it would answer a question nobody asked here
    and could not name a tag you follow that has no concerts yet.
  - **The route lives in `routes/preferences.py`**, beside the routes that WRITE
    `TagSubscription`, rather than in a router of its own: it is the read
    surface for exactly those rows, `/following` is a literal path that collides
    with no path template (contrast the `imports.py`-before-`concerts.py`
    footgun), and a new module would need registering in `web/app.py` for a
    separation this file does not already have. `routes/subscriptions.py` is a
    different feature -- CONCERT subscriptions and leg opt-outs.
  - **A chip states ONLY how its subscription DIFFERS from the viewer's
    defaults, and the comparison basis is `ReminderPreset.is_default`, which
    already existed -- which is why this phase needed no migration.**
    `TagSubscription.preset_id` and `.notify` already existed too; Preferences'
    Auto-apply boolean was only ever "link my default preset, or clear it". This
    phase EXPOSED per-tag presets, it did not add them. The deviation reading is
    derived in `followed_tag_families`, never in the template, and it is a
    two-sided comparison -- `sub.preset_id != default_preset_id` -- because BOTH
    directions are deviations:

    | | default preset exists | no default preset |
    |---|---|---|
    | **subscription holds a preset** | conforms if it IS the default; otherwise shows that preset's NAME | shows the preset's NAME |
    | **subscription holds none** | shows "No preset" | **conforms -- no marker at all** |

    The bottom-right cell is the one that shipped a bug in review (2026-08-12):
    it is the state of every brand-new account, and a reasonable-looking
    `(sub.preset_id or 0) != (default_preset_id or -1)` puts a spurious "No
    preset" badge on EVERY chip a new user has, while leaving the whole file
    green. The top-left "conforms" half is the other easy loss: an
    `if sub.preset_id and sub.preset_id != default` drops the "No preset" case
    entirely. `preset_name` is a USER-AUTHORED name and is read only when the
    subscription deviates; the label for the None case stays in the template,
    where it resolves at render time (CLAUDE.md's i18n footgun -- a label copied
    into a dataclass resolves at the COPY site). The point of the whole rule is
    that scanning forty chips, only the exceptions draw the eye, so resist
    adding a badge that renders on every chip however useful it looks on one.
  - **`/following` is in `_ALLOWED_NEXT` (`routes/preferences.py`), and only a
    redirect-target assertion catches its absence.** That set is a CLOSED
    internal allowlist of landing pages, not the open-redirect guard
    (`domain/urls.py:safe_next`); anything absent silently becomes
    `/preferences`. The precedent is not hypothetical: `/tags` was missing from
    it from the 2026-07-24 UX pass until phase 2 found it, so every follow press
    on that page bounced the reader to Preferences for weeks, with nothing
    failing and nothing logged. Both of this page's dialog forms post
    `next=/following`. Add the path WITH the surface, and assert where the
    response redirects TO -- a test that only checks the row was written passes
    against the bounce.
  - **The dialog holds THREE things because a subscription has three**: which
    preset it links (`POST /subscriptions/{id}/settings`, both fields in one
    submit, so a Save is one round trip rather than two either of which can land
    alone), whether it DMs you about new events, and whether it exists at all
    (Unfollow, in the footer as the destructive action, posting the EXISTING
    `/subscriptions/{id}/delete` -- deleting the row is not a setting on it).
    The id-keyed unfollow is safe HERE, unlike on `/tags`: this page renders
    each followed tag exactly once and answers every save with a whole
    re-rendered page, so no copy can be left holding a deleted id. `preset_id`
    is re-checked with `owned_preset`, because a `<select>` is not a permission.
    No rule resync: `notify` is only the new-event notice and `preset_id`
    governs FUTURE matching events, so invariant 2's queue is untouched by
    either -- the same reasoning `/subscriptions` and Preferences' two toggles
    already run on.
  - **The chips here are `<span>`s, deliberately, and must not reuse
    `_tag_chip.html`.** That partial's forms hard-code `next=/tags` and a hidden
    `chip` shape naming the fragment `/tags`' routes swap back; rendering it
    here would put /tags-shaped markup into this page on every press. This page
    also renders PLAIN chips, never split pills -- a subscription is one tag,
    and the pill exists on `/tags` because two tags are being offered at once.
- **Preferences' Following section is a fixed-height summary now, and the
  standing default is `ReminderPreset.is_default` WIDENED, not a new column**
  -- shipped 2026-08-13, phase 4 (the last) of WISHLIST's "Following is due a
  rework", design in
  `docs/superpowers/specs/2026-08-12-following-rework-design.md` (see the
  dated correction on its §Preferences, added by this phase). `is_default`
  already meant "which preset the Discord DM's `[Set my reminders]` button
  applies" (`apply_default_preset`, whose only caller is
  `bot/views.py:ApplyDefaultButton` -- a `discord.ui.Button` on a DM view, not
  a web control; `routes/preferences.py`'s `make_default` and
  `preferences.html`'s two "Default" pills all describe it the same way);
  this phase widens the SAME flag to also
  mean "which preset a new follow inherits" (`subscribe`, via
  `get_default_preset` -- shipped earlier on this branch, task 2) and "which
  preset the retroactive fill writes" (`POST /presets/apply-to-following`,
  below). Nothing was added to the schema for any of it --
  `TagSubscription.preset_id`/`.notify` and `ReminderPreset.is_default`
  carried the entire four-phase rework, which is why it shipped with ZERO
  migrations. **The owner dropped notify from the standing default**
  (2026-08-13): the design called for the default to cover both preset AND
  notify, but there is no per-user column for a standing notify default the
  way `is_default` is a per-preset one to widen, and the owner chose not to
  add one. Preferences therefore shows only the preset half of "the standing
  default"; notify is still set per tag, one at a time, on `/following`'s
  dialog. Do not read the missing notify row here as an omission to fix --
  adding a column reopens a decision that was made on purpose.
  - **The retroactive fill (`POST /presets/apply-to-following`,
    `routes/preferences.py`) is FILL-ONLY, and the report is the only
    evidence of that.** It UPDATEs every `TagSubscription` the caller owns
    whose `preset_id IS NULL`; that `is_(None)` clause is the ENTIRE safety
    property -- drop it and the statement becomes a blanket overwrite that
    raises nothing, renders a cheerful count, and silently retimes every
    hand-tuned tag's reminders, with no undo and no audit row. Because the
    failure is silent, the route reports TWO numbers, `filled` and `kept`,
    not one -- `kept` is the only thing that tells the reader the fill left
    something alone rather than clobbering it; a `filled`-only report would
    look identical either way. `preset_id = NULL` is also OVERLOADED (the
    deleted `toggle_subscription_autoapply` used to write it for "auto-apply
    off", and `/subscriptions/{id}/settings`'s "none" option still does), so
    the fill silently re-arms switches a user had turned off. Owner ruling,
    2026-08-13: fill them anyway, but the banner MUST say so plainly rather
    than add a column to tell the two NULLs apart -- losing that sentence
    from `preferences.html` is a silent regression, not a cosmetic one.
  - **`handle_newly_tagged` (`db/core.py`) now asks "which preset wins",
    not "which is oldest."** The fill writes the default into the OLDEST
    blank rows, so the previous earliest-created-wins rule let a blanket
    default beat a tag the reader had tuned by hand on the same concert --
    invariant 3 attaches a group and its members together, so following both
    is the ordinary case here, not an edge. Measured: reminder offsets moved
    from `-1` to `-3` on a concert matching both, with the tuned
    subscription's row byte-identical. The loop now prefers the first
    candidate whose linked preset is NOT `is_default`, falling back to
    earliest-first only when every candidate ties on that (all-default or
    all-non-default) -- which is where order was always genuinely arbitrary.
    This is SHARED code: it changes which preset every user's future-matching
    concert gets, not only the ones who press the fill button, and
    "simplifying" the loop back to plain earliest-wins reopens the exact bug
    the owner ruled on.
  - **The welcome wizard's step-0 chip sends an explicit `preset_id=0`, and
    `subscribe` (the `POST /subscriptions` route this phase's `get_default_preset`
    change lives in, 41110f5) treats it identically to a `/tags` chip's absent
    field.** `_tag_chip.html`'s follow forms post no `preset_id` at all;
    `welcome.html`'s carries `value="0"`. Both are falsy, so both hit the same
    `if preset_id:` branch and inherit the viewer's standing default. This is
    deliberate, not an oversight: the wizard's Follow step (step 0) always
    precedes its Reminders step (step 1) and never regresses, so in the
    ordinary new-user flow no default preset exists yet at follow time and the
    value resolves to `None` exactly as it did before this rule. The only case
    where it now differs is a user who already has a default preset from
    elsewhere while still sitting at onboarding step 0 -- and inheriting it
    there is the same "did not choose -> apply default" rule as everywhere
    else, not a reason to carve out a second meaning for a literal 0. Do not
    special-case the wizard's `preset_id=0` to mean "no preset": that would
    reintroduce two meanings for one falsy value, which is exactly what this
    rule was written to remove. If the wizard ever needs to skip the default,
    it should stop sending `preset_id=0` at all (matching `/tags`), not grow a
    branch in `subscribe`.
  - **What Preferences owns now vs `/following`.** Preferences keeps only:
    the followed-tag COUNT with a "Manage →" link, the standing default
    (read-only preset name), the fill button, and the unchanged skipped-events
    restore list (invariant 8's opted-out overrides -- has no home on a tag
    catalogue). Everything per-tag -- which preset, notify, unfollow -- lives
    on `/following`'s dialog only; `POST /subscriptions/{id}/notify` and
    `.../auto-apply` are DELETED, not merely unreachable, since
    `/subscriptions/{id}/settings` already covers the same two fields less
    ambiguously. Measured (browser, seeded at 0 and at 9 followed tags): the
    section is a fixed **218.9px** regardless of follow count -- the
    reduction's whole point -- and the only variance is **3.1px**, between
    having and not having a default preset (the button renders taller than
    the pill it replaces). A "helpful" per-tag summary row creeping back in
    here would restore the growth this phase was built to remove.
  - **The two counts on this row read as the same word in English and in
    Japanese until this build, and no test could see it.** "N tags followed"
    (`TagSubscription` rows) sat next
    to "N tracked · N upcoming · N skipped" (`tracked_concert_ids`, invariant
    8's derivation) -- the reduction deleted the markup that used to keep them
    apart. In JAPANESE this was a true collision: both strings literally
    contained フォロー中 (`%(sub_count)s件のタグをフォロー中` beside
    `%(tracked)s件をフォロー中 ...`). In CHINESE it was two near-synonyms,
    not one repeated word -- the pill said 关注了 (`关注了 %(sub_count)s
    个标签`) while the clock said 已关注 (`%(tracked)s 场已关注 ...`), related
    but not identical, so do not describe the two languages as the same
    failure. Neither msgid CHANGED in either language, so no i18n or copy test
    caught either version; it only showed up in a rendered-page review. Fixed
    by giving the tracked-concerts clock its own word, "tracked" (`home.html`'s
    existing "events tracked" vocabulary) -- 追跡中 / 追踪中 in ja/zh, which
    resolves the true collision in ja and sharpens the near-synonym in zh.
    A future change that puts these two numbers back on one line under
    one shared word will be just as invisible to the suite.
  - **The fill button is suppressed, not disabled, when it can only ever
    report a no-op.** With no default preset, or with no followed tags at
    all, a press could report nothing but "0 filled" -- so the button does
    not render in either case (`{% if default_preset %}` / `{% if
    followed_count %}` in `preferences.html`), rather than rendering to
    confirm nothing happened. The row still renders non-empty: the
    default-preset pill, or its "No default preset yet" absence, stays.
- **Invariant 7's third rule has a repo-wide sweep now**
  (`tests/test_xss_escaping.py`, 2026-08-12). Never interpolating user text into
  an inline `on*` handler used to be enforced per page, by whoever remembered --
  that file covered only the tag picker's `| tojson`. It is now a property of
  the whole template directory: every `{{ }}` inside an `on[a-z]+="..."` must be
  a BARE dotted path whose last segment is in `INTEGER_ID_SEGMENTS`, or be named
  in a one-entry allowlist (`kindname`, which comes from a literal list three
  lines above its uses). A filter, a call or an inline expression is not
  id-shaped BY CONSTRUCTION, so `t.name | e` is judged whole rather than on its
  last word.
  - **`INTEGER_ID_SEGMENTS = {"id"}` is an ALLOWLIST, not an `endswith("_id")`
    suffix rule, and the difference is the whole point.** A suffix rule closes
    today's one known gap and lets the next one through in silence:
    `Concert.event_id` is the standing counterexample and is already in this
    schema -- an editor-TYPED STRING that sails straight through a suffix test,
    safe in an `href` today only because `EVENT_ID_RE` happens to forbid quotes
    and parens. Safe by accident is exactly what a repo-wide sweep exists to
    stop the codebase relying on. Widening the set is a one-line addition, and
    having to write the line down is the moment to check the column really is an
    integer.
  - **The `\b` in `\bon[a-z]+\s*=` is load-bearing, but not for the obvious
    reason.** `action=` cannot match either way, because `[a-z]+` demands a
    letter between `on` and `=` and `actiON=` has none. (`on[a-z]*=` with a STAR
    does match it -- that near-miss produced an early estimate of 58 handlers in
    this app when the real inventory is 5.) What the boundary excludes is `on`
    buried mid-word before a real `=` and a real quote, of which this repo has
    three shapes; only `data-confirm="{{ _('Delete?') }}"` carries an
    interpolation, and it is the idiom invariant 7 PRESCRIBES -- so dropping the
    `\b` does not merely add noise, it reports the recommended remedy as the
    violation.
  - **A sweep that finds nothing passes vacuously**, so a second test pins the
    scanner against a synthetic sample carrying one of each case -- a form
    action that must NOT be seen, an id, a name, a filtered name, the
    allowlisted value, a commented-out handler, a `data-confirm` line -- and
    asserts the exact expressions and line numbers, which is also what proves
    Jinja comments are BLANKED rather than deleted. On its first run the sweep
    found nothing and no production markup was changed to make it green; there
    was nothing to change.
- `src/app/domain/board.py` -- pure column precedence for Home's campaign
  board. `column_for(outcomes, has_open_round)` returns the ONE column a
  concert shows in; PAID > WON > APPLIED > open, deliberately, because money
  you owe outranks a round you could still enter. LOST and NOT_APPLIED place
  nothing (neither is an end state). `service.board_cards` gathers its
  inputs and `OPEN_COLUMN_LIMIT` caps the open column.
- `src/app/fetching.py` — the ONE outbound HTTP fetch, top-level beside
  `i18n.py` and `ops.py` (it does I/O, so it cannot live in `domain/`; both a
  web route and the scheduler import it). It was private to the ramen.events
  importer first, and it was EXTRACTED rather than copied when discovery needed
  it: two copies of a security control means a weakness found later gets fixed
  in one and missed in the other. The guard raises its own
  `FetchError`/`HostNotAllowed`/`FetchFailed` and each caller translates (the
  web route to HTTP status codes, the sweep to a per-artist skip). The redirect
  hook is built PER CALL so it closes over that caller's policy — a
  module-level hook pinned to one is the obvious extraction bug and is exactly
  what a shared guard must not have.
  **It takes a host POLICY, not a host string** (2026-08-06). `HostPolicy`'s
  one required method is `check_async`, run before the request and again on
  every redirect hop. `PinnedHost` is the original guard unchanged — the
  ramen.events importer, the Eventernote sweep, the calendar feeds and
  phase-1 triage, i.e. every pre-existing caller — and additionally keeps a
  genuinely SYNCHRONOUS `check` for its one synchronous caller
  (`web/routes/imports.py`'s `_check_host`) — a property of that policy, not a
  second method every policy must grow. `ApprovedPublicHosts` is the
  completion pass's, and it is the FIRST fetch in this app that is not pinned
  to a host named in code, because a draft's `official_url` is by nature
  somebody else's domain. Three things stand in for the pin: https only, a
  host an admin has approved by name (`FetchDomain`, reviewed at
  `/admin/fetch-domains` — a human is what the pin became), and every address
  the host resolves to being public unicast, ALL of them and not any, since a
  host answering with one public and one private address is a rebinding setup
  rather than a deployment to accommodate. The policy is what makes the check
  async: resolution goes through `_resolve_async`, off the event loop and
  under a total deadline, because this process runs discord.py, FastAPI and
  the 60s tick on ONE loop and a stalling nameserver would otherwise block all
  three, not merely this fetch. `_is_actually_global` deliberately does not
  trust `ip.is_global` alone — measured, not assumed, that the IPv6 wrapper's
  classification wins over an embedded IPv4's in `::/96`, `::ffff:0:0:0/96`
  and `64:ff9b::/96`, each of which can encode 169.254.169.254, which on this
  deploy is a real credential source. Don't add a third policy or a
  "just this once" bypass: the paste fallback
  (`POST /concerts/import/pending/{id}/complete`) is what exists for the cases
  the policy declines, and it needs no fetch at all.
- `src/app/discovery.py` — the discovery sweep: the Eventernote fetch, and
  since 2026-08-02 a calendar-feed pass in front of it. Sits ABOVE `db/` like
  `ops.py`: it imports `domain/`, `app/calendars.py` and `db.service`, and
  nothing in `db/` imports it. **The daily sweep SKIPS CHARACTER tags**
  (`Tag.kind != TagKind.CHARACTER` in `run_sweep`'s tag query) — a reversal of
  the kind-blind rule this file used to state, made on LOAD grounds (owner
  ruling, 2026-08-02): a character's `eventernote_url` is her seiyuu's own
  actor page, and the owner's ~90-tag im@s/LL expansion would have added
  hundreds of daily third-party fetches for pages whose events are franchise
  events the calendar feeds now cover. The URL stays storable and rendered (it
  is still the right link for a character), and `sweep_one_tag` — the manual
  per-tag button — deliberately does NOT filter: one fetch the owner asked for
  is not a daily cost. Don't re-widen the daily query to "every tag with a
  URL"; that is the thing that was undone. The EDITOR side is unaffected and
  keeps CHARACTER: `EVENTERNOTE_KINDS`
  (`domain/types.py`) is the one table saying which kinds' dialogs render the
  field AND which submits `edit_tag` may write it from, and the two must never
  become two lists. FastAPI folds an empty form value into an optional field's
  default, so `""` cannot mean "absent" and the omitted-leaves-alone trick
  `slug`/`voiced_by_tag_id` use is unavailable here — the KIND is what says
  whether this submit had a box. Writing it unconditionally is what erased a
  character's discovery link on the next rename. The sweep keeps the
  future prefix of each page, hands the whole sweep's events to
  `record_discovered` in ONE call (its event-id key is what stops the LoveLive
  15th, listed by nine catalogue tags, being reported nine times), and queues
  ONE `Notification` — never a DM of its own (invariant 4). **The calendar
  pass runs FIRST and pours into that same call and that same digest**: two
  pipelines, one `seen` list, one DM, and a feed that fails to fetch or parse
  is counted and skipped without ever costing the Eventernote half. It is
  outside the actor budget on purpose, so the worst-case tick is the SUM of
  both phases (spelled out at the top of `discovery.py`) — a feed roster that
  grows must never starve the artist rotation behind it. Fetches are
  SEQUENTIAL with a 1s pause; 86 parallel requests at a third party is how an
  IP gets blocked. Gated by `settings.discovery_enabled` (default False, same
  shape as `rehearsal_enabled`), which is also what keeps tests and dev runs
  off the network. Two operational rules, both learned the hard way and both
  silent when broken:
  - **A long in-tick job must beat the heartbeat inside its own loop.** The
    scheduler calls `heartbeat.beat()` BEFORE `tick()`, and `/healthz` reports
    unhealthy once the last beat is `MAX_AGE_SECONDS` (180s) old. A sweep of 86
    pages each with its own deliberate pause occupies the tick for minutes, so
    without a beat per artist it pages the owner about a perfectly healthy app.
    The loop genuinely IS alive, so beating in it is honest, not a workaround.
  - **`stamp_discovery_run` only FLUSHES**, so a stamp written in run_sweep's
    `finally` is thrown away by `scheduler/loop.py`'s handler when it (correctly)
    rolls the poisoned session back. The handler therefore RE-stamps and commits
    on the cleaned transaction. Both halves are needed, and the failure mode is
    the nastiest kind: tests are green because they never roll back, while in
    production a sweep that dies leaves `discovery_due` true and re-runs 86
    fetches every 60 seconds forever. Any future "record that we ran" written in
    a `finally` on the scheduler's session has the same hole.
- `src/app/calendars.py` — WHICH public `.ics` feeds discovery reads, and what
  counts as a lead in each. Same layer as `discovery.py`/`ops.py` (imports
  `domain/` and `db.service`; nothing in `db/` imports it), and it must NOT
  import `app.discovery` — the sweep imports THIS, so the reverse would close
  a cycle, which is why the User-Agent string is spelled out here rather than
  borrowed. The roster is CODE-LEVEL CONFIG, not a table or an env var: it
  changes rarely, changing it is an edit+deploy exactly like the admin
  whitelist, and `dates_are`/`include_prefixes` are typed fields no env CSV
  expresses. **`dates_are` is per FEED, and that is what keeps stored dates
  honest** — the LL-Fans main calendar carries performance dates while its
  per-group subs carry 申込期限, so they are separate roster entries rather
  than one feed with mixed semantics; mixing them would file a deadline as a
  show date, which is the exact mistake `date_is_deadline` exists to prevent.
  `include_prefixes` matches with `str.startswith` and empty means "take every
  VEVENT" (right for the single-purpose imas feed); a SUMMARY the list does
  not want is DROPPED and NOT counted as skipped, because skipped means
  UNREADABLE and folding a working filter into it would make a healthy feed
  read as a rotting one. The nine-feed launch roster was fetched and parsed
  one by one before inclusion and the verdicts — including why an
  empty-but-alive feed was KEPT, and the accepted promoter-named-round gap —
  are in the module's own probe block. Update it there when the roster
  changes; a roster nobody can audit is how a dead feed survives.
  Leads are namespaced (`"<feed key>:<UID>"`), which is what lets one UNIQUE
  column hold both sources.
- `src/app/llm.py` — the ONE DeepSeek call, top-level beside `fetching.py` for
  the same reason (it does I/O, so it cannot live in `domain/`). A hand-rolled
  httpx POST to `/chat/completions` rather than an OpenAI-compatible SDK — the
  same trade `domain/ics_read.py` made against a calendar library, since the
  whole surface this app uses is one JSON request and one JSON response.
  Everything arrives as `LlmError`: an unset key or model raises BEFORE the
  network (misconfiguration named plainly), and transport failure, non-200, a
  non-JSON 200 and a body missing `choices[0].message.content` are one class
  because its one caller treats them identically. It has no opinion about what
  the messages SAY — the prompts and the fence-stripping are pure, in
  `domain/triage_prompts.py`, and what a model's proposed round has to prove is
  pure too, in `domain/round_evidence.py`. The request body pins
  `"thinking": {"type": "disabled"}` unconditionally, and a non-`"stop"`
  `finish_reason` or empty `content` also raises `LlmError` — a 2026-08-05
  incident found `deepseek-v4-flash` thinks by default, burning ~50k reasoning
  tokens per classify call until an overrun emptied `content` and only failed
  later, opaquely, in the YAML parser. `max_tokens` is likewise sent
  EXPLICITLY (`settings.deepseek_max_tokens`, 8192) rather than inherited:
  DeepSeek's own default is the same number, and on 2026-08-09 an unbatched
  classify reply hit it exactly and lost a press that had already been billed —
  a ceiling nobody in this app had chosen was acting as a design constraint.
  Batched, the largest reply over that queue was 1,473 output tokens, so the
  value is a guard against a runaway reply, not a limit anything approaches.
- `src/app/triage.py` — the AI-triage runner: one LLM pass over the open
  discovery queue, on an admin's press. Same layer and discipline as
  `discovery.py` (imports `domain/`, `app/llm.py`, `app/fetching.py` and
  `db.service`; nothing in `db/` imports it), and it is the RUN ORDER only.
  **The load-bearing idea is that the model writes text this app ALREADY
  parses**: the classify half emits the prune-list YAML `parse_prune_list`
  reads, the draft half the `add-concert` YAML `parse_drafts` reads. Malformed
  model output therefore dies at the same boundary a bad agent draft does, and
  no second validation vocabulary exists to drift from the first. It creates no
  concert and dismisses no lead — drafts land as `PendingDraft` rows, so
  `import_commit` stays the only write path into `concerts`, and the prune YAML
  is stored TEXT the owner still pastes through the plan/apply screen, which
  stays the only path to a dismissal. **Every round of every generated draft is
  EVIDENCE-GROUNDED, whatever the model returned** — `verify_rounds`
  (`domain/round_evidence.py`) keeps only the rounds whose verbatim quote it can
  find in the same page text the model was shown, and `strip_rounds`, which used
  to delete all of them unconditionally, is gone. The failure being prevented is
  unchanged and is still this app's worst — an invented `apply_closes_jst`
  reaching a real user as a real reminder for a deadline that never existed —
  but the guarantee moved from "delete everything" to "verify everything" by
  **owner ruling, 2026-08-10, and the ruling is a measurement**: `strip_rounds`
  rested on the claim that Eventernote pages carry no ticket data, and they
  routinely carry the whole ladder in their free-text description. Over 13 real
  productions the model read 7 real rounds, every one verifiable on its own
  page, and `strip_rounds` deleted all 7; `round_evidence.py` in the same run
  accepted 39 rounds across three models with zero invented timestamps. What
  made the old rule right when it shipped was that phase 1 had no way to tell a
  read deadline from an invented one, and that is exactly what no longer holds.
  Eventernote is also sometimes the ONLY source left — an official page drops a
  round once it closes, so a deadline this pass declines to read is one phase 2
  can never recover.
  **The model is shown page TEXT, not HTML**, and that is not a tidying: the
  central property of `round_evidence.py` is that the text the model read and
  the text the verifier searches are the SAME text, so phase 1 now runs
  `html_to_text` and prompts under the one `PAGE_TEXT_CAP` the verifier
  re-normalizes under, exactly as phase 2 does. The old 120k HTML cap against a
  60k text check would have failed a real quote for a transformation nobody
  applied to both sides. The measured cost of dropping the tags is none that a
  leg needs: the 2026-08-10 sample page went 28,296 characters of HTML to 5,141
  of text and kept its date, doors/start, venue, cast, related links (the
  `official_url` phase 2 later fetches is printed as visible text, not only as
  an `href`) and its 受付期間 block; the script bodies and image URLs it loses
  were never evidence.
  **Nothing is dropped silently**: every rejection is written to the new
  draft's `PendingDraft.completion_yaml` — the record phase 2 already writes,
  through the one `completion_record` builder, rendered on the same preview
  banner — because a real deadline quietly discarded is as harmful as a fake one
  quietly kept. That record carries `pass: triage`, and
  `completion_candidates` reads it: a phase-1 record must NOT spend phase 2's
  one attempt, since the two passes read DIFFERENT PAGES and a draft this pass
  could not ground is precisely one that still wants its official page read. A
  draft this pass DID ground is kept away from phase 2 by the older "no rounds
  yet" filter, and that is also correct — `merge_rounds` replaces the whole
  `rounds:` key, so re-reading it would delete the very deadlines phase 1
  rescued. Gated by `settings.triage_enabled`
  exactly as the sweep is gated by `discovery_enabled`; `deepseek_model` has NO
  default, because hardcoding a guess at a third party's current alias starts
  billing a model nobody chose the moment the flag flips. A press costs one
  classify call per `TRIAGE_CLASSIFY_BATCH` (60) leads plus at most
  `TRIAGE_DRAFT_CAP` (25) fetch+draft pairs whatever the queue's size — the
  draft cap is what makes the price of the draft half predictable — with
  fetches SEQUENTIAL and paused and a `heartbeat.beat()` per classify batch and
  per drafted production, for the reasons the sweep has both.
  **The classify batch size is a MEASUREMENT, not a style choice** (2026-08-09,
  against a real 511-lead queue). Unbatched, that queue failed twice: at
  DeepSeek's 8,192 default output cap the reply hit the cap exactly and raised,
  and given a raised cap it completed at 27,142 output tokens only to be
  rejected whole — one lead id under two dismiss reasons, which `parse_prune_list`
  treats as fatal for the entire list, with 494 of 511 leads placed more than
  once. A model cannot hold "each lead exactly once" over a list that long. The
  same queue at 60 per call: 9 calls, all `finish_reason: stop` inside the
  shipped cap, largest 1,473 output tokens, 9,485 total against 27,142, 60s
  against 124s. Cheaper, faster and correct, so raising it buys nothing and
  walks back toward an incoherence that surfaces only as an unusable batch.
  Batching also CHANGED THE FAILURE POLICY, deliberately: one unusable classify
  batch is caught, counted (`TriageReport.classify_batches_failed`, named in the
  admin notice so a partial classify is not silent) and stepped over — the draft
  loop's "one bad production must not cost the other twenty-four" one step
  earlier, which was unavailable while a single call decided everything. A press
  where EVERY batch failed still propagates: then there genuinely is no partial
  to salvage. `domain/triage_prompts.py:merge_classify_results` folds the
  per-batch results back into one, and its load-bearing detail is that the
  merged `dismiss` block is re-dumped as ONE mapping — concatenating two
  batches' text would repeat a reason key, which `parse_prune_list`'s
  `_UniqueKeyLoader` refuses outright.
  It queues ONE admin `Notification` (invariant 4) whose kind `"triage"` is
  deliberately NOT in `UNREPORTED_NOTE_KINDS` — that set is for notices
  reporting ON deliveries, and this one reports on a model's proposals.
  **The request stamp IS the `TriageRun` row** (unlike the sweep, which stamps
  the `DiscoveryState` singleton — triage wants per-run history), which makes
  `stamp_discovery_run`'s two-halves rule apply IN ROW FORM: a rollback restores
  the row to `"requested"`, so `scheduler/loop.py` re-marks it failed and
  commits on the cleaned transaction, or a dead run re-fires 25 fetches and 26
  LLM calls every 60 seconds forever. One refinement found the hard way there:
  `session.rollback()` expires every attribute of every object in the
  transaction, PRIMARY KEY INCLUDED on this aiosqlite stack, so reading
  `run.id` inside the handler raises `MissingGreenlet` rather than a value —
  the id is captured BEFORE the run, and any future post-rollback bookkeeping
  keyed on a row needs the same.
- `src/app/draft_completion.py` — phase 2: filling a pending skeleton's
  `rounds:` from the official page the draft itself names.
  **`HOST_USER_AGENTS` is a per-host exception table, never a global switch**
  (owner ruling, 2026-08-10). `COMPLETION_USER_AGENT` — the honest one — stays
  the default for every host, and a row here says only that this host refuses
  it. `www.lovelive-anime.jp` is the first and, at the time of writing, only
  entry: measured, it answers that UA with HTTP 403 from an S3 error page and
  an ordinary browser string with 200 from Apache, which is a blanket CDN
  filter on non-browser agents rather than a decision about this app — the
  site's own `robots.txt` disallows only `/common/` and publishes a sitemap,
  so its machine-readable policy invites exactly the read the filter refuses.
  It earns the exception on scale rather than convenience: 8 of the owner's 12
  exported concerts and 28 of their 47 hand-typed rounds sit behind that host,
  so without it phase 2 cannot read the franchise the catalogue is mostly made
  of. Nothing else moves — the approved-public policy, the 15-page cap, the 1s
  pause and the 30s deadline are untouched, so the request RATE stays what a
  person clicking would produce. Look the host up through `_user_agent_for`,
  which normalizes via the same `_normalize_host` the approval policy uses (a
  `WWW.`-cased or trailing-dot URL must not miss the table by spelling) and
  falls through to the default on a malformed host rather than raising —
  `urlparse(...).hostname` raises by itself on a bad IPv6 literal, which is
  pinned by a test. Adding a second row is a deliberate act needing its own
  reason; a general "pretend to be a browser" mode is the thing this shape
  exists to prevent.
  Same layer and
  discipline as `triage.py`, and it reuses that feature's `TriageRun` row
  through a `kind` column (`"complete"` vs the classify default), so the
  request/pickup handshake, the budget shape and the re-stamp-after-rollback
  rule exist once rather than twice; `scheduler/loop.py` picks up the oldest
  requested run OF ANY KIND and dispatches on `kind`, so the two halves
  serialize against each other by construction and neither starves the
  reminder tick behind the other. **The rule that replaces `strip_rounds` is
  EVIDENCE GROUNDING**: the model must quote the page line it read each
  timestamp from, and `domain/round_evidence.py` drops any round whose quote
  it cannot find in the same text the model was given — plus the nastier
  case, a quote that IS on the page but does not carry that timestamp. Since
  2026-08-10 it is BOTH passes' rule, not this one's alone (see `triage.py`
  above): phase 1 runs the same `parse_completion_response` →
  `verify_rounds` → `merge_rounds` sequence over the Eventernote page, and
  writes the same `completion_record`. What did NOT move is the half of
  `complete_one` around it — that one amends a STORED draft a human may
  already have proofread, where phase 1 merges into the model's own fresh
  reply and has no row yet.
  **That last check is a CONTIGUITY rule, and it is an owner ruling
  (2026-08-05) made after a review defeated the looser one.** "Do this
  timestamp's digits appear somewhere in the quote" accepts far too much:
  against a correct quote of `申込締切 2026年1月10日(土)23:59` it also
  validates a claimed 01:00 (the hour matches the `1` of `1月`) and a claimed
  10:00 (it matches the day), and a model that quotes the whole page validates
  anything assembled from digits anywhere on it. So month must be immediately
  followed by day as the next number token, hour must be the VERY NEXT number
  token after that date (immediately followed by minute), the date→time span
  is capped at 60 characters and the whole quote at 200 — quoting half the
  page is not evidence, whatever it contains. Two deliberate looseners inside
  that tight rule: the minute is waived only when it is 0 AND the quote
  carries no time separator (`:`/`：`/`分`), because `10時` states no zero to
  find; and the YEAR is not required adjacent to the date, since Japanese
  ticket pages put it in a heading and omit it from the deadline line.
  **The YEAR is the one part of a stamp that is not localised, and until
  2026-08-10 it was not localised AT ALL** — it passed if the number appeared
  anywhere on the page. A mutation harness over the real evidence corpus (129
  timestamp claims three models produced across the real catalogue, each with
  its page) shifted every claim forward one year: **111 of 129 were still
  ACCEPTED, an 86% false-accept rate**, the worst hole this module has had, and
  the one whose consequence is a reminder that fires AFTER the real deadline.
  The page cannot be the fallback because this catalogue is full of pages whose
  SHOW is next year and whose DEADLINES are this year (`2027年4月24日 公演 …
  受付期間：2026年7月24日（金）18:00～`, reproduced on the real zombieland page),
  and the year cannot simply be required in the quote either: measured over the
  same 129 claims, 92 (71%) carry it and 37 (29%) do not (`9月13日（日）23:59`).
  So `verify_rounds` now takes the draft's LEG DATES beside its leg labels
  (`draft_leg_dates`, `domain/round_completion.py`) and decides the year in
  three branches — (1) the quote states one or more years, and the claim must
  be one of them, no fallback; (2) it states none, and the year is ARITHMETIC:
  the latest year in which that month-day falls strictly before the FIRST
  performance, since an application deadline precedes its show; (3) it states
  none and there are no leg dates (a dateless skeleton, which
  `duplicate_concert` legitimately creates), refused. Measured after: **year
  shift 111 → 0 with every other mutation column still 0 and all 129 real
  claims still accepted** (branch 1 carries 92, branch 2 the other 37, all
  resolved correctly). Two things that look like omissions and are not: the
  show date only ever RESOLVES an absent year and never overrules a stated one
  — a `goods_sale` or `stream_ticket_sale` legitimately opens after the live
  date (archive access), so refusing every post-show deadline would be a new
  false-rejection class — and `leg_dates` is REQUIRED with no default, so a
  caller reaches the refusing branch 3 only by saying it has no dates, never by
  forgetting. `page_numbers` went with the fallback: the page's digits are now
  read for exactly one purpose, the on-page substring test.
  **That rule reads the Japanese shape only, and ENGLISH gets a SECOND matcher
  rather than a looser first one** (2026-08-10, after a live run over the real
  catalogue accepted 39 rounds with zero invented timestamps and false-rejected
  exactly one). An international page carries its overseas-package section in
  English, which states the time FIRST, the month as a WORD and the year AFTER
  the day — `"From 19:00 on Wednesday, August 5, 2026 JST to 23:59 on Monday,
  August 17, 2026 JST"`, verbatim from the LoveLive! Series 15th Anniversary
  page — so the number-token adjacency rule cannot match it at all.
  `_english_stamp_in` matches a month WORD adjacent to a day (either order,
  ordinal suffixes allowed and not grammar-checked) and binds it to an `HH:MM`
  by an EXHAUSTIVE WHITELIST of the connectives that join the two (`on`, a
  weekday, `at`, `from`, `JST`, a comma), matched in full — not by distance,
  because that quote's second time sits nine characters after the FIRST date,
  nearer to it than to its own, and any distance rule proves a deadline the
  page never states. Two deliberate divergences from the Japanese path, both
  strictly tighter: a year written beside the day MUST equal the claimed one
  (English gives the year a place, so it is usable evidence; absent, the
  three-branch rule above stands), and 12-hour times and lowercase month words
  are refused
  outright — `7:00 PM` claimed as 07:00 is twelve hours wrong, and "may" is a
  modal verb far more often than a month. The accepted cost is false
  rejections on some phrasings, and that trade is the whole feature: a
  rejection is visible, carries its reason,
  and costs one round typed by hand, while a false accept is a fabricated
  deadline reaching a real user as a real reminder. NOTHING IS DROPPED
  SILENTLY — every rejection reaches the preview with its reason, because a
  real deadline quietly discarded is as harmful as a fake one quietly kept:
  the operator has no way to know to look in either case.
  `domain/page_text.py` produces that text ONCE for both the prompt and the
  check, under one 60k cap; two normalizations would make the guarantee
  theatre by letting a quote fail on a whitespace rule the model never saw, or
  verify against text it was never shown. The completion pass rewrites
  exactly ONE key of the stored draft, `rounds:` (`merge_rounds`,
  `domain/round_completion.py`), and preserves the leading comment prefix,
  because phase 1's duplicate containment matches the whole `# source: ...`
  line and a naive YAML round-trip drops it; a body that will not read back as
  a mapping raises `DraftMergeError` and writes NOTHING, rather than
  "succeeding" by wiping the document. Evidence lives BESIDE the draft
  (`PendingDraft.completion_yaml`), never inside it: a draft is a document
  that gets committed into `concerts`. That record has ONE builder
  (`completion_record`) and names the pass that wrote it, which is what
  `completion_candidates` now reads instead of mere non-emptiness — a phase-1
  record is not an attempt at the official page, and anything without the key
  (every record predating it) reads as phase 2, the reading that withholds an
  attempt rather than paying twice. It creates no concert — `import_commit`
  stays the only write path — and it never fetches `eventernote_url`, which
  carries no ticket information and so could not contain the answer.
  Two failure rules worth keeping: `complete_one` writes `completion_yaml`
  even when the reply or the merge is unusable, because the call was already
  paid for and a second press must not pay for the same junk twice; and
  `SQLAlchemyError` is the ONE exception the per-draft handler does not
  absorb, since a poisoned session means the remaining fourteen paid calls
  would write nothing at all.
- `routes/fetch_domains.py` — `/admin/fetch-domains`, the approval queue that
  pays for the widening above. Its own module for the reason `discoveries.py`
  and `rehearsal.py` are: a router registers whole. English-only and NOT
  wrapped in `_()` like the other admin pages; only the Preferences LINK is
  translated. An unapproved host costs one PASSED-OVER DRAFT, never a failed
  run — counted apart from `skipped` as `blocked_domains`, because nothing
  failed and the remedy is a click; the draft keeps an empty
  `completion_yaml`, stays a candidate and the next press picks it up — and a
  declined
  host is never proposed again, because an approval queue that keeps re-asking
  becomes one nobody reads. `note_fetch_domain` (`db/service.py`) is the
  single write path and RAISES `ValueError` on a host that is URL-shaped,
  port-bearing or blank: storing one would fail closed but SILENTLY, since
  `approved_fetch_hosts` could never match it and an admin would think they
  had approved something. It normalizes through `fetching._normalize_host`,
  the exact function the guard runs before calling `is_approved`, or an
  approval recorded here silently fails to match the lookup done there.
- `src/app/scheduler/` — the tick loop that delivers DMs.
- `routes/welcome.py` -- the five-step welcome wizard, rebuilt on the design
  system and flowing seamlessly into `/setup` (`POST /welcome/advance`
  redirects there once the step count is exhausted). Its default-reminders
  step is the one step here that writes: it materialises a real
  `ReminderPreset` through the single `db/service.py` helper
  `create_preset_from_rules` (no second write path), offering three
  template rule sets (Relaxed / Standard / On the ball) plus a
  sentence-style fine-tune list over the five anchors. Offsets are
  days+hours only -- `PresetItem` has no minutes column, so the wizard
  cannot offer a "30 minutes before" choice; see the minute-offset entry
  in WISHLIST.md.
- `routes/setup.py` — the first-run capture flow, run AFTER the `/welcome`
  wizard. Three plain GETs (`/setup` prune tiles → `/setup/applications` →
  `/setup/ready` reveal) plus two batch POSTs. NO capture-flow step state
  exists anywhere: each screen renders current DB truth (tag-implied concerts
  minus overrides, outcomes), which is what makes it tamper-safe and
  re-runnable (Preferences' "Run first-time setup again" points here). Pruning
  goes through the branch-4 `set/clear_concert_subscription` writers;
  applications funnel EXCLUSIVELY through `record_round_outcome`. All logic is
  in `db/service.py`'s `# First-run capture flow (/setup)` section.
- `routes/calendar.py` — the personal calendar-feed subscription
  (`POST /me/calendar-feed` mints the token, `GET /calendar/{token}.ics` is
  the feed itself). The `.ics` route deliberately has NO `require_user` —
  calendar apps poll it directly with no cookies, so the token in the URL
  *is* the credential.
  **The feed is the user's STANDING-AWARE LANDSCAPE, not a mirror of their
  reminder rules** (ruling 2026-08-04). `user_calendar_events` reads no
  `reminder_queue` at all: it derives every tracked concert's live show dates
  plus each surviving round's next moments, selected by that user's outcome —
  no outcome → future opens + closes, APPLIED → `_result_moment`, WON →
  payment deadline, LOST/NOT_APPLIED/PAID → nothing (a LOST round's auto-armed
  successor is an ordinary no-outcome round and carries the ladder on).
  Future-only, and every exclusion goes through the shared per-user helpers
  the other read surfaces use — no suppression rule is invented here.
  **Reminder rules therefore mean exactly one thing: when Discord DMs you**;
  a sparse preset used to read as a broken calendar, which is the bug this
  replaced. `/mydeadlines` (`bot/cogs/reminders.py`) reads the SAME function,
  so it inherits the landscape — a deliberate behavior change, one derivation.
  `CalendarEvent.anchor` is required because a no-outcome round emits two
  events with one summary: the feed qualifies canonically from
  `CANONICAL_ANCHOR_QUALIFIERS` (`domain/ics_export.py` — 受付開始/申込締切/
  当落発表/支払期限, plain data and deliberately NOT gettext, since canonical
  text is by definition untranslated), while the cog qualifies through `_()`
  in the recipient's language. The locale contract is unchanged: feed
  canonical (`locale=None`), cog localized.
  There is NO per-round `.ics` download any more — the 📅 link,
  `GET /rounds/{id}/ics` and `build_ics` were deleted (a file is a snapshot
  that rots the moment a deadline moves; the feed re-plans on every fetch).
  `build_calendar` and its VEVENT helpers stay; a 404 test pins the absence.
  A minted URL is shown through ONE partial, `_feed_links.html` (webcal://
  link + copy button + the URL), consumed by Preferences, welcome step 4 and
  the concert page's calendar dialog, so the ergonomics cannot drift; the
  mint's `next` runs `safe_next` first, then an allowlist of SHAPES
  (`/preferences`, `/welcome`, `/concerts/` prefix) — `_allowed_next`.
- `routes/rehearsal.py` — the local rehearsal harness (`/admin/rehearsal`):
  seed one canonical concert, pull its reminders forward so the real 60s tick
  delivers them now, and send any DM shape in any language on demand. **Gated,
  not guarded**: `web/app.py` registers this router only when
  `settings.rehearsal_enabled` is true, which production never sets, so there
  the routes do not exist at all — `require_admin` on each one is a second
  layer for a misconfigured deploy, not the primary guard. Its own module
  because a router registers whole and `admin.py` serves routes production
  needs. English-only and NOT wrapped in `_()`, like `/admin/deliveries`. Its
  shape catalogue (`POST /admin/rehearsal/shape`) sends a DM straight from a
  web route: the SECOND sanctioned exception to invariant 4, alongside
  `POST /me/test-dm` and for the same reason (a manual, one-at-a-time
  diagnostic, not a system-initiated notice) — with the extra claim that this
  route is absent from production entirely. Don't read either as licence for a
  third. Operator setup (second Discord app, test server, the redirect URI
  that bites) is `docs/local-dev-bot.md`.
- `routes/discoveries.py` — the discovery review surface
  (`GET /admin/discoveries`, `POST /admin/discoveries/{id}/dismiss`), admin-only,
  linked from Preferences with the other admin pages. Its own module rather than
  a section of `admin.py` for the same reason `rehearsal.py` is: a router
  registers whole, and discovery is a fourth unrelated concern beside the
  delivery log, the broadcast and the catalogue round-trip. English-only and NOT
  wrapped in `_()`, like `/admin/deliveries`; only the Preferences LINK is
  translated. **It writes exactly one column, `dismissed_at`** — it never creates
  a concert, because neither source carries a verified round: Eventernote has no
  ticket information at all, and a fan-maintained calendar entry is a POINTER a
  human still checks against the official page. A lead says "this exists and you
  are not tracking it" and nothing more. Turning one
  into a concert stays with an agent following `.claude/skills/add-concert`,
  which is what the page's copy block (the same `build_discovery_dm`, with
  `budget=None`) is for. `import_commit` remains the only write path into
  `concerts`. Two things it deliberately does NOT do: `open_leads` does not
  filter on `announced_at` (announced is not triaged — the sweep marks every
  fresh lead announced whether the DM named it or merely counted it, so this page
  is where a first sweep's "+N more" is actually reachable; the column is SHOWN
  instead), and a same-date-same-venue collision with an existing leg is a HINT
  on the row, never a suppression, because 昼公演 and 夜公演 are two Eventernote
  events on one date at one venue and suppressing would hide exactly the second
  show. `ConcertDay.eventernote_event_id` is the exact-match half of the same
  question: populated by the import path going forward, so "do I already have
  this?" is an id lookup rather than a guess about Japanese titles that vary in
  spacing, brackets and 〜 marks. It is not backfilled, so that branch gains
  coverage over time rather than arriving complete. It keeps its
  Eventernote-specific NAME on purpose — a calendar lead never exact-matches a
  leg, it only ever gets the date+venue hint, and only when the VEVENT carried
  a LOCATION.
  **A lead's id column is `DiscoveredEvent.source_event_id`** (migration
  `d446e6c0a3e6` renamed it from `eventernote_event_id` and widened it):
  Eventernote rows keep their bare numeric ids, calendar rows carry a
  namespaced `"<feed key>:<UID>"`, and the prefix is what lets the single
  UNIQUE column serve both without a cross-source collision. Two sibling
  columns say the rest — `source` (`"eventernote"` or a `CalendarFeed.key`,
  stored EXPLICITLY rather than parsed back out of the id, so nothing has to
  split a string to know where a row came from) and `date_is_deadline`, which
  is why this page and the DM render such rows as `申込締切 {date}`: the imas
  feed's DTSTART is an application deadline, and showing it as a performance
  date would mislead exactly the person triaging it. Both server-default to
  the pre-calendar behaviour, so every pre-existing row reads back correct.
- Concert edit history: `db/service.py`'s `snapshot_concert`/
  `record_concert_edit`/`concert_audit_log`, backing the `ConcertAudit`
  table (`db/models.py`). Deliberately lightweight — only the concert's own
  top-level scalar fields (title, organizer, URLs, notes, ...), NOT
  day/round/tag adds-removes-edits. `edit_concert` (`web/routes/concerts.py`)
  must call `snapshot_concert` BEFORE mutating the concert and
  `record_concert_edit` AFTER — get that order backwards and every diff
  reads as unchanged.
- `src/app/i18n.py` — gettext plumbing, top-level (not `domain/`, since it
  does file I/O at startup; not `web/`, since the bot imports it too).
  `messages.po` in `src/app/translations/{ja,zh}/LC_MESSAGES/` compile to
  `.mo` in memory at first use per locale (no `.mo` on disk, no deploy-ritual
  change). `en` is `NullTranslations` — the identity function, so English
  output stays byte-identical to the pre-i18n app and no EN test should ever
  assert a translated string. Locale is an asyncio-context `ContextVar`
  (`get_locale`/`set_locale`), set once per request by `web/app.py`'s
  middleware and once per recipient by the scheduler. Write translatable
  strings as `_("literal")` at the point they're rendered/looked up (`_` is
  `gettext`); a module-level dict keyed or valued by translatable text (e.g.
  `LABEL_BY_ROUND_KIND`) instead wraps each literal in `N_()`, a no-op marker
  that only makes `pybabel extract` see it — the real translation happens
  later, at lookup time, via `_`/`gettext`, never at the dict's definition
  time.
  `tags_diff.py` is the third piece of the tags vocabulary and deliberately its
  own module: `tags_yaml.py` is about the FORMAT (serialize/parse), this is
  about COMPARISON, and one module doing all three is how a file starts growing
  unwieldy. It reuses `TagExport` as the current-catalogue carrier rather than
  inventing a second shape, and `service.current_tag_exports` is the ONE builder
  of that snapshot -- the zip export and the differ must compare against exactly
  the same thing or a restore drifts. `gettext_in(locale, msg)` is the explicit-locale escape hatch for
  text composed before a per-recipient locale is known (e.g. `NoticeContext`,
  built once for many recipients up front). `loc_field(obj, field, locale)`
  resolves a UGC field's viewer-locale variant: en → `{field}_en`, zh →
  `{field}_zh`, ja → the original column (Japanese IS the source of truth,
  there's no `_ja` column); an empty string counts as unfilled and falls
  through; there is no cross-locale chaining (zh never falls back through en
  to the original). The UGC layer now also covers venue names through tags
  (`Tag.name_en`/`name_zh`, plus `city_en`/`city_zh`), and phase 2 added
  `ConcertDay.label_en`/`label_zh` and `Round.label_zh` (migration
  `a589d82c11b4`) so leg and round labels resolve in the viewer's language
  too. `Round.label_en` CHANGED MEANING there: it predates the i18n layer
  and used to render to EVERY viewer as an English gloss beside the Japanese
  label, and it is now a true locale variant selected by `loc_field`.
  THREE locale sources are in play, and choosing the wrong one is SILENT --
  nothing raises, the text just comes out in somebody else's language.
  `get_locale()` for anything inside a web request; `user.language` for
  per-recipient text composed once for many recipients OUTSIDE any request
  (scheduler DMs, `NoticeContext`); and an explicit `locale: str | None`
  parameter where the caller must decide -- currently `user_calendar_events`
  alone, where `None` is DELIBERATE so the `.ics` feed stays canonical rather
  than following whoever happened to trigger the render. This bites hardest in
  `db/service.py`, where ~10 sites COPY a label string into a dataclass before
  it ever reaches a template: the field resolves at the copy site, not at
  render time, so the locale has to be right there.
  Editing existing English copy must keep the msgid
  byte-identical (or both catalogues silently lose that translation) and
  update BOTH `messages.po` files — `tests/test_i18n_catalogues.py` extracts
  every msgid in-process and fails on anything untranslated (fuzzy entries
  count as untranslated, since `i18n.py` compiles with `use_fuzzy=False`).
  Locale resolution treats the `lang` cookie as a CACHE of `users.language`,
  never the source of truth: `web/app.py`'s middleware reads the cookie if
  present and supported, else negotiates from `Accept-Language`
  (`i18n.negotiate`), else `en`; the single write path is public
  `POST /language` (`web/app.py`), which always sets the cookie and also
  updates the DB column when signed in (Discord DMs read the column, not the
  cookie); the OAuth callback (`web/auth.py`) seeds the column from the
  cookie, but ONLY at account creation, since the column can't otherwise
  distinguish "defaulted to en" from "chose en".
- Bot and web NEVER contain business logic; they call `db/service.py` — which
  is still literally true after the split, because that module is the facade
  re-exporting the whole layer (see the `src/app/db/` entry above). Keep
  importing from `app.db.service`, not from `app.db.core` or a feature module:
  the facade is the seam, and routing around it is what would make this rule
  stop meaning anything.
- `docs/superpowers/specs/` + `plans/` — date-prefixed design specs and
  implementation plans; each recent feature (cancelled legs, Tags redesign,
  index reorg) committed one of each before code. Follow that pattern for
  substantial features. `docs/codebase-review-2026-07-17.md` records a
  full-codebase review and the fixes it drove.
- `docs/superpowers/demo/` — the interactive concept demos that drove the UI,
  and the **design source of truth** for it. All are self-contained
  single-file mockups on the same design tokens the app ships. Review UI/UX
  changes against the matching one; when the shipped design deliberately
  moves, update that demo so it stays the reference. The full inventory,
  because reaching for the wrong file wastes a whole pass:
  - `dekimasen-demo.html` — the reconciliation reference for Home/Discover/
    concert/editor/tags/preferences/setup. The default answer.
  - `dekimasen-onboarding-demo.html` — the signed-out landing, the new-user
    flow, import + import preview, retroactive-apply, legal.
  - `dekimasen-mobile-demo.html` (static frames, reference CSS values) and
    `dekimasen-mobile-live.html` (interactions) — the phone reference.
  - `dekimasen-tablet-demo.html` — the 701-1040px band.
  - `dekimasen-ux-pass-demo.html` — the 2026-07-24 UX pass's 20 changes,
    including the two-shape callout grammar.
  - `_tablet_harness.html` — not a demo: the measuring rig the tablet band
    was built against (the measure-don't-reason rule lives in CLAUDE.md's
    "UI conventions" and in full in `docs/ui-conventions.md`).
