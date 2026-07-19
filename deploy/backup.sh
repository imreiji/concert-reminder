#!/usr/bin/env bash
# Nightly SQLite backup to S3.
#   cron: 0 9 * * * /home/ubuntu/app/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
#   (09:00 UTC = early morning Atlantic)
# Retention is handled by an S3 lifecycle rule (expire after 30 days) - see docs/deploy.md.
set -euo pipefail

DB=/home/ubuntu/app/app.db
ENV_FILE=/etc/default/dekimasen-backup
# Read by the app's `backup` health check. Keep in sync with the
# backup_marker_path setting in src/app/config.py.
MARKER=/home/ubuntu/.dekimasen-backup-ok

# The bucket is server-local config, so it lives outside the repo: editing it
# into this tracked file makes every future `git pull` conflict here, and the
# obvious "keep mine" resolution silently keeps the OLD script.
# Read here rather than by the cron line, so the crontab entry never has to
# change. An already-exported BACKUP_BUCKET wins over the file, so a one-off
# `BACKUP_BUCKET=s3://scratch ./backup.sh` behaves as written.
#
# Parsed, NOT sourced. Sourcing runs the file as shell: a stray `sudo tee`
# with no input once captured pasted terminal lines into this file, and the
# resulting `~/app/deploy/backup.sh` line made the script source-and-re-exec
# itself until the box ran out of processes. One string is not worth arbitrary
# execution; a garbled file now just yields an empty value and trips the
# guard below.
if [[ -e "$ENV_FILE" ]]; then
  # Checked separately from -e: cron runs this as the app user, so an env file
  # left owned by root (mode 600) exists but cannot be read. Without this the
  # failure is a bare "Permission denied" naming no fix.
  if [[ ! -r "$ENV_FILE" ]]; then
    echo "backup.sh: $ENV_FILE is not readable by $(id -un) - fix with:" >&2
    echo "  sudo chown $(id -un) $ENV_FILE" >&2
    exit 1
  fi
  if [[ -z "${BACKUP_BUCKET:-}" ]]; then
    # Last assignment wins, matching what sourcing would have done.
    BACKUP_BUCKET=$(sed -n 's/^[[:space:]]*BACKUP_BUCKET=//p' "$ENV_FILE" | tail -n 1)
    BACKUP_BUCKET=${BACKUP_BUCKET%\"} ; BACKUP_BUCKET=${BACKUP_BUCKET#\"}
    BACKUP_BUCKET=${BACKUP_BUCKET%\'} ; BACKUP_BUCKET=${BACKUP_BUCKET#\'}
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

# Success marker for the app's health check. The IAM user is PutObject-only by
# design, so the app cannot ask S3 whether this landed - this file is the only
# local evidence a backup ran. Written only after the upload succeeds, so a
# failed run leaves it stale rather than lying. set -e means we never reach
# here on failure.
date -u +%Y-%m-%dT%H:%M:%S+00:00 > "$MARKER"

echo "backup ok: $STAMP"
