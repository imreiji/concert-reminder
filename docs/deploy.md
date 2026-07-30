# Deploying dekimasen.app

Architecture: `browser -> Cloudflare (proxy, edge TLS) -> Caddy (origin TLS) -> uvicorn :8000`
One Ubuntu box, one systemd service, one SQLite file.

This runbook is written to be followed top-to-bottom on a fresh deploy and
usable piecemeal for disaster recovery.

## 0. Prerequisites

- Domain `dekimasen.app` registered (Porkbun)
- AWS account, Cloudflare account (Free plan is enough)
- The GitHub repo (private)

## 1. Server

Lightsail: Create instance -> Ubuntu 24.04 -> $5 plan (512MB is enough; $10/1GB if
you want headroom) -> create. Then **Networking tab**:
- attach a **static IP** (free while attached)
- firewall rules: SSH 22 (ideally restricted to your home IP), HTTP 80, HTTPS 443

(EC2 equivalent: t4g.micro + Elastic IP + security group with the same three ports.)

## 2. GitHub deploy key (private repo -> server)

On the server:
```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "dekimasen-server"
cat ~/.ssh/id_ed25519.pub
```
GitHub repo -> Settings -> Deploy keys -> Add key -> paste. Read-only (do NOT
check write access). This lets the server `git pull` and nothing else.

## 3. Cloudflare zone

1. Cloudflare -> Add a site -> `dekimasen.app` -> Free plan.
2. Cloudflare shows two nameservers. At **Porkbun** -> domain -> Nameservers ->
   replace with Cloudflare's pair. (DNS now lives at Cloudflare; Porkbun is
   registrar only.) Activation takes minutes to a few hours.
3. DNS records (both **Proxied** / orange cloud):
   - `A`     `@`    -> your static IP
   - `CNAME` `www`  -> `dekimasen.app`
4. SSL/TLS -> Overview -> set mode to **Full (strict)**. Never "Flexible".
5. SSL/TLS -> Edge Certificates -> enable **Always Use HTTPS**.

## 4. Origin certificate

Cloudflare -> SSL/TLS -> **Origin Server** -> Create Certificate:
- Key type RSA, hosts `dekimasen.app, *.dekimasen.app`, validity 15 years.
- Copy the certificate into `/etc/caddy/certs/origin.pem` on the server,
  the private key into `/etc/caddy/certs/origin.key`, then:
```bash
sudo chown caddy:caddy /etc/caddy/certs/*
sudo chmod 600 /etc/caddy/certs/origin.key
```
This cert is only trusted by Cloudflare - which is exactly the point. Browsers
see Cloudflare's edge certificate (auto-managed, satisfies .app's HSTS rule).

## 5. App

```bash
git clone git@github.com:YOURUSER/concert-reminder.git ~/app
cd ~/app && ./deploy/setup.sh        # installs uv + caddy, syncs deps
nano .env                            # production values, see below
./deploy/setup.sh                    # second run: migrates, starts services
```

Production `.env`:
```
DISCORD_TOKEN=<bot token>
DISCORD_CLIENT_ID=<id>
DISCORD_CLIENT_SECRET=<secret>
EDITOR_WHITELIST=<your discord id>
ADMIN_WHITELIST=<your discord id>
BASE_URL=https://dekimasen.app
SESSION_SECRET=<fresh: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=sqlite+aiosqlite:////home/ubuntu/app/app.db
DEFAULT_TIMEZONE=America/Moncton
PRIVACY_CONTACT_DISCORD=<your handle>
PRIVACY_CONTACT_EMAIL=<your address>
```
Note the **four** slashes in DATABASE_URL: absolute path, so the DB location
doesn't depend on the service's working directory.

`ADMIN_WHITELIST` is not optional in practice even though the app starts
without it. It is the only way to be an admin (env-only by design, no runtime
UI), and admins are who the ops alerts and the per-tick delivery digest are
DM'd to, and who can reach `/admin/deliveries` and `/admin/broadcast`. Deploy
without it and the app runs fine while nobody is told when backups go stale or
the disk fills. `PRIVACY_CONTACT_*` fill the public `/privacy` page's
data-request channel; set either, both, or neither (the page shows a neutral
fallback), but they are the operator's real contact details and so live only
here and in the local `.env`, never in the repo.

**`REHEARSAL_ENABLED` must stay absent or false here.** It registers
`/admin/rehearsal`, whose whole purpose is a "deliver every reminder now"
button and a send-any-DM-shape catalogue. When the flag is off the router is
never registered, so the routes do not exist at all — the `require_admin` on
each one is a second layer for a misconfigured deploy, not the guard. It is a
local-development tool; see `docs/local-dev-bot.md`.

SESSION_SECRET is validated at startup: with an https BASE_URL, a blank,
placeholder, or under-32-character secret is fatal. `alembic/env.py` imports
the app config, so a bad `.env` now fails at `alembic upgrade head` - one step
earlier in the deploy ritual than the service restart.

## 6. Discord OAuth redirect

Developer Portal -> your app -> OAuth2 -> Redirects -> **add**
`https://dekimasen.app/auth/callback` (keep the localhost one for dev).

## 7. Verify

```bash
git log --oneline -1                         # confirm the server is on the commit
                                             # you just deployed - a pull that
                                             # failed partway leaves the box on
                                             # the OLD commit, still healthy
curl -s https://dekimasen.app/healthz        # {"ok":true,"bot_enabled":true}
journalctl -u concert-reminder -f            # bot online + scheduler running
```
Then in a browser: sign in with Discord, confirm the editor badge.

Caddyfile changes are not picked up by `git pull` alone - the live config is
a copy:
```bash
caddy validate --config ~/app/deploy/Caddyfile     # validate the REPO copy,
                                                   # i.e. the change you are about
                                                   # to deploy - validating
                                                   # /etc/caddy/Caddyfile instead
                                                   # passes even if you skip the cp
sudo cp ~/app/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -sI https://dekimasen.app/ | grep -i 'x-frame\|x-content-type\|referrer'
```
That last line should show `X-Frame-Options: DENY`, `X-Content-Type-Options:
nosniff`, `Referrer-Policy: same-origin`. If they are missing, the copy step
was skipped.

## 8. Hardening (do these, they take 10 minutes)

- **Lock the origin to Cloudflare**: edit the Lightsail firewall so 80/443
  accept only Cloudflare's IP ranges (https://www.cloudflare.com/ips/).
  Otherwise anyone who discovers the origin IP can bypass Cloudflare.
- SSH: restrict port 22 to your home IP; key-only auth (Lightsail default).
- Point a free uptime monitor (UptimeRobot) at `https://dekimasen.app/healthz`.
- Backups: see the full section below.

## 9. Backups (Phase 7)

The entire application state is one file, so backup = copy one file nightly.

**S3 bucket** (AWS console -> S3 -> Create bucket):
- name: globally unique, e.g. `dekimasen-backups-<random suffix>`
- keep Block Public Access ON (default)
- after creating: Management tab -> Lifecycle rule -> apply to whole bucket ->
  "Expire current versions" after **30 days**. This caps storage at ~30 copies
  (pennies/month) with zero maintenance.

**IAM user that can ONLY write backups** (IAM -> Users -> Create user, no console access
-> attach this inline policy, then create an access key of type "Other"):
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "s3:PutObject",
    "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/*"
  }]
}
```
PutObject only: if the server is ever compromised, the key can add backups but
not read, list, or delete them.

**On the server:**
```bash
sudo apt-get install -y awscli
aws configure          # paste the access key id + secret; region e.g. ca-central-1; output json
# The bucket is server-local config and is NOT stored in the repo - backup.sh
# reads BACKUP_BUCKET out of this file itself if it exists. The file is
# PARSED, not sourced - only a BACKUP_BUCKET= line means anything, and shell
# syntax in it will not run:
sudo tee /etc/default/dekimasen-backup <<< 'BACKUP_BUCKET="s3://YOUR-BUCKET-NAME/dekimasen"'
# chown as well as chmod: sudo tee leaves it root-owned, and cron runs
# backup.sh as ubuntu, which then cannot read it.
sudo chown ubuntu:ubuntu /etc/default/dekimasen-backup
sudo chmod 600 /etc/default/dekimasen-backup
~/app/deploy/backup.sh         # test run -> "backup ok: <date>"
crontab -e                     # add:
# 0 9 * * * /home/ubuntu/app/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
```
With no bucket configured the script exits non-zero on line 1 of real work and
says which file to create, so a misconfigured cron fails loudly in
`~/backup.log` rather than silently backing up nothing.

**Health check.** On success `backup.sh` writes a UTC timestamp to
`/home/ubuntu/.dekimasen-backup-ok`. `/healthz` reports it as the `backup`
check, and the scheduler DMs everyone in `ADMIN_WHITELIST` when it goes stale
(36h) or recovers. Only a successful `aws s3 cp` updates it, so a failed run
leaves it stale rather than lying.

The app cannot verify the object actually reached S3 - the IAM user above is
PutObject-only by design, with no `ListBucket` - so this marker proves the
script succeeded, which is close to but not the same as the backup existing.
Do not widen the IAM policy to close that gap: the check is worth less than
the property that a compromised server cannot read or delete your backups.

Right after this first deploys, the check reports "no backup recorded yet"
until 09:00 UTC writes the first marker. That is expected and does not alert -
there is a startup grace. To exercise it immediately, run
`~/app/deploy/backup.sh` by hand and confirm `/healthz` flips
`checks.backup.ok` to true.

**One-time migration (server currently has an edited `backup.sh`):** the old
script carried a hardcoded `BUCKET=`, so servers set up before this change have
a locally-modified tracked file that will conflict on the next `git pull`.
`git checkout --` destroys the only record of the bucket name, so the env file
is written FIRST and the value is read straight out of the old script rather
than retyped. Ordered this way the whole block is safe to paste at once:
```bash
cd ~/app
OLD_BUCKET=$(grep '^BUCKET=' deploy/backup.sh | cut -d'"' -f2)
echo "$OLD_BUCKET"                   # sanity-check: should read s3://...
sudo tee /etc/default/dekimasen-backup <<< "BACKUP_BUCKET=\"$OLD_BUCKET\""
sudo chown ubuntu:ubuntu /etc/default/dekimasen-backup   # cron runs as ubuntu
sudo chmod 600 /etc/default/dekimasen-backup
git checkout -- deploy/backup.sh     # safe now: the value is saved
git pull
~/app/deploy/backup.sh               # confirm: "backup ok: <date>"
```
If `echo "$OLD_BUCKET"` prints nothing, stop - the script was already migrated
(or edited by hand in some other shape) and checking it out would lose nothing,
but the env file would be written empty and the next backup would fail.
The crontab line does not change: `backup.sh` sources
`/etc/default/dekimasen-backup` itself, so there is no half-migrated state
where the script is updated but cron still runs it without a bucket.

**Restore drill (do this once now so it isn't theory):**
```bash
aws s3 ls s3://YOUR-BUCKET-NAME/dekimasen/        # requires ListBucket - run from
                                                  # AWS console/CloudShell instead,
                                                  # since the server key can't list
# from the console, download a backup, or on a trusted machine:
gunzip app-YYYY-MM-DD.db.gz
sqlite3 app-YYYY-MM-DD.db "SELECT count(*) FROM concerts"
```

## 10. Monitoring (Phase 7)

`/healthz` now reports the SCHEDULER's health, not just the web server's:
```json
{"ok": true, "bot_enabled": true, "scheduler_ok": true, "scheduler_last_tick": "..."}
```
`ok` flips to false if the reminder loop misses 3 ticks - catching silent
scheduler death, the failure mode that actually loses lotteries.

UptimeRobot (free): Add monitor -> type **Keyword** -> URL
`https://dekimasen.app/healthz` -> keyword `"ok":true` -> alert when keyword
**not exists** -> interval 5 min. This alerts on full outages AND on a dead
scheduler behind a live website.

UptimeRobot is the ONLY thing that catches scheduler death. The in-process
checks DM the admin whitelist on a confirmed change for `backup` and `disk`,
but deliberately never for `scheduler`: that check runs inside the tick, and
the tick beats the heartbeat immediately before running, so from in there the
last beat is always seconds old. It can never observe its own death - only
false-alarm about a tick that is legitimately running long. It stays on
`/healthz`, where an outside caller can see the truth.

### Watching what actually got delivered

Both DM drains - reminders AND notifications - write a `delivery_log` row per
attempt, and every tick that delivered anything DMs the admins a digest of
COUNTS. `/admin/deliveries` is the reader and the only surface that names
recipients; the digest deliberately does not, because a name in Discord
history is a record `POST /me/delete` cannot reach. When a user reports a
missing reminder, this is the first place to look: a `FORBIDDEN` row means
their DMs are closed (the app also banners them about it), and no row at all
means the queue never planned it, which is a different bug in a different
place. All three admin pages are linked from Preferences, admin-only, so none
of them needs its URL remembered.

## Updating (every deploy after the first)

```bash
cd ~/app && git pull && uv sync && uv run alembic upgrade head \
  && sudo systemctl restart concert-reminder
```
Then verify with section 7: `git log --oneline -1` (right commit), `/healthz`
(app alive), and the `curl -sI` header check if you touched the Caddyfile.

### If the pull brings a migration

Check before running the ritual above: `git log -p --stat -1 -- alembic/versions/`.
If a new revision appeared, use this instead - the one-liner runs migrations
against a live writer, which is fine for adding a column and NOT fine for a
table rebuild (SQLite migrates in batch mode, and a rebuild against a running
app has already failed here once, leaving an `_alembic_tmp_*` table behind).

```bash
sudo systemctl stop concert-reminder
sqlite3 ~/app/app.db ".backup /home/ubuntu/pre-migration.db"
ls -lh /home/ubuntu/pre-migration.db     # STOP if this is missing or zero
cd ~/app && git pull && uv sync
uv run alembic upgrade head
sudo systemctl start concert-reminder
sqlite3 ~/app/app.db "PRAGMA foreign_key_check;"   # want no output
```

The backup line is a gate, not a suggestion: `alembic upgrade head` rewrites
whole tables, and the nightly S3 backup can be up to 24h stale (or broken -
check `~/backup.log`).

**If a migration fails partway**, it will usually have rolled back cleanly -
confirm with `sqlite3 ~/app/app.db "SELECT * FROM alembic_version;"`. If that
still shows the OLD revision, the data is intact and only debris is left:

```bash
sqlite3 ~/app/app.db "SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp%';"
sqlite3 ~/app/app.db "DROP TABLE _alembic_tmp_<whatever_it_named>;"
```
Then fix the migration and re-run. If `alembic_version` has ADVANCED but the
schema looks wrong, do not improvise - restore `pre-migration.db` instead.

### Migrations needing a non-standard ritual

Named individually because in both cases the diff does not look like what it
is, and the section above cannot be followed correctly by reading the revision
file alone. Both are already written; this list is for whoever deploys them and
for whoever writes the next one.

**`ce43bfcfcae3` (drop the legacy free-text venue columns) reverses the
order**: restart on the NEW code BEFORE `alembic upgrade head`, so the old
process cannot SELECT columns that no longer exist mid-deploy. The rule and
the reasoning live in CLAUDE.md's `src/app/db/` section (the "legacy free-text
venue columns are GONE" entry) and are deliberately not repeated here - it
binds every future column-DROP migration, so it is a codebase rule that
happens to have a deploy consequence, not a one-off. Already deployed; listed
so the precedent is findable from this file.

**`aebefef6ca70` (broadcasts) rebuilds `notifications`** by copy-and-move,
because SQLite cannot add a foreign key outside Alembic's batch mode.
`notifications` has live writers (the scheduler every 60s, and
`handle_newly_tagged` from web routes), so STOP the service for that deploy
rather than upgrading underneath it - the full stop-and-back-up block above,
not the one-liner:

    sudo systemctl stop concert-reminder
    sqlite3 ~/app/app.db ".backup /home/ubuntu/pre-migration.db"
    cd ~/app && git pull && uv sync && uv run alembic upgrade head
    sudo systemctl start concert-reminder

Queued reminders are unaffected by the pause: `reminder_queue` is materialized,
so a tick missed during the stop delivers on the next one. Worth spelling out
because `git log -p --stat -1 -- alembic/versions/` shows two `add_column`
calls and a `create_foreign_key` on an existing table, which reads like the
add-a-column case the one-liner is fine for; the FK is what forces batch mode,
and batch mode on SQLite IS a table rebuild.

## Disaster recovery

New box -> steps 1-2 -> restore latest `app-*.db.gz` from S3 to `~/app/app.db`
-> steps 4-5 -> flip the Cloudflare A record to the new IP. Total state is one
file; total recovery is under an hour.
