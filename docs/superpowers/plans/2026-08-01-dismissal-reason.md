# Dismissal reason Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record WHY a discovery lead was dismissed, using the taxonomy classes,
so triage becomes data a classifier can be scored against instead of a judgment
that evaporates as it is made.

**Architecture:** A `DismissReason` StrEnum in `domain/types.py`, a nullable
`DiscoveredEvent.dismiss_reason` column, the reason threaded through
`dismiss_lead` as a REQUIRED parameter, a reason picker replacing the single
Dismiss button on `/admin/discoveries`, and a counts-by-reason summary on the
same page so the column is readable rather than write-only.

**Tech Stack:** SQLAlchemy 2.0 async, Alembic (SQLite batch mode), FastAPI,
Jinja2, pytest-asyncio.

## Global Constraints

- `/admin/discoveries` is **English-only and NOT wrapped in `_()`**, like
  `/admin/deliveries`. Do not add catalogue entries for any string in this work.
- The migration is additive (one nullable column). No `drop_constraint`, so no
  `naming_convention=` argument is needed. After autogenerate, replace
  `app.db.models.UTCDateTime()` with `sa.DateTime()` and delete the
  `import app.db.models` line if present.
- Migration files and `alembic.ini` stay **ASCII-only** (the owner's Windows
  machine uses a GBK locale).
- `alembic` head at plan time is `bb9780f0ad82`. The new revision chains off it.
- This surface still **writes only to `discovered_events`**. It never creates a
  concert; `import_commit` remains the only write path into `concerts`.
- Existing dismissals are **NEVER backfilled**. NULL means "dismissed before
  reasons existed" and stays NULL, matching how `ConcertSubscription` and
  `LegOptOut` hold only explicit user edits.
- `POST` handlers redirect **303, never 307**.
- Tests: `uv run pytest -q` must pass; `uv run ruff check .` must be clean.

---

### Task 1: The vocabulary, the column, the migration

**Files:**
- Modify: `src/app/domain/types.py` (add `DismissReason` after `BroadcastMode`)
- Modify: `src/app/db/models.py` (`DiscoveredEvent`, beside `dismissed_at`)
- Create: `alembic/versions/<rev>_dismissal_reason.py`
- Test: `tests/test_admin_discoveries.py`

**Interfaces:**
- Produces: `DismissReason` (StrEnum, 8 members) and
  `DiscoveredEvent.dismiss_reason: Mapped[str | None]`. Tasks 2 and 3 consume
  both.

- [ ] **Step 1: Write the failing test**

```python
async def test_dismiss_reason_defaults_to_null_and_takes_a_taxonomy_value(db):
    """NULL is a real state: a lead dismissed before reasons existed. It is
    never backfilled, exactly as subscriptions and leg opt-outs are not."""
    from app.domain.types import DismissReason

    async with db() as s:
        s.add(DiscoveredEvent(
            eventernote_event_id="1", title="t", event_date=date(2026, 9, 1),
        ))
        await s.commit()
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.dismiss_reason is None

        row.dismiss_reason = DismissReason.RELEASE
        await s.commit()
        assert (await s.execute(select(DiscoveredEvent))).scalar_one().dismiss_reason == "release"


def test_dismiss_reason_covers_every_taxonomy_class():
    """Eight values, one per class in docs/discovery-lead-taxonomy-2026-08-01.md
    plus `other`. LIVE is present deliberately: a real concert you do not want to
    track is a dismissal, and without a value for it every such lead lands in
    `other` and destroys the agreement rate this column exists to measure."""
    from app.domain.types import DismissReason

    assert {r.value for r in DismissReason} == {
        "live", "stage", "release", "talk",
        "festival", "fanmeet", "free", "other",
    }
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_admin_discoveries.py -q -k dismiss_reason`
Expected: FAIL — `ImportError: cannot import name 'DismissReason'`

- [ ] **Step 3: Add the enum**

In `src/app/domain/types.py`, after `BroadcastMode`:

```python
class DismissReason(enum.StrEnum):
    """Why a discovery lead was waved off -- the taxonomy class it belongs to.

    The CLASS, not an operational excuse ("not interested"), because the point
    of recording it is to score a classifier against real human decisions: a
    guess is only measurable against a judgment of the same kind. Values track
    docs/discovery-lead-taxonomy-2026-08-01.md.

    LIVE and FANMEET name classes that FIT the app, which is not a
    contradiction -- a genuine concert you do not want to follow is still a
    dismissal, and giving it a value keeps it out of `other`, where it would
    quietly wreck the agreement rate.
    """

    LIVE = "live"           # A real concert or tour, not one to track
    STAGE = "stage"         # 朗読劇 / ミュージカル / 舞台 / リーディング
    RELEASE = "release"     # 発売記念 / お渡し会 / 特典会 / 写真集
    TALK = "talk"           # ラジオ / 番組イベント / トークショー
    FESTIVAL = "festival"   # Multi-artist bill; the concert is the festival
    FANMEET = "fanmeet"     # ファンミーティング / バースデーイベント
    FREE = "free"           # No ticket at all -- 餅まき, 盆踊り, 駅長就任式
    OTHER = "other"         # Anything the taxonomy does not name yet
```

- [ ] **Step 4: Add the column**

In `src/app/db/models.py`, in `DiscoveredEvent`, directly after `dismissed_at`:

```python
    # WHY it was dismissed -- a DismissReason value. Nullable, and NULL is a
    # real state rather than missing data: it means "dismissed before reasons
    # existed". Never backfilled, for the reason ConcertSubscription and
    # LegOptOut are not -- these rows hold explicit human judgments only, and
    # inventing one would put a guess into the very column that exists to
    # measure guesses.
    dismiss_reason: Mapped[str | None] = mapped_column(String(20))
```

- [ ] **Step 5: Generate and hand-edit the migration**

Run: `uv run alembic revision --autogenerate -m "dismissal reason"`

Then edit the generated file: confirm it is a single
`batch_op.add_column(sa.Column('dismiss_reason', sa.String(length=20), nullable=True))`
inside `with op.batch_alter_table('discovered_events', schema=None) as batch_op:`,
that `downgrade` drops only that column, that no `import app.db.models` line
remains, and that the file is ASCII-only.

- [ ] **Step 6: Apply and re-run**

Run: `uv run alembic upgrade head` then
`uv run pytest tests/test_admin_discoveries.py -q -k dismiss_reason`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/app/domain/types.py src/app/db/models.py alembic/versions tests/test_admin_discoveries.py
git commit -m "feat: a dismissal records which taxonomy class it was"
```

---

### Task 2: The write path

**Files:**
- Modify: `src/app/db/service.py` (`dismiss_lead`, ~line 7083)
- Modify: `src/app/web/routes/discoveries.py` (`dismiss`, ~line 249)
- Test: `tests/test_admin_discoveries.py`

**Interfaces:**
- Consumes: `DismissReason` from Task 1.
- Produces: `dismiss_lead(session, lead_id, now, reason: DismissReason) -> bool`
  — `reason` is **positional-or-keyword and REQUIRED, deliberately not
  defaulted**. A field added after a format ships and quietly defaulting is
  exactly how the concert draft lost characters; there is one production caller
  to update. Task 3 consumes the route's 422 behaviour.

- [ ] **Step 1: Write the failing tests**

```python
async def test_dismissing_records_the_reason(client_admin, db):
    lead_id = await _seed_lead(db)
    r = await client_admin.post(
        f"/admin/discoveries/{lead_id}/dismiss",
        data={"reason": "release"}, follow_redirects=False,
    )
    assert r.status_code == 303
    async with db() as s:
        row = await s.get(DiscoveredEvent, lead_id)
        assert row.dismissed_at is not None
        assert row.dismiss_reason == "release"


async def test_an_unknown_reason_is_422_and_writes_nothing(client_admin, db):
    """The value reaches an enum, so a hand-posted body cannot invent a class
    and pollute the very column that exists to be counted."""
    lead_id = await _seed_lead(db)
    r = await client_admin.post(
        f"/admin/discoveries/{lead_id}/dismiss",
        data={"reason": "nonsense"}, follow_redirects=False,
    )
    assert r.status_code == 422
    async with db() as s:
        row = await s.get(DiscoveredEvent, lead_id)
        assert row.dismissed_at is None, "a refused reason must not dismiss"
        assert row.dismiss_reason is None


async def test_a_missing_reason_is_422(client_admin, db):
    lead_id = await _seed_lead(db)
    r = await client_admin.post(
        f"/admin/discoveries/{lead_id}/dismiss", data={}, follow_redirects=False,
    )
    assert r.status_code == 422
    async with db() as s:
        assert (await s.get(DiscoveredEvent, lead_id)).dismissed_at is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_admin_discoveries.py -q -k "reason"`
Expected: FAIL — the route takes no `reason`, so the first asserts
`dismiss_reason == "release"` against None and the others get 303s.

- [ ] **Step 3: Thread it through the service writer**

In `src/app/db/service.py`, change `dismiss_lead`:

```python
async def dismiss_lead(
    session: AsyncSession, lead_id: int, now: datetime, reason: DismissReason
) -> bool:
    """Kill a lead for good, recording which taxonomy class it was.

    False when there was nothing to dismiss (an unknown id, or one already
    dismissed) so a caller can 404 rather than report a write that did not
    happen.

    `reason` is required rather than defaulted on purpose: a column added after
    the fact that quietly accepts a default is how the concert draft silently
    shipped without characters, and there is exactly one production caller.
    """
    row = await session.get(DiscoveredEvent, lead_id)
    if row is None or row.dismissed_at is not None:
        return False
    row.dismissed_at = now
    row.dismiss_reason = reason
    await session.flush()
    return True
```

Add `DismissReason` to the `app.domain.types` import block at the top of the file.

- [ ] **Step 4: Take it at the route boundary**

In `src/app/web/routes/discoveries.py`, change `dismiss` — declaring the
parameter as the enum is what makes FastAPI answer 422 on an unknown value
before any write happens:

```python
@router.post("/admin/discoveries/{lead_id}/dismiss")
async def dismiss(
    lead_id: int,
    reason: Annotated[DismissReason, Form()],
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Wave a lead off for good, recording which taxonomy class it was.

    `reason` is typed as the enum rather than validated by hand, so an invented
    class is a 422 from FastAPI before anything is written -- this column's
    whole value is that every row in it is a real human judgment.

    404 on a False from `dismiss_lead` -- an unknown id, or one already
    dismissed. Reporting a write that did not happen as a cheerful 303 is how a
    double-submit looks like it worked.

    303, never 307: the POST must not be replayed against the page it lands on.
    """
    if not await dismiss_lead(session, lead_id, datetime.now(UTC), reason):
        raise HTTPException(status_code=404, detail="no such lead")
    await session.commit()
    return RedirectResponse("/admin/discoveries", status_code=303)
```

Add the imports this needs: `from typing import Annotated`, `from fastapi import Form`, and `DismissReason` from `app.domain.types`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_admin_discoveries.py -q`
Expected: PASS. Fix any other caller the signature change breaks — search with
`grep -rn "dismiss_lead" src/ tests/`.

- [ ] **Step 6: Commit**

```bash
git add src/app/db/service.py src/app/web/routes/discoveries.py tests/test_admin_discoveries.py
git commit -m "feat: take the dismissal reason at the route boundary, refuse invented ones"
```

---

### Task 3: The picker, and making the column readable

**Files:**
- Modify: `src/app/web/templates/admin_discoveries.html` (~line 139 and the note at ~line 152)
- Modify: `src/app/web/routes/discoveries.py` (`discoveries`, ~line 115-170)
- Modify: `src/app/db/service.py` (new reader beside `open_leads`, ~line 7058)
- Test: `tests/test_admin_discoveries.py`

**Interfaces:**
- Consumes: `DismissReason` (Task 1), the route's `reason` field (Task 2).
- Produces: `dismissed_reason_counts(session) -> dict[str, int]`.

**Why this task exists at all:** `open_leads` filters
`dismissed_at.is_(None)`, so a dismissed lead is never rendered anywhere. Without
a reader the column is write-only and the feature is a column, not a feature.

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_page_offers_a_reason_per_taxonomy_class(client_admin, db):
    await _seed_lead(db)
    r = await client_admin.get("/admin/discoveries")
    assert r.status_code == 200
    for value in ("live", "stage", "release", "talk",
                  "festival", "fanmeet", "free", "other"):
        assert f'value="{value}"' in r.text, f"no way to dismiss as {value}"


async def test_dismissed_counts_are_shown_so_the_column_is_readable(client_admin, db):
    """open_leads hides dismissed rows, so without this the reason is
    write-only and nothing can ever be scored against it."""
    a, b, c = await _seed_lead(db), await _seed_lead(db), await _seed_lead(db)
    for lead_id, reason in ((a, "release"), (b, "release"), (c, "stage")):
        await client_admin.post(
            f"/admin/discoveries/{lead_id}/dismiss",
            data={"reason": reason}, follow_redirects=False,
        )
    r = await client_admin.get("/admin/discoveries")
    assert "2 release" in r.text
    assert "1 stage" in r.text


async def test_counts_ignore_pre_reason_dismissals(client_admin, db):
    """A NULL reason is a real state -- dismissed before reasons existed -- and
    must not be counted as `other`, which would invent a judgment nobody made."""
    lead_id = await _seed_lead(db)
    async with db() as s:
        row = await s.get(DiscoveredEvent, lead_id)
        row.dismissed_at = datetime.now(UTC)
        await s.commit()
    r = await client_admin.get("/admin/discoveries")
    assert "other" not in r.text.split("Dismissed so far")[-1][:200]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_admin_discoveries.py -q -k "taxonomy_class or readable or pre_reason"`
Expected: FAIL — one Dismiss button, no counts rendered.

- [ ] **Step 3: Add the reader**

In `src/app/db/service.py`, directly after `open_leads`:

```python
async def dismissed_reason_counts(session: AsyncSession) -> dict[str, int]:
    """How many leads were dismissed as each taxonomy class.

    Rows with a NULL reason are EXCLUDED rather than bucketed as `other`: they
    predate the column, and folding them in would invent a human judgment in
    the one place whose value is that every entry is a real one.
    """
    rows = await session.execute(
        select(DiscoveredEvent.dismiss_reason, func.count())
        .where(DiscoveredEvent.dismiss_reason.is_not(None))
        .group_by(DiscoveredEvent.dismiss_reason)
    )
    return {reason: n for reason, n in rows.all()}
```

- [ ] **Step 4: Put it in the page context**

In `routes/discoveries.py`'s `discoveries` handler, call it and add
`"reason_counts": await dismissed_reason_counts(session)` and
`"dismiss_reasons": list(DismissReason)` to the template context.

- [ ] **Step 5: Replace the button with a picker**

In `admin_discoveries.html`, replace the single-button form:

```html
          <form method="post" action="/admin/discoveries/{{ r.lead.id }}/dismiss">
            {# One submit per class: the reason IS the dismissal, so a separate
               select plus a confirm button would be two controls for one act. #}
            <div class="reasons">
              {% for reason in dismiss_reasons %}
              <button class="act no" type="submit" name="reason" value="{{ reason.value }}">
                {{ reason.value }}
              </button>
              {% endfor %}
            </div>
          </form>
```

Then, after the existing `<p class="dim">` note, add the counts:

```html
{% if reason_counts %}
<p class="dim">Dismissed so far —
  {% for reason, n in reason_counts | dictsort %}{{ n }} {{ reason }}{{ ", " if not loop.last }}{% endfor %}.
  Leads dismissed before reasons were recorded are not counted.
</p>
{% endif %}
```

Extend the existing note's last sentence to say the reason is recorded and is
the taxonomy class, not an excuse.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_admin_discoveries.py -q`
Expected: PASS

- [ ] **Step 7: Full suite and lint**

Run: `uv run pytest -q` then `uv run ruff check .`
Expected: all pass, ruff clean.

- [ ] **Step 8: Commit**

```bash
git add src/app/db/service.py src/app/web/routes/discoveries.py src/app/web/templates/admin_discoveries.html tests/test_admin_discoveries.py
git commit -m "feat: a reason button per class, and counts so the column reads back"
```
