# First-run guided setup design

## Context

The last remaining WISHLIST.md item after per-round lottery outcome
tracking (in progress) and free-text search matching tags (shipped);
daily digest mode was raised alongside these but rejected by the owner
before this spec (see WISHLIST.md's Rejected section). From the wishlist
entry (raised 2026-07-18 UX review, impact: medium, effort: medium):
"All the onboarding pieces exist (tag subscriptions, default preset,
timezone auto-detect, `/mydeadlines`, the calendar feed) but a new login
lands on an index shaped by none of them." Confirmed against the current
code: `auth.py`'s OAuth callback and `ensure_user` treat every login
identically — there is no existing signal distinguishing a brand-new
user's first login from a returning user's hundredth.

## Non-goals

- Changing any of the five underlying actions (tag subscription,
  preset creation, timezone setting, the test-DM diagnostic, calendar-feed
  generation) — every step reuses its existing route/service function
  verbatim. This spec is purely about *sequencing* them for a new user,
  not modifying what any of them do.
- A resurfacing/re-prompting mechanism for users who dismissed onboarding
  and later cleared out their subscriptions/presets. Once dismissed
  (finished or skipped), it's dismissed for good.
- Any change to how `/mydeadlines` works — it's referenced in the
  wishlist text as an existing onboarding-adjacent feature, but isn't
  part of the guided sequence itself (it's a Discord command, not a web
  step) and needs no changes here.

## Section 1: The wizard mechanism

One new column, `User.onboarding_step: int` (default `0`, `server_default="0"`).
Five steps, indices 0-4, in the wishlist's own order: (0) follow artists,
(1) pick a default preset, (2) confirm timezone, (3) send a test DM,
(4) calendar feed link. `onboarding_step >= 5` means "done" (either
finished naturally or skipped entirely) — no separate boolean needed.

`GET /welcome` is the wizard page: if the user's `onboarding_step >= 5`,
redirect to `/`; otherwise render whichever step's screen matches the
current value. Each step is a small, purpose-built form that POSTs to the
**existing** backend route for that action (`/subscriptions`, `/presets`,
`/me/timezone`, `/me/test-dm`, `/me/calendar-feed`) — no new backend logic
for what each step *does*. A shared `POST /welcome/advance` increments
`onboarding_step` by one, called either after a real action succeeds or
directly via a "Skip this" link on the current step. `POST
/welcome/skip-all` jumps straight to `5`, the escape hatch out of the
whole sequence. This keeps the flow a single continuous path rather than
bouncing the user across Tags/Preferences/Calendar pages, which was the
reason a dedicated wizard was chosen over lighter banners layered onto
those existing pages.

**New-user detection**: `ensure_user` (`db/service.py`) currently doesn't
report whether it created a new row or found an existing one — it needs a
small return-shape change so the OAuth callback (`auth.py`) can redirect a
genuinely brand-new user to `/welcome` instead of `/`. A returning user
(row already existed) is never redirected here regardless of their
`onboarding_step` value — the wizard is offered once, at first login, not
re-offered on every subsequent login just because they haven't finished it
(they can always navigate back to `/welcome` manually if they want to
pick it back up).

## Section 2: The five steps

- **Step 0 (follow artists)**: a trimmed tag-search-and-subscribe list —
  the same `POST /subscriptions` mechanism Preferences already uses, just
  a focused subset of that page's UI (search box + chip list, no preset
  linking controls yet since no preset exists at this point in the
  sequence).
- **Step 1 (default preset)**: the existing "create a preset with its
  first item" form verbatim.
- **Step 2 (timezone)**: this step is different in kind from the others —
  the app already auto-detects timezone via browser JS on every page load
  (`tz_auto`), so most users arrive here with an already-correct value.
  The screen shows the detected timezone with a "Looks right" confirmation
  alongside a manual override select; "confirm" and "skip" are
  functionally the same action (both just advance) since there's nothing
  to *fix* for the common case.
- **Step 3 (test DM)**: the existing "Send test DM" button, reusing
  `/me/test-dm` and the `dm_blocked` banner infrastructure exactly as
  built for the surface-undeliverable-dms feature — no new diagnostic
  logic.
- **Step 4 (calendar feed)**: the existing "Generate feed link" button,
  showing the raw URL once, exactly as Preferences does today.

Skipping any individual step (via "Skip this") never performs that step's
underlying action — it only advances `onboarding_step`. Finishing the
final step (or skipping it) redirects to `/`.

## Testing

- **Service-layer**: `ensure_user`'s new return shape correctly reports
  "created" only for a genuinely new row, not an existing one whose
  username changed.
- **HTTP-level**: a brand-new login redirects to `/welcome`; a returning
  user's login redirects to `/` regardless of their `onboarding_step`;
  `GET /welcome` redirects to `/` once `onboarding_step >= 5`; each step's
  form correctly POSTs to its existing route and the response correctly
  reflects the real state change (e.g. step 0's subscribe actually creates
  a `TagSubscription` row); `POST /welcome/advance` increments by exactly
  one; `POST /welcome/skip-all` jumps straight to 5; skipping a step
  advances without creating any of that step's underlying rows.
- The standard "every page needs a logged-in GET render test" rule
  applies to `/welcome` at each of its 5 step states.
