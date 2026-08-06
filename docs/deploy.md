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

**AI triage** (the button on `/admin/discoveries` that classifies the open
discovery leads and drafts the survivors) needs three more keys, all absent by
default:
```
DEEPSEEK_API_KEY=<your DeepSeek API key>
DEEPSEEK_MODEL=<the exact V4 Flash model id>
TRIAGE_ENABLED=true
```
`TRIAGE_ENABLED` gates the SCHEDULER PICKUP exactly as `DISCOVERY_ENABLED`
gates the daily sweep - and unlike the sweep's button, a triage request is NOT
honoured with the flag off. The row is written and simply never picked up, so a
deploy that has not opted in cannot spend a key by accident (the button and its
status strip are hidden there too). `DEEPSEEK_MODEL` deliberately has no
default: hardcoding a guess at a third party's current alias would start
billing a model nobody chose the moment the flag flipped. Only one of the three
is a credential - `DEEPSEEK_API_KEY`, which lives only here and in the owner's
local `.env`, like every secret; `DEEPSEEK_MODEL` and `TRIAGE_ENABLED` are
configuration and are safe to write down anywhere. Leave all three absent and
the app runs exactly as before.

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
- **Crawler-trap WAF rule** (dashboard-only -- recreate it on any Cloudflare
  re-setup): Security -> WAF -> Custom rules -> Managed Challenge when
  URI path equals `/discover` AND query string contains `tag=`. Challenge,
  NOT block: Discover writes filtered URLs into the address bar via
  `history.replaceState`, so real signed-in humans reload/bookmark/share
  `?tag=` URLs and must be able to pass. Also enable Cloudflare's AI-crawler
  blocking toggle. These are the edge half of the 2026-08-04 crawl-outage
  remedy; the repo half (`rel="nofollow"` on Discover's filter links plus
  the `/robots.txt` route) deploys with the app. Whatever edge rules exist
  must leave `/robots.txt` itself reachable to crawlers -- a bot challenged
  on its robots.txt fetch never reads the disallow.
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

Add a SECOND UptimeRobot monitor with a response-time alert on
`https://dekimasen.app/` (dashboard-only -- recreate it alongside the
keyword monitor). It targets `/` rather than `/healthz` because user-facing
latency is the signal that was actually missing -- `/healthz`'s own latency
is already proven meaningless as an alert: during the 2026-08-04 crawler
outage it answered 200 `"ok":true` in 72 seconds and the monitor stayed
green for the entire half-day the site was unusable. If the account's tier
will not alert on response time (that has historically been a paid-tier
feature), fall back to a second keyword monitor on `/` with a short request
timeout -- a slow response times out and fires the alert on any tier.

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

### Calibrating the first AI-triage run

The AI-triage deploy brings ONE migration, `ff500647fa9c`, and it is the
easiest kind: a single `CREATE TABLE triage_runs` with nothing existing writing
to it, so the standard block below covers it and it needs no entry in the
non-standard list. Set the three keys, restart, and `/admin/discoveries` grows
a triage button and a status strip.

Then calibrate, because prompt quality is a judgment no test in CI can make.
**One press is one capped batch** - one classify call over every open lead plus
at most 25 fetch-and-draft pairs, roughly 7-8 minutes of scheduler tick and a
cost measured in cents. The status strip flips to done when the run finishes;
the run row carries the counts and the tokens it billed. A classify call over a
216-lead queue should bill roughly 14k input tokens and a few thousand output -
if the run row's token counts run tens of thousands of tokens higher than
that, or a press comes back `TriageResponseError`, check `deepseek_model` is
still `deepseek-v4-flash` and that the deploy includes the 2026-08-05 fix
(`app/llm.py` sends `"thinking": {"type": "disabled"}`). The first production
press, before that fix, burned ~74k tokens reasoning silently before an empty
reply tripped the parser - `app.llm.chat` now fails loud with the
`finish_reason`/empty-content it saw instead of leaving that to the YAML
parser to discover. Three things to check before pressing again, in this
order:

- **Read the prune plan critically BEFORE applying it.** "Review prune plan"
  prefills the existing paste box from the run's stored YAML, and nothing has
  been dismissed at that point - the plan → apply screen is still the only
  thing that dismisses a lead, exactly as when an agent wrote the file. A
  dismissal has no undo anywhere in the app, so this is the step that matters.
- **Open `/concerts/import/pending` and read one skeleton draft.** Judge the
  trilingual titles and leg labels; that is the quality question. Its `rounds`
  list is empty by design and stays empty whatever the model returned - rounds
  are stripped in code - so the draft is a starting point a human still
  completes, not a finished event. (Phase 2's completion pass is what fills
  them, on a separate press and under a separate rule; see the next section.)
- **Note which productions got drafted.** `open_leads` orders by
  `event_date DESC`, so on a backlog longer than the 25-draft cap the
  FURTHEST-FUTURE events are drafted first and the most imminent ones wait for
  later presses. On a large backlog that is the opposite of urgency order:
  press repeatedly to work through it, or triage the near-term leads by hand
  rather than assuming the button reached them.

If the judgment disappoints, the fallback needs no code and no config: stop at
the prune plan and never press past it. The classify half alone is what cuts
the queue by the largest factor.

### The completion pass, and its approval queue

Phase 2 brings ONE migration, `2fa4d11a473a` (columns on `triage_runs` and
`pending_drafts`, plus a new `fetch_domains` table) and NO new env vars - it
reuses `TRIAGE_ENABLED` and the same two DeepSeek keys. With the flag on,
`/concerts/import/pending` grows a **Complete drafts with AI** button: one
press writes a `TriageRun` row with `kind="complete"`, the next 60s tick picks
it up, and for up to 15 of that admin's pending drafts it reads the
`official_url` the draft already names, asks the model for the ticket rounds,
and keeps only the ones it can verify.

**The first press will complete NOTHING, and that is correct.** Every host it
wants is unknown, so it records them and stops - the admin DM the run queues
counts those drafts as "waiting on domain approval" rather than skipped, and
the pending page grows a banner saying how many websites are waiting. Open
`/admin/fetch-domains` (linked from that banner, and from Preferences with the
other admin pages), approve the ticket vendors and franchise sites you
recognise, decline the rest, and press again. Thereafter only genuinely NEW hosts interrupt, and a declined
host is never proposed again. That page is the whole of why an arbitrary-host
fetch is acceptable here: every other fetch this app makes is pinned to a host
named in code, and for `official_url` a person is what the pin became. An
unapproved host costs one skipped draft, never a failed run.

Two things to check on the first real completion:

- **Read the quotes, not just the timestamps.** Every round the pass keeps
  carries the page line it was read from, rendered under the round on the
  preview. A round whose quote does not say what the timestamp says is exactly
  the failure this feature is built to make visible - and the quote is meant to
  be enough to check the round WITHOUT opening the ticket page. If it is not,
  say so; that is the property no test can assert.
- **Read the rejection banner.** A rejected round is often a REAL deadline the
  model quoted loosely rather than an invented one - the grounding rule is
  deliberately the stricter reading, so it false-rejects some phrasings (a
  one-line 受付期間 window with two times on it is a known example, and its
  closing time is currently rejected). Those are the ones to type in by hand.
  Nothing is dropped silently: every rejection reaches that banner with its
  reason.

Nothing here creates a concert. A completed draft is still a pending draft
whose preview you press **Create event** on, exactly as before. A draft is
attempted at most ONCE - the moment a call has been paid for, the draft is
marked and later presses skip it, even if it came back with nothing - so
pressing repeatedly works through the queue rather than re-billing the front of
it. A draft skipped WITHOUT a call (no URL, unapproved host, dead fetch) is not
marked and is retried on the next press.

If a page comes back empty or useless (a JavaScript-rendered vendor page is the
usual cause), open that draft and use **Fill rounds from a page I paste**:
select all on the real page in a browser, paste, and the same verification
rules apply to what you pasted. That path needs no fetch and no approval, so it
also covers any host you would rather not put on the approved list.
That box only offers itself on a draft with no rounds yet, and the route
refuses the same case with a 422 - `merge_rounds` replaces a draft's rounds
wholesale, so pasting a page over a draft that already has a real ladder
(one an agent authored, or one you already typed by hand) would destroy it
with no undo.

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
