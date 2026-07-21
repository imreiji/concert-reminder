# Testing conventions


- Async tests via pytest-asyncio auto mode — `await` directly, never
  `run_until_complete` inside a test.
- DB fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener
  (production does; cascades silently don't fire without it).
- Every page must have at least one logged-in GET render test — a missing
  one shipped a 500 once (template context drift).
- Discord is never imported in service tests; button/scheduler behavior is
  tested through service functions and fake bot objects.
- Slash-command cogs (`bot/cogs/*.py`) ARE tested directly (see
  `tests/test_bot_reminders.py`): call `Cog.command_name.callback(cog, ...)`
  (the `app_commands.Command` wrapper exposes the original coroutine as
  `.callback`) with a minimal fake `discord.Interaction` (just `.user.id`/
  `.name` and an async `.response.send_message` that records its args), and
  monkeypatch the cog module's `SessionMaker` to a real in-memory async
  engine -- same fixture shape as the service-layer tests, no Discord
  gateway involved.

