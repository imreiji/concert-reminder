#!/usr/bin/env bash
# Nightly SQLite backup to S3.
#   cron: 0 9 * * * /home/ubuntu/app/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
#   (09:00 UTC = early morning Atlantic)
# Retention is handled by an S3 lifecycle rule (expire after 30 days) - see docs/deploy.md.
set -euo pipefail

DB=/home/ubuntu/app/app.db
ENV_FILE=/etc/default/dekimasen-backup

# The bucket is server-local config, so it lives outside the repo: editing it
# into this tracked file makes every future `git pull` conflict here, and the
# obvious "keep mine" resolution silently keeps the OLD script.
# Sourced by the script rather than by the cron line, so the crontab entry
# never has to change. An already-exported BACKUP_BUCKET wins over the file,
# so a one-off `BACKUP_BUCKET=s3://scratch ./backup.sh` behaves as written.
if [[ -f "$ENV_FILE" ]]; then
  bucket_from_env="${BACKUP_BUCKET:-}"
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  if [[ -n "$bucket_from_env" ]]; then
    BACKUP_BUCKET="$bucket_from_env"
  fi
fi

BUCKET="${BACKUP_BUCKET:?backup.sh: BACKUP_BUCKET is not set - create $ENV_FILE containing BACKUP_BUCKET=\"s3://your-bucket/dekimasen\" (see docs/deploy.md)}"
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

# .backup takes a consistent snapshot even while the app is writing (WAL-safe).
sqlite3 "$DB" ".backup /tmp/app-$STAMP.db"
gzip -f "/tmp/app-$STAMP.db"
aws s3 cp "/tmp/app-$STAMP.db.gz" "$BUCKET/app-$STAMP.db.gz" --only-show-errors
echo "backup ok: $STAMP"
