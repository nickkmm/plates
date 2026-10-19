#!/bin/bash
# Nightly backup of the plates app (runs on the VPS). Suggested cron:
#   45 2 * * * /opt/plates/scripts/backup_plates.sh >> /var/log/backup_plates.log 2>&1
# Backs up (1) the Postgres database (pg_dump custom format) and (2) the volume
# with the files uploaded through the public declaration form (signatures).
# The DB password is not here: pg_dump runs inside the container.
set -euo pipefail

DEST=/var/backups/plates
KEEP_DAYS=30
DB_CONTAINER=plates-db-1
UPLOADS_VOLUME=plates_plates_uploads
STAMP=$(date +%F_%H%M)
mkdir -p "$DEST"

DB_FILE="$DEST/plates_db_${STAMP}.dump"
docker exec "$DB_CONTAINER" sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$DB_FILE.tmp"
# A failed dump must never be kept as a valid backup: the archive has to be listable.
docker exec -i "$DB_CONTAINER" pg_restore -l < "$DB_FILE.tmp" > /dev/null
mv "$DB_FILE.tmp" "$DB_FILE"

UPLOADS_DIR_HOST=$(docker volume inspect "$UPLOADS_VOLUME" --format '{{ .Mountpoint }}')
UP_FILE="$DEST/plates_uploads_${STAMP}.tar.gz"
tar -czf "$UP_FILE.tmp" -C "$UPLOADS_DIR_HOST" .
gzip -t "$UP_FILE.tmp"
mv "$UP_FILE.tmp" "$UP_FILE"

find "$DEST" -name 'plates_db_*.dump' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name 'plates_uploads_*.tar.gz' -mtime +"$KEEP_DAYS" -delete
echo "Backup OK $(date): $(basename "$DB_FILE") ($(du -h "$DB_FILE" | cut -f1)), $(basename "$UP_FILE") ($(du -h "$UP_FILE" | cut -f1)); $(ls "$DEST" | wc -l) files kept"
