#!/usr/bin/env bash
# Nightly SQLite backup to S3.
#   cron: 0 9 * * * /home/ubuntu/app/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
#   (09:00 UTC = early morning Atlantic)
# Retention is handled by an S3 lifecycle rule (expire after 30 days) - see docs/deploy.md.
set -euo pipefail

DB=/home/ubuntu/app/app.db
BUCKET="s3://YOUR-BUCKET-NAME/dekimasen"   # <-- edit me
STAMP=$(date -u +%F)

if [[ "$BUCKET" == *YOUR-BUCKET-NAME* ]]; then
  echo "backup.sh: edit BUCKET first" >&2
  exit 1
fi

# .backup takes a consistent snapshot even while the app is writing (WAL-safe).
sqlite3 "$DB" ".backup /tmp/app-$STAMP.db"
gzip -f "/tmp/app-$STAMP.db"
aws s3 cp "/tmp/app-$STAMP.db.gz" "$BUCKET/app-$STAMP.db.gz" --only-show-errors
rm -f "/tmp/app-$STAMP.db.gz"
echo "backup ok: $STAMP"
