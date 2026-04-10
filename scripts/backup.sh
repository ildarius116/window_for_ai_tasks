#!/usr/bin/env bash
# MWS GPT Platform — PostgreSQL backup script
# Dumps all 4 databases to backups/ with date-stamped filenames.
# Retains last 7 days of backups.
#
# NOTE: Run `chmod +x scripts/backup.sh` to make this executable.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_DIR/backups"
DATE="$(date +%Y-%m-%d_%H%M%S)"
DATABASES=(openwebui litellm langfuse memory)
PG_USER="${POSTGRES_USER:-mws}"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

echo "=== MWS GPT Backup — $DATE ==="

for db in "${DATABASES[@]}"; do
  file="$BACKUP_DIR/${db}_${DATE}.sql.gz"
  echo "Dumping $db → $file"
  docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T postgres \
    pg_dump -U "$PG_USER" "$db" | gzip > "$file"
  echo "  Done ($(du -h "$file" | cut -f1))"
done

# Remove backups older than $RETENTION_DAYS days
echo "Cleaning up backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +"$RETENTION_DAYS" -delete

echo "=== Backup complete ==="
