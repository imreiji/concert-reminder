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
BASE_URL=https://dekimasen.app
SESSION_SECRET=<fresh: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=sqlite+aiosqlite:////home/ubuntu/app/app.db
DEFAULT_TIMEZONE=America/Moncton
```
Note the **four** slashes in DATABASE_URL: absolute path, so the DB location
doesn't depend on the service's working directory.

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
# reads BACKUP_BUCKET, and sources this file itself if it exists:
sudo tee /etc/default/dekimasen-backup <<< 'BACKUP_BUCKET="s3://YOUR-BUCKET-NAME/dekimasen"'
sudo chmod 600 /etc/default/dekimasen-backup
~/app/deploy/backup.sh         # test run -> "backup ok: <date>"
crontab -e                     # add:
# 0 9 * * * /home/ubuntu/app/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
```
With no bucket configured the script exits non-zero on line 1 of real work and
says which file to create, so a misconfigured cron fails loudly in
`~/backup.log` rather than silently backing up nothing.

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

## Updating (every deploy after the first)

```bash
cd ~/app && git pull && uv sync && uv run alembic upgrade head \
  && sudo systemctl restart concert-reminder
```
Then verify with section 7: `git log --oneline -1` (right commit), `/healthz`
(app alive), and the `curl -sI` header check if you touched the Caddyfile.

## Disaster recovery

New box -> steps 1-2 -> restore latest `app-*.db.gz` from S3 to `~/app/app.db`
-> steps 4-5 -> flip the Cloudflare A record to the new IP. Total state is one
file; total recovery is under an hour.
