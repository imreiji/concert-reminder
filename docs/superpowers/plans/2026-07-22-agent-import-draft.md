# Agent-Driven Concert Import (YAML Draft Round-Trip) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent (guided by a new repo skill) merges concert sources into one trilingual YAML draft; the editor pastes it at `/concerts/import` and gets the existing import preview fully prefilled, then commits through the unchanged `import_commit`.

**Architecture:** The `yaml_export.py` vocabulary becomes two-way. Shared draft dataclasses move to a new `domain/draft.py` (extended with trilingual/anchor/tag fields); `domain/ingest.py` (ramen parser) and a new `domain/yaml_import.py` (draft parser) both produce them, so `import_preview.html` renders one shape from two producers. One new POST route resolves tag/venue *names* to ids and renders the preview; zero new write paths. A `.claude/skills/add-concert/` skill teaches the agent the whole workflow.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy async / Jinja2 / PyYAML (already a dependency) / pytest + pytest-asyncio (auto mode).

**Spec:** `docs/superpowers/specs/2026-07-22-agent-import-draft-design.md`

## Global Constraints

- `uv run pytest -q` MUST pass and `uv run ruff check .` MUST be clean before every commit.
- All draft/form datetimes are **naive JST wall-clock** strings `YYYY-MM-DD HH:MM`; conversion to UTC happens only at the existing commit boundary (`parse_jst`) — never add a second path (invariant 1).
- `import_commit` stays the ONLY write path for imports. The new draft route renders a preview and writes nothing.
- `yaml.safe_load` only — never `yaml.load`.
- Template injection rules (invariant 7): raw Python dicts through `| tojson` for script data; never interpolate user-controlled text into inline `on*` handlers; URLs are validated at commit via `form_url`, not at preview.
- Every NEW translatable template string needs entries in BOTH `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po`; reused strings must keep msgids byte-identical. `tests/test_i18n_catalogues.py` enforces this.
- `domain/` files: pure logic, NO I/O, no fastapi/sqlalchemy/discord imports.
- Config files stay ASCII-only. Python/HTML/YAML files may contain Japanese/Chinese text.
- Work happens on the existing `agent-import` branch.

---

### Task 1: `domain/draft.py` — shared draft dataclasses, extended

The ramen parser's `ParsedDay`/`ParsedRound`/`ParsedConcert` move to a new module and gain the fields a pasted draft can carry (trilingual labels, per-leg venue, results/payment anchors, tag names). `ingest.py` re-exports them so every existing import keeps working. The ramen parser itself changes only its `import` line.

**Files:**
- Create: `src/app/domain/draft.py`
- Modify: `src/app/domain/ingest.py` (replace the three dataclass definitions with an import)
- Test: `tests/test_yaml_import.py` (new file; first test here, more added in Task 2)

**Interfaces:**
- Produces: `app.domain.draft.ParsedDay`, `ParsedRound`, `ParsedConcert` — exact field lists below. Tasks 2, 4, 5 consume these. `app.domain.ingest.ParsedConcert` etc. keep working as re-exports.

- [ ] **Step 1: Write the failing test**

Create `tests/test_yaml_import.py`:

```python
"""domain/draft.py + domain/yaml_import.py: the two-way draft vocabulary.

Pure-domain tests -- no DB, no routes (route coverage is
tests/test_draft_import.py). Mirrors test_ingest.py's style.
"""

from datetime import datetime

from app.domain.draft import ParsedConcert, ParsedDay, ParsedRound
from app.domain.types import RoundKind


def test_extended_fields_default_empty():
    """The ramen parser fills only the original fields; everything the draft
    path adds must default to empty so ingest.py needs no changes beyond its
    import line."""
    day = ParsedDay(label="Day 1", starts_at_jst=datetime(2026, 11, 7, 17, 0))
    assert day.label_en is None and day.label_zh is None
    assert day.doors_at_jst is None and day.venue_name is None
    assert day.venue_city is None and day.venue_address is None
    assert day.matched_venue_tag_id is None

    rnd = ParsedRound(
        label="1次先行", kind=RoundKind.LOTTERY_ROUND,
        opens_at_jst=None, closes_at_jst=None, url=None,
    )
    assert rnd.label_en is None and rnd.label_zh is None
    assert rnd.results_at_jst is None and rnd.payment_at_jst is None
    assert rnd.notes is None and rnd.applies_to_labels == []
    assert rnd.leg_keys == "" and rnd.leg_keys_selected == set()

    parsed = ParsedConcert(title="T", venue_name=None)
    assert parsed.title_en is None and parsed.title_zh is None
    assert parsed.notes is None and parsed.notes_en is None and parsed.notes_zh is None
    assert parsed.organizer is None and parsed.categories is None
    assert parsed.kind is None
    assert parsed.source_url is None and parsed.official_url is None
    assert parsed.eventernote_url is None
    assert parsed.performers_text is None
    assert parsed.franchise_names == [] and parsed.group_names == []
    assert parsed.artist_names == []


def test_ingest_reexports_the_shared_types():
    from app.domain import ingest
    assert ingest.ParsedConcert is ParsedConcert
    assert ingest.ParsedDay is ParsedDay
    assert ingest.ParsedRound is ParsedRound
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_yaml_import.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.draft'`

- [ ] **Step 3: Create `src/app/domain/draft.py`**

```python
"""The draft-concert vocabulary: what a not-yet-saved concert looks like.

Two producers fill these dataclasses -- domain/ingest.py (the ramen.events
scrape) and domain/yaml_import.py (a pasted agent draft) -- and one consumer
renders them (import_preview.html via routes/imports.py). The ramen parser
fills only the original subset; every field the draft path added defaults to
empty, so a producer never has to know about the other's fields.

Pure data carriers: no I/O, no ORM. Datetimes are naive JST wall-clock,
exactly like a web form's <input type="datetime-local"> -- conversion to UTC
happens at the commit boundary (concerts.parse_jst), never here.

`matched_venue_tag_id`, `leg_keys` and `leg_keys_selected` are resolved at
the ROUTE boundary (they need the DB's tag list / the preview's day_key
scheme) and stamped onto the dataclasses before rendering; the parsers
always leave them empty.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.types import ConcertKind, RoundKind


@dataclass
class ParsedDay:
    label: str
    starts_at_jst: datetime
    label_en: str | None = None
    label_zh: str | None = None
    doors_at_jst: datetime | None = None
    venue_name: str | None = None
    venue_city: str | None = None
    venue_address: str | None = None
    matched_venue_tag_id: int | None = None  # route-resolved, never parsed


@dataclass
class ParsedRound:
    label: str
    kind: RoundKind
    opens_at_jst: datetime | None
    closes_at_jst: datetime | None
    url: str | None
    label_en: str | None = None
    label_zh: str | None = None
    results_at_jst: datetime | None = None
    payment_at_jst: datetime | None = None
    notes: str | None = None
    applies_to_labels: list[str] = field(default_factory=list)
    leg_keys: str = ""                                  # route-resolved
    leg_keys_selected: set[str] = field(default_factory=set)  # route-resolved


@dataclass
class ParsedConcert:
    title: str
    venue_name: str | None
    days: list[ParsedDay] = field(default_factory=list)
    rounds: list[ParsedRound] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title_en: str | None = None
    title_zh: str | None = None
    notes: str | None = None
    notes_en: str | None = None
    notes_zh: str | None = None
    organizer: str | None = None
    categories: str | None = None
    kind: ConcertKind | None = None
    source_url: str | None = None
    official_url: str | None = None
    eventernote_url: str | None = None
    performers_text: str | None = None
    franchise_names: list[str] = field(default_factory=list)
    group_names: list[str] = field(default_factory=list)
    artist_names: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Point `ingest.py` at the shared module**

In `src/app/domain/ingest.py`, DELETE the three dataclass definitions (the `@dataclass class ParsedDay:`, `@dataclass class ParsedRound:`, `@dataclass class ParsedConcert:` blocks, currently lines 65-86) and DELETE `from dataclasses import dataclass, field` (line 23). ADD in the imports section:

```python
from app.domain.draft import ParsedConcert, ParsedDay, ParsedRound
```

(This is a re-export: `ingest.ParsedConcert` stays importable, which `routes/imports.py` and the existing tests rely on. Ruff will not flag it unused because the module's own functions use all three names.)

- [ ] **Step 5: Run the new tests, then the ingest suite**

Run: `uv run pytest tests/test_yaml_import.py tests/test_ingest.py -q`
Expected: all PASS (ingest behavior unchanged — pure move).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check .
git add src/app/domain/draft.py src/app/domain/ingest.py tests/test_yaml_import.py
git commit -m "refactor: move draft dataclasses to domain/draft.py, extended for the two-way draft"
```

---

### Task 2: `domain/yaml_import.py` — the pure draft parser

`parse_draft(text) -> ParsedConcert`. Tolerant like the ramen parser: warnings over failures; `DraftError` only for not-YAML / not-a-mapping / no title.

**Files:**
- Create: `src/app/domain/yaml_import.py`
- Test: `tests/test_yaml_import.py` (extend)

**Interfaces:**
- Consumes: `app.domain.draft.*` (Task 1).
- Produces: `app.domain.yaml_import.parse_draft(text: str) -> ParsedConcert` and `app.domain.yaml_import.DraftError(Exception)`. Task 5's route consumes both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_yaml_import.py`:

```python
import pytest

from app.domain.types import ConcertKind
from app.domain.yaml_import import DraftError, parse_draft

FULL_DRAFT = """\
title: 蓮ノ空女学院スクールアイドルクラブ 6th ライブ
title_en: Hasunosora 6th Live
title_zh: 莲之空女学院学园偶像社 6th 演唱会
kind: tour
organizer: バンダイナムコ
categories: anime song
series:
  franchises: [Love Live!]
  groups: [蓮ノ空女学院スクールアイドルクラブ]
  artists: [日野下花帆, 村野さやか]
performers: [日野下花帆, 村野さやか]
eventernote_url: https://www.eventernote.com/events/465358
official_url: https://www.lovelive-anime.jp/hasunosora/
source_url: https://www.lovelive-anime.jp/hasunosora/live-event/live_detail.php?p=6th
performances:
  - label: Day 1
    label_en: Day 1
    label_zh: 第1天
    venue: Kアリーナ横浜
    city: 横浜
    venue_address: 神奈川県横浜市西区みなとみらい6-2-14
    doors_jst: 2026-11-07 15:30
    starts_at_jst: 2026-11-07 17:00
  - label: Day 2
    label_en: Day 2
    label_zh: 第2天
    venue: Kアリーナ横浜
    starts_at_jst: 2026-11-08 17:00
rounds:
  - label: 最速先行抽選
    label_en: Earliest advance lottery
    label_zh: 最速先行抽选
    kind: lottery_round
    applies_to: [Day 1, Day 2]
    apply_opens_jst: 2026-08-01 12:00
    apply_closes_jst: 2026-08-16 23:59
    results_jst: 2026-08-22 15:00
    payment_deadline_jst: 2026-08-25 23:00
    url: https://eplus.jp/hasu6th/
    notes: CD封入シリアル
notes: 全席指定
notes_en: All seats reserved
notes_zh: 全部为指定席
"""


def test_full_draft_parses_without_warnings():
    p = parse_draft(FULL_DRAFT)
    assert p.warnings == []
    assert p.title == "蓮ノ空女学院スクールアイドルクラブ 6th ライブ"
    assert p.title_en == "Hasunosora 6th Live"
    assert p.title_zh == "莲之空女学院学园偶像社 6th 演唱会"
    assert p.kind is ConcertKind.TOUR
    assert p.organizer == "バンダイナムコ"
    assert p.categories == "anime song"
    assert p.franchise_names == ["Love Live!"]
    assert p.group_names == ["蓮ノ空女学院スクールアイドルクラブ"]
    assert p.artist_names == ["日野下花帆", "村野さやか"]
    assert p.performers_text == "日野下花帆\n村野さやか"
    assert p.eventernote_url == "https://www.eventernote.com/events/465358"
    assert p.source_url is not None and p.official_url is not None
    assert p.notes == "全席指定" and p.notes_en and p.notes_zh

    assert len(p.days) == 2
    d1 = p.days[0]
    assert d1.label == "Day 1" and d1.label_zh == "第1天"
    assert d1.venue_name == "Kアリーナ横浜" and d1.venue_city == "横浜"
    assert d1.venue_address.startswith("神奈川県")
    assert d1.doors_at_jst == datetime(2026, 11, 7, 15, 30)
    assert d1.starts_at_jst == datetime(2026, 11, 7, 17, 0)
    assert p.days[1].doors_at_jst is None

    assert len(p.rounds) == 1
    r = p.rounds[0]
    assert r.label == "最速先行抽選" and r.label_en and r.label_zh
    assert r.kind is RoundKind.LOTTERY_ROUND
    assert r.applies_to_labels == ["Day 1", "Day 2"]
    assert r.opens_at_jst == datetime(2026, 8, 1, 12, 0)
    assert r.closes_at_jst == datetime(2026, 8, 16, 23, 59)
    assert r.results_at_jst == datetime(2026, 8, 22, 15, 0)
    assert r.payment_at_jst == datetime(2026, 8, 25, 23, 0)
    assert r.url == "https://eplus.jp/hasu6th/"
    assert r.notes == "CD封入シリアル"


def test_not_yaml_raises_draft_error():
    with pytest.raises(DraftError):
        parse_draft("title: [unclosed")


def test_non_mapping_raises_draft_error():
    with pytest.raises(DraftError):
        parse_draft("- just\n- a\n- list\n")


def test_missing_title_raises_draft_error():
    with pytest.raises(DraftError):
        parse_draft("kind: tour\n")


def test_unknown_round_kind_falls_back_to_other_with_warning():
    p = parse_draft("title: T\nrounds:\n  - label: X\n    kind: mystery_meat\n")
    assert p.rounds[0].kind is RoundKind.OTHER
    assert any("mystery_meat" in w for w in p.warnings)


def test_round_kind_accepts_enum_name_case_insensitively():
    p = parse_draft("title: T\nrounds:\n  - label: X\n    kind: FCFS_SALE\n")
    assert p.rounds[0].kind is RoundKind.FCFS_SALE
    assert p.warnings == []


def test_unknown_concert_kind_warns_and_clears():
    p = parse_draft("title: T\nkind: hootenanny\n")
    assert p.kind is None
    assert any("hootenanny" in w for w in p.warnings)


def test_malformed_datetime_warns_and_blanks():
    p = parse_draft(
        "title: T\nperformances:\n  - label: Day 1\n    starts_at_jst: sometime soon\n"
    )
    assert p.days[0].starts_at_jst is None
    assert any("sometime soon" in w for w in p.warnings)


def test_t_separator_datetime_accepted():
    p = parse_draft(
        "title: T\nperformances:\n  - label: D\n    starts_at_jst: 2026-11-07T17:00\n"
    )
    assert p.days[0].starts_at_jst == datetime(2026, 11, 7, 17, 0)


def test_unknown_keys_warn_but_do_not_fail():
    p = parse_draft("title: T\nfrobnicator: 9\nperformances:\n  - label: D\n    starts_at_jst: 2026-11-07 17:00\n    hovercraft: full of eels\n")
    assert p.title == "T"
    assert any("frobnicator" in w for w in p.warnings)
    assert any("hovercraft" in w for w in p.warnings)


def test_slug_and_venues_keys_are_ignored_silently():
    """Both appear in every yaml_export output; neither is draft input (slug is
    derived, concert venues are derived from legs), so round-tripping an export
    must not warn about them."""
    p = parse_draft("title: T\nslug: t\nvenues: [Somewhere]\n")
    assert not p.warnings
```

Note: `datetime` and `RoundKind` are already imported at the top of this file from Task 1.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_yaml_import.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.yaml_import'`

- [ ] **Step 3: Create `src/app/domain/yaml_import.py`**

```python
"""Parse a pasted YAML draft into a ParsedConcert.

The other half of domain/yaml_export.py: the same vocabulary, read back.
The producer is normally an agent following .claude/skills/add-concert, but
the parser trusts nothing -- it is exactly as tolerant as the ramen.events
parser (domain/ingest.py): warnings over failures, so a slightly-off draft
still renders a preview the editor can fix, instead of bouncing them back
to a blank form.

Hard failure (DraftError) only when there is nothing to render a preview
FROM: not YAML, not a mapping, or no title. Everything else degrades to a
warning carried on the result and shown in the preview's warning strip:
unknown kinds fall back, malformed datetimes blank the field, unknown keys
are ignored loudly (they usually mean the skill and this parser have
drifted apart -- the warning is the drift alarm).

Pure function: no I/O. Datetimes are naive JST wall-clock (see
domain/draft.py). yaml.safe_load ONLY -- a draft is pasted text from
outside the trust boundary.
"""

from datetime import date, datetime

import yaml

from app.domain.draft import ParsedConcert, ParsedDay, ParsedRound
from app.domain.types import ConcertKind, RoundKind

_TOP_KEYS = {
    "slug", "title", "title_en", "title_zh", "kind", "organizer",
    "categories", "series", "venues", "performers", "eventernote_url",
    "official_url", "source_url", "performances", "rounds", "notes",
    "notes_en", "notes_zh",
}
_SERIES_KEYS = {"franchises", "groups", "artists"}
_DAY_KEYS = {
    "label", "label_en", "label_zh", "city", "venue", "venue_address",
    "doors_jst", "starts_at_jst",
}
_ROUND_KEYS = {
    "label", "label_en", "label_zh", "kind", "applies_to",
    "apply_opens_jst", "apply_closes_jst", "results_jst",
    "payment_deadline_jst", "url", "notes",
}


class DraftError(Exception):
    """The pasted text can't produce a preview at all."""


def _warn_unknown(mapping: dict, known: set[str], where: str, warnings: list[str]) -> None:
    for key in mapping:
        if key not in known:
            warnings.append(
                f"{where}: unknown key {key!r} ignored -- the draft and the app "
                "may be on different schema versions"
            )


def _text(value) -> str | None:
    """A scalar as trimmed text, or None. YAML may hand back numbers etc."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _names(value, where: str, warnings: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{where}: expected a list, got {type(value).__name__} -- ignored")
        return []
    return [t for v in value if (t := _text(v))]


def _dt(value, where: str, warnings: list[str]) -> datetime | None:
    """Naive JST from 'YYYY-MM-DD HH:MM' (the yaml_export/web-form format).

    YAML itself may resolve a value with seconds to a datetime, or a bare
    date to a date -- accept the former, warn on the latter (a deadline
    without a time of day is not usable data, and JST midnight would be a
    silently wrong guess).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            warnings.append(f"{where}: timezone-aware datetime -- treated as JST wall-clock")
            return value.replace(tzinfo=None)
        return value
    if isinstance(value, date):
        warnings.append(f"{where}: date without a time -- left blank, fill it in the form")
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    warnings.append(f"{where}: couldn't read {text!r} as 'YYYY-MM-DD HH:MM' -- left blank")
    return None


def _concert_kind(value, warnings: list[str]) -> ConcertKind | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return ConcertKind(text.lower())
    except ValueError:
        warnings.append(f"kind: {text!r} isn't a concert kind -- left unset")
        return None


def _round_kind(value, where: str, warnings: list[str]) -> RoundKind:
    text = _text(value)
    if text is None:
        warnings.append(f"{where}: no kind -- defaulted to 'other'")
        return RoundKind.OTHER
    try:
        return RoundKind(text.lower())
    except ValueError:
        warnings.append(f"{where}: unknown kind {text!r} -- defaulted to 'other'")
        return RoundKind.OTHER


def parse_draft(text: str) -> ParsedConcert:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DraftError(f"that doesn't parse as YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise DraftError("a draft is a YAML mapping (key: value lines) -- this isn't one")
    title = _text(data.get("title"))
    if title is None:
        raise DraftError("the draft has no title -- 'title:' is the one required key")

    warnings: list[str] = []
    _warn_unknown(data, _TOP_KEYS, "draft", warnings)

    series = data.get("series") or {}
    if not isinstance(series, dict):
        warnings.append("series: expected a mapping -- ignored")
        series = {}
    _warn_unknown(series, _SERIES_KEYS, "series", warnings)

    performers = _names(data.get("performers"), "performers", warnings)

    days: list[ParsedDay] = []
    raw_days = data.get("performances") or []
    if not isinstance(raw_days, list):
        warnings.append("performances: expected a list -- ignored")
        raw_days = []
    for i, raw in enumerate(raw_days, start=1):
        where = f"performance {i}"
        if not isinstance(raw, dict):
            warnings.append(f"{where}: expected a mapping -- skipped")
            continue
        _warn_unknown(raw, _DAY_KEYS, where, warnings)
        days.append(ParsedDay(
            label=_text(raw.get("label")) or f"Day {i}",
            starts_at_jst=_dt(raw.get("starts_at_jst"), f"{where} starts_at_jst", warnings),
            label_en=_text(raw.get("label_en")),
            label_zh=_text(raw.get("label_zh")),
            doors_at_jst=_dt(raw.get("doors_jst"), f"{where} doors_jst", warnings),
            venue_name=_text(raw.get("venue")),
            venue_city=_text(raw.get("city")),
            venue_address=_text(raw.get("venue_address")),
        ))

    rounds: list[ParsedRound] = []
    raw_rounds = data.get("rounds") or []
    if not isinstance(raw_rounds, list):
        warnings.append("rounds: expected a list -- ignored")
        raw_rounds = []
    for i, raw in enumerate(raw_rounds, start=1):
        where = f"round {i}"
        if not isinstance(raw, dict):
            warnings.append(f"{where}: expected a mapping -- skipped")
            continue
        _warn_unknown(raw, _ROUND_KEYS, where, warnings)
        rounds.append(ParsedRound(
            label=_text(raw.get("label")) or f"Round {i}",
            kind=_round_kind(raw.get("kind"), where, warnings),
            opens_at_jst=_dt(raw.get("apply_opens_jst"), f"{where} apply_opens_jst", warnings),
            closes_at_jst=_dt(raw.get("apply_closes_jst"), f"{where} apply_closes_jst", warnings),
            url=_text(raw.get("url")),
            label_en=_text(raw.get("label_en")),
            label_zh=_text(raw.get("label_zh")),
            results_at_jst=_dt(raw.get("results_jst"), f"{where} results_jst", warnings),
            payment_at_jst=_dt(raw.get("payment_deadline_jst"), f"{where} payment_deadline_jst", warnings),
            notes=_text(raw.get("notes")),
            applies_to_labels=_names(raw.get("applies_to"), f"{where} applies_to", warnings),
        ))

    return ParsedConcert(
        title=title,
        venue_name=None,  # drafts carry venues per leg, never event-level
        days=days,
        rounds=rounds,
        warnings=warnings,
        title_en=_text(data.get("title_en")),
        title_zh=_text(data.get("title_zh")),
        notes=_text(data.get("notes")),
        notes_en=_text(data.get("notes_en")),
        notes_zh=_text(data.get("notes_zh")),
        organizer=_text(data.get("organizer")),
        categories=_text(data.get("categories")),
        kind=_concert_kind(data.get("kind"), warnings),
        source_url=_text(data.get("source_url")),
        official_url=_text(data.get("official_url")),
        eventernote_url=_text(data.get("eventernote_url")),
        performers_text="\n".join(performers) or None,
        franchise_names=_names(series.get("franchises"), "series.franchises", warnings),
        group_names=_names(series.get("groups"), "series.groups", warnings),
        artist_names=_names(series.get("artists"), "series.artists", warnings),
    )
```

NOTE: `ParsedDay.starts_at_jst` is typed `datetime` but a malformed value leaves `None` — the preview's `fmt()` helper already renders `None` as `""` (the required input then stops submission until the editor fills it). That is the tolerant-preview behavior we want; do not "fix" it by raising.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_yaml_import.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check .
git add src/app/domain/yaml_import.py tests/test_yaml_import.py
git commit -m "feat: pure YAML draft parser (domain/yaml_import.py)"
```

---

### Task 3: yaml_export parity + round-trip test

Export gains `title_zh` / `notes_en` / `notes_zh` so export → paste round-trips whole. The export route passes the three columns it already reads siblings of.

**Files:**
- Modify: `src/app/domain/yaml_export.py` (signature + data dict)
- Modify: `src/app/web/routes/concerts.py:1407-1422` (`export_concert_yaml`'s `concert_to_yaml(...)` call)
- Test: `tests/test_yaml_import.py` (round-trip), `tests/test_yaml_export.py` (new fields)

**Interfaces:**
- Consumes: `parse_draft` (Task 2).
- Produces: `concert_to_yaml(..., title_zh=None, notes_en=None, notes_zh=None)` — three new keyword-only-position params appended after `performers`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_yaml_import.py`:

```python
from datetime import timezone

from app.domain.yaml_export import YamlDay, YamlRound, concert_to_yaml


def _utc(y, mo, d, h, mi):
    """The export takes aware UTC; 17:00 JST == 08:00 UTC."""
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_export_then_parse_round_trips():
    text = concert_to_yaml(
        title="6thライブ", kind="tour",
        franchises=["Love Live!"], groups=["蓮ノ空"], artists=["日野下花帆"],
        venues=["Kアリーナ横浜"],
        days=[YamlDay(
            label="Day 1", label_en="Day 1", label_zh="第1天",
            starts_at_utc=_utc(2026, 11, 7, 8, 0),
            city="横浜", venue="Kアリーナ横浜", venue_address="みなとみらい6-2-14",
            doors_at_utc=_utc(2026, 11, 7, 6, 30),
        )],
        rounds=[YamlRound(
            label="最速先行", label_en="Earliest", label_zh="最速先行(中)",
            kind="lottery_round", applies_to_labels=["Day 1"],
            opens_at_utc=_utc(2026, 8, 1, 3, 0), closes_at_utc=_utc(2026, 8, 16, 14, 59),
            results_at_utc=_utc(2026, 8, 22, 6, 0),
            payment_deadline_at_utc=_utc(2026, 8, 25, 14, 0),
            url="https://eplus.jp/x/", notes="シリアル",
        )],
        notes="全席指定", title_en="6th Live", organizer="バンナム",
        categories="anime", eventernote_url="https://www.eventernote.com/events/1",
        official_url="https://example.jp/", source_url="https://example.jp/t/",
        performers=["日野下花帆"],
        title_zh="6th 演唱会", notes_en="All reserved", notes_zh="全指定席",
    )
    p = parse_draft(text)
    assert p.warnings == []
    assert (p.title, p.title_en, p.title_zh) == ("6thライブ", "6th Live", "6th 演唱会")
    assert (p.notes, p.notes_en, p.notes_zh) == ("全席指定", "All reserved", "全指定席")
    assert p.kind is ConcertKind.TOUR
    assert p.franchise_names == ["Love Live!"] and p.artist_names == ["日野下花帆"]
    assert p.performers_text == "日野下花帆"
    d = p.days[0]
    assert d.starts_at_jst == datetime(2026, 11, 7, 17, 0)   # 08:00 UTC -> 17:00 JST
    assert d.doors_at_jst == datetime(2026, 11, 7, 15, 30)
    assert d.venue_name == "Kアリーナ横浜" and d.venue_city == "横浜"
    r = p.rounds[0]
    assert (r.label, r.label_en, r.label_zh) == ("最速先行", "Earliest", "最速先行(中)")
    assert r.kind is RoundKind.LOTTERY_ROUND
    assert r.applies_to_labels == ["Day 1"]
    assert r.closes_at_jst == datetime(2026, 8, 16, 23, 59)
    assert r.results_at_jst == datetime(2026, 8, 22, 15, 0)
    assert r.payment_at_jst == datetime(2026, 8, 25, 23, 0)
    assert r.url == "https://eplus.jp/x/" and r.notes == "シリアル"
```

Append to `tests/test_yaml_export.py` (match its existing test style — it builds `concert_to_yaml` calls and asserts on `yaml.safe_load` of the output):

```python
def test_title_zh_and_notes_variants_export():
    text = concert_to_yaml(
        title="T", kind=None, franchises=[], groups=[], artists=[], venues=[],
        days=[], rounds=[], notes="メモ",
        title_zh="T中文", notes_en="note", notes_zh="笔记",
    )
    data = yaml.safe_load(text)
    assert data["title_zh"] == "T中文"
    assert data["notes_en"] == "note"
    assert data["notes_zh"] == "笔记"
```

(If `tests/test_yaml_export.py` doesn't already `import yaml`, add it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_yaml_import.py tests/test_yaml_export.py -q`
Expected: FAIL — `concert_to_yaml() got an unexpected keyword argument 'title_zh'`

- [ ] **Step 3: Extend `concert_to_yaml`**

In `src/app/domain/yaml_export.py`, add three parameters to the signature (after `performers: list[str] | None = None,`):

```python
    title_zh: str | None = None,
    notes_en: str | None = None,
    notes_zh: str | None = None,
```

In the `data` dict: after the `"title_en": title_en,` line add `"title_zh": title_zh,`; after the final `"notes": notes,` line add `"notes_en": notes_en,` and `"notes_zh": notes_zh,`.

- [ ] **Step 4: Pass the columns in the export route**

In `src/app/web/routes/concerts.py`, in `export_concert_yaml`'s `concert_to_yaml(...)` call, change the line

```python
        title_en=concert.title_en, organizer=concert.organizer, categories=concert.categories,
```

to

```python
        title_en=concert.title_en, title_zh=concert.title_zh,
        organizer=concert.organizer, categories=concert.categories,
        notes_en=concert.notes_en, notes_zh=concert.notes_zh,
```

- [ ] **Step 5: Run tests, lint, commit**

Run: `uv run pytest tests/test_yaml_import.py tests/test_yaml_export.py -q` — expect PASS.

```bash
uv run ruff check .
git add src/app/domain/yaml_export.py src/app/web/routes/concerts.py tests/test_yaml_import.py tests/test_yaml_export.py
git commit -m "feat: yaml export carries title_zh + notes variants; export round-trips through parse_draft"
```

---

### Task 4: `match_tag_ids_by_name` service helper

Resolves draft tag names to ids across all three name columns. Pure sync function over an already-loaded tag list — same shape and same U+3000 reasoning as its neighbor `match_venue_tag_id` (`db/service.py:2943`).

**Files:**
- Modify: `src/app/db/service.py` (insert directly after `match_venue_tag_id`, i.e. after line 2973)
- Test: `tests/test_yaml_import.py` (pure — `Tag(...)` instantiates without a DB)

**Interfaces:**
- Produces: `match_tag_ids_by_name(names: Sequence[str], tags: Sequence[Tag]) -> tuple[list[int], list[str]]` — (matched ids, unmatched names), both in input order, ids deduplicated. Task 5 consumes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_yaml_import.py`:

```python
from app.db.models import Tag, TagKind
from app.db.service import match_tag_ids_by_name


def _tag(id_, name, name_en=None, name_zh=None):
    t = Tag(name=name, kind=TagKind.ARTIST, name_en=name_en, name_zh=name_zh)
    t.id = id_
    return t


def test_match_tag_ids_by_name_across_all_three_columns():
    tags = [
        _tag(1, "日野下花帆", name_en="Kaho Hinoshita"),
        _tag(2, "村野さやか", name_zh="村野沙耶香"),
    ]
    ids, missing = match_tag_ids_by_name(
        ["Kaho Hinoshita", "村野沙耶香", "誰それ"], tags
    )
    assert ids == [1, 2]
    assert missing == ["誰それ"]


def test_match_tag_ids_by_name_trims_and_casefolds():
    tags = [_tag(3, "Liella!", name_en="liella!")]
    ids, missing = match_tag_ids_by_name(["　LIELLA!　"], tags)
    assert ids == [3] and missing == []


def test_match_tag_ids_by_name_dedupes_ids():
    tags = [_tag(4, "Aqours", name_en="Aqours")]
    ids, missing = match_tag_ids_by_name(["Aqours", "aqours"], tags)
    assert ids == [4] and missing == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_yaml_import.py -q`
Expected: FAIL with `ImportError: cannot import name 'match_tag_ids_by_name'`

- [ ] **Step 3: Implement in `src/app/db/service.py`** (immediately after `match_venue_tag_id`)

```python
def match_tag_ids_by_name(
    names: Sequence[str], tags: Sequence[Tag]
) -> tuple[list[int], list[str]]:
    """Resolve draft-supplied tag NAMES to ids: (matched ids, unmatched names).

    The pasted-draft path's counterpart to match_venue_tag_id above, with one
    deliberate difference: it matches name_en and name_zh too, not just the
    canonical column. A draft is written by an agent that read sources in
    whichever language the site used, and every tag name here is resolved
    into a picker the editor immediately SEES (a wrong match is a lit chip
    to un-click, not a silently bound FK) -- the accidental-locale-match
    risk that keeps the venue matcher narrow doesn't apply.

    Same trim reasoning as the neighbor: Python's str.strip() drops U+3000,
    and the comparison stays in Python over the already-loaded tag list so
    SQLite's U+0020-only trim() can never be substituted in.

    Ids come back deduplicated in first-mention order; unmatched names keep
    their input order so the preview can list them verbatim.
    """
    matched: list[int] = []
    unmatched: list[str] = []
    for name in names:
        needle = name.strip().casefold()
        if not needle:
            continue
        for tag in tags:
            if any(
                col and col.strip().casefold() == needle
                for col in (tag.name, tag.name_en, tag.name_zh)
            ):
                if tag.id not in matched:
                    matched.append(tag.id)
                break
        else:
            unmatched.append(name.strip())
    return matched, unmatched
```

(`Sequence` is already imported in `db/service.py`; verify, and add `from collections.abc import Sequence` only if absent.)

- [ ] **Step 4: Run tests, lint, commit**

Run: `uv run pytest tests/test_yaml_import.py -q` — expect PASS.

```bash
uv run ruff check .
git add src/app/db/service.py tests/test_yaml_import.py
git commit -m "feat: match_tag_ids_by_name resolves draft tag names across all three name columns"
```

---

### Task 5: The web seam — paste box, draft route, prefilled preview

The biggest task: `POST /concerts/import/draft`, the paste card on the import form, and threading prefill values through `import_preview.html`. Also brings `import_commit` + the preview's Details fold to full field parity with manual creation (`eventernote_url` / `official_url` / `performers_text` / `categories`), which the draft carries.

**Files:**
- Modify: `src/app/web/routes/imports.py` (new route + per-day venue stamping in the existing preview route + new commit fields)
- Modify: `src/app/web/templates/import_form.html` (paste card)
- Modify: `src/app/web/templates/import_preview.html` (prefill values; NOT the `<template>` blocks at the bottom — cloned rows stay blank)
- Test: Create `tests/test_draft_import.py`

**Interfaces:**
- Consumes: `parse_draft`/`DraftError` (Task 2), `match_tag_ids_by_name` (Task 4), `ParsedConcert` fields (Task 1).
- Produces: `POST /concerts/import/draft` accepting form field `draft` (the YAML text). No other task consumes this; the skill (Task 6) references the URL `/concerts/import`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_draft_import.py`:

```python
"""POST /concerts/import/draft: paste a YAML draft, get a prefilled preview.

Same fixture shape as test_imports.py (which owns the URL-scrape path);
this file owns the pasted-draft path. No network anywhere -- the draft
route never fetches.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, Tag, TagKind
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID, FAN_ID = 42, 777

DRAFT = """\
title: 蓮ノ空 6thライブ
title_en: Hasunosora 6th Live
title_zh: 莲之空 6th 演唱会
kind: tour
organizer: バンナム
series:
  franchises: [Love Live!]
  artists: [日野下花帆]
eventernote_url: https://www.eventernote.com/events/465358
official_url: https://example.jp/6th/
source_url: https://example.jp/6th/ticket/
performances:
  - label: Day 1
    label_en: Day 1
    label_zh: 第1天
    venue: Kアリーナ横浜
    starts_at_jst: 2026-11-07 17:00
  - label: Day 2
    label_en: Day 2
    label_zh: 第2天
    venue: 幻の新会場
    starts_at_jst: 2026-11-08 17:00
rounds:
  - label: 最速先行
    label_en: Earliest lottery
    label_zh: 最速先行(中)
    kind: lottery_round
    applies_to: [Day 1, Day 2]
    apply_opens_jst: 2026-08-01 12:00
    apply_closes_jst: 2026-08-16 23:59
    results_jst: 2026-08-22 15:00
    payment_deadline_jst: 2026-08-25 23:00
notes: 全席指定
notes_en: All reserved
notes_zh: 全部指定席
"""


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
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


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def _seed_tags(db):
    async with db() as s:
        s.add(Tag(name="Love Live!", kind=TagKind.FRANCHISE))
        s.add(Tag(name="日野下花帆", kind=TagKind.ARTIST, name_en="Kaho Hinoshita"))
        s.add(Tag(name="Kアリーナ横浜", kind=TagKind.VENUE, city="横浜"))
        await s.commit()
        rows = list((await s.execute(select(Tag))).scalars())
        return {t.name: t.id for t in rows}


# ── Gating ───────────────────────────────────────────────────────────────


def test_anonymous_is_redirected(client):
    assert client.post("/concerts/import/draft", data={"draft": "title: X"}).status_code == 303


def test_non_editor_is_forbidden(client):
    login_as(client, FAN_ID, "fan")
    assert client.post("/concerts/import/draft", data={"draft": "title: X"}).status_code == 403


# ── The paste card on the import form ────────────────────────────────────


def test_import_form_offers_the_paste_card(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/import")
    assert r.status_code == 200
    assert 'action="/concerts/import/draft"' in r.text
    assert 'name="draft"' in r.text


# ── Preview prefill ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_draft_renders_fully_prefilled_preview(client, db):
    ids = await _seed_tags(db)
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/draft", data={"draft": DRAFT})
    assert r.status_code == 200
    text = r.text
    # trilingual title + notes trio + organizer land in the Details fold
    assert 'value="Hasunosora 6th Live"' in text
    assert 'value="莲之空 6th 演唱会"' in text
    assert ">全席指定</textarea>" in text
    assert ">All reserved</textarea>" in text
    assert 'value="バンナム"' in text
    # concert kind pre-selected
    assert '<option value="tour" selected>' in text
    # trilingual day + round labels
    assert 'value="第1天"' in text
    assert 'value="Earliest lottery"' in text
    assert 'value="最速先行(中)"' in text
    # all four round anchors
    assert 'value="2026-08-01T12:00"' in text
    assert 'value="2026-08-16T23:59"' in text
    assert 'value="2026-08-22T15:00"' in text
    assert 'value="2026-08-25T23:00"' in text
    # round applies to both legs: hidden field + both chips pressed
    assert 'name="round_legs" value="d0 d1"' in text
    assert text.count('aria-pressed="true"') >= 2
    # matched venue pre-selected on Day 1's select
    assert f'<option value="{ids["Kアリーナ横浜"]}" selected>' in text
    # unmatched venue -> visible per-leg hint, not silence
    assert "幻の新会場" in text
    # matched tags pre-selected for the picker script
    assert f'"{ids["Love Live!"]}"' in text
    assert f'"{ids["日野下花帆"]}"' in text
    # parity links prefilled
    assert 'value="https://www.eventernote.com/events/465358"' in text
    assert 'value="https://example.jp/6th/ticket/"' in text


@pytest.mark.anyio
async def test_unmatched_tag_names_render_a_hint(client, db):
    login_as(client, EDITOR_ID, "reiji")  # no tags seeded at all
    r = client.post("/concerts/import/draft", data={"draft": DRAFT})
    assert r.status_code == 200
    assert "Love Live!" in r.text  # named in the unmatched hint


def test_draft_error_rerenders_the_form(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/draft", data={"draft": "- not\n- a\n- mapping"})
    assert r.status_code == 200
    assert 'name="draft"' in r.text  # back on the form, message shown
    assert "mapping" in r.text


def test_oversized_draft_is_rejected(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/draft", data={"draft": "title: X\n" + "#x\n" * 120_000})
    assert r.status_code == 200
    assert "too large" in r.text


# ── Paste-to-commit flow (the commit route is unchanged; this proves the
#    prefilled field shape it receives is one it accepts) ─────────────────


@pytest.mark.anyio
async def test_full_paste_then_commit_flow(client, db):
    ids = await _seed_tags(db)
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "蓮ノ空 6thライブ", "title_en": "Hasunosora 6th Live",
        "title_zh": "莲之空 6th 演唱会", "kind": "tour", "organizer": "バンナム",
        "notes": "全席指定", "notes_en": "All reserved", "notes_zh": "全部指定席",
        "source_url": "https://example.jp/6th/ticket/",
        "official_url": "https://example.jp/6th/",
        "eventernote_url": "https://www.eventernote.com/events/465358",
        "performers_text": "日野下花帆",
        "franchise_tags": [ids["Love Live!"]], "artist_tags": [ids["日野下花帆"]],
        "day_key": ["d0", "d1"], "day_label": ["Day 1", "Day 2"],
        "day_label_en": ["Day 1", "Day 2"], "day_label_zh": ["第1天", "第2天"],
        "day_starts_at": ["2026-11-07T17:00", "2026-11-08T17:00"],
        "day_venue_tag_id": [str(ids["Kアリーナ横浜"]), ""],
        "round_label": ["最速先行"], "round_label_en": ["Earliest lottery"],
        "round_label_zh": ["最速先行(中)"], "round_kind": ["lottery_round"],
        "round_opens_at": ["2026-08-01T12:00"], "round_closes_at": ["2026-08-16T23:59"],
        "round_results_at": ["2026-08-22T15:00"], "round_payment_at": ["2026-08-25T23:00"],
        "round_url": ["https://eplus.jp/x/"], "round_legs": ["d0 d1"],
    })
    assert r.status_code == 303
    async with db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
        assert concert.title_zh == "莲之空 6th 演唱会"
        assert concert.eventernote_url == "https://www.eventernote.com/events/465358"
        assert concert.official_url == "https://example.jp/6th/"
        assert concert.performers_text == "日野下花帆"
```

(If the project's async tests don't use `@pytest.mark.anyio`, mirror whatever marker `tests/test_imports.py` uses for its async tests — pytest-asyncio auto mode may need none at all; copy the exact convention.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_draft_import.py -q`
Expected: FAIL — 404s on `/concerts/import/draft`, missing form markup.

- [ ] **Step 3: Add the paste card to `import_form.html`**

After the existing URL `<form>` (after line 26) insert:

```html
<div class="section-head" style="max-width:42rem; margin-top:2.25rem">
  <h2>{{ _("Or paste an agent draft") }}</h2>
  <p class="dim tiny">{% trans %}Produced by the <code>add-concert</code> skill — the agent merges the
    official ticket page and eventernote into one trilingual draft. Nothing is saved
    until you review and submit the next page.{% endtrans %}</p>
</div>

<form class="stack" method="post" action="/concerts/import/draft"
      style="max-width:42rem; margin-top:1rem; display:grid; gap:1rem">
  <label class="fld"><span>{{ _("Draft YAML") }}</span>
    <textarea name="draft" rows="10" required
              placeholder="title: …&#10;performances:&#10;  - label: Day 1"></textarea></label>
  <div><button class="btn" type="submit">{{ _("Preview draft") }}</button></div>
</form>
```

- [ ] **Step 4: Add the draft route to `routes/imports.py`**

Add imports at the top of `src/app/web/routes/imports.py`:

```python
from app.db.service import match_tag_ids_by_name
from app.domain.yaml_import import DraftError, parse_draft
```

(merge into the existing `from app.db.service import (...)` block alphabetically). Add a constant next to the fetch caps:

```python
MAX_DRAFT_CHARS = 200_000
```

Add the route after `import_preview`:

```python
@router.post("/draft", response_class=HTMLResponse)
async def import_draft(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    draft: str = Form(...),
):
    """Paste an agent-authored YAML draft, get the SAME preview the URL path
    renders -- fully prefilled. Renders only; import_commit stays the one
    write path, and every commit-boundary gate (variants rule, form_url,
    venue rollup) applies to this data exactly as to typed data.

    Tag and venue NAMES resolve to ids here, at the route boundary, the same
    place the URL path matches its scraped venue name -- the draft never
    carries database ids.
    """

    def form_error(message: str):
        return templates.TemplateResponse(
            request,
            "import_form.html",
            # lang_next_url: POST-only render, same reason as import_preview.
            {"user": user, "error": message, "lang_next_url": "/concerts/import"},
        )

    if len(draft) > MAX_DRAFT_CHARS:
        return form_error("draft too large -- pastes are capped at 200k characters")
    try:
        parsed = parse_draft(draft)
    except DraftError as e:
        return form_error(str(e))

    picker = await tag_picker_context(session)
    venue_tags = await all_venue_tags(session)

    # Per-leg venue resolution: a draft names a venue on each performance.
    for d in parsed.days:
        d.matched_venue_tag_id = match_venue_tag_id(d.venue_name, venue_tags)

    # Tag names -> picker pre-selection. Unmatched names surface as one hint
    # in the Tags fold rather than vanishing.
    initial_selected: dict[str, list[str]] = {}
    unmatched_tag_names: list[str] = []
    for kind_name, names in (
        ("franchise", parsed.franchise_names),
        ("group", parsed.group_names),
        ("artist", parsed.artist_names),
    ):
        ids, missing = match_tag_ids_by_name(names, picker["by_kind"].get(kind_name, []))
        if ids:
            initial_selected[kind_name] = [str(i) for i in ids]
        unmatched_tag_names.extend(missing)

    # applies_to leg labels -> the preview's day_key scheme ("d0", "d1", ...),
    # first row claiming a duplicate label keeps it (same rule as
    # import_commit's key_to_day_id).
    label_to_key: dict[str, str] = {}
    for i, d in enumerate(parsed.days):
        label_to_key.setdefault(d.label.strip(), f"d{i}")
    for r in parsed.rounds:
        keys = []
        for lbl in r.applies_to_labels:
            key = label_to_key.get(lbl.strip())
            if key is None:
                parsed.warnings.append(
                    f"round {r.label!r}: no performance labelled {lbl!r} -- "
                    "that leg reference was dropped, tick it by hand"
                )
            else:
                keys.append(key)
        r.leg_keys = " ".join(keys)
        r.leg_keys_selected = set(keys)

    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {
            "user": user, "parsed": parsed,
            "source_url": parsed.source_url or "",
            "lang_next_url": "/concerts/import",
            "fmt": _fmt, "kinds": list(RoundKind),
            "concert_kinds": list(ConcertKind),
            "by_kind": picker["by_kind"],
            "groups": picker["groups"],
            "tag_names": picker["tag_names"],
            "initial_selected": initial_selected,
            "venue_tags": venue_tags,
            "round_phrases": await round_label_phrases(session),
            # Event-level venue hint is the URL path's concept; drafts carry
            # venues per leg and hint per leg instead.
            "venue_hint": None,
            "matched_venue_tag_id": None,
            "legs": _preview_legs(parsed),
            "unmatched_tag_names": unmatched_tag_names,
        },
    )
```

- [ ] **Step 5: Stamp per-day venue in the EXISTING preview route, and pass the new context key**

In `import_preview` (the URL path), after `venue_tags = await all_venue_tags(session)` add:

```python
    # One scraped venue for the whole event: stamp the match onto every day so
    # the template reads a single per-day attribute for both paths.
    matched = match_venue_tag_id(parsed.venue_name, venue_tags)
    for d in parsed.days:
        d.matched_venue_tag_id = matched
```

and in its context dict change `"matched_venue_tag_id": match_venue_tag_id(parsed.venue_name, venue_tags),` to `"matched_venue_tag_id": matched,` and add `"unmatched_tag_names": [],`.

- [ ] **Step 6: Thread prefills through `import_preview.html`**

All edits are in the top (rendered) sections — the two `<template>` blocks at the bottom stay untouched (cloned rows start blank).

a. Lede (line 16-18): make the source line conditional:

```html
  <p>{% if source_url %}{% set source_link %}<a href="{{ source_url }}" target="_blank" rel="noopener">{{ source_url }}</a>{% endset %}{% trans url=source_link %}Parsed from
    {{ url }}.{% endtrans %}{% else %}{{ _("Reviewing a pasted draft.") }}{% endif %}
    {% trans %}Edit anything below, then create the concert.{% endtrans %}</p>
```

b. Concert kind select (line 54): pre-select the draft's kind:

```html
        {% for k in concert_kinds %}<option value="{{ k.value }}"{% if parsed.kind == k %} selected{% endif %}>{{ k.value.replace("_", " ") | capitalize }}</option>{% endfor %}
```

c. Day label variants (lines 86-90): add values (ramen path renders `None or ''` = blank, same as today):

```html
        <label class="vfld"><span class="vfld-tag">English</span>
          <input name="day_label_en" maxlength="100" placeholder="{{ _('Day 1') }}"
                 value="{{ d.label_en or '' }}"
                 data-variant-group="day_label" data-variant-slot="en"></label>
        <label class="vfld"><span class="vfld-tag">中文</span>
          <input name="day_label_zh" maxlength="100" placeholder="{{ _('Day 1') }}"
                 value="{{ d.label_zh or '' }}"
                 data-variant-group="day_label" data-variant-slot="zh"></label>
```

Also delete the now-stale comment block at lines 83-84 ("No `value=`: the ramen.events parse produces a Japanese label only...").

d. Per-day venue select (line 114): switch from the global to the per-day match:

```html
          <option value="{{ v.id }}"{% if d.matched_venue_tag_id == v.id %} selected{% endif %}>{{ loc(v, "name") }}</option>
```

e. Per-leg unmatched-venue hint + doors prefill (lines 117-120): after the `+ New venue` button's line, extend the fields:

```html
        {% if d.venue_name and not d.matched_venue_tag_id %}
        <span class="dim tiny">{% trans venue=d.venue_name %}No venue tag matches “{{ venue }}” yet — add it with + New venue.{% endtrans %}</span>
        {% endif %}
        <label>{{ _("Doors (JST)") }} <input type="datetime-local" name="day_doors_at"
          value="{{ fmt(d.doors_at_jst) }}"></label>
```

(The `{% trans %}` body is byte-identical to the event-level hint at line 69 — reusing the msgid, no new catalogue entry.)

f. Round label variants (lines 147-152): add values:

```html
        <label class="vfld"><span class="vfld-tag">English</span>
          <input name="round_label_en" maxlength="200" placeholder="{{ _('1st-round advance lottery') }}"
                 value="{{ r.label_en or '' }}"
                 data-variant-group="round_label" data-variant-slot="en"></label>
        <label class="vfld"><span class="vfld-tag">中文</span>
          <input name="round_label_zh" maxlength="200" placeholder="{{ _('1st-round advance lottery') }}"
                 value="{{ r.label_zh or '' }}"
                 data-variant-group="round_label" data-variant-slot="zh"></label>
```

g. Round leg chips (line 163): feed the resolved keys (ramen rounds carry `""`/empty set — behavior unchanged):

```html
      {% with value = r.leg_keys, selected = r.leg_keys_selected or none %}{% include "_round_leg_chips.html" %}{% endwith %}
```

h. Round times + notes (lines 169-174): prefill results/payment/notes:

```html
        <label>{{ _("Results (JST)") }} <input type="datetime-local" name="round_results_at"
          value="{{ fmt(r.results_at_jst) }}"></label>
        <label>{{ _("Payment deadline (JST)") }} <input type="datetime-local" name="round_payment_at"
          value="{{ fmt(r.payment_at_jst) }}"></label>
      </div>
      <div class="redit-extra">
        <input name="round_url" type="url" placeholder="{{ _('Apply URL (optional)') }}" value="{{ r.url or '' }}">
        <input name="round_notes" maxlength="300" placeholder="{{ _('Notes (optional)') }}" value="{{ r.notes or '' }}">
      </div>
```

i. Details fold (lines 195-219): prefill everything and add the four parity fields (names/labels copied from `concert_new.html` so the msgids already exist):

```html
      <div class="grid2">
        <label>{{ _("Title (EN)") }} <input name="title_en" maxlength="200"
          value="{{ parsed.title_en or '' }}"
          data-variant-group="title" data-variant-slot="en"></label>
        <label>{{ _("Title (中文)") }} <input name="title_zh" maxlength="200"
          value="{{ parsed.title_zh or '' }}"
          data-variant-group="title" data-variant-slot="zh"></label>
        <label>{{ _("Artist / organizer") }} <input name="organizer" maxlength="200"
          value="{{ parsed.organizer or '' }}" placeholder="{{ _('non-series acts, etc.') }}"></label>
        <label>{{ _("Categories") }} <input name="categories" maxlength="200"
          value="{{ parsed.categories or '' }}"></label>
        <label>{{ _("eventernote URL") }} <input name="eventernote_url" type="url"
          value="{{ parsed.eventernote_url or '' }}"></label>
        <label>{{ _("Official URL") }} <input name="official_url" type="url"
          value="{{ parsed.official_url or '' }}"></label>
        <label>{{ _("Source URL") }} <input name="source_url" type="url" value="{{ source_url }}"
          placeholder="{{ _('where this was sourced from') }}"></label>
      </div>
      <label>{{ _("Performers (one per line)") }} <textarea name="performers_text"
        rows="3">{{ parsed.performers_text or '' }}</textarea></label>
      <label>{{ _("Notes") }} <textarea name="notes" rows="2"
        data-variant-group="notes" data-variant-slot="ja"
        data-variant-label="{{ _('Notes') }}">{{ parsed.notes or '' }}</textarea></label>
      <label>{{ _("Notes (EN)") }} <textarea name="notes_en" rows="2"
        data-variant-group="notes" data-variant-slot="en">{{ parsed.notes_en or '' }}</textarea></label>
      <label>{{ _("Notes (中文)") }} <textarea name="notes_zh" rows="2"
        data-variant-group="notes" data-variant-slot="zh">{{ parsed.notes_zh or '' }}</textarea></label>
```

Check `concert_new.html` for the exact existing msgids of "Categories" / "eventernote URL" / "Official URL" / "Performers (one per line)" and copy them byte-identically; if any differs (e.g. no "Categories" label exists), match whatever `concert_new.html` uses.

j. Unmatched-tag hint in the Tags fold (before the `{% include "_tag_picker_fields.html" %}` at line 230):

```html
      {% if unmatched_tag_names %}
      <p class="dim tiny">{% trans names=unmatched_tag_names | join(", ") %}No tag yet for: {{ names }} — create them on the <a href="/tags">tags page</a>, then re-paste, or pick by hand.{% endtrans %}</p>
      {% endif %}
```

- [ ] **Step 7: Bring `import_commit` to field parity**

In `import_commit`'s signature, after `source_url: str = Form(default=""),` add:

```python
    eventernote_url: str = Form(default=""),
    official_url: str = Form(default=""),
    performers_text: str = Form(default=""),
```

After `concert.categories = categories.strip() or None` add (mirroring `create_concert` at `routes/concerts.py:668-671`):

```python
    concert.eventernote_url = form_url(eventernote_url)
    concert.official_url = form_url(official_url)
    concert.performers_text = performers_text.strip() or None
```

- [ ] **Step 8: Run the new tests, then the full suite**

Run: `uv run pytest tests/test_draft_import.py -q` — expect PASS.
Run: `uv run pytest -q` — expect PASS (test_imports.py exercises the modified preview route/template; test_variant_enforcement's census is satisfied because the preview form already carries `data-variant-guard` and no new create boundary was added).

- [ ] **Step 9: Lint and commit**

```bash
uv run ruff check .
git add src/app/web/routes/imports.py src/app/web/templates/import_form.html src/app/web/templates/import_preview.html tests/test_draft_import.py
git commit -m "feat: paste-a-draft import path -- POST /concerts/import/draft renders the prefilled preview"
```

---

### Task 6: The `add-concert` skill

The agent-side half. A project skill plus a reference example draft, with a test pinning the example to the parser so skill/schema drift fails CI.

**Files:**
- Create: `.claude/skills/add-concert/SKILL.md`
- Create: `.claude/skills/add-concert/references/example-draft.yaml`
- Test: `tests/test_yaml_import.py` (one drift-guard test)

**Interfaces:**
- Consumes: the draft schema (Tasks 2-3), the paste URL (Task 5).
- Produces: nothing code-level; the skill is documentation the agent executes.

- [ ] **Step 1: Write the failing drift-guard test**

Append to `tests/test_yaml_import.py`:

```python
from pathlib import Path

SKILL_EXAMPLE = (
    Path(__file__).parent.parent / ".claude" / "skills" / "add-concert"
    / "references" / "example-draft.yaml"
)


def test_skill_example_draft_parses_clean():
    """The example the add-concert skill shows agents MUST parse with zero
    warnings -- a warning here means the skill and parser have drifted."""
    p = parse_draft(SKILL_EXAMPLE.read_text(encoding="utf-8"))
    assert p.warnings == []
    assert p.title and p.title_en and p.title_zh
    assert p.days and p.rounds
    assert all(d.venue_name for d in p.days)
    assert all(r.label_en and r.label_zh for r in p.rounds)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_yaml_import.py::test_skill_example_draft_parses_clean -q`
Expected: FAIL with `FileNotFoundError`

- [ ] **Step 3: Create `references/example-draft.yaml`**

```yaml
# A complete draft the add-concert skill produces. Paste-ready at
# https://dekimasen.app/concerts/import -- every field here is optional
# except title, but a field you fill must respect the trilingual rule
# (ja+en+zh or none for title/notes and every label).
title: 蓮ノ空女学院スクールアイドルクラブ 6thライブツアー
title_en: Hasunosora Jogakuin School Idol Club 6th Live Tour
title_zh: 莲之空女学院学园偶像社 6th 演唱会巡回
kind: tour
organizer: バンダイナムコミュージックライブ
categories: anime song
series:
  franchises: [Love Live!]
  groups: [蓮ノ空女学院スクールアイドルクラブ]
  artists: [日野下花帆, 村野さやか, 乙宗梢]
performers: [日野下花帆, 村野さやか, 乙宗梢]
eventernote_url: https://www.eventernote.com/events/465358
official_url: https://www.lovelive-anime.jp/hasunosora/
source_url: https://www.lovelive-anime.jp/hasunosora/live-event/live_detail.php?p=6th
performances:
  - label: Day 1
    label_en: Day 1
    label_zh: 第1天
    venue: Kアリーナ横浜
    city: 横浜
    venue_address: 神奈川県横浜市西区みなとみらい6-2-14
    doors_jst: 2026-11-07 15:30
    starts_at_jst: 2026-11-07 17:00
  - label: Day 2
    label_en: Day 2
    label_zh: 第2天
    venue: Kアリーナ横浜
    city: 横浜
    doors_jst: 2026-11-08 15:30
    starts_at_jst: 2026-11-08 17:00
rounds:
  - label: 最速先行抽選（CD封入シリアル）
    label_en: Earliest advance lottery (CD serial code)
    label_zh: 最速先行抽选（CD附带序列号）
    kind: lottery_round
    applies_to: [Day 1, Day 2]
    apply_opens_jst: 2026-08-01 12:00
    apply_closes_jst: 2026-08-16 23:59
    results_jst: 2026-08-22 15:00
    payment_deadline_jst: 2026-08-25 23:00
    url: https://eplus.jp/hasunosora-6th/
    notes: 1次シリアル先行。CD初回限定盤に封入。
  - label: 一般発売（先着）
    label_en: General sale (first come, first served)
    label_zh: 一般发售（先到先得）
    kind: fcfs_sale
    applies_to: []
    apply_opens_jst: 2026-10-24 12:00
    url: https://eplus.jp/hasunosora-6th/
notes: 全席指定。
notes_en: All seats reserved.
notes_zh: 全部为指定席。
```

- [ ] **Step 4: Create `SKILL.md`**

```markdown
---
name: add-concert
description: Build a paste-ready trilingual concert draft for dekimasen.app from source URLs (official ticket pages, eventernote, ramen.events). Use when the owner says "add this concert", "import this event", gives concert/live event URLs, or asks to draft a new event for the tracker.
---

# Add a concert from source URLs

Turn one or more source pages into ONE YAML draft the owner pastes at
`https://dekimasen.app/concerts/import` (the "Or paste an agent draft"
box). The app renders a prefilled review form; nothing is saved until the
owner submits it, so your draft is a proposal, not a write.

The schema is `references/example-draft.yaml` in this skill directory --
read it first, copy its shape exactly. It is pinned by a test
(`tests/test_yaml_import.py::test_skill_example_draft_parses_clean`);
if the app evolves, that example is the current truth.

## 1. Gather sources -- roles differ

| Source | Authority for | Never trust it for |
|---|---|---|
| Official site's TICKET page | rounds: 先行 names, windows, results, payment, prices | -- |
| eventernote.com event pages | per-LEG facts: date, venue, doors/start, cast | rounds (it has none) |
| ramen.events post | convenience cross-check | completeness |

**One eventernote event page = ONE LEG, not one concert.** A tour is one
concert with several performances; collect every leg's eventernote page
(the artist's `/actors/<name>/<id>/events` page lists them) and merge
them into a single draft's `performances` list. Never emit one draft per
eventernote page.

Official sites often split per-leg ticket info into subpages (e.g.
`/information/final.php`) -- follow the TICKET / チケット navigation until
you find actual application windows.

## 2. Fetching

- WebFetch first.
- On 403 (lovelive-anime.jp blocks every non-browser client), fall back to
  Claude-in-Chrome through the owner's signed-in browser: call
  `tabs_context_mcp` first, open the page in a new tab, read it with
  `get_page_text`.
- If a page is unreachable both ways, say so and continue with what you
  have -- an incomplete draft with a note beats an invented one.

## 3. Extraction rules

- **Times are JST wall-clock**, formatted `YYYY-MM-DD HH:MM`. Japanese
  sources write 23:59 as-is but may write 27:00 for 3am next day --
  normalize to the real calendar day.
- **Never invent a time.** If a source gives only a date ("8月中旬"), omit
  the field and mention it under `notes` so the owner sees it.
- Round kinds (the `kind` value strings):
  - 抽選 / 先行 / 最速先行 / 次先行 -> `lottery_round`
  - CD封入シリアル / シリアル対象商品の発売 -> the round is still
    `lottery_round`; if the CD sale itself is listed, `eligibility_item_sale`
  - 一般発売 that is explicitly 先着 (first come) -> `fcfs_sale`
  - 一般発売 that is itself a lottery -> `general_sale`
  - 配信 / streaming tickets -> `stream_ticket_sale`
  - overseas hotel+ticket packages -> `tour_package`
  - アップグレード (needs an existing ticket) -> do NOT emit; upgrade rounds
    have qualifier semantics the import path doesn't carry -- note it in
    `notes` for the owner to add by hand.
- 当落発表 / results and 入金期限 / payment are ANCHORS on their lottery
  round (`results_jst`, `payment_deadline_jst`), not separate rounds.
- `applies_to`: the exact `label` strings of the performances a round
  covers. Empty list = whole event. A round selling "全公演" or with no
  per-leg distinction gets `[]`.

## 4. Trilingual rules (the app enforces these at submit)

- Japanese is canonical. For the title, notes and EVERY performance/round
  label you fill, provide all three of ja/en/zh -- or none of the three.
- Translate faithfully and plainly; keep proper nouns (venue names, fan
  club names, retailer names like ファミリーマート) recognizable --
  established romanizations for en, established fan translations for zh
  where they exist.
- Venue names: use the JAPANESE canonical name in `venue` (it must match
  the app's VENUE tag names, which are canonical Japanese).

## 5. Tags

- `series.franchises` / `series.groups`: the franchise and unit names as
  the app's Tags page spells them (ask the owner or check
  https://dekimasen.app/tags if unsure).
- `series.artists`: list the PERFORMERS explicitly (from eventernote's
  cast list) -- group tags do not auto-expand on this path, and the cast
  actually announced is the truth anyway.
- `performers`: the same cast list, one name per entry (this fills the
  free-text performers field).

## 6. Emit and hand off

- Output the complete YAML in ONE fenced block, nothing else in it.
- After the block, list anything uncertain or missing (unfetchable page,
  date-only deadline, guessed kind) as bullet points.
- Tell the owner: paste it at https://dekimasen.app/concerts/import --
  unmatched tag/venue names show as hints there, venues can be created
  inline with "+ New venue", and nothing is saved until "Create concert".
```

- [ ] **Step 5: Run tests, lint, commit**

Run: `uv run pytest tests/test_yaml_import.py -q` — expect PASS.

```bash
uv run ruff check .
git add .claude/skills/add-concert tests/test_yaml_import.py
git commit -m "feat: add-concert skill -- agent workflow for building paste-ready trilingual drafts"
```

---

### Task 7: i18n catalogues for the new strings

New msgids from Task 5: `"Or paste an agent draft"`, the paste-card explainer (`"Produced by the <code>add-concert</code> skill — ..."`), `"Draft YAML"`, `"Preview draft"`, `"Reviewing a pasted draft."`, the unmatched-tags hint (`"No tag yet for: %(names)s — ..."`), plus `"Categories"` IF `concert_new.html` didn't already define it. Reused msgids (the per-leg venue hint, all Details-fold labels) must have stayed byte-identical — the catalogue test proves it.

**Files:**
- Modify: `src/app/translations/ja/LC_MESSAGES/messages.po`
- Modify: `src/app/translations/zh/LC_MESSAGES/messages.po`
- Test: `tests/test_i18n_catalogues.py` (existing — must pass)

- [ ] **Step 1: Confirm the catalogue test currently fails**

Run: `uv run pytest tests/test_i18n_catalogues.py -q`
Expected: FAIL, naming exactly the new msgids from Task 5. (If it PASSES, Task 5 introduced no new msgids — skip to Step 4.)

- [ ] **Step 2: Extract and update**

```bash
uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run pybabel update -i messages.pot -d src/app/translations -l ja
uv run pybabel update -i messages.pot -d src/app/translations -l zh
```

- [ ] **Step 3: Fill in the new/fuzzy msgstrs by hand in BOTH .po files**

Translate each new msgid (ja and zh). Suggested translations — review in context:

| msgid | ja | zh |
|---|---|---|
| Or paste an agent draft | またはエージェントのドラフトを貼り付け | 或粘贴代理生成的草稿 |
| Draft YAML | ドラフト YAML | 草稿 YAML |
| Preview draft | ドラフトをプレビュー | 预览草稿 |
| Reviewing a pasted draft. | 貼り付けたドラフトを確認しています。 | 正在审阅粘贴的草稿。 |

For the two longer `{% trans %}` strings, keep placeholders (`%(names)s`) and inline HTML tags exactly as extracted. Remove any `#, fuzzy` markers after verifying those entries (fuzzy counts as untranslated). Then delete `messages.pot` (gitignored, regenerable).

- [ ] **Step 4: Run the catalogue test and full suite**

Run: `uv run pytest tests/test_i18n_catalogues.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check .
git add src/app/translations
git commit -m "i18n: catalogue entries for the paste-a-draft import strings"
```

---

### Task 8: Docs — CLAUDE.md, WISHLIST, onboarding demo

**Files:**
- Modify: `CLAUDE.md` (the `routes/imports.py` paragraph in Layout)
- Modify: `WISHLIST.md` (Shipped entry + two new Proposed entries + revision-pass note)
- Modify: `docs/superpowers/demo/dekimasen-onboarding-demo.html` (paste card on the import frame)

- [ ] **Step 1: CLAUDE.md**

In the Layout section's `routes/imports.py` paragraph, after the sentence ending "...the flat import form could not express a round spanning more than one leg." insert:

```
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
```

- [ ] **Step 2: WISHLIST.md**

Add to the Shipped section (top), dated 2026-07-22, an entry titled "Agent-driven concert import (YAML draft round-trip + add-concert skill)" summarizing: the trilingual arc made manual creation ~3x the typing; the fix is agent-side (no API budget), a paste-a-draft seam rendering the existing preview prefilled, export made two-way, tag/venue name resolution with visible unmatched hints, commit path untouched, and the add-concert skill with its drift-guard test. Then run the required revision pass over Proposed and record its outcome in the header notes; at minimum:

- Add new Proposed entry "Eventernote actor-page discovery" (impact: medium, effort: small now that the skill exists) — the skill or a scheduled agent walks followed artists' `/actors/<id>/events` pages and flags concerts not yet in the app; raised 2026-07-22 during the import design discussion.
- Add new Proposed entry "In-app LLM extraction behind the same draft seam" (impact: low-medium, effort: medium, blocked on API budget) — the seam is producer-agnostic by design; raised and deliberately deferred 2026-07-22 (owner: no budget for per-import API calls).
- Re-review existing #1 (minute-level offsets): unchanged by this build — note that.

- [ ] **Step 3: Onboarding demo**

In `docs/superpowers/demo/dekimasen-onboarding-demo.html`, find the import screen frame (search for "Import from a URL" or `ramen.events`) and add below its URL form a second card matching Task 5's paste card: an `h2`-equivalent "Or paste an agent draft", a short dim explainer line, a 10-row textarea placeholder and a "Preview draft" button, using the demo's existing card/label/button classes (copy the URL form card's markup shape). Keep it static — no JS.

- [ ] **Step 4: Full suite, lint, commit**

Run: `uv run pytest -q` and `uv run ruff check .` — both clean.

```bash
git add CLAUDE.md WISHLIST.md docs/superpowers/demo/dekimasen-onboarding-demo.html
git commit -m "docs: record the agent-import build -- CLAUDE.md, WISHLIST revision pass, onboarding demo paste card"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest -q` — full suite green.
- [ ] `uv run ruff check .` — clean.
- [ ] Manual smoke: `uv run python -m app.main` (web-only), sign in as editor, `/concerts/import`, paste `.claude/skills/add-concert/references/example-draft.yaml`'s content, confirm the preview arrives prefilled end to end, and submit it against a dev DB.
