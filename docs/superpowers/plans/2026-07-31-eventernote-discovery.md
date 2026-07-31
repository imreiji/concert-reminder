# Eventernote Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell the maintainer, once a day by DM, about performances by catalogue artists that the catalogue does not have — with a paste-ready prompt that starts the add-concert workflow.

**Architecture:** A pure parser turns an Eventernote actor-events page into rows; a guarded fetch feeds it; a diff keyed on the Eventernote event id decides which rows are unaccounted for; the daily scheduler sweep queues one DM through the existing notifications outbox. No LLM is involved anywhere in the app — the deploy has no API access. Turning a lead into a concert stays with an agent following the add-concert skill.

**Tech Stack:** Python 3.14, SQLAlchemy 2.0 async + Alembic (SQLite batch mode), FastAPI + Jinja2, httpx, BeautifulSoup4, pytest-asyncio (auto mode), uv.

**Spec:** `docs/superpowers/specs/2026-07-31-eventernote-discovery-design.md` — read it before Task 1. Where this plan and the spec disagree, the spec wins; report the conflict rather than guessing.

**Branch:** `eventernote-discovery` (already exists, spec already committed). Do not create another.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` MUST both pass before every commit.
- Run tests in the FOREGROUND. A backgrounded suite run stalls the implementer.
- Use `uv run --isolated` for every command — an external process locks `.venv`.
- `src/app/domain/` may NOT import discord, fastapi, sqlalchemy, or httpx.
- The DB stores aware UTC only (`UTCDateTime` rejects naive datetimes). `DiscoveredEvent.event_date` is a plain `Date`, not a datetime — see Task 2.
- Never send a DM from a web route or from the walk. Notices go through the `notifications` outbox (invariant 4).
- Admin pages are English-only and NOT wrapped in `_()`, following `/admin/deliveries` — `tests/test_i18n_catalogues.py` would otherwise demand ja+zh msgids.
- After `alembic revision --autogenerate`, ALWAYS replace `app.db.models.UTCDateTime()` with `sa.DateTime()` and delete the `import app.db.models` line.
- `alembic.ini` and config files stay ASCII-only (the owner's Windows machine uses a GBK locale).
- Tag chips, radiuses (3px), sentence case: see the UI conventions in CLAUDE.md for any template work.
- **Assert the property, not a proxy for it.** This project has shipped three tests in two days that passed or failed for reasons unrelated to their claims.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/app/domain/eventernote.py` (new) | PURE. HTML string → `list[ActorEvent]`; the future-only stop rule; actor-id extraction from a stored URL. No I/O. |
| `src/app/domain/discovery_message.py` (new) | PURE. Leads → the DM text, including the fenced copy block and the 2000-char budget. Separate module from the parser: one is about READING a source, the other about COMPOSING a message, and `tags_yaml`/`tags_diff` set the precedent for splitting on that line. |
| `src/app/discovery.py` (new) | The SSRF-guarded fetch plus sweep orchestration. Sits above `db/` like `app/ops.py` does — it imports `domain/` and `db.service`, and nothing in `db/` imports it. |
| `src/app/db/models.py` | Add `DiscoveredEvent`; add `ConcertDay.eventernote_event_id`. |
| `src/app/db/service.py` | The diff and lead queries — everything that touches the DB. |
| `src/app/scheduler/loop.py` | Daily sweep hook, its own try/except and its own commit. |
| `src/app/config.py` | `discovery_enabled: bool = False`. |
| `src/app/web/routes/discoveries.py` (new) | `GET /admin/discoveries`, `POST /admin/discoveries/{id}/dismiss`. Its own module because a router registers whole and `admin.py` serves routes production needs. |
| `src/app/web/templates/admin_discoveries.html` (new) | The review surface. |

---

## Task 1: The pure parser and the stop rule

**Files:**
- Create: `src/app/domain/eventernote.py`
- Create: `tests/fixtures/eventernote_actor_events.html` (saved real page)
- Test: `tests/test_eventernote_parse.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `ActorEvent` (frozen dataclass: `event_id: str`, `title: str`, `date: datetime.date`, `venue: str`); `parse_actor_events(html: str) -> ParsedActorPage`; `ParsedActorPage` (dataclass: `events: list[ActorEvent]`, `skipped: int`); `future_events(events: Sequence[ActorEvent], today_jst: date) -> list[ActorEvent]`; `actor_id_from_url(url: str) -> str | None`; `actor_events_url(actor_id: str, name: str) -> str`.

- [ ] **Step 1: Save a real page as a fixture**

```bash
curl -s "https://www.eventernote.com/actors/Liyuu/34637/events" \
  -H "User-Agent: dekimasen.app/1.0 (event discovery)" \
  -o tests/fixtures/eventernote_actor_events.html
```

Confirm it is a real listing and not an error page, and note how many rows it holds:

```bash
grep -c "/events/" tests/fixtures/eventernote_actor_events.html
```

If the fetch fails or returns fewer than 10 event links, STOP and report — the plan's parser assumptions were measured against a live page and a broken fixture invalidates every test below.

- [ ] **Step 2: Write the failing tests**

```python
"""The Eventernote actor-events parser: pure, and forgiving of a redesign."""

import datetime as dt
from pathlib import Path

import pytest

from app.domain.eventernote import (
    ActorEvent,
    actor_events_url,
    actor_id_from_url,
    future_events,
    parse_actor_events,
)

FIXTURE = Path(__file__).parent / "fixtures" / "eventernote_actor_events.html"


def _page():
    return parse_actor_events(FIXTURE.read_text(encoding="utf-8"))


def test_it_finds_every_event_row():
    page = _page()
    assert len(page.events) >= 10, "the fixture should hold a full page of rows"


def test_each_event_carries_an_id_a_date_and_a_title():
    for event in _page().events:
        assert event.event_id.isdigit(), event
        assert isinstance(event.date, dt.date)
        assert event.title.strip()


def test_event_ids_are_unique_within_a_page():
    ids = [e.event_id for e in _page().events]
    assert len(ids) == len(set(ids))


def test_rows_are_newest_first():
    """The stop rule depends on this ordering. If the site ever changes it,
    future_events would silently truncate at the first past row and report
    almost nothing -- so the assumption is pinned here, not just documented."""
    dates = [e.date for e in _page().events]
    assert dates == sorted(dates, reverse=True)


def test_a_page_with_no_events_yields_nothing_and_does_not_raise():
    page = parse_actor_events("<html><body><p>no events</p></body></html>")
    assert page.events == []


def test_a_truncated_page_does_not_raise():
    """A site redesign must degrade to 'found nothing', which an operator can
    see, not to a crashed scheduler tick."""
    half = FIXTURE.read_text(encoding="utf-8")[: len(FIXTURE.read_text(encoding="utf-8")) // 2]
    parse_actor_events(half)  # must not raise


# ── the stop rule ────────────────────────────────────────────────────────

def _ev(day: int) -> ActorEvent:
    return ActorEvent(
        event_id=str(day), title=f"show {day}", date=dt.date(2026, 8, day), venue="v"
    )


def test_future_events_takes_the_prefix_and_stops_at_the_first_past_row():
    rows = [_ev(20), _ev(15), _ev(10), _ev(5)]
    assert [e.date.day for e in future_events(rows, dt.date(2026, 8, 12))] == [20, 15]


def test_an_event_today_counts_as_future():
    """A deadline can still be today. Excluding today would drop same-day
    announcements, which are the most urgent leads there are."""
    rows = [_ev(20), _ev(12), _ev(5)]
    assert [e.date.day for e in future_events(rows, dt.date(2026, 8, 12))] == [20, 12]


def test_an_all_past_page_yields_nothing():
    assert future_events([_ev(5), _ev(4)], dt.date(2026, 8, 12)) == []


def test_it_stops_rather_than_filters():
    """Take-while, not filter: a stray out-of-order future row AFTER a past row
    must not resurrect the walk, because that would mean reading the whole
    18-page history of every artist."""
    rows = [_ev(20), _ev(5), _ev(25)]
    assert [e.date.day for e in future_events(rows, dt.date(2026, 8, 12))] == [20]


# ── URL helpers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.eventernote.com/actors/Liyuu/34637", "34637"),
    ("https://www.eventernote.com/actors/%E5%A4%A7%E8%A5%BF/25872", "25872"),
    ("https://www.eventernote.com/actors/x/5847/events", "5847"),
    ("https://example.com/actors/x/1", None),
    ("", None),
    ("not a url", None),
])
def test_actor_id_from_url(url, expected):
    assert actor_id_from_url(url) == expected


def test_actor_events_url_percent_encodes_the_name():
    url = actor_events_url("34637", "大西亜玖璃")
    assert url.startswith("https://www.eventernote.com/actors/")
    assert url.endswith("/34637/events")
    assert "大西" not in url, "the name segment must be percent-encoded"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --isolated pytest tests/test_eventernote_parse.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.eventernote'`

- [ ] **Step 4: Implement the parser**

Inspect the fixture first to find the real row markup — do NOT guess selectors:

```bash
grep -o '<div class="[^"]*event[^"]*"' tests/fixtures/eventernote_actor_events.html | sort | uniq -c | head
```

Then write `src/app/domain/eventernote.py`. The shape below is fixed. Two things in it are MEASURED, not assumed, and must be checked against the fixture before you trust them: the `/places/` href prefix `_venue` keys on, and the assumption that each event link's nearest `li`/`div`/`tr` ancestor contains that row's date. If either does not hold in the fixture, adjust those two spots — not the dataclasses or the function signatures, which later tasks depend on.

```python
"""Parse an Eventernote actor's events page into rows.

Pure: takes an HTML string, returns rows. No httpx, exactly like
`domain/ingest.py` -- the fetch lives in `app/discovery.py` so this module
stays testable against a saved page with no network.

WARNINGS OVER FAILURES, following parse_draft and parse_tags: a row that
cannot be read is skipped and counted, never raised on. A site redesign must
degrade to "found nothing", which an operator can see on /admin/discoveries,
not to a scheduler tick that crashes every day.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup

HOST = "www.eventernote.com"
_EVENT_HREF = re.compile(r"/events/(\d+)")
_ACTOR_PATH = re.compile(r"^/actors/[^/]+/(\d+)(?:/|$)")
_DATE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")


@dataclass(frozen=True)
class ActorEvent:
    event_id: str
    title: str
    date: date
    venue: str


@dataclass
class ParsedActorPage:
    events: list[ActorEvent] = field(default_factory=list)
    skipped: int = 0


def parse_actor_events(html: str) -> ParsedActorPage:
    """One actor-events page -> its rows, in the order the page lists them."""
    page = ParsedActorPage()
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()

    for link in soup.find_all("a", href=_EVENT_HREF):
        match = _EVENT_HREF.search(link.get("href", ""))
        if match is None:
            continue
        event_id = match.group(1)
        if event_id in seen:
            continue

        row = link.find_parent(["li", "div", "tr"])
        text = row.get_text(" ", strip=True) if row is not None else ""
        stamp = _DATE.search(text)
        title = link.get_text(" ", strip=True)
        if stamp is None or not title:
            page.skipped += 1
            continue

        seen.add(event_id)
        page.events.append(ActorEvent(
            event_id=event_id,
            title=title,
            date=date(int(stamp.group(1)), int(stamp.group(2)), int(stamp.group(3))),
            venue=_venue(row),
        ))
    return page


def _venue(row) -> str:
    """The venue as displayed, or "" -- free text, never resolved to a tag here.

    Resolving a venue name to a VENUE tag is a NAME match, which invariant 3
    forbids as an identity test, and this module cannot reach the DB anyway.
    """
    if row is None:
        return ""
    for link in row.find_all("a", href=True):
        if "/places/" in link["href"]:
            return link.get_text(" ", strip=True)
    return ""


def future_events(events: Sequence[ActorEvent], today_jst: date) -> list[ActorEvent]:
    """The future prefix of a newest-first page.

    TAKE-WHILE, not filter, and that is the whole economy of this feature: rows
    are strictly newest-first (pinned by a test), so stopping at the first past
    row means one fetch covers nearly every artist -- ~86 per sweep instead of
    ~1,548 if all 18 pages of every artist were read. An event dated TODAY
    counts as future: a same-day announcement is the most urgent lead there is.
    """
    out: list[ActorEvent] = []
    for event in events:
        if event.date < today_jst:
            break
        out.append(event)
    return out


def actor_id_from_url(url: str) -> str | None:
    """The numeric id out of a stored eventernote_url, or None if it is not one."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.hostname != HOST:
        return None
    match = _ACTOR_PATH.match(parsed.path or "")
    return match.group(1) if match else None


def actor_events_url(actor_id: str, name: str) -> str:
    """Build the events URL. The name segment is DECORATIVE -- /actors/x/5847
    resolves the same as the site's own path (verified against the live site) --
    so it is built from OUR name, percent-encoded, and only the id matters."""
    return f"https://{HOST}/actors/{quote(name, safe='')}/{actor_id}/events"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --isolated pytest tests/test_eventernote_parse.py -q`
Expected: PASS. If `test_it_finds_every_event_row` or `test_rows_are_newest_first` fails, the selectors in `_rows`/`_venue` do not match the real markup — re-inspect the fixture rather than loosening the assertion.

- [ ] **Step 6: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/domain/eventernote.py tests/test_eventernote_parse.py tests/fixtures/eventernote_actor_events.html
git commit -m "feat: parse an Eventernote actor-events page"
```

---

## Task 2: Schema — `discovered_events` and the leg's event id

**Files:**
- Modify: `src/app/db/models.py`
- Create: `alembic/versions/<generated>_discovered_events.py`
- Test: `tests/test_discovery_schema.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `DiscoveredEvent` ORM model with columns `id`, `eventernote_event_id` (unique), `title`, `event_date` (`Date`), `venue`, `first_seen_via_tag_id`, `first_seen_at`, `last_seen_at`, `announced_at`, `dismissed_at`, `concert_id`; and `ConcertDay.eventernote_event_id: Mapped[str | None]`.

- [ ] **Step 1: Write the failing test**

```python
"""The discovery tables: an event is identified by its Eventernote id."""

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, ConcertDay, DiscoveredEvent


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_a_lead_round_trips(db):
    async with db() as s:
        s.add(DiscoveredEvent(
            eventernote_event_id="464372",
            title="ラブライブ！フェス Day.2",
            event_date=dt.date(2026, 11, 15),
            venue="バンテリンドーム ナゴヤ",
        ))
        await s.commit()
    async with db() as s:
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.event_date == dt.date(2026, 11, 15)
        assert row.announced_at is None and row.dismissed_at is None
        assert row.concert_id is None


async def test_the_event_id_is_unique(db):
    """One event, one row -- the anniversary concert lists nine catalogue tags
    as performers, and without this the maintainer hears about it nine times."""
    async with db() as s:
        s.add(DiscoveredEvent(
            eventernote_event_id="1", title="a", event_date=dt.date(2026, 1, 1), venue=""
        ))
        await s.commit()
    async with db() as s:
        s.add(DiscoveredEvent(
            eventernote_event_id="1", title="b", event_date=dt.date(2026, 1, 2), venue=""
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_event_date_is_a_plain_date_not_a_datetime(db):
    """The list gives a calendar day and no time. Inventing midnight would put a
    fake deadline-shaped value into a schema where every datetime is an aware
    UTC instant (invariant 1)."""
    async with db() as s:
        s.add(DiscoveredEvent(
            eventernote_event_id="2", title="a", event_date=dt.date(2026, 3, 4), venue=""
        ))
        await s.commit()
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert type(row.event_date) is dt.date


async def test_a_leg_can_carry_its_eventernote_event_id(db):
    assert ConcertDay.eventernote_event_id is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_discovery_schema.py -q`
Expected: FAIL — `ImportError: cannot import name 'DiscoveredEvent'`

- [ ] **Step 3: Add the model and the column**

In `src/app/db/models.py`, add `Date` to the existing `from sqlalchemy import (...)` block (it is NOT currently imported), then add near the other outbox-ish tables:

```python
class DiscoveredEvent(Base):
    """A performance Eventernote lists that the catalogue may not have.

    A LEAD, not a concert: Eventernote carries no ticket information at all, so
    this can say "this exists and you are not tracking it" and nothing more.
    Rounds come from the official ticket page, via an agent following the
    add-concert skill. Nothing here ever writes to `concerts`.

    Keyed on the Eventernote event id, one row per EVENT rather than per
    artist: the LoveLive 15th anniversary concert lists nine catalogue tags as
    performers, and a per-artist key would announce it nine times.
    """

    __tablename__ = "discovered_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    eventernote_event_id: Mapped[str] = mapped_column(String(20), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    # A plain Date, NOT a UTCDateTime: the source gives a calendar day with no
    # time, and inventing midnight would put a fake deadline-shaped value into a
    # schema where every datetime is an aware UTC instant (invariant 1). It is a
    # JST calendar date, like the performance dates rendered by fmt_day_month.
    event_date: Mapped[date] = mapped_column(Date)
    venue: Mapped[str] = mapped_column(String(200), default="")
    # Which artist surfaced it. SET NULL, never CASCADE: deleting a tag must not
    # silently drop leads the maintainer has not triaged yet.
    first_seen_via_tag_id: Mapped[int | None] = mapped_column(
        ForeignKey("tags.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # A lead is OPEN when all three of these are NULL.
    announced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    concert_id: Mapped[int | None] = mapped_column(
        ForeignKey("concerts.id", ondelete="SET NULL")
    )
```

Add `date` to the `from datetime import ...` line at the top of the file.

On `ConcertDay`, after `cancelled`:

```python
    # The Eventernote event this leg came from, when it was imported from one.
    # This is what makes discovery's "do I already have this?" an exact id
    # lookup instead of fuzzy title matching -- Japanese titles vary in spacing,
    # brackets and ~ marks; ids do not. Nullable and never backfilled: legs that
    # predate discovery simply fall through to the date-and-venue hint.
    eventernote_event_id: Mapped[str | None] = mapped_column(String(20), index=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_discovery_schema.py -q`
Expected: PASS

- [ ] **Step 5: Generate and fix the migration**

```bash
uv run --isolated alembic revision --autogenerate -m "discovered events"
```

Then EDIT the generated file:
- Replace every `app.db.models.UTCDateTime()` with `sa.DateTime()`.
- Delete the `import app.db.models` line.
- Confirm it uses `batch_alter_table` for the `concert_days` column add.
- It must NOT call `drop_constraint` on anything. If autogenerate emitted one, delete it and report — the live DB predates the naming convention and a `drop_constraint` that passes locally dies on the server.

- [ ] **Step 6: Apply and verify the migration round-trips**

```bash
uv run --isolated alembic upgrade head
uv run --isolated alembic downgrade -1
uv run --isolated alembic upgrade head
uv run --isolated pytest -q
```
Expected: all pass. A downgrade that fails is a broken migration, not an optional extra.

- [ ] **Step 7: Commit**

```bash
git add src/app/db/models.py alembic/versions tests/test_discovery_schema.py
git commit -m "feat: discovered_events table and a leg's eventernote event id"
```

---

## Task 3: Extract the shared guarded fetch, then build discovery's on it

**TWO COMMITS, in this order.** The extraction lands first and is proven by the
EXISTING ramen.events import tests before anything new depends on it. If those
tests do not stay green, stop — you have changed a working production path.

Rationale: `routes/imports.py` already contains exactly the three-way guard
discovery needs. Copying it would leave two copies of a security control, and a
weakness found later would be fixed in one and missed in the other. The two
callers genuinely differ only in the exception they raise.

**Files:**
- Create: `src/app/fetching.py`
- Modify: `src/app/web/routes/imports.py`
- Create: `src/app/discovery.py`
- Test: `tests/test_discovery_fetch.py`

**Interfaces:**
- Consumes: `app.domain.eventernote.HOST` (Task 1).
- Produces, in `src/app/fetching.py`:
  - `class FetchError(Exception)` — base
  - `class HostNotAllowed(FetchError)` — scheme or host rejected
  - `class FetchFailed(FetchError)` — non-200, oversized, timeout, transport error
  - `def check_host(url: str, allowed_host: str) -> None` — raises `HostNotAllowed`
  - `async def fetch_html(url: str, *, allowed_host: str, user_agent: str, timeout: float = 10.0, max_bytes: int = 2_000_000, max_redirects: int = 5, transport: httpx.AsyncBaseTransport | None = None) -> str`
- Produces, in `src/app/discovery.py`: `fetch_actor_events(url, transport=None) -> str`; `DiscoveryFetchError(Exception)`; `SWEEP_DELAY_SECONDS = 1.0`.

`src/app/fetching.py` is top-level, beside `i18n.py` and `ops.py`: it does I/O so
it cannot live in `domain/`, and both a web route and the scheduler import it.

### Commit 1 — the extraction

- [ ] **Step 1: Move the guard into `src/app/fetching.py`**

Lift `_check_host`, `_check_redirect_host` and the streaming body of
`fetch_ramen_html` from `web/routes/imports.py` into the new module, replacing
the hard-coded `ALLOWED_HOST` with the `allowed_host` parameter and raising
`HostNotAllowed` / `FetchFailed` instead of `HTTPException`. Keep every comment
explaining WHY the redirect hook exists — it is the reason the guard works.

- [ ] **Step 2: Make `imports.py` call it, preserving its exact status codes**

`imports.py` currently answers **400** for a bad host and **502** for a fetch
failure. That mapping is observable behaviour with tests on it and MUST NOT
change:

```python
def _check_host(url: str) -> None:
    try:
        check_host(url, ALLOWED_HOST)
    except HostNotAllowed as exc:
        raise HTTPException(
            status_code=400, detail=f"only https://{ALLOWED_HOST}/... URLs are supported"
        ) from exc


async def fetch_ramen_html(url: str, transport=None) -> str:
    try:
        return await fetch_html(
            url,
            allowed_host=ALLOWED_HOST,
            user_agent="dekimasen.app/1.0 (event import)",
            transport=transport,
        )
    except HostNotAllowed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FetchFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
```

- [ ] **Step 3: Prove the extraction changed nothing**

Run the existing import tests in the FOREGROUND:

```bash
uv run --isolated pytest tests/test_imports.py tests/test_import_preview.py -q
uv run --isolated pytest -q
```

Expected: PASS, with no test edited. If a test needed changing to pass, the
extraction altered behaviour — revert and report rather than adjusting the test.

- [ ] **Step 4: Commit the extraction on its own**

```bash
uv run --isolated ruff check .
git add src/app/fetching.py src/app/web/routes/imports.py
git commit -m "refactor: extract the host-pinned fetch shared by importers"
```

### Commit 2 — discovery's fetch

- [ ] **Step 5: Write the failing test**

```python
"""The discovery fetch: pinned to one host, on every hop."""

import httpx
import pytest

from app.discovery import DiscoveryFetchError, fetch_actor_events

OK = "https://www.eventernote.com/actors/x/1/events"


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_it_fetches_an_allowed_url():
    async def handler(request):
        return httpx.Response(200, text="<html>hi</html>")

    assert "hi" in await fetch_actor_events(OK, transport=_transport(handler))


@pytest.mark.parametrize("url", [
    "http://www.eventernote.com/actors/x/1/events",   # not https
    "https://evil.example.com/actors/x/1/events",     # wrong host
    "https://eventernote.com.evil.example/actors/x/1",  # suffix trick
])
async def test_a_disallowed_url_is_refused_before_any_request(url):
    async def handler(request):
        raise AssertionError("no request should have been made")

    with pytest.raises(DiscoveryFetchError):
        await fetch_actor_events(url, transport=_transport(handler))


async def test_a_redirect_off_host_is_refused():
    """NOT hypothetical: the site advertises its next-page link on an
    eventernote.s3.amazonaws.com host, so a fetcher that follows where the page
    points leaves the host it was pinned to."""
    async def handler(request):
        if request.url.host == "www.eventernote.com":
            return httpx.Response(
                302,
                headers={"location": "https://eventernote.s3.amazonaws.com/x"},
            )
        raise AssertionError("followed the redirect off-host")

    with pytest.raises(DiscoveryFetchError):
        await fetch_actor_events(OK, transport=_transport(handler))


async def test_an_oversized_body_is_aborted():
    async def handler(request):
        return httpx.Response(200, content=b"x" * 3_000_000)

    with pytest.raises(DiscoveryFetchError):
        await fetch_actor_events(OK, transport=_transport(handler))


async def test_a_non_200_raises():
    async def handler(request):
        return httpx.Response(503)

    with pytest.raises(DiscoveryFetchError):
        await fetch_actor_events(OK, transport=_transport(handler))
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_discovery_fetch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.discovery'`

- [ ] **Step 7: Implement the fetch**

Thin: the guard now lives in `app/fetching.py` (Commit 1). This module only pins
the host and translates the error type — the scheduler has no request to fail,
so `HTTPException` would be wrong here.

```python
"""Eventernote discovery: the fetch, and the daily sweep.

Sits ABOVE db/ like app/ops.py: it imports domain/ and db.service, and nothing
in db/ imports it. The parser is pure and lives in domain/eventernote.py; the
host-pinned fetch is shared with the ramen.events importer and lives in
app/fetching.py -- ONE copy of that guard, deliberately, so a weakness found in
it cannot be fixed in one caller and missed in the other.
"""

import logging

import httpx

from app.domain.eventernote import HOST
from app.fetching import FetchError, fetch_html

log = logging.getLogger(__name__)

ALLOWED_HOST = HOST
USER_AGENT = "dekimasen.app/1.0 (event discovery)"
# Sequential with a pause: 86 parallel requests at a third party is rude and is
# how an IP gets blocked.
SWEEP_DELAY_SECONDS = 1.0


class DiscoveryFetchError(Exception):
    """A page could not be fetched. One artist failing must not abort a sweep."""


async def fetch_actor_events(
    url: str, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Fetch one actor-events page. `transport` is test-only.

    Catches FetchError -- the BASE class, so both HostNotAllowed and FetchFailed
    become the one error a sweep knows how to skip past.
    """
    try:
        return await fetch_html(
            url, allowed_host=ALLOWED_HOST, user_agent=USER_AGENT, transport=transport
        )
    except FetchError as exc:
        raise DiscoveryFetchError(str(exc)) from exc
```

- [ ] **Step 8: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_discovery_fetch.py -q`
Expected: PASS

- [ ] **Step 9: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/discovery.py tests/test_discovery_fetch.py
git commit -m "feat: SSRF-guarded fetch for Eventernote actor pages"
```

---

## Task 4: The diff

**Files:**
- Modify: `src/app/db/service.py`
- Test: `tests/test_discovery_diff.py`

**Interfaces:**
- Consumes: `ActorEvent` (Task 1); `DiscoveredEvent`, `ConcertDay.eventernote_event_id` (Task 2).
- Produces:
  - `async def record_discovered(session, events: Sequence[tuple[ActorEvent, int]], now: datetime) -> list[DiscoveredEvent]` — upserts by event id; the int is the surfacing tag's id. Returns rows that are OPEN and not yet announced.
  - `async def open_leads(session) -> list[DiscoveredEvent]`
  - `async def dismiss_lead(session, lead_id: int, now: datetime) -> bool`
  - `async def mark_leads_announced(session, lead_ids: Sequence[int], now: datetime) -> None`
  - `async def leads_matching_existing_legs(session, leads: Sequence[DiscoveredEvent]) -> set[int]` — lead ids whose date AND venue collide with an existing leg (the HINT set).

- [ ] **Step 1: Write the failing test**

```python
"""The diff: what counts as already present, and what is merely a hint."""

import datetime as dt
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, DiscoveredEvent, Tag
from app.db.service import (
    dismiss_lead,
    leads_matching_existing_legs,
    mark_leads_announced,
    open_leads,
    record_discovered,
)
from app.domain.eventernote import ActorEvent
from app.domain.types import TagKind

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _ev(event_id="1", day=15, title="show", venue="Zepp Haneda"):
    return ActorEvent(
        event_id=event_id, title=title, date=dt.date(2026, 11, day), venue=venue
    )


async def test_a_new_event_becomes_an_open_lead(db):
    async with db() as s:
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
        assert [r.eventernote_event_id for r in fresh] == ["1"]
        assert len(await open_leads(s)) == 1


async def test_one_event_seen_via_several_tags_is_one_lead(db):
    """The anniversary concert lists nine catalogue tags. Without an id key the
    maintainer hears about it nine times."""
    async with db() as s:
        s.add_all([
            Tag(name=f"a{i}", kind=TagKind.ARTIST, slug=f"a{i}") for i in range(3)
        ])
        await s.flush()
        await record_discovered(s, [(_ev(), 1), (_ev(), 2), (_ev(), 3)], NOW)
        await s.commit()
        assert len(await open_leads(s)) == 1


async def test_a_second_sweep_returns_nothing_new(db):
    async with db() as s:
        await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
    async with db() as s:
        again = await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
        assert again == [], "an already-recorded event is not fresh"


async def test_an_announced_lead_is_not_re_announced(db):
    async with db() as s:
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await mark_leads_announced(s, [r.id for r in fresh], NOW)
        await s.commit()
        assert await open_leads(s) == []


async def test_a_dismissed_lead_stays_gone(db):
    async with db() as s:
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
        assert await dismiss_lead(s, fresh[0].id, NOW) is True
        await s.commit()
        assert await open_leads(s) == []


async def test_an_event_already_held_by_a_leg_is_never_a_lead(db):
    """The exact branch: a leg carrying this event id means we have it."""
    async with db() as s:
        s.add(Concert(title="t", event_id="c1"))
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="Day 1",
            starts_at_utc=datetime(2026, 11, 15, 9, 0, tzinfo=UTC),
            eventernote_event_id="1",
        ))
        await s.commit()
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
        assert fresh == []
        assert await open_leads(s) == []


async def test_same_date_same_venue_is_a_HINT_not_a_suppression(db):
    """The matinee and evening shows of one day are two Eventernote events at
    one venue on one date, and two legs. Auto-suppressing on date-and-venue
    would hide exactly the second one."""
    async with db() as s:
        venue = Tag(name="Zepp Haneda", kind=TagKind.VENUE, slug="zepp-haneda")
        s.add_all([Concert(title="t", event_id="c1"), venue])
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="昼公演", venue_tag_id=venue.id,
            starts_at_utc=datetime(2026, 11, 15, 4, 0, tzinfo=UTC),
        ))
        await s.commit()

        fresh = await record_discovered(s, [(_ev(event_id="99", title="夜公演"), None)], NOW)
        await s.commit()
        assert len(fresh) == 1, "the evening show is still reported"
        assert {r.id for r in fresh} == await leads_matching_existing_legs(s, fresh)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_discovery_diff.py -q`
Expected: FAIL — `ImportError: cannot import name 'record_discovered'`

- [ ] **Step 3: Implement in `src/app/db/service.py`**

Add a section banner `# ── Eventernote discovery ──` and implement the five functions. `record_discovered` must:

1. Collect the incoming event ids, and in ONE query find which are already held by a leg (`select(ConcertDay.eventernote_event_id).where(...in_(ids))`) — those are dropped entirely and never stored.
2. In ONE query load existing `DiscoveredEvent` rows for the remaining ids.
3. For an existing row: update `last_seen_at` only, and do not return it.
4. For a new one: insert with `first_seen_at = last_seen_at = now`, and return it.
5. `await session.flush()` before returning, so callers have ids.

`leads_matching_existing_legs` compares each lead's `event_date` against `ConcertDay.starts_at_utc` converted to a JST date, AND the venue string against the leg's venue tag name (case-insensitive, both `name` and `name_en`). Import `utc_to_jst` from `app.domain.timezones` rather than doing arithmetic inline.

`open_leads` returns rows where all three of `announced_at`, `dismissed_at`, `concert_id` are NULL, newest `event_date` first.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_discovery_diff.py -q`
Expected: PASS

- [ ] **Step 5: Run the whole suite, lint, commit**

```bash
uv run --isolated pytest -q
uv run --isolated ruff check .
git add src/app/db/service.py tests/test_discovery_diff.py
git commit -m "feat: the discovery diff, keyed on the eventernote event id"
```

---

## Task 5: The DM text and its copy block

**Files:**
- Create: `src/app/domain/discovery_message.py`
- Test: `tests/test_discovery_message.py`

**Interfaces:**
- Consumes: nothing (pure; takes plain data, NOT ORM rows — see below).
- Produces: `Lead` (frozen dataclass: `event_id: str`, `title: str`, `date: date`, `venue: str`, `artist: str`, `maybe_held: bool`); `build_discovery_dm(leads: Sequence[Lead], total: int) -> str`; constants `DM_CHAR_BUDGET = 1900`, `DM_LIST_LIMIT = 10`.

`Lead` is a plain dataclass rather than a `DiscoveredEvent` so this module stays pure — `domain/` may not import sqlalchemy. Task 6 adapts rows into `Lead`s.

- [ ] **Step 1: Write the failing test**

```python
"""The discovery DM: readable above, copyable below, inside Discord's limit."""

import datetime as dt

from app.domain.discovery_message import (
    DM_CHAR_BUDGET,
    Lead,
    build_discovery_dm,
)


def _lead(n=1, artist="Liyuu", maybe_held=False):
    return Lead(
        event_id=str(400000 + n),
        title=f"Show {n}",
        date=dt.date(2026, 11, n),
        venue="Zepp Haneda",
        artist=artist,
        maybe_held=maybe_held,
    )


def test_it_names_the_artist_and_the_event():
    body = build_discovery_dm([_lead()], total=1)
    assert "Liyuu" in body and "Show 1" in body


def test_it_carries_a_closed_fenced_block():
    """An unclosed fence swallows the rest of the message into a code block --
    invisible to a length check, obvious to a reader."""
    body = build_discovery_dm([_lead()], total=1)
    assert body.count("```") == 2


def test_the_block_names_the_skill_and_the_grouping_rule():
    """Pasting it must be the whole action. Grouping legs into one concert is
    judgment and stays with the agent -- so the prompt has to say so."""
    body = build_discovery_dm([_lead()], total=1)
    assert "add-concert" in body
    assert "ONE draft" in body


def test_every_listed_lead_appears_in_the_copy_block():
    leads = [_lead(n) for n in range(1, 4)]
    block = build_discovery_dm(leads, total=3).split("```")[1]
    for lead in leads:
        assert f"/events/{lead.event_id}" in block


def test_a_maybe_held_lead_is_marked():
    body = build_discovery_dm([_lead(maybe_held=True)], total=1)
    assert "already have" in body.lower()


def test_a_remainder_is_counted_and_linked():
    body = build_discovery_dm([_lead(n) for n in range(1, 4)], total=40)
    assert "37 more" in body
    assert "/admin/discoveries" in body


def test_it_stays_inside_the_budget_with_many_leads():
    body = build_discovery_dm([_lead(n) for n in range(1, 11)], total=200)
    assert len(body) <= DM_CHAR_BUDGET


def test_dropped_block_lines_are_announced_in_the_block():
    """A DM that lists a lead above but silently omits it from the copy block is
    the quiet kind of wrong. If lines are dropped, the block must say so."""
    long_title = "x" * 300
    leads = [
        Lead(
            event_id=str(i), title=long_title, date=dt.date(2026, 11, 1),
            venue="v", artist="a", maybe_held=False,
        )
        for i in range(10)
    ]
    body = build_discovery_dm(leads, total=10)
    assert len(body) <= DM_CHAR_BUDGET
    block = body.split("```")[1]
    assert "truncated" in block.lower() or "more not shown" in block.lower()


def test_no_leads_produces_no_message():
    """Silence is the correct output for a quiet day: a daily 'nothing found'
    trains the reader to ignore the channel."""
    assert build_discovery_dm([], total=0) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_discovery_message.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Compose in this order and enforce the budget by dropping BLOCK lines last-first, appending a truncation line inside the block when any were dropped. Budget is 1900, not 2000, to leave Discord room — assert the real limit is not approached rather than exactly met.

```python
"""Compose the discovery DM.

Pure, and its own module rather than joining eventernote.py: that one is about
READING a source, this is about COMPOSING a message. tags_yaml/tags_diff set
the precedent for splitting on exactly that line.

Two halves, because Discord forces it: text inside a fenced block is NOT
linkified, so the readable list stays clickable and the block stays copyable.
The same content twice is deliberate.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

DM_CHAR_BUDGET = 1900
DM_LIST_LIMIT = 10
EVENT_URL = "https://www.eventernote.com/events/{event_id}"
REVIEW_URL = "https://dekimasen.app/admin/discoveries"

PROMPT_HEADER = (
    "Add these to dekimasen.app using the add-concert skill.\n"
    "Group legs of the same tour into ONE draft."
)


@dataclass(frozen=True)
class Lead:
    event_id: str
    title: str
    date: date
    venue: str
    artist: str
    maybe_held: bool
```

Then the builder itself:

```python
def build_discovery_dm(leads: Sequence[Lead], total: int) -> str:
    """The message, or "" when there is nothing to say.

    Silence is the correct output for a quiet day: a daily "nothing found"
    trains the reader to ignore the channel.
    """
    if not leads:
        return ""

    head = [f"**{total} new lead{'s' if total != 1 else ''} from your artists**", ""]
    by_artist: dict[str, list[Lead]] = {}
    for lead in leads:
        by_artist.setdefault(lead.artist, []).append(lead)

    for artist, group in by_artist.items():
        head.append(f"**{artist}**")
        for lead in group:
            url = EVENT_URL.format(event_id=lead.event_id)
            hint = " *(you may already have this)*" if lead.maybe_held else ""
            head.append(
                f"· [{lead.title}]({url}) — {lead.date:%d %b}, {lead.venue}{hint}"
            )
        head.append("")

    if total > len(leads):
        head.append(f"+{total - len(leads)} more — {REVIEW_URL}")
        head.append("")

    prose = "\n".join(head)
    block_lines = [
        f"{EVENT_URL.format(event_id=l.event_id)}  {l.date:%Y-%m-%d}  {l.venue}"
        for l in leads
    ]

    # The block yields FIRST. Announcing a lead in the prose above while
    # silently dropping it from the copy block is the quiet kind of wrong, so
    # when lines go, the block says so.
    kept = list(block_lines)
    while kept:
        dropped = len(block_lines) - len(kept)
        body = _assemble(prose, kept, dropped)
        if len(body) <= DM_CHAR_BUDGET:
            return body
        kept.pop()
    return _assemble(prose, [], len(block_lines))


def _assemble(prose: str, kept: Sequence[str], dropped: int) -> str:
    lines = [PROMPT_HEADER, ""]
    lines.extend(kept)
    if dropped:
        lines.append(f"# {dropped} more not shown -- see {REVIEW_URL}")
    return f"{prose}```\n" + "\n".join(lines) + "\n```"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --isolated pytest tests/test_discovery_message.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/domain/discovery_message.py tests/test_discovery_message.py
git commit -m "feat: compose the discovery DM with its paste-ready block"
```

---

## Task 6: The sweep, the cadence, and the queued DM

**Files:**
- Modify: `src/app/discovery.py`
- Modify: `src/app/config.py`
- Modify: `src/app/scheduler/loop.py`
- Modify: `src/app/db/models.py` (one row of sweep state)
- Create: `alembic/versions/<generated>_discovery_state.py`
- Test: `tests/test_discovery_sweep.py`

**Interfaces:**
- Consumes: `fetch_actor_events`, `DiscoveryFetchError` (Task 3); `parse_actor_events`, `future_events`, `actor_id_from_url`, `actor_events_url` (Task 1); `record_discovered`, `leads_matching_existing_legs`, `mark_leads_announced` (Task 4); `build_discovery_dm`, `Lead` (Task 5).
- Produces: `async def run_sweep(session, now, *, fetcher=fetch_actor_events) -> SweepReport`; `SweepReport` (dataclass: `fetched: int`, `failed: int`, `new_leads: int`, `announced: int`); `settings.discovery_enabled`; `DiscoveryState` model with `last_run_at`.

`fetcher` is injected so tests never touch the network.

- [ ] **Step 1: Write the failing test**

```python
"""The sweep: one DM, no network in tests, and silence on a quiet day."""

import datetime as dt
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Notification, Tag, User
from app.discovery import DiscoveryFetchError, run_sweep
from app.domain.types import TagKind

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
PAGE = """
<html><body>
<li><a href="/events/464372">Anniversary Day 2</a>
    <span>2026-11-15</span><a href="/places/1">Zepp Haneda</a></li>
</body></html>
"""


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(s, url="https://www.eventernote.com/actors/Liyuu/34637"):
    s.add(User(discord_id=42, username="reiji"))
    s.add(Tag(name="Liyuu", kind=TagKind.ARTIST, slug="liyuu", eventernote_url=url))
    await s.commit()


async def test_a_sweep_records_leads_and_queues_one_dm(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return PAGE

        report = await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.new_leads == 1
        notes = (await s.execute(select(Notification))).scalars().all()
        assert len(notes) == 1
        assert notes[0].kind == "discovery"
        assert notes[0].concert_id is None
        assert "add-concert" in notes[0].body


async def test_a_quiet_sweep_sends_nothing(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return "<html></html>"

        await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert (await s.execute(select(Notification))).scalars().all() == []


async def test_a_second_sweep_does_not_re_announce(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")

    async def fake_fetch(url, transport=None):
        return PAGE

    async with db() as s:
        await _seed(s)
        await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
    async with db() as s:
        await run_sweep(s, NOW + dt.timedelta(days=1), fetcher=fake_fetch)
        await s.commit()
        assert len((await s.execute(select(Notification))).scalars().all()) == 1


async def test_one_artist_failing_does_not_abort_the_sweep(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)
        s.add(Tag(
            name="Other", kind=TagKind.ARTIST, slug="other",
            eventernote_url="https://www.eventernote.com/actors/o/2",
        ))
        await s.commit()

        async def flaky(url, transport=None):
            if "/2" in url:
                raise DiscoveryFetchError("boom")
            return PAGE

        report = await run_sweep(s, NOW, fetcher=flaky)
        await s.commit()
        assert report.failed == 1 and report.fetched == 1
        assert report.new_leads == 1


async def test_a_tag_without_a_url_is_skipped(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        s.add(User(discord_id=42, username="reiji"))
        s.add(Tag(name="Test", kind=TagKind.ARTIST, slug="test", eventernote_url=None))
        await s.commit()

        async def boom(url, transport=None):
            raise AssertionError("should not fetch")

        report = await run_sweep(s, NOW, fetcher=boom)
        assert report.fetched == 0


async def test_an_admin_who_never_signed_in_gets_a_user_row(db, monkeypatch):
    """Notification.user_id is an FK to users.discord_id -- queuing without a
    row raises IntegrityError at flush, far from the cause. Follows
    evaluate_and_alert's precedent."""
    monkeypatch.setattr(settings, "admin_whitelist", "99")
    async with db() as s:
        s.add(Tag(
            name="Liyuu", kind=TagKind.ARTIST, slug="liyuu",
            eventernote_url="https://www.eventernote.com/actors/Liyuu/34637",
        ))
        await s.commit()

        async def fake_fetch(url, transport=None):
            return PAGE

        await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert await s.get(User, 99) is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_discovery_sweep.py -q`
Expected: FAIL — `ImportError: cannot import name 'run_sweep'`

- [ ] **Step 3: Add the config flag and the state row**

In `src/app/config.py`, beside `rehearsal_enabled`:

```python
    # Same shape as bot_enabled and rehearsal_enabled: one config value
    # switching a subsystem off. Default False so the feature ships switched
    # off, and so tests and dev runs never reach the network.
    discovery_enabled: bool = False
```

In `src/app/db/models.py`:

```python
class DiscoveryState(Base):
    """When the last Eventernote sweep ran. One row, id=1.

    A table rather than memory for the same reason OpsCheckState is one: a
    restart must not re-run a sweep that already went out, because the sweep
    ends in a DM.
    """

    __tablename__ = "discovery_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

Generate the migration, apply the same edits as Task 2 Step 5 (`sa.DateTime()`, drop the `import app.db.models` line), then `alembic upgrade head`.

- [ ] **Step 4: Implement `run_sweep` in `src/app/discovery.py`**

The order matters and is the whole correctness of the task:

1. Select tags whose `eventernote_url` is not NULL, ordered by id.
2. For each: `actor_id_from_url`, skip if None; build `actor_events_url`; `await fetcher(url)`; on `DiscoveryFetchError`, log, `report.failed += 1`, and CONTINUE. `asyncio.sleep(SWEEP_DELAY_SECONDS)` between fetches, but not after the last.
3. `parse_actor_events` then `future_events(events, utc_to_jst(now).date())`.
4. Accumulate `(ActorEvent, tag.id)` pairs across all artists, then call `record_discovered` ONCE with the lot — one query pass, and the id key deduplicates an event surfaced by several artists.
5. `leads_matching_existing_legs` over the fresh rows for the hint set.
6. Adapt fresh rows into `Lead`s (artist name from the surfacing tag), take `DM_LIST_LIMIT`, and `build_discovery_dm(listed, total=len(fresh))`.
7. If the body is empty, return without queuing.
8. For each admin id: `ensure_user` ONLY if `session.get(User, admin_id)` is None, then add a `Notification(user_id=..., body=body, kind="discovery")`.
9. `mark_leads_announced` on **every** fresh lead id, not just the listed ones — see the spec's first-sweep reasoning.
10. Update `DiscoveryState.last_run_at`, `flush`, return the report.

Do NOT add `"discovery"` to `UNREPORTED_NOTE_KINDS`: that set is only for notices that report ON deliveries.

- [ ] **Step 5: Hook the daily cadence into the scheduler**

In `src/app/scheduler/loop.py`'s `tick`, after the delivery-log prune block, add a block with its OWN try/except and its OWN commit, following the pattern already established there:

```python
        # Discovery sweeps once a DAY, not once a tick: 86 third-party fetches
        # on a 60s loop would be both useless and rude. Its own try/except and
        # its own commit, for the same reason the prune has them -- the least
        # important operation in the tick must never be able to roll back the
        # most important one.
        if settings.discovery_enabled:
            try:
                if await discovery_due(session, now):
                    report = await run_sweep(session, now)
                    await session.commit()
                    log.info(
                        "discovery sweep: %d fetched, %d failed, %d new",
                        report.fetched, report.failed, report.new_leads,
                    )
            except Exception:
                log.exception("discovery sweep failed; delivery was unaffected")
                await session.rollback()
```

Implement `discovery_due(session, now)` in `db/service.py`: True when there is no `DiscoveryState` row or `last_run_at` is more than 24 hours before `now`.

- [ ] **Step 6: Run to verify it passes**

```bash
uv run --isolated pytest tests/test_discovery_sweep.py -q
uv run --isolated pytest -q
```
Expected: PASS. The full suite matters here — a new column on `ConcertDay` and a new branch in `tick` both touch shared paths.

- [ ] **Step 7: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/discovery.py src/app/config.py src/app/scheduler/loop.py src/app/db/models.py src/app/db/service.py alembic/versions tests/test_discovery_sweep.py
git commit -m "feat: daily Eventernote sweep queues one discovery DM"
```

---

## Task 7: `/admin/discoveries`

**Files:**
- Create: `src/app/web/routes/discoveries.py`
- Create: `src/app/web/templates/admin_discoveries.html`
- Modify: `src/app/web/app.py` (register the router)
- Modify: `src/app/web/templates/preferences.html` (admin index link)
- Test: `tests/test_admin_discoveries.py`

**Interfaces:**
- Consumes: `open_leads`, `dismiss_lead`, `leads_matching_existing_legs` (Task 4); `build_discovery_dm`, `Lead` (Task 5).
- Produces: `GET /admin/discoveries`, `POST /admin/discoveries/{lead_id}/dismiss`.

- [ ] **Step 1: Write the failing test**

```python
"""The discovery review surface: admin-only, and it writes only dismissals."""

import datetime as dt
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, DiscoveredEvent
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID, EDITOR_ID = 42, 77
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False)
    c.db = db
    c.monkeypatch = monkeypatch
    return c


def login_as(client, discord_id, name):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def _seed(client):
    async with client.db() as s:
        s.add(DiscoveredEvent(
            eventernote_event_id="464372", title="Anniversary Day 2",
            event_date=dt.date(2026, 11, 15), venue="Zepp Haneda",
            first_seen_at=NOW, last_seen_at=NOW,
        ))
        await s.commit()


async def test_an_editor_cannot_reach_it(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/discoveries").status_code == 403


async def test_the_admin_sees_open_leads(client):
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "Anniversary Day 2" in body
    assert "/events/464372" in body


async def test_the_page_offers_the_copy_block(client):
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "add-concert" in body


async def test_dismissing_removes_it_from_the_list(client):
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    async with client.db() as s:
        lead = (await s.execute(select(DiscoveredEvent))).scalar_one()
    client.post(f"/admin/discoveries/{lead.id}/dismiss")
    assert "Anniversary Day 2" not in client.get("/admin/discoveries").text
    async with client.db() as s:
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.dismissed_at is not None, "dismissed, never deleted"


async def test_preferences_links_it_for_an_admin(client):
    login_as(client, ADMIN_ID, "reiji")
    assert "/admin/discoveries" in client.get("/preferences").text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_admin_discoveries.py -q`
Expected: FAIL — 404 on `/admin/discoveries`

- [ ] **Step 3: Implement the route module**

`src/app/web/routes/discoveries.py`, both routes behind `require_admin`. Its own module because a router registers whole and `admin.py` serves routes production needs. English-only, no `_()`. The GET renders open leads plus the same block `build_discovery_dm` produces (reuse it — a second formatter would drift), and the copy button is a `data-` attribute read via `dataset`, never interpolated into an `on*` handler (invariant 7).

Register it in `src/app/web/app.py` beside the other admin routers.

- [ ] **Step 4: Add the template and the Preferences link**

`admin_discoveries.html` extends the admin page shape used by `admin_deliveries.html`. Follow the UI conventions: sentence case, 3px radiuses, `.edgecard`/`.banner` for callouts. Mark a `maybe_held` lead with a `.banner.warn`.

Add the link to the admin index in `preferences.html` next to `/admin/deliveries`.

- [ ] **Step 5: Run to verify it passes**

```bash
uv run --isolated pytest tests/test_admin_discoveries.py -q
uv run --isolated pytest -q
```
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
uv run --isolated ruff check .
git add src/app/web/routes/discoveries.py src/app/web/templates/admin_discoveries.html src/app/web/app.py src/app/web/templates/preferences.html tests/test_admin_discoveries.py
git commit -m "feat: /admin/discoveries review surface"
```

---

## Task 8: The import path records the event id

**Files:**
- Modify: `src/app/domain/draft.py` (per-leg field)
- Modify: `src/app/domain/yaml_import.py`, `src/app/domain/yaml_export.py`
- Modify: `src/app/web/routes/imports.py` (`import_commit`)
- Modify: `src/app/web/templates/import_preview.html`
- Modify: `src/app/web/skill_dist/add-concert/references/example-draft.yaml` and `SKILL.md`
- Test: `tests/test_import_records_event_id.py`

**Interfaces:**
- Consumes: `ConcertDay.eventernote_event_id` (Task 2).
- Produces: a per-performance `eventernote_event_id` key in the draft vocabulary, round-tripping through export and import and persisted by `import_commit`.

This is what makes the diff's exact branch grow coverage over time. Without it every lead falls through to the date-and-venue hint forever.

- [ ] **Step 1: Write the failing test**

```python
"""A draft's per-leg eventernote event id survives to the database."""

from app.domain.yaml_import import parse_yaml_draft

DRAFT = """
title: テスト
title_en: Test
performances:
  - label: Day 1
    starts_at_jst: "2026-11-15 17:00"
    eventernote_event_id: "464372"
"""


def test_the_parser_reads_a_per_leg_event_id():
    draft = parse_yaml_draft(DRAFT)
    assert draft.days[0].eventernote_event_id == "464372"


def test_an_absent_event_id_is_none_not_an_error():
    draft = parse_yaml_draft(
        'title: t\nperformances:\n  - {label: Day 1, starts_at_jst: "2026-11-15 17:00"}\n'
    )
    assert draft.days[0].eventernote_event_id is None
```

Add a commit-level test asserting the value lands on `ConcertDay`, following the fixture shape in `tests/test_admin_discoveries.py` and posting to `/concerts/import/commit`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_import_records_event_id.py -q`
Expected: FAIL — `AttributeError: 'ParsedDay' object has no attribute 'eventernote_event_id'`

- [ ] **Step 3: Thread the field through**

Add `eventernote_event_id: str | None = None` to `ParsedDay` in `domain/draft.py`; read it in `yaml_import` via the existing `_text` helper; emit it in `yaml_export` only when set (the exporter omits empty fields); carry it as a hidden input per leg in `import_preview.html`; and set it on the `ConcertDay` in `import_commit`.

Add the key to the skill's `example-draft.yaml` and document it in one line in `SKILL.md` — a test pins the example draft to the parser, so a drifted example fails CI.

- [ ] **Step 4: Run to verify it passes**

```bash
uv run --isolated pytest tests/test_import_records_event_id.py -q
uv run --isolated pytest -q
```
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run --isolated ruff check .
git add -A
git commit -m "feat: an imported leg remembers its eventernote event id"
```

---

## Task 9: Documentation and wishlist bookkeeping

**Files:**
- Modify: `CLAUDE.md`
- Modify: `WISHLIST.md`
- Modify: `README.md` (test count)

- [ ] **Step 1: Update CLAUDE.md**

Add to the layout section: `domain/eventernote.py` (pure parser, take-while stop rule), `domain/discovery_message.py` (the DM, two halves because Discord does not linkify inside a fence), `app/discovery.py` (guarded fetch + sweep, sits above db/ like ops.py), and `routes/discoveries.py`. Note in the invariant-4 paragraph that `discovery` is an ordinary notice and is deliberately NOT in `UNREPORTED_NOTE_KINDS`. Add one line to the header paragraph listing the feature as shipped.

- [ ] **Step 2: Update WISHLIST.md**

Move entry #1 to Shipped with today's date and a one-line summary, then do the FULL revision pass CLAUDE.md requires: re-rank every remaining entry and reconsider which are still useful. Note explicitly whether in-app LLM extraction is changed by this shipping (it is not — that entry is about extraction, this is about discovery).

- [ ] **Step 3: Update the test count in README.md**

```bash
uv run --isolated pytest -q 2>&1 | tail -3
```
Use the real number.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md WISHLIST.md README.md
git commit -m "docs: record Eventernote discovery"
```

---

## Task 10: Verify against the live site once, behind the flag

**Files:** none — this is a manual verification gate.

The parser was built against a fixture. Before this ships, prove it works against the live site exactly once, with a seeded local DB.

- [ ] **Step 1: Enable discovery locally and run one sweep**

```bash
uv run --isolated python -c "
import asyncio
from datetime import UTC, datetime
from app.db.session import SessionMaker
from app.discovery import run_sweep
async def main():
    async with SessionMaker() as s:
        print(await run_sweep(s, datetime.now(UTC)))
        await s.rollback()   # verification only: write nothing
asyncio.run(main())
"
```

- [ ] **Step 2: Judge the result honestly**

A healthy sweep fetches ~86 pages, fails on none or few, and finds leads. If it fetches 86 and finds ZERO leads, the parser is matching nothing — that is a failure that looks like success, and the fixture tests would not have caught it. Report the numbers rather than declaring victory.

- [ ] **Step 3: Report, do not commit**

No commit. Report the counts and any failures, and stop for the owner to decide whether to enable `DISCOVERY_ENABLED` in production.

---

## Self-Review Notes

**Spec coverage.** Every spec section maps to a task: the pure parser and stop rule (1), the schema including the leg's event id (2), the three-way fetch guard with the S3 redirect case named (3), the four-branch precedence and the 昼/夜 hint (4), the DM's two halves and character budget (5), cadence, outbox, admin resolution and the announce-everything first-sweep rule (6), the review surface (7), the import path that grows the exact branch (8), docs (9), and the live check the fixture cannot give (10).

**Known deliberate gaps, carried from the spec:** a dismissed lead is not linked back to the leg it duplicates; existing legs are not backfilled with event ids; past events are never walked; nothing auto-creates a concert.

**Type consistency:** `ActorEvent` (parser) and `Lead` (message) are deliberately different types — `domain/` may not import sqlalchemy, so Task 6 adapts `DiscoveredEvent` rows into `Lead`s at the boundary. `event_id` is a `str` everywhere, never an int, because it is an opaque identifier and leading zeros must survive.
