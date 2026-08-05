# Crawler-Trap Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the repo-visible second layer against the Discover `?tag=` crawler trap that took production down on 2026-08-04: `rel="nofollow"` on every filter link, a `robots.txt` route, and runbook lines naming the dashboard-only mitigations.

**Architecture:** Three independent layers, no schema, no catalogue strings. (1) Template-level `rel="nofollow"` on every anchor pointing at a query-stringed `/discover` URL — both the server-rendered Jinja anchors and the two `<script>` sites that CREATE anchors client-side (sites that merely rewrite `.href` on existing anchors keep the server-set `rel` and need no change). (2) A `GET /robots.txt` route in `web/app.py` beside `/healthz`, plain text, disallowing query-stringed `/discover` only. (3) Docs: `docs/deploy.md` gains the Cloudflare WAF rule and the UptimeRobot response-time alert so a re-setup recreates them, and `WISHLIST.md` gets its mandated ship-move + re-rank pass.

**Tech Stack:** FastAPI (`PlainTextResponse`), Jinja2 templates, pytest.

**Why this is WISHLIST #1 (context for the engineer):** Meta's `meta-webindexer` and `SemrushBot` exhaustively walked Discover's tag-filter URLs (`?sort=…&tag=…&tag=…` — combinatorially infinite after the 605-tag expansion), each a full server-side render. 21 hours of that drained the $5 Lightsail's burst credits; the instance was hypervisor-throttled and the site timed out for half a day. The outage is already mitigated at the edge (Cloudflare Managed Challenge, dashboard-only); this plan is the code-side layer. Both culprit bots respect `nofollow` and `robots.txt`.

**Load-bearing design constraint (do not violate):** the filter chips are real `<a href>` links so the page degrades without JavaScript, and `history.replaceState` means real signed-in humans reload/bookmark/share `?tag=` URLs. Therefore NOTHING here may block or break a `?tag=` request — we only mark the links as not-for-crawling. Do not remove or alter any `href`; only add `rel` attributes.

## Global Constraints

- Tests: `uv run --isolated pytest -q` MUST pass before any commit. (`--isolated` is mandatory on this machine — an external `serve.py` locks `.venv`; never resync it.)
- Lint: `uv run --isolated ruff check .` MUST be clean before any commit.
- No new dependencies.
- No translatable strings are added anywhere in this plan (robots.txt and docs are not user-facing copy), so NO pybabel/catalogue work.
- Config files stay ASCII-only.
- UI copy, if any, is sentence case — this plan adds none.
- Commit messages end with the Co-Authored-By / Claude-Session trailer block per the harness rules.

---

### Task 1: `rel="nofollow"` on every Discover filter link

**Files:**
- Modify: `src/app/web/templates/discover.html`
- Test: `tests/test_discover.py` (append two tests at the end)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing other tasks consume. Independent of Tasks 2–4.

**Background:** `discover.html` has EIGHT server-rendered anchor sites whose `href` is a query-stringed `/discover` URL, and its inline script has TWO sites that create such anchors from scratch (`document.createElement("a")`). The script's `updateLinks()` function only rewrites `.href` on anchors the server already rendered — the `rel` attribute survives that, so `updateLinks` needs NO change. Anchors pointing at `/concerts/…`, `/tags`, `/concerts/new` are NOT filter links and must NOT get `nofollow`.

- [ ] **Step 1: Write the two failing tests**

Append to `tests/test_discover.py` (it already imports `re`, `Tag`, `ConcertTag`, `TagKind`, and has the `client`/`seeded` fixtures and `Seed` class shown at the top of the file):

```python
# ── crawler-trap hardening: rel="nofollow" on filter links ───────────────


async def test_every_discover_filter_link_carries_nofollow(client):
    """The 2026-08-04 outage: crawlers exhaustively walked the ?tag=
    combinatorial URL space. Every anchor whose href is a query-stringed
    /discover URL must carry rel="nofollow" -- asserted as the PROPERTY
    (sweep every rendered <a>), not as a list of known sites that would
    silently rot when a new filter link is added.

    The fixture selects a tag AND a status so the active-filter chips, the
    "Clear all" link and the region chips all render -- the maximal set of
    filter-link sites on one page."""
    async def build(seed):
        c = await seed.concert("nofollow-fixture")
        await seed.open_round(c)
        artist = Tag(name="nofollow band", kind=TagKind.ARTIST)
        venue = Tag(name="nofollow hall", kind=TagKind.VENUE, region="Kanto")
        seed.s.add_all([artist, venue])
        await seed.s.flush()
        seed.s.add_all([
            ConcertTag(concert_id=c.id, tag_id=artist.id),
            ConcertTag(concert_id=c.id, tag_id=venue.id),
        ])
        await seed.s.flush()
        return artist

    artist = await seeded(client.db, build)
    r = client.get(f"/discover?tag={artist.id}&status=open")
    assert r.status_code == 200

    anchors = re.findall(r"<a\s[^>]*>", r.text)
    filter_links = [a for a in anchors if "/discover?" in a]
    # Sort links, status facets, tag chips, region chips, active-filter
    # chips, "Clear all" and "Clear filters" -- if this floor is not met the
    # sweep is matching nothing and the test is asserting vacuously.
    assert len(filter_links) >= 8, filter_links
    missing = [a for a in filter_links if 'rel="nofollow"' not in a]
    assert not missing, missing


def test_discover_script_created_filter_links_set_nofollow():
    """The client-side rebuild CREATES two kinds of anchors from scratch
    (afChip and the Clear-all link); a rendered-page test cannot see them,
    so pin the source. updateLinks() only rewrites .href on server-rendered
    anchors, whose rel survives -- deliberately not asserted."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "app" / "web" / "templates" / "discover.html"
    ).read_text(encoding="utf-8")
    assert 'a.rel = "nofollow";' in src
    assert 'clear.rel = "nofollow";' in src
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --isolated pytest "tests/test_discover.py::test_every_discover_filter_link_carries_nofollow" "tests/test_discover.py::test_discover_script_created_filter_links_set_nofollow" -q`

Expected: both FAIL — the first with a non-empty `missing` list, the second on the first `assert`.

- [ ] **Step 3: Add `rel="nofollow"` to the eight server-rendered sites**

In `src/app/web/templates/discover.html`, add the attribute to exactly these anchors (line numbers as of `bf084aa`; match on content, not line number):

1. ~line 40 — active-filter tag chip:
```html
      <a class="chip on" data-af-tag="{{ t.id }}" rel="nofollow"
         href="/discover?{{ filter_query(sort, rest, status) }}"
```
2. ~line 45 — active-filter status chip:
```html
      <a class="chip on" data-af-status rel="nofollow"
         href="/discover?{{ filter_query(sort, selected_tags, "") }}"
```
3. ~line 50 — "Clear all":
```html
      <a class="af-clear" rel="nofollow" href="/discover?sort={{ sort }}"
```
4. ~line 173 — sort links:
```html
      <a data-sort="{{ key }}" rel="nofollow" href="/discover?{{ filter_query(key, selected_tags, status) }}"
```
5. ~line 189 — status facet links:
```html
      <a data-status="{{ key }}" rel="nofollow"
         href="/discover?{{ filter_query(sort, selected_tags, '' if status == key else key) }}"
```
6. ~line 202 — the `chiplink` macro (tag chips):
```html
      <a class="chip kind-{{ t.kind.value }} {% if active %}on{% endif %}" data-name="{{ t.name | lower }}"
         data-tag-ids="{{ t.id }}" onclick="return toggleTagFilter(event, this)" rel="nofollow"
         href="/discover?{{ filter_query(sort, (others if active else others + [t.id]), status) }}">
```
7. ~line 220 — region chips (their `href` is built server-side in `routes/discover.py`'s `region_sidebar_links`, but the attribute lives here in the template — no route change):
```html
        <a class="chip kind-venue {% if r.active %}on{% endif %}" data-name="{{ r.name | lower }}"
           data-tag-ids="{{ r.ids | join(',') }}" onclick="return toggleTagFilter(event, this)"
           rel="nofollow" href="{{ r.href }}">
```
8. ~line 228 — "Clear filters":
```html
      <a class="dim" rel="nofollow" href="/discover?{{ filter_query(sort, [], status) }}"
```

Do NOT touch: the tile anchors (`/concerts/…`), the deadline-list anchors, the search `<form>` (robots.txt covers any URL a form submit could mint), "Manage tags", "Create the first one".

- [ ] **Step 4: Set `.rel` at the two script sites that create anchors**

In the same file's inline script — in `afChip` (~line 279), after `a.href = href;`:

```js
      a.href = href;
      a.rel = "nofollow";
```

and in `rebuildActiveRow`'s Clear-all branch (~line 314), after `clear.href = …`:

```js
        clear.href = "/discover?sort=" + sort;
        clear.rel = "nofollow";
```

Also update the script's header comment (the block at ~line 256 beginning `// Tag/region filtering, …`) — append one sentence to it:

```js
  // All filter links additionally carry rel="nofollow" (server-rendered and
  // created-here alike): the ?tag= URL space is combinatorial and crawling
  // it took production down on 2026-08-04. updateLinks() only rewrites
  // .href on existing anchors, so the server-set rel survives it.
```

- [ ] **Step 5: Run the two tests to verify they pass**

Run: `uv run --isolated pytest "tests/test_discover.py::test_every_discover_filter_link_carries_nofollow" "tests/test_discover.py::test_discover_script_created_filter_links_set_nofollow" -q`

Expected: both PASS.

- [ ] **Step 6: Run the whole discover file, then the full gates**

Run: `uv run --isolated pytest tests/test_discover.py -q` (a template change can break other render tests — check the whole file), then `uv run --isolated pytest -q` and `uv run --isolated ruff check .`

Expected: all pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/app/web/templates/discover.html tests/test_discover.py
git commit -m "feat: rel=nofollow on every Discover filter link

Both bots from the 2026-08-04 crawl outage respect nofollow. Server-
rendered anchors get the attribute in the template; the two script
sites that create anchors set .rel; updateLinks only rewrites .href so
the server-set rel survives. No href changes -- the no-JS degradation
and reload-of-a-filtered-view are untouched."
```

---

### Task 2: `robots.txt` route

**Files:**
- Modify: `src/app/web/app.py` (add route beside `/healthz`, ~line 398)
- Test: `tests/test_web.py` (append one test)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `GET /robots.txt` → `text/plain` body `"User-agent: *\nDisallow: /discover?\n"`. Task 3's docs mention it exists.

**Directive-shape decision (the wishlist entry left this to build time; here is the check):** RFC 9309 and the original 1994 grammar both match rules as a **literal prefix against the URL's path-plus-query**, and `?` is NOT a metacharacter in either. So `Disallow: /discover?` matches every query-stringed `/discover` URL and does not match the bare `/discover` page — with no wildcard support required of the crawler. A `Disallow: /*?tag=` wildcard shape would need RFC 9309 `*` support and buys nothing here; the sort-only URLs (`/discover?sort=…`) are a handful and blocking them too is fine (they carry no content the bare page lacks). Use the literal-prefix shape.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py` (its `client` fixture is at the top of the file):

```python
def test_robots_txt_blocks_query_stringed_discover_only(client):
    """The 2026-08-04 crawl outage: Discover's ?tag= filter URL space is
    combinatorially infinite and every hit was a full render. robots.txt
    disallows the query-stringed URLs by literal prefix ('?' is not a
    metacharacter in any robots grammar) while the bare catalogue page --
    and everything else -- stays crawlable."""
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    lines = [line.strip() for line in r.text.splitlines()]
    assert "User-agent: *" in lines
    assert "Disallow: /discover?" in lines
    # The bare page must stay crawlable: no broader disallow may appear.
    assert "Disallow: /discover" not in lines
    assert "Disallow: /" not in lines
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --isolated pytest "tests/test_web.py::test_robots_txt_blocks_query_stringed_discover_only" -q`

Expected: FAIL — 404 (route does not exist).

- [ ] **Step 3: Add the route**

In `src/app/web/app.py`: extend the existing `fastapi.responses` import with `PlainTextResponse` (the file already imports `HTMLResponse`/`RedirectResponse` — add to that same import line), then add the route directly ABOVE the `@app.get("/healthz")` handler (~line 398):

```python
    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots_txt() -> str:
        # Discover's filter chips are real links over a combinatorial ?tag=
        # URL space -- the open crawler trap that took production down on
        # 2026-08-04 (WISHLIST has the incident; deploy.md the dashboard
        # half). "Disallow: /discover?" is a literal prefix match against
        # path-plus-query under both the 1994 grammar and RFC 9309 ('?' is
        # not a metacharacter), so every query-stringed /discover URL is
        # blocked while the bare catalogue page stays crawlable -- no
        # wildcard support required of the crawler.
        return "User-agent: *\nDisallow: /discover?\n"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --isolated pytest "tests/test_web.py::test_robots_txt_blocks_query_stringed_discover_only" -q`

Expected: PASS.

- [ ] **Step 5: Full gates**

Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .`

Expected: all pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/app/web/app.py tests/test_web.py
git commit -m "feat: robots.txt disallowing query-stringed /discover

Literal-prefix 'Disallow: /discover?' works under both the 1994 grammar
and RFC 9309 with no wildcard support required; the bare catalogue page
stays crawlable."
```

---

### Task 3: Runbook lines for the dashboard-only mitigations

**Files:**
- Modify: `docs/deploy.md` (section 8 "Hardening" ~line 140, section 10 "Monitoring" ~line 248)

**Interfaces:**
- Consumes: Task 2's route existing (the text mentions it).
- Produces: nothing code-level. Docs only — no tests, but run the gates anyway before committing (cheap, and the rule is unconditional).

**Background:** the cure for the 2026-08-04 outage lives ONLY in dashboards: a Cloudflare WAF Managed Challenge rule and an AI-crawler toggle (deployed by the owner during the incident), plus an UptimeRobot response-time alert that is STILL UNSET — the keyword monitor stayed green through the whole outage because `/healthz` answered 200 `"ok":true` in 72 seconds and a keyword monitor has no latency threshold. If either dashboard is ever rebuilt from this runbook, these must not be lost.

- [ ] **Step 1: Add the WAF bullet to section 8 ("Hardening")**

In `docs/deploy.md`, after the "Lock the origin to Cloudflare" bullet (~line 144), insert:

```markdown
- **Crawler-trap WAF rule** (dashboard-only -- recreate it on any Cloudflare
  re-setup): Security -> WAF -> Custom rules -> Managed Challenge when
  URI path equals `/discover` AND query string contains `tag=`. Challenge,
  NOT block: Discover writes filtered URLs into the address bar via
  `history.replaceState`, so real signed-in humans reload/bookmark/share
  `?tag=` URLs and must be able to pass. Also enable Cloudflare's AI-crawler
  blocking toggle. These are the edge half of the 2026-08-04 crawl-outage
  remedy; the repo half (`rel="nofollow"` on Discover's filter links plus
  the `/robots.txt` route) deploys with the app.
```

- [ ] **Step 2: Add the response-time alert to section 10 ("Monitoring")**

After the existing UptimeRobot keyword-monitor paragraph (~line 260), insert:

```markdown
Add a SECOND UptimeRobot monitor with a response-time alert on
`https://dekimasen.app/` (dashboard-only -- recreate it alongside the
keyword monitor). The keyword monitor has no latency threshold: during the
2026-08-04 crawler outage `/healthz` answered 200 `"ok":true` in 72
seconds and the monitor stayed green for the entire half-day the site was
unusable. A response-time alert is the one that would have fired.
```

- [ ] **Step 3: Gates and commit**

Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .` (docs-only change; both should be untouched and pass).

```bash
git add docs/deploy.md
git commit -m "docs: runbook lines for the crawl-outage dashboard mitigations

The WAF Managed Challenge rule and the (previously unset) UptimeRobot
response-time alert live only in dashboards; name them so a re-setup
recreates them."
```

---

### Task 4: WISHLIST ship-move and re-rank pass

**Files:**
- Modify: `WISHLIST.md` (entry "### 1. Discover's filter links are an open crawler trap" ~line 945; the `## Shipped` section ~line 1524; the intro re-rank notes near the top)

**Interfaces:**
- Consumes: Tasks 1–3 committed (the entry records what shipped).
- Produces: nothing. Docs only.

**Background (the mandated ritual, from CLAUDE.md's "Feature wishlist" section):** every ship moves its entry to Shipped with the date, then a full revision pass re-ranks what remains. Follow the file's own house style — Shipped entries record what shipped, when, and anything the build learned; re-rank notes say whether moves are by removal/insertion or on merit.

- [ ] **Step 1: Move the entry and re-rank**

In `WISHLIST.md`:

1. Cut the whole `### 1. Discover's filter links are an open crawler trap` entry out of Proposed.
2. Add a Shipped entry at the TOP of the `## Shipped` section (it is newest-first — verify against the first existing entry and match its heading style), dated 2026-08-04, recording: `rel="nofollow"` on all eight server-rendered filter-link sites plus the two script-created anchors; the `/robots.txt` route with the literal-prefix `Disallow: /discover?` shape (and WHY that shape: `?` is not a metacharacter in any robots grammar, so no wildcard support is required — the build-time check the entry asked for); the two runbook additions; and the explicit non-build: no caching/cheap-render path for anonymous filtered Discover, per the entry's own "explicitly NOT tracked" line — that remains the heavyweight remedy if a challenge-passing crawler ever fires the trap again.
3. Renumber the remaining Proposed entries (2→1, 3→2, … 14→13). Minute-level reminder offsets returns to #1 **by pure removal** — its twelfth move; append one sentence to its displacement history in the entry's own voice (position, never substance).
4. Scan the remaining entries for cross-references to the moved entry or to now-stale ranks (the sign-in-bounce entry has a history of these) and fix any in place.
5. Add a short dated note in the file's intro section (where every other ship's note lives) recording this pass.

- [ ] **Step 2: Gates and commit**

Run: `uv run --isolated pytest -q` and `uv run --isolated ruff check .`

```bash
git add WISHLIST.md
git commit -m "docs: wishlist ship-move for the crawler-trap hardening

Entry moves to Shipped; minute-level offsets returns to #1 by pure
removal (twelfth move); remaining entries renumbered."
```

---

## Execution notes (for the coordinating session, not the task subagents)

- Branch: create `crawler-trap-hardening` off up-to-date `origin/main` (fetch first — the session git snapshot goes stale) in an isolated worktree per `superpowers:using-git-worktrees`.
- Tasks 1 and 2 are independent of each other; Task 3 depends on Task 2 (it names the route); Task 4 depends on all three. Run 1 and 2 first (either order or parallel worktrees are overkill — sequential is fine at this size), then 3, then 4.
- After all tasks: `superpowers:requesting-code-review`, then `superpowers:finishing-a-development-branch` (PR to `main`; CI runs the same two gates).
- Deploy note for the PR body: no migration, normal deploy order. The edge-side Cloudflare rule already live in production is UNAFFECTED by this PR and stays.
