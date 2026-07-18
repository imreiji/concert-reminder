# Surface undeliverable DMs design

## Context

Feature #1 from `WISHLIST.md` (raised 2026-07-18 UX review, impact: high,
effort: small). Today, when a user has Discord DMs blocked,
`scheduler/loop.py`'s `deliver()` and `_send_notification()` both catch
`discord.Forbidden`, log a warning, and mark the row sent anyway (retrying
a permanently-blocked user forever would spam the log for no benefit) —
confirmed against the actual code, not just the wishlist's description of
it. From the user's side, the app simply goes dark: no reminder, no
notification, no visible signal anything went wrong, even though they can
still log in on the web and see the page render normally.

## Non-goals

- Any change to the reminder/notification queue's retry or delivery
  semantics beyond adding outcome tracking. `mark_sent`/
  `mark_notification_sent` still fire on the same conditions
  (success or Forbidden) as today.
- Any change to `notifications`-table-driven system notices (new-event,
  leg-cancelled) — those keep going through the outbox exactly as today.
  Only the new manual test-DM action is a synchronous exception.
- Retrying a blocked user's queued reminders once DMs come back — a
  postponed-deadline re-arm already exists for other reasons; this feature
  is purely about the user *knowing* DMs are blocked, not about replaying
  missed reminders.
- Rate-limiting the test-DM button. It's a per-user, login-gated action;
  abuse risk is a user spamming their own account, which isn't a concern
  worth engineering around at this app's scale.

## Section 1: Data model

`User` (`db/models.py`) gains one nullable column:

```python
dm_blocked_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

`None` means DMs are working (or never tested); a timestamp means the most
recent attempted send to this user hit `discord.Forbidden`. One shared
signal across both reminder-DM and notification-DM sends — a Forbidden
from either path sets it, a success from either path (including the
manual test DM) clears it. Requires an Alembic migration (autogenerate,
then the usual review: replace `app.db.models.UTCDateTime()` with
`sa.DateTime()`, drop the `import app.db.models` line).

## Section 2: Scheduler changes

`deliver()` and `_send_notification()` (`scheduler/loop.py`) currently
return a bare `bool` meaning "mark the row sent" — both a successful send
and a caught `Forbidden` return `True` today, conflating two outcomes that
now need to diverge (success clears the flag, Forbidden sets it, a
transient `discord.HTTPException` touches neither). Both functions change
to return a three-value enum instead:

```python
class DeliveryOutcome(Enum):
    SUCCESS = "success"
    FORBIDDEN = "forbidden"
    TRANSIENT_FAILURE = "transient_failure"
```

`tick()`'s existing "mark sent on success-or-Forbidden, leave unsent on
transient failure" logic is unchanged in effect (`SUCCESS` and `FORBIDDEN`
both still mark the row sent; `TRANSIENT_FAILURE` still doesn't) — it just
now dispatches on the three-value outcome instead of a bare bool, and
additionally calls a new `db/service.py` function once per item:

```python
async def record_dm_outcome(session: AsyncSession, discord_id: int, blocked: bool) -> None:
    ...
```

`blocked=True` sets `dm_blocked_since` to now; `blocked=False` clears it to
`None`. `TRANSIENT_FAILURE` doesn't call this at all — an unrelated network
hiccup says nothing about whether DMs are blocked. Keeping the actual
column write in `db/service.py` matches CLAUDE.md's "bot and web never
contain business logic" rule; the enum and the calling logic stay in
`scheduler/loop.py`, where the Discord exception handling already lives.

## Section 3: The sitewide banner

`auth.current_user()` already does a `db.get(User, user_id)` on every
authenticated request (to resolve `is_editor`) — so `SessionUser` gains a
`dm_blocked: bool` field sourced from that same already-loaded row's
`dm_blocked_since is not None`, at zero extra query cost.

The banner renders in `base.html`, immediately below the site `<header>`,
whenever `user.dm_blocked` is true — every authenticated page, not just
the index, since the data is already there for free and a user could land
directly on any page. Copy along the lines of: "Your last Discord DM
couldn't be delivered — check your Discord privacy settings to allow DMs
from server members, or [test it from Preferences](/preferences)."

## Section 4: The "send test DM" button

New `POST /me/test-dm` route in `web/routes/preferences.py`.

**Explicit exception to CLAUDE.md's invariant #4** ("never send DMs
directly from web routes"): this route sends synchronously, bypassing the
`notifications` outbox. CLAUDE.md's invariant gets a one-line addendum
documenting exactly why — a manual, user-initiated, low-volume diagnostic
action is a different animal from a system-initiated notice, which must
still go through the outbox for its retry/ordering/audit properties. This
carve-out is scoped to this one route; nothing else gets to bypass the
outbox on the strength of this precedent.

Implementation: a function-local `from app.bot.client import bot` (mirrors
`main.py`'s existing lazy import of the same singleton, for the same
reason — avoid any discord.py setup cost in web-only dev mode), guarded by
`settings.bot_enabled` first. If the bot's disabled, the route responds
with a message explaining the bot isn't running in this environment. Since
`app.bot.client` is a module-level singleton, this lazy import reaches the
exact same live, gateway-connected object `main.py` handed to
`reminder_loop(bot)` at startup — no new wiring needed between web and bot.

Otherwise: fetch the user via `bot.get_user(...)  or await
bot.fetch_user(...)`, attempt `await user.send(...)` with a short test
message, and catch `discord.Forbidden`/`discord.HTTPException` the same
way `deliver()` does. Call `record_dm_outcome()` (Section 2) with the
result.

This codebase has no flash-message system (every other POST route here
just does a plain `RedirectResponse("/preferences", 303)` with no
success/failure feedback), so rather than inventing one, `/me/test-dm`
follows the htmx fragment-swap idiom `_rules.html` already establishes
(`hx-post`/`hx-target`/`hx-swap="outerHTML"` against a small partial
response) — `response_class=HTMLResponse`, returning a one-line rendered
fragment: "Test DM sent!" / "Still blocked — check your Discord privacy
settings" / "Couldn't reach Discord, try again." The button in
`preferences.html` is `hx-post="/me/test-dm" hx-target="#dm-test-result"
hx-swap="innerHTML"`, swapping the result into a small `<span
id="dm-test-result">` next to it — no full-page reload, no new
page-wide infrastructure. Replaced with an explanatory note (reusing the
`bot_enabled` context flag `index.html` already receives) when the bot
isn't running.

## Testing

- **Service-layer**: `record_dm_outcome` sets and clears
  `dm_blocked_since` correctly.
- **Scheduler-layer**: extend the existing fake-bot `tick()` tests (same
  shape as the current scheduler tests in `test_presets.py`) to cover all
  three `DeliveryOutcome` cases — a `Forbidden`-raising fake user sets the
  flag and still marks the row sent (unchanged queue semantics); a
  successful send clears a previously-set flag; a transient
  `HTTPException` leaves `dm_blocked_since` untouched and leaves the row
  unsent (both exactly as today).
- **HTTP-level**: a logged-in GET render test showing the banner present
  when `dm_blocked_since` is set and absent when it's `None` (both states,
  per this project's "every page needs a render test" rule); `/me/test-dm`
  tested with a monkeypatched fake `app.bot.client.bot` object (mirroring
  `test_bot_reminders.py`'s pattern of monkeypatching a module attribute
  before the route's lazy import resolves it) covering success, Forbidden,
  and bot-disabled cases.
