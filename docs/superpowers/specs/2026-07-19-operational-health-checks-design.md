# Operational health checks and owner alerts

Date: 2026-07-19

## Problem

Failures in this system are silent. On 2026-07-19 the nightly S3 backup stopped
working and nobody noticed -- it surfaced only because someone happened to read
`~/backup.log` while debugging something else. A migration was then run against
the live database with no verified backup.

Nothing watches the parts of the system that fail quietly. `/healthz` proves the
scheduler is ticking, and UptimeRobot keyword-monitors it, but that is the only
signal. A full disk, a backup that stopped, or DMs silently bouncing all look
identical from outside: fine.

Goal: know when any of these processes breaks, without having to go and look.

## Constraint that shapes the design

The server's IAM user is **`s3:PutObject` only** (`docs/deploy.md`), deliberately,
so a compromised server can add backups but cannot read, list, or delete them.

**The app therefore cannot ask S3 whether a backup exists.** Freshness must be
inferred from what `backup.sh` reports locally. This proves "the script ran and
`aws s3 cp` exited 0", which is close to but not identical to "the object is in
the bucket".

Keep the IAM policy as-is. Granting `ListBucket` to simplify monitoring would
trade a real security property for a small convenience.

## Approach

A registry of named checks, consumed by two independent paths:

- **`/healthz`** renders every check (pull; also covers scheduler death)
- **the scheduler** evaluates them periodically and DMs admins on state change
  (push; carries the detail)

Both are needed. The scheduler cannot report its own death, and UptimeRobot can
only say "the keyword is missing", not what broke.

Rejected alternatives:

- **Everything through the database** (`backup.sh` writing a row via `sqlite3`)
  -- couples the cron script to the app schema, writes to the database it is
  backing up, and adds lock contention to a script that already died once from a
  live-writer conflict. The script must work when the app is broken.
- **`/healthz` only, no DM** -- half the work, but UptimeRobot cannot say what
  failed, which is the actual requirement.

## Components

### `deploy/backup.sh`

Writes an ISO-8601 UTC timestamp to `/home/ubuntu/.dekimasen-backup-ok` as its
final step, after `aws s3 cp` succeeds. Inside the existing success path, so a
failed upload leaves the marker stale rather than updating it.

Path is a constant in the script and a setting on the app side
(`BACKUP_MARKER_PATH`, defaulting to that path) so tests can point elsewhere.

### `src/app/domain/health.py` -- pure

No I/O, no discord/fastapi/sqlalchemy imports, per the domain invariant.
Threshold and state logic only:

- `backup_is_stale(last_ok: datetime | None, now: datetime, max_age: timedelta) -> bool`
- `disk_is_low(free_bytes: int, total_bytes: int, min_free_ratio: float) -> bool`
- `should_alert(stored: StoredState | None, observed_ok: bool, now: datetime) -> AlertDecision`

`should_alert` is the transition machine, and keeping it pure is the point of
the split -- it is the highest-risk logic and the hardest to exercise through
real I/O.

`StoredState` is a plain frozen dataclass defined in this module (`ok`,
`changed_at`, `last_notified_at`, `pending_since`), NOT the `OpsCheckState` ORM
row -- `domain/` must not import sqlalchemy. `ops.py` reads the row and passes a
`StoredState` in.

Disk threshold: alert below **10% free OR under 1 GB free**, whichever triggers
first. The ratio alone is wrong on a 20 GB disk (2 GB free is fine), and the
absolute alone is wrong if the disk is ever resized.

### `src/app/ops.py` -- I/O adapters and registry

Each check is a function returning `(name, ok, detail)`. The registry is a list
of them, iterated by both consumers.

Checks in scope:

| name | signal | source |
|---|---|---|
| `backup` | marker file older than 36h, or absent | filesystem |
| `disk` | free space on the DB partition below threshold | `shutil.disk_usage` |
| `scheduler` | loop ticking | existing `scheduler/heartbeat.py` |
| `dms` | count of users with `dm_blocked_since` set | DB |

`dms` is **reported but never alerted on** (`alerting=False` in the registry). A
user blocking the bot is their choice, not an outage; paging the owner about
someone else's privacy setting is the kind of noise that trains an operator to
ignore alerts. It stays visible in `/healthz` for when the owner goes looking.
The registry entry therefore carries an `alerting` flag, so "report this" and
"wake me for this" are separate decisions from the start.

Deliberately excluded: TLS cert expiry (15-year Cloudflare Origin cert, not a
real risk), memory (swap is configured; noisy), and anything needing new AWS
permissions. The failure mode of health surfaces is monitoring everything until
none of it is trusted.

### `OpsCheckState` -- new table

One row per check name: `ok`, `changed_at`, `last_notified_at`. Persistence is
what makes transition alerting survive restarts; in-memory state would re-alert
on every deploy.

Plain `CREATE TABLE`, no `drop_constraint`, so it does not need the
legacy-schema fixture from `tests/test_migration_legacy_anonymous_constraints.py`.
State that in the revision docstring so the next person does not wonder.

## Data flow

**`/healthz`** calls the registry and renders each check as a sibling field.

`ok` stays **scheduler-only**. UptimeRobot keyword-matches `"ok":true` today;
making `ok` mean "all checks pass" would silently change what an existing
external alert means. Degraded checks are visible in the body, and a second
monitor can be added later if paging on them is wanted.

**Scheduler** evaluates the registry every 5th tick (~5 min). Disk stats and
file reads do not need per-minute resolution, and a slower cadence damps
flapping. For each check whose result differs from stored state, it writes the
new state and queues a `Notification` per admin in `ADMIN_WHITELIST` -- in both
directions, broken and recovered. An alert you never see resolve is worse than
no alert.

Alerts go through the `notifications` outbox, never a direct DM. This honours
invariant 4 rather than adding a second carve-out, and inherits retry, ordering,
and `discord.Forbidden` handling.

`Notification.user_id` is a FK to `users.discord_id`, so an admin who has never
logged into the web app has no `User` row and queuing would violate the
constraint. Call `ensure_user` for each admin first, as the OAuth callback does.

## Error handling

- **A check that throws is a failure.** Each runs in its own try/except;
  an exception becomes `ok=False` with the message as detail. It must never take
  down the tick, which is delivering reminders.
- **Anti-flap:** a new state must hold for two consecutive evaluations before
  alerting. At a 5-minute cadence that is a 10-minute confirmation delay --
  negligible for these signals, and it kills "disk hovering at the threshold
  pages forty times". `pending_since` on the state row tracks the unconfirmed
  observation.
- **Still-broken re-alerts every 24h** via `last_notified_at`. Transition-only
  alerting has a hole: you see it, mean to fix it, forget, and it stays broken
  silently.
- **First evaluation, no stored state:** record the observation, do not alert
  yet. A failing check alerts on the *next* evaluation once confirmed, ~5 min
  later. This is the same confirmation rule as every other transition rather
  than a special case -- an earlier draft of this spec had first-run failures
  alerting immediately, which contradicted the anti-flap rule. A passing check
  simply records, so deploys never page.
- **Missing marker file:** reports not-ok with `"no backup recorded yet"`, but
  alerts are suppressed during a startup grace window -- the pattern
  `heartbeat.py` already uses for restarts. A genuinely broken backup surfaces
  ~36h after deploy rather than instantly at deploy time.
- **Web-only dev mode** (`bot_enabled` false): skip queuing entirely. A laptop's
  disk is not an operational signal, and every local run would otherwise
  accumulate junk notifications.

## Testing

- `domain/health.py`: unit tests per threshold and its boundaries.
- Registry: fake marker path, monkeypatched `disk_usage`, and a check that
  raises.
- **Transition machine** (highest risk, explicit coverage): first-run-failing
  stays silent then alerts on the confirming evaluation; first-run-passing stays
  silent throughout; ok->broken and broken->ok both alert once confirmed; a
  single-evaluation blip does *not* alert; still-broken re-alerts after 24h but
  not before.
- A queued alert creates one `Notification` per admin and calls `ensure_user`
  first.
- **`/healthz` regression:** `ok` still tracks the scheduler alone. That field
  has an external consumer in UptimeRobot; breaking it silently would defeat the
  purpose of the work.
- DB fixtures register the `PRAGMA foreign_keys=ON` connect listener, per the
  testing conventions -- the `Notification` FK cascade is load-bearing here.

## Known boundaries

- **The scheduler cannot report its own death.** If the loop stops, nothing
  evaluates checks and nothing queues alerts. `/healthz` plus UptimeRobot covers
  exactly that case, which is why both paths exist.
- **Backup freshness proves the script succeeded, not that the object is in S3.**
  A consequence of the PutObject-only IAM policy, and the right trade.
- Alerts depend on the bot being connected. If Discord is unreachable they queue
  in the outbox and deliver on recovery, which is the intended outbox behaviour.

## Out of scope

Self-service account deletion, CSP, a render-layer URL filter, and
`--sse AES256` on the S3 upload. All tracked separately; none belongs in this
change.
