#!/usr/bin/env bash
# Nightly SQLite backup to S3.
#   cron: 0 9 * * * /home/ubuntu/app/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
#   (09:00 UTC = early morning Atlantic)
# Retention is handled by an S3 lifecycle rule (expire after 30 days) - see docs/deploy.md.
set -euo pipefail

# The snapshot is the entire user database sitting in world-readable /tmp.
# Cron runs this outside the service's namespace, so the systemd unit's
# PrivateTmp=true does NOT cover it - umask is the only thing between the
# snapshot and every other local user. 077 makes both the .db and the .gz
# mode 600 (gzip copies the source file's mode, but it is created under this
# umask either way, so both paths are covered).
umask 077

DB=/home/ubuntu/app/app.db
BUCKET="s3://YOUR-BUCKET-NAME/dekimasen"   # <-- edit me
STAMP=$(date -u +%F)

# Installed only after STAMP is set: with STAMP empty this would expand to
# /tmp/app-.db and /tmp/app-.db.gz - harmless as written, but never let this
# line drift above the assignment or grow a glob. set -euo pipefail means a
# failed `aws s3 cp` exits before any cleanup, so without this trap a failed
# upload leaves an unencrypted copy of the database in /tmp indefinitely.
trap 'rm -f "/tmp/app-$STAMP.db" "/tmp/app-$STAMP.db.gz"' EXIT

if [[ "$BUCKET" == *YOUR-BUCKET-NAME* ]]; then
  echo "backup.sh: edit BUCKET first" >&2
  exit 1
fi

# .backup takes a consistent snapshot even while the app is writing (WAL-safe).
sqlite3 "$DB" ".backup /tmp/app-$STAMP.db"
gzip -f "/tmp/app-$STAMP.db"
aws s3 cp "/tmp/app-$STAMP.db.gz" "$BUCKET/app-$STAMP.db.gz" --only-show-errors
echo "backup ok: $STAMP"
# No explicit rm here any more - the EXIT trap removes both temp files on the
# success path and the failure path alike.
