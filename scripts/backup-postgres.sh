#!/usr/bin/env bash
# Nightly pg_dump backup of the MeshWeave Postgres volume.
#
# Cron example (03:00 every night, keep 14 daily backups):
#   0 3 * * * MESHWEAVE_BACKUP_DIR=/var/backups/meshweave /path/to/backup-postgres.sh
#
# Restores: see docs/backup-restore.md
set -euo pipefail

BACKUP_DIR="${MESHWEAVE_BACKUP_DIR:-/var/backups/meshweave}"
RETENTION_DAYS="${MESHWEAVE_BACKUP_RETENTION_DAYS:-14}"
COMPOSE_FILE="${MESHWEAVE_COMPOSE_FILE:-docker-compose.prod.yaml}"
ENV_FILE="${MESHWEAVE_ENV_FILE:-.env.prod}"
CONTAINER="${MESHWEAVE_PG_CONTAINER:-postgres}"
DB_NAME="${POSTGRES_DB:-meshweave}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$BACKUP_DIR/meshweave-$STAMP.sql.gz"

echo "Backing up $DB_NAME to $FILE"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T "$CONTAINER" \
  pg_dump -U "${POSTGRES_USER:?}" "$DB_NAME" | gzip > "$FILE"

find "$BACKUP_DIR" -name 'meshweave-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
echo "Done. Current backups:"
ls -lh "$BACKUP_DIR"
