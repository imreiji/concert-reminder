# AI draft completion (AI triage, phase 2)

**Status:** design, approved by the owner 2026-08-05.
**Predecessor:** `docs/superpowers/specs/2026-08-05-ai-triage-design.md` (phase 1),
shipped and calibrated against real leads the same day — the owner's verdict on
DeepSeek V4 Flash's Japanese-domain judgment was that it holds up, which is the
condition phase 2 was gated on.
**Wishlist entry:** #3, "AI completion of a skeleton draft (AI triage, phase 2)".

## What phase 1 left, and what this is

Phase 1 turns discovery leads into SKELETON drafts: trilingual titles and leg
labels, legs, cast tags — and `rounds: []`, always, stripped in code whatever
the model returned. Every skeleton it queues is therefore a concert with an
empty ladder on purpose, because a round is the one promise this app makes to a
user ("a deadline it names is real") and an invented `apply_closes_jst` is the
worst thing this system can produce.

Phase 2 fills that ladder. An open pending draft usually already carries an
`official_url` (the model reads it off the Eventernote page), so the run does
not need to be told where to look: it fetches the URL the draft already names,
reads the page, and proposes rounds — leaving the owner proofreading rather than
typing four timestamps per round.

**The hard question the phase-1 spec recorded was how the official ticket page
reaches the model.** The owner's ruling (2026-08-05): automatically, from the
URL already in the draft, with a paste fallback for everything that declines.
That is a deliberate widening of this app's fetch surface, and section 4 is
about paying for it honestly.

## The one rule that replaces `strip_rounds`

Phase 1's guarantee was mechanical: `strip_rounds` ran on every draft whatever
the model said, so a hallucinated round could not exist. Phase 2 removes that
rule by design, and it must not be replaced by trust in a prompt.

**A round survives only if the model can show where it read it, and the app can
find that text on the page.** Each proposed round carries an `evidence` mapping:
one quoted source line per timestamp field it filled. Verification is pure code
(`domain/round_evidence.py`) and drops any round where:

- a timestamp field has no quote;
- a quote does not occur in the page text (compared whitespace-normalized);
- the timestamp's own digits do not occur inside its quote (compared after
  normalizing full-width digits to ASCII and 年/月/日/時/分 to separators) — so a
  quote cannot be some *other* real line from the page;
- the anchors are out of order (opens ≤ closes ≤ results ≤ payment, over
  whichever are present);
- an `applies_to` label names a leg the draft does not have.

The check runs against **exactly the text the model was given**. If the model
sees HTML and evidence is checked against extracted text — or the reverse — the
guarantee is theatre, so one function produces the text and both consume it.

**A rejected round becomes a visible warning, never a silent drop.** Dropping a
proposed round without saying so hides a real deadline exactly as effectively as
inventing one would fabricate a fake one, and the operator would never know to
look. Each rejection is recorded with its reason and rendered on the preview:
*"proposed a 2026-01-10 23:59 close I couldn't find on the page — check by
hand."*

## 1. Surface and run shape

`/concerts/import/pending` grows one button, **Complete drafts**, admin-only
(it spends money, exactly as the triage button does) and gated on
`settings.triage_enabled` — one flag for the whole LLM feature family, because
the two presses are the same decision to spend.

The press writes a run row; the scheduler tick picks it up. The work is minutes
long and a web request cannot hold it, which is the same reason phase 1 has this
shape, and reusing it means one pickup, one failure path, one history.

**The run row is `TriageRun` with a new `kind` column** (`"classify"` server
default, `"complete"` for this run). `pending_triage_run` keeps returning the
oldest requested row whatever its kind and `scheduler/loop.py` dispatches on it,
so the two run kinds are serialized against each other by construction — one
tick never runs both. The stamp-after-rollback discipline (`mark_triage_failed`
on a cleaned transaction, the run id captured BEFORE the run because
`session.rollback()` expires the primary key on this stack) is inherited whole.

Counts are kind-specific. `leads_seen`/`productions`/`dismissals_proposed`/
`calendar_skipped` stay NULL on a completion run; four new nullable columns
(`drafts_completed`, `rounds_added`, `rounds_rejected`, `blocked_domains`) stay
NULL on a classify run. NULL keeps meaning "never got there / not this kind's
business" and a written 0 keeps meaning "looked, found none", which is the
distinction the nullable columns already exist to hold.

Which drafts it walks: the requester's own open pending drafts (never committed,
never discarded) that parse, still have no rounds, and have not been attempted
yet. **Attempted means `completion_yaml` is non-empty, and that is written only
when an LLM call actually happened** — a draft skipped for a missing URL, an
unapproved domain or a dead fetch is left clear, so the next press retries it
once the reason is fixed. That is phase 1's containment rule in this feature's
terms: a press must never re-pay for a decision already handed to the operator.

Budget copies phase 1 wholesale: `COMPLETION_DRAFT_CAP = 15` (an official ticket
page is a much bigger read than an Eventernote event page, so the cap is lower
than triage's 25), a 1s pause between fetches, `heartbeat.beat()` per draft, a
240s wall clock over the loop checked at the TOP of each iteration, per-draft
failures caught/counted/stepped over, and `SQLAlchemyError` propagating rather
than being absorbed — a poisoned session cannot persist anything, so stepping
over it would spend fourteen more paid calls writing nothing.

One admin `Notification` per run through the outbox (invariant 4), kind
`"triage"` reused: it reports on a model's proposals exactly as phase 1's does,
and adding a second kind that behaves identically would only be a second thing
to remember to keep out of `UNREPORTED_NOTE_KINDS`.

## 2. Per draft, six steps

1. **Pick the URL** — `official_url`, else `source_url`. **Never
   `eventernote_url`**: Eventernote carries no ticket information at all, which
   the phase-1 draft prompt already states, so fetching it would spend a request
   to read a page that cannot contain the answer. No URL at all → skipped and
   counted, left for the paste fallback.
2. **Host gate** — the URL's host must be an approved `fetch_domains` row. An
   unknown host creates a *pending* row and the draft is skipped as blocked; a
   declined host is skipped and never proposed again. Nothing is fetched from a
   host a human has not approved.
3. **Fetch** — through `fetching.py` under the new public-host policy (section 4).
4. **HTML → text** — `domain/page_text.py`, pure, bs4 (already a dependency;
   `ingest.py` and `eventernote.py` both use it): drop `script`/`style`, extract
   text with a separator, collapse runs of whitespace, cap the length. This text
   is what the model sees and what evidence is verified against.
5. **One LLM call** — the draft's own YAML (so the model knows the leg labels
   `applies_to` must bind to) plus the page text. The reply is **only** a
   `rounds:` list in the add-concert vocabulary, each round carrying an extra
   `evidence` mapping.
6. **Verify, merge, record** — reject per the rule above; write the survivors
   into the draft; store evidence and rejections beside it.

## 3. The merge writes exactly one key

Only `rounds:` in the stored `draft_text` changes. Every other key stays as it
was, so nothing the operator already proofread churns underneath them.

**The leading comment prefix must survive, and that is not cosmetic.** A
phase-1 draft's first line is `# source: https://www.eventernote.com/events/N`,
and phase 1's containment check reads that exact line back out of
`pending_draft_texts` to avoid re-drafting a production nobody has triaged yet.
A naive `yaml.safe_load` → mutate → `yaml.safe_dump` drops it, which would break
containment silently and hand the operator duplicate drafts on the next triage
press. So the merge splits the stored text at the first non-comment line, keeps
that prefix verbatim, round-trips the body, and re-prepends. A test pins that
the source line survives a completion.

Known and accepted effect: comments *inside* the body are lost to the
round-trip. Phase-1 drafts have none (they are `safe_dump` output) and an
agent-pasted draft's inline comments are not load-bearing. Adding a
comment-preserving YAML library for this would be a new dependency to protect
data nothing reads.

**Evidence never enters the draft.** It is proofreading scaffolding, not concert
data, and a draft is a document that can be exported, re-pasted and committed
into `concerts`. It lives in a new `PendingDraft.completion_yaml` column, one
small YAML document per row holding `source_url`, `evidence` (round index →
field → quote) and `rejected` (a list of human-readable reasons). One column
rather than three: it is written once, read once, by one feature.

## 4. The fetch guard grows a policy, not a loophole

`fetching.py` is the one host-pinned fetch, extracted rather than copied
precisely so a weakness found later is fixed once. Widening it to arbitrary
public hosts must not fork it.

The `allowed_host: str` parameter becomes a **policy object** with one method,
`check(url)`, called before the request and again by the existing redirect hook
on every hop:

- `PinnedHost("www.eventernote.com")` — today's behaviour exactly, for the
  ramen.events importer, the discovery sweep and phase 1's event fetch.
- `ApprovedPublicHosts(is_approved)` — https only; the host must resolve
  (`getaddrinfo`) to public addresses ONLY, so a private, loopback, link-local
  or CGNAT address is refused and the Lightsail instance metadata endpoint at
  169.254.169.254 is unreachable; and `is_approved(host)` must return true, so a
  redirect off an approved domain onto an unapproved one is refused on the hop.

Byte cap, timeout and redirect limit are unchanged and apply to both.

Accepted, documented residual risk: DNS rebinding between the resolution check
and the connection. Closing it fully means connecting to the resolved IP with a
`Host` header override and re-doing TLS verification by name, which is a
meaningful amount of machinery; the exposure here is an attacker who both
controls a hostname a human has explicitly approved and can flip its DNS inside
the request window. Recorded rather than silently ignored.

## 5. Domain approval

New table `fetch_domains`: `host` (unique, lowercased), `first_seen_at`,
`first_seen_url` (what wanted it, so the approver can judge), `approved_at`,
`declined_at`, `decided_by` (FK `users.discord_id`, SET NULL). Both timestamps
NULL means pending — the nullable-timestamp idiom `dismissed_at`/`announced_at`
already use, rather than a status string.

`/admin/fetch-domains` in its own router (`routes/fetch_domains.py`), admin-only,
English-only and not wrapped in `_()`, exactly like `/admin/deliveries` and
`/admin/discoveries` — a router registers whole, and this is a fifth unrelated
admin concern. Linked from Preferences with the other admin pages. Two POSTs,
approve and decline. The pending-drafts list shows a callout when hosts are
waiting, so a blocked completion run is discoverable from where you pressed the
button rather than only from an admin page you had no reason to open.

No seed list. The first completion run proposes whatever the drafts name, and
the operator approves the handful of ticket vendors and franchise sites that
actually recur.

## 6. Fallbacks

Every decline leaves the draft at `rounds: []` — today's exact behaviour — and
the review page offers a paste box: drop the page text in and the same
completion runs on it. **That path needs no fetch and no domain approval**, so
it works when everything else declined, which is what makes the automatic half
safe to keep narrow.

`POST /concerts/import/pending/{id}/complete` takes the pasted text and runs the
verify/merge inline: one LLM call is a bounded wait in a request, unlike a
15-draft batch. Admin-only and `triage_enabled`-gated like the batch press.

Two caps, and the byte arithmetic matters. The form field is refused above
**150,000 characters** — Starlette hard-caps every `Form(...)` field at 1MB
whatever an app constant says (CLAUDE.md), and Japanese costs 3 bytes per
character in UTF-8, so 150k CJK characters is ~450KB and stays clear of the wall
that would otherwise arrive as an opaque failure. What actually reaches the
model is then capped at **`PAGE_TEXT_CAP` = 60,000 characters**, the same cap a
fetched page's extracted text gets: extracted text is far denser than HTML, and
one cap with one meaning is what keeps the pasted path and the fetched path
behaving identically.

Pasted text runs through the same whitespace normalization `page_text.py`
applies to extracted HTML, for the section-2 reason: evidence is verified
against exactly what the model saw, and two normalizations would make the
guarantee depend on which path produced the page.

## 7. Preview rendering

The round cards on `import_preview.html` come from the shared
`_editor_round_card.html`, which `concert_new.html` and `concert_edit.html` also
render. The partial gains ONE optional variable: when evidence for that round is
passed, it renders the quotes beneath the timestamps; when it is not (the other
two surfaces, and every non-completed draft), the card's output is
byte-identical to today. That keeps the coherence pass's rule — never hand-roll
a card copy — and a test pins the two other surfaces unchanged.

Shapes follow the callout grammar: evidence is ongoing state, so `.edgecard`;
rejections need attention, so `.banner .warn`. Both strings are user-facing
editor copy and go through `_()` with both catalogues updated.

## 8. What this does NOT do

- **No auto-commit.** `import_commit` remains the only write path into
  `concerts` (invariant 6's neighbourhood), and a completed draft is still a
  preview a human presses commit on.
- **No search.** The URL must already be in the draft or the page pasted by
  hand. A model choosing what to fetch is the version of this feature that puts
  its judgment where the app's core promise lives.
- **No round information from Eventernote.** It has none.
- **No sweep-time automation.** Admin-initiated only, like phase 1.
- **No change to phase 1.** The classify and skeleton-draft halves, including
  `strip_rounds`, are untouched: a skeleton is still born with an empty ladder,
  and filling it is a separate, human-initiated act.

## 9. Testing

Fake LLM client and fake fetch throughout, as phase 1 does — nothing touches the
network. What gets pinned:

- an ungrounded round is dropped, and its rejection reaches the preview;
- a quote that occurs on the page but does not contain its own timestamp's
  digits is rejected (the "some other real line" case);
- out-of-order anchors are rejected; an `applies_to` naming a missing leg is;
- the merge changes only `rounds:` and the `# source:` line survives;
- an unapproved host is never fetched, and a redirect from an approved host to
  an unapproved one is refused;
- a host resolving to a private address is refused;
- `concert_new` and `concert_edit` render byte-identically (the shared partial);
- the run's pickup, its kind dispatch, and the re-stamp-after-rollback path;
- the paste fallback needs neither fetch nor approval;
- the cap, the budget and per-draft skip-and-count.

Prompt quality stays uncalibratable in CI, exactly as phase 1 records: it is
judged operationally on the first real press, for cents.

## 10. Build order

1. Migration + models (`fetch_domains`, `TriageRun.kind` + four counts,
   `PendingDraft.completion_yaml`).
2. `domain/page_text.py`.
3. `domain/round_completion.py` (prompt, reply parse, merge).
4. `domain/round_evidence.py` (verification).
5. `fetching.py` host policies; `db/service.py` fetch-domain and
   pending-completion helpers.
6. `app/draft_completion.py` runner; scheduler dispatch.
7. Web: the button, the run request, the pending-list callout, the preview
   rendering, the paste fallback.
8. `/admin/fetch-domains` router and the Preferences link.
9. Catalogues (ja + zh), CLAUDE.md, `docs/deploy.md`, WISHLIST.
