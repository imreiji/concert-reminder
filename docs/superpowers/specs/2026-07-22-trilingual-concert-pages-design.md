# Trilingual concert pages — design

Date: 2026-07-22
Status: approved, pending implementation plan

## Problem

The i18n build shipped catalogue translation (static UI text) and a partial
UGC translation layer: `loc_field` plus `_en`/`_zh` columns on `Concert.title`,
`Concert.venue`, `Concert.notes` and `Tag.name`. A concert page viewed in
English or Chinese still renders substantial Japanese, because the layer stops
short of the fields the page actually leans on:

- `ConcertDay` has **zero** translation columns. Its `label` ("2日目 夜公演"),
  `city`, `venue` and `venue_address` render raw to every viewer.
- `Round` is asymmetric: `label_en` exists, `label_zh` does not. Worse,
  `label_en` predates `loc_field` — it is a pre-i18n English gloss rendered to
  *every* viewer at once (`_round_rows.html:52`), not a locale-selected variant.
- Nothing is required. Every variant is nullable and optional, so a concert can
  be created entirely in Japanese and silently stay that way.
- The tag **create** dialog omits `name_en`/`name_zh` even though `create_tag`
  accepts them (`tags.py:99-100`) — a new tag must be created, then reopened in
  the edit dialog to add variants.

## Goal

An editor creates a concert once and it renders correctly in all three
languages, sharing one `event_id`. There are no per-language concert rows: the
concert is one row whose text fields each carry three variants, exactly as the
existing `loc_field` scheme already models (`ja` = the original column, since
Japanese is the source of truth).

## Non-goals

- Details-and-links fields (`organizer`, `categories`, `performers_text`,
  `franchise`) — deferred, revisit later.
- Machine translation. All variants are hand-entered.
- Reshaping `RoundKind` (see Findings).
- Franchise-aware round-label suggestions. The phrase library makes this
  cheap later; it is not built now.

---

## Findings that shaped the design

These were established by reading the code during design and are recorded
because they are load-bearing and non-obvious.

### `Concert.venue` is a stale, write-once fallback

It is derived, never typed — there is no venue input on the concert form. It is
written **once at creation** (`concerts.py:471`) as
`", ".join(t.name for t in v_tags)`. The edit route recomputes `v_tags` but
**never re-derives `concert.venue`** (`concerts.py:990` assigns only
`venue_en`/`venue_zh`). Change a concert's venue tags after creation and the
string goes stale permanently. This is a live bug independent of this work.

Every consumer already treats it as a legacy fallback, with a uniform shape:

```python
venue=(_("Multiple") if len(venue_tags) > 1
       else (venue_tags[0] if venue_tags
             else loc_field(concert, "venue", get_locale())))   # only if NO tag
```

Sites: `service.py:1506-1533` (`DeadlineRow.venue`), `:1590-1598`
(`_setup_tile_venue`), `:3342-3358` (`NoticeContext.venue`), `discover.py:119-132`
(search fallback). YAML export uses tag names, not this string; ICS export has
no venue field at all.

Consequence: `venue_en`/`venue_zh` are a trap. An editor types an English venue
name; `venue` is regenerated from tags while `venue_en` is not; they drift and
the English viewer sees the stale one.

### The leg→venue-tag link already exists, as a string match

`concerts.py:723` calls `find_venue_tag(venue_tags, day.venue)`, which loads
*every* VENUE tag in the DB and matches the day's free-text venue against
`Tag.name` case-insensitively (`concerts.py:370-380`). Making this a real FK
formalises what the code already attempts; it is not a new relationship.

### `RoundKind` is almost entirely cosmetic

Of ten members, exactly one — `UPGRADE` — carries behaviour (eligibility gate,
suppression exemption at `service.py:287-306`, auto-arm guards at `:385-417`,
board column rank, deadline-row dropping, capture gating). There is no `match`
or `if` over any other kind anywhere in `src/`. The reminder planner never sees
the kind at all: `RoundInfo` (`domain/reminders.py:32-37`) deliberately does not
copy it.

`RESULT_ANNOUNCEMENT` and `PAYMENT_DEADLINE` do not even control whether a round
*has* a results or payment moment — that is `results_at_utc` /
`payment_deadline_at_utc` being non-null.

**Not acted on here.** `RoundKind` is already trilingual via `LABEL_BY_ROUND_KIND`
(`service.py:831-842`, each entry `N_()`-marked), so it does not block this work.
Logged in `WISHLIST.md` as a separate cleanup.

### Real round labels do not decompose

An ordinal enum (最速 / 1次 / 2次 …) was designed and then rejected against real
data. Hasunosora 6th Live decomposes cleanly (5 of 7 rounds), but Liella! does
not (0 of 9):

| Label | kind | ordinal | leftover |
|---|---|---|---|
| 最速先行抽選 | lottery | 最速 | — |
| 一般発売（一次抽選） | general sale | 1次 | — |
| 一般発売（先着） | fcfs | — | — |
| 当日券販売（先着） | fcfs | — | **当日券** |
| 「Liella! CLUB 2025/2026」最速先行 | lottery | 最速 | **「Liella! CLUB 2025/2026」** |
| いち早プレリザーブ | lottery | — | **whole label** |
| オフィシャル2次抽選 | lottery | 2次 | **オフィシャル** |
| ファミリーマート先行 | lottery | — | **ファミリーマート** |
| オフィシャル最終抽選 | lottery | 最終 | **オフィシャル** |

Three conclusions:

1. The missing axis is **channel** (オフィシャル, ファミリーマート,
   「Liella! CLUB 2025」, プレオーダー, 当日券), not ordinal — and channels are
   proper nouns, which no enum can enumerate.
2. Ordinals are **sparse**: Liella! runs 2次 → 3次 → **5次** → 最終.
3. `RoundKind` conflates two axes: 一般発売（一次抽選） and 一般発売（先着） are
   both 一般発売 with different mechanisms, yet `GENERAL_SALE` and `FCFS_SALE`
   are separate members.

The decisive observation is that オフィシャル appears **five times on one
concert**. The problem is reuse, not taxonomy — which is what the phrase library
addresses.

---

## Design

### 1. Schema

```
Tag           + city, city_en, city_zh   String(100) nullable   (VENUE)
              + address                  String(500) nullable   (VENUE)
                name/name_en/name_zh, region, location_url      (exist)

ConcertDay    + venue_tag_id  FK -> tags.id, nullable, ON DELETE SET NULL
              + label_en, label_zh       String(100) nullable
              - city, venue, venue_address

Round         + label_zh                 String(200) nullable
                label, label_en                                 (exist)

Concert       - venue, venue_en, venue_zh

NEW  RoundLabelPhrase
       id, ja, en, zh, used_count, created_at
       unique on (ja, en, zh)
```

`city` moves onto the VENUE tag because a venue is always in exactly one city —
it is a property of the venue, not of a leg. It needs variants (横浜 / Yokohama /
横滨). `address` stays a single untranslated field: its job is to be pasted into
a map, and the tag already carries `location_url` for the maps link.

`Round.label_en` changes meaning — from an English gloss shown to everyone into a
true locale variant selected by `loc_field`. Existing data survives unchanged
because the values are already English.

### 2. Venue rollup

On every concert save (create, edit, and import commit), the VENUE rows in
`concert_tags` are rewritten as the union of the legs' `venue_tag_id`. This:

- makes the leg the single place a venue is entered, so the two levels cannot
  disagree;
- fixes the write-once staleness bug as a side effect;
- leaves **Discover untouched** — its region filter is client-side off each
  tile's `data-tags`, which reads `concert_tags` (`discover.py:153-155`,
  `discover.html:39`). Keep writing `concert_tags` and nothing downstream moves.

Every consumer of the old `Concert.venue` string switches to the tag-derived
path it already prefers. The `"Multiple"` collapse for >1 venue is preserved.

### 3. Display fixes

| Site | Fix |
|---|---|
| `_round_rows.html:52` | Render `loc(r, "label")`; stop showing `label_en` to every viewer |
| `concert_detail.html:29` | Guard is `{% if concert.notes %}`, so notes filled only in EN/ZH render nothing. Guard on the resolved value |
| `_round_rows.html:130` | Venue, city, address from the tag, trilingual |
| `_round_leg_chips.html:32` | Fallback becomes `label -> venue tag name -> date` (`city` is gone) |
| `_leg_chips_script.html:45` | Client-side mirror of the same fallback |
| `service.py:3341-3342` | `notice_context` builds `tags_line` from raw `t.name`; DM tag lines are untranslated |
| Day label | Render via `loc(day, "label")` |

### 4. Inline venue creation

The leg editor gains a "+ New venue" affordance opening a `<dialog>` on the
existing picker pattern (header + ×, no footer, backdrop-click and Esc close,
bottom sheet under 700px per the mobile retrofit rules).

Fields: `name`, `name_en`, `name_zh` (all required), plus optional `region`,
`city`/`city_en`/`city_zh`, `address`, `location_url`.

POSTs to a new endpoint, returns the created tag, selects it into the leg
without leaving the concert form. This removes the current dead end — the tag
picker today says "Create it on the tags page first"
(`_tag_picker_fields.html`).

Business logic goes in `db/service.py`; the route is a thin shell.

### 5. Round label phrase library

The round label stays free text but becomes trilingual, backed by reuse rather
than taxonomy.

- On save, any round with all three variants filled records the triple in
  `RoundLabelPhrase` (upserting `used_count`).
- The round label field offers those triples as an autocomplete. Picking one
  fills all three fields at once, all still editable.
- Ranked by `used_count` then recency.

This handles 最速先行抽選 and いち早プレリザーブ equally well because it never
tries to parse the label. Liella!'s five オフィシャル rounds cost one translation
and four clicks.

Franchise-aware ranking (rank suggestions by concerts sharing a franchise tag)
is a natural later extension and deliberately out of scope.

### 6. Enforcement

The rule is **all-or-nothing per field**, not "every field must be filled":

> If any variant of a field is filled, all three must be. A field left entirely
> blank across all three stays blank.

This distinction matters because `ConcertDay.label` and `Round.label` are
`Mapped[str]` (NOT NULL) but are empty strings in practice — the form marks
neither as required, and `concerts.py:653-655` uses a blank `label.strip()` as
its skip-this-row guard. A flat "no blank variants" rule would break saving a
round that legitimately has no label, and would break the free-text back door
that exists precisely so an unusual label is *optional*.

| Path | Behaviour |
|---|---|
| Create — `Tag.name`, `Concert.title` (genuinely mandatory) | All three always required; 422 otherwise |
| Create — `label`, `notes` (optional fields) | All blank is fine; any one filled means 422 unless all three are |
| Edit of a record with partial variants | Warning banner naming exactly what is missing; **saves anyway** |
| Tags page | Counter of how many tags are still untranslated |

Chosen so nothing blocks the ship: existing rows keep rendering through
`loc_field`'s fallback and are cleared down deliberately rather than at the
moment an editor wanted to fix a typo.

The tag **create** dialog must expose `name_en`/`name_zh` — today it omits them
while the route already accepts them.

---

## Migration

**Two deploys.** Dropping free-text venue data in the same deploy that backfills
it means an unmatched venue is gone with no way back.

**Migration 1 — add and backfill**

1. Add every new column and the `RoundLabelPhrase` table.
2. Backfill `ConcertDay.venue_tag_id` by reusing the existing `find_venue_tag`
   matcher — the current string-matching logic becomes the migration.
3. **Report every unmatched `day.venue`** rather than silently nulling it.
4. Leave the old columns in place.

Verify on live data that every leg matched before proceeding.

**Migration 2 — drop (a later deploy)**

Drop `Concert.venue`, `venue_en`, `venue_zh`, `ConcertDay.city`, `venue`,
`venue_address`.

### Migration safety — legacy anonymous constraints

This touches `concerts` and `tags`, which are exactly the tables `CLAUDE.md`
flags as predating the `NAMING_CONVENTION`. They carry anonymous constraints on
the live server while every test DB is built from `Base.metadata` and is fully
named — so a `drop_constraint` migration **passes locally and dies on the
server**. This has shipped once.

Required:

- pass `naming_convention=NAMING_CONVENTION` into every `batch_alter_table`;
- add a legacy-shaped fixture test alongside
  `tests/test_migration_legacy_anonymous_constraints.py`, hand-writing the real
  server DDL for the tables touched here (its existing fixture covers only the
  four tables its own migration touched);
- after autogenerate, replace `app.db.models.UTCDateTime()` with
  `sa.DateTime()` and drop the `import app.db.models` line.

---

## Testing

- `loc_field` resolution over every new field (`ConcertDay.label`, `Round.label`,
  `Tag.city`).
- A logged-in GET render test for every touched page, in all three locales —
  `CLAUDE.md` notes a missing one shipped a 500 once.
- Concert detail renders no Japanese for an EN/ZH viewer on a fully translated
  concert (the assertion the current suite lacks — `test_i18n_ugc.py` has only
  one display test, on Discover).
- The notes guard: notes filled only in `notes_en` render for an EN viewer.
- Venue rollup: editing a leg's venue tag updates `concert_tags`, and Discover's
  region filter still matches (the regression this design's rollup exists to
  prevent).
- Create rejects blank variants with 422; edit of a legacy record saves and
  surfaces the warning.
- Phrase library: a saved trilingual round label becomes a suggestion; picking
  one fills all three.
- Migration 1 against a legacy-shaped DDL fixture; unmatched venues reported.
- `tests/test_i18n_catalogues.py` must stay green — every new `_()`/`N_()`
  string needs both `messages.po` files filled (fuzzy counts as untranslated).

## Catalogue work

Per `CLAUDE.md`: `pybabel extract` then `pybabel update` for `ja` and `zh`, fill
the new msgstrs by hand in both `.po` files, delete the regenerable
`messages.pot`. No `.mo` is committed.

Editing any existing English copy must keep the msgid byte-identical or both
catalogues silently lose that translation.

## Suggested phasing

This is large enough that the implementation plan should stage it. Each phase
below leaves the app shippable and green.

1. **Venue to tags.** Migration 1, `venue_tag_id`, the concert rollup, inline
   venue creation, and every display site that read the dropped fields. The
   biggest phase and the one with migration risk.
2. **Trilingual legs and rounds.** `ConcertDay.label_en/_zh`, `Round.label_zh`,
   the `label_en` semantic change, and the display fixes.
3. **Phrase library.** `RoundLabelPhrase` and the autocomplete.
4. **Enforcement.** Create-time 422s, the edit warning banner, the untranslated
   counter, and the tag create dialog exposing its variants.
5. **Migration 2.** Drop the old columns, only after live verification that
   phase 1's backfill matched every leg.

## Consequences for CLAUDE.md

On completion, update the i18n section to record that UGC translation now covers
legs and rounds, that leg venues are tag-derived with the concert rolling up, and
that `Concert.venue` is gone.
