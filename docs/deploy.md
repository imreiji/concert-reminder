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

SESSION_SECRET is enforced, not advisory: with an https BASE_URL, startup
raises on a missing, placeholder, or under-32-character secret. Since
`alembic/env.py` imports the app config, that means `alembic upgrade head`
is where a bad `.env` dies -- before the service ever starts.

## 6. Discord OAuth redirect

Developer Portal -> your app -> OAuth2 -> Redirects -> **add**
`https://dekimasen.app/auth/callback` (keep the localhost one for dev).

## 7. Verify

```bash
curl -s https://dekimasen.app/healthz        # {"ok":true,"bot_enabled":true}
journalctl -u concert-reminder -f            # bot online + scheduler running
```
Then in a browser: sign in with Discord, confirm the editor badge.

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
nano ~/app/deploy/backup.sh    # set BUCKET
~/app/deploy/backup.sh         # test run -> "backup ok: <date>"
crontab -e                     # add:
# 0 9 * * * /home/ubuntu/app/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
```

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

## Disaster recovery

New box -> steps 1-2 -> restore latest `app-*.db.gz` from S3 to `~/app/app.db`
-> steps 4-5 -> flip the Cloudflare A record to the new IP. Total state is one
file; total recovery is under an hour.
