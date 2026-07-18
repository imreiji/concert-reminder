# WISHLIST.md

Every potential feature raised in roadmap and UX discussions, ordered by
user impact (highest first). Maintained per the "Feature wishlist" section
in CLAUDE.md: fully re-evaluated and re-ranked every time a feature ships.
Each entry notes impact and effort so re-ranking has a basis. Shipped and
rejected ideas move to the bottom sections instead of being deleted.

## Proposed (highest impact first)

### 1. Per-round personal lottery outcome tracking (applied / won / lost / paid)

Impact: high — the most domain-shaped feature here. Effort: large.
Raised: 2026-07-18 UX review.

The domain is a chain: apply → results → payment → next round if you
lose. Reminders currently fire regardless of the user's situation —
someone who lost the 先行抽選 still gets its payment reminder. Let users
mark an outcome per round and tailor the queue: lost → suppress that
round's payment reminder and arm the next round's opens reminder; won →
skip the general-sale ping. Natural surface: the existing state-aware DM
buttons ("Did you apply?") right in the reminder embed.

### 2. Daily digest mode

Impact: medium — noise reduction for multi-subscription users. Effort: medium.
Raised: 2026-07-18 UX review.

One DM per deadline per rule trains busy users to ignore pings. Opt-in
"one morning DM listing everything due in the next N days", batched at
delivery time when the outbox is drained — the queue design already
supports grouping rows per user per tick.

### 3. First-run guided setup

Impact: medium — new-user activation. Effort: medium.
Raised: 2026-07-18 UX review.

All the onboarding pieces exist (tag subscriptions, default preset,
timezone auto-detect, `/mydeadlines`, the calendar feed) but a new login
lands on an index shaped by none of them. Sequence them: follow some
artists → pick a default reminder preset → confirm timezone → send a
test DM to confirm delivery works → here's your calendar feed URL. Goal:
first useful reminder armed in one sitting.

## Shipped

### Free-text search matches artists, groups, and venues (2026-07-18)

Shipped as: a centralized `concert_search_text` helper (`web/app.py`) that
is now the single source of truth for what search matches — title,
title_en, every attached tag's name across all four kinds
(franchise/group/artist/venue), and a free-text `Concert.venue` fallback
only when no VENUE tag is attached. Used at all three call sites: the
server-side `matches_query` fallback, the tile grid's `data-search`
attribute, and the "Coming up soon" deadline list's `data-search`
attribute (previously title-only).

### Surface undeliverable DMs to the user (2026-07-18)

Shipped as: a sitewide `dm_blocked` banner (driven by `SessionUser` /
`users.dm_blocked_since`, set by the scheduler's delivery-outcome
tracking) plus a synchronous `POST /me/test-dm` diagnostic route and
"Send test DM" button on the preferences page.

## Rejected

(none yet — move entries here with the reason instead of deleting them)
