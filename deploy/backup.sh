#!/usr/bin/env bash
# Nightly SQLite backup to S3.
#   cron: 0 9 * * * /home/ubuntu/app/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
#   (09:00 UTC = early morning Atlantic)
# Retention is handled by an S3 lifecycle rule (expire after 30 days) - see docs/deploy.md.
set -euo pipefail

DB=/home/ubuntu/app/app.db
BUCKET="s3://YOUR-BUCKET-NAME/dekimasen"   # <-- edit me
STAMP=$(date -u +%F)

# The snapshot is the entire user database in the clear. Under the default
# umask 022 it lands mode 644 - readable by every local account for as long
# as the upload takes. (The systemd unit's PrivateTmp does not cover this:
# cron runs the script outside the service's namespace.) 077 makes both the
# .db and the .gz mode 600.
umask 077

# This trap is the only cleanup: it fires on success AND on failure, whereas
# an rm at the end of the happy path would not - set -e means a failed aws
# s3 cp leaves an unencrypted copy of the database in /tmp forever.
# Installed AFTER STAMP is set, and with quoted literal paths rather than a
# glob - with STAMP empty, /tmp/app-.db* would match unrelated files.
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
