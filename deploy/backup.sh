#!/usr/bin/env bash
# Nightly SQLite backup to S3. Cron: 0 9 * * * /home/ubuntu/app/deploy/backup.sh
# (09:00 UTC = early morning Atlantic). Requires: aws cli + an S3 bucket.
set -euo pipefail
DB=/home/ubuntu/app/app.db
BUCKET=s3://YOUR-BUCKET/concert-reminder
STAMP=$(date +%F)
sqlite3 "$DB" ".backup /tmp/app-$STAMP.db"
gzip -f "/tmp/app-$STAMP.db"
aws s3 cp "/tmp/app-$STAMP.db.gz" "$BUCKET/app-$STAMP.db.gz"
rm "/tmp/app-$STAMP.db.gz"
