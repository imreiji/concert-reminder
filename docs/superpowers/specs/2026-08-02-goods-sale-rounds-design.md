# Goods sale rounds and the item-requirement link

Date: 2026-08-02. Status: approved by the owner (option 1 of 3 on the link's
depth, "new round kind as goods sale" on the taxonomy, storage shape A).

## What this is

Two things the owner asked for in one breath, built as one small feature:

1. **A real `Goods sale` round kind**, so a merch pre-order window stops
   masquerading as "General sale" or "Other" and renders with its own label
   and emoji everywhere a kind shows.
2. **A display-only link from a round to the item sale it requires** —
   "you may enter 最速先行 only with the serial code from this CD sale" —
   so both rounds can say so, in the viewer's language, with the item
   sale's own deadline attached while it still matters.

What already existed, for the record: `RoundKind.ELIGIBILITY_ITEM_SALE`
(the serial-code CD/BD sale) has been a round kind from the start, and a
goods sale was *expressible* as a mislabeled General round. What did not
exist was the goods label itself, and any machine-readable connection
between an item sale and the round it feeds.

## Decisions taken during brainstorm (owner)

- **Link depth: display only** (option 1). No per-user "I bought it"
  capture, no reminder suppression keyed on it. That can layer on later
  exactly the way per-leg outcomes layered onto round outcomes; the FK
  built here is the hook it would hang from.
- **Storage shape: single nullable FK** (`Round.required_item_round_id`),
  not a qualifier-set join table mirroring UPGRADE's `round_qualifiers`.
  One round requires at most one item — no real campaign asks for two
  serial codes — and several lottery rounds pointing at the same CD sale
  falls out of an FK for free. YAGNI on the join table.
- **A free-text "requires" note was rejected**: it cannot render the item
  sale's own deadline, and gives a future capture feature nothing to key on.

## 1. Taxonomy

`RoundKind.GOODS_SALE = "goods_sale"` joins `domain/types.py` as a tenth
member, **deliberately cosmetic like the other nine** (the WISHLIST #3
observation stands: only UPGRADE carries behavior; this adds zero behavior
branches). It flows through every existing kind surface:

- `LABEL_BY_ROUND_KIND` (`db/service.py`): `N_("Goods sale")`, translated
  in both catalogues.
- `KIND_EMOJI` (`bot/messages.py`): 🛍️.
- The editor round card's kind select (shared partial — all three editor
  surfaces at once).
- ramen.events import heuristics (`domain/ingest.py`): グッズ / 物販
  keywords map to `goods_sale`.
- The add-concert skill's classification table (`SKILL.md`, both the repo
  copy and `web/skill_dist`): グッズ販売 → `goods_sale`. A CD/BD sale that
  exists to distribute serial codes **stays** `eligibility_item_sale` —
  the goods kind is for merch whose point is the merch.

Eventernote discovery is untouched: standalone グッズ発売 leads remain the
release-events class the 2026-08-02 scope ruling dismisses. This feature is
about rounds on catalogued concerts.

## 2. The link

`Round.required_item_round_id`: nullable FK → `rounds.id`,
`ON DELETE SET NULL`, indexed. Deleting the item-sale round degrades the
requiring round to "no requirement" — never cascades, same reasoning as
`ConcertDay.venue_tag_id`.

**Write-boundary validation**, same shape as `resolve_seiyuu`: the target
must be

- a round on the **same concert**,
- of kind `ELIGIBILITY_ITEM_SALE` or `GOODS_SALE` (抽選券付き goods exist),
- and not the round itself.

Routes answer 422; the draft parser warns and drops the link (warnings over
failures, per `parse_draft`'s philosophy). The kind check runs at write
time only — an item-sale round later *edited* to another kind leaves stale
links pointing at a non-item round, which display simply renders as-is;
accepted, matching how the app treats other cross-row edits.

## 3. Editor

Each round card (`_editor_round_card.html`, so concert_new, concert_edit
and import_preview all get it at once) gains a "Requires item from" select
listing the form's rounds of the two item kinds. New rounds have no DB id
at submit time, so the select's value is the target round's **position in
the submitted arrays**, resolved to real ids after the rounds flush — the
same shape as the `round_legs`/`day_key`/`key_to_day_id` binding for legs.
Edit-page rounds that already exist still submit by position, keeping one
code path. An empty value clears the link.

The select's options are maintained client-side as rounds are added,
removed and re-kinded, by the same script that maintains the cards.

## 4. Display

Display-only, everywhere. No board changes, no Coming up changes, no
suppression changes, no `_wants_you` changes.

- **Concert page, requiring round**: one line —
  "🛍️ Requires: *{item round label, viewer-locale via `loc_field`}*", plus
  the item sale's close time (via the standard dual rendering) while that
  sale is still open. The close time is the actionable half: "you still
  need to buy this, and its sale ends 6/15".
- **Concert page, item-sale round**: the reverse line —
  "Feeds: {labels of rounds pointing here}" — derived in
  `concert_rounds_context` from rounds already loaded; no new query shape.
- **DM reminder embeds** for a requiring round get the same one "Requires"
  line via `bot/messages.py`, using the recipient's `user.language` (the
  scheduler path — NOT `get_locale()`).

## 5. Round-trip

The concert draft vocabulary gains an optional per-round `requires:` field
naming another round **in the same draft** by its ja label. Both halves at
once, per the tags_yaml lesson: `domain/yaml_export.py` writes it,
`domain/yaml_import.py` resolves it (unmatched or ambiguous label →
warning, never a failure). `export.zip` thereby stays a faithful backup,
and the add-concert skill can author the link. The form-array position
binding in §3 is what `import_commit` resolves the parsed link through.

## 6. Out of scope, on purpose

- Per-user "I bought it" capture (owner's option 2) — later layer.
- Standalone goods drops with no concert — still the dismissed
  release-events class.
- Any behavior keyed on `GOODS_SALE`. It joins the cosmetic nine as a
  cosmetic tenth.

## 7. Migration

One autogenerated revision: the enum is stored as strings so the new member
costs nothing; the new column + index is a plain batch add. Post-
autogenerate ritual applies (`sa.DateTime()` swap does not arise — no
datetime column — but review per CLAUDE.md anyway). No legacy-constraint
concern: no `drop_constraint`.

## 8. Tests

- Migration upgrades on a fresh DB; column exists, SET NULL fires (with
  the `PRAGMA foreign_keys=ON` listener, per testing conventions).
- Kind renders: label/emoji on concert page, editor select, DM embed.
- Link validation: cross-concert target 422, wrong-kind target 422,
  self-target 422, valid target persists, empty clears.
- Position binding: create and import commit resolve positions to ids;
  a dangling position is dropped with a warning, not a 500.
- Display: requiring round shows label + close time while open, hides the
  close time after; item round shows the reverse line; ja/zh label
  variants resolve.
- Draft round-trip: export writes `requires:`, import resolves it,
  unmatched label warns.
- Catalogues: `test_i18n_catalogues.py` enforces the new msgids itself.
