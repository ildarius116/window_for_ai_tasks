#!/usr/bin/env bash
# MWS GPT Platform — PostgreSQL restore script
# Restores a gzipped SQL dump to a specified database.
#
# Usage: ./scripts/restore.sh <database> <backup_file>
#   e.g.: ./scripts/restore.sh memory backups/memory_2026-03-29_120000.sql.gz
#
# NOTE: Run `chmod +x scripts/restore.sh` to make this executable.

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <database> <backup_file>"
  echo ""
  echo "Databases: openwebui, litellm, langfuse, memory"
  echo ""
  echo "Example:"
  echo "  $0 memory backups/memory_2026-03-29_120000.sql.gz"
  exit 1
fi

DB="$1"
FILE="$2"
PG_USER="${POSTGRES_USER:-mws}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$FILE" ]; then
  echo "Error: File not found: $FILE"
  exit 1
fi

echo "=== MWS GPT Restore ==="
echo "Database: $DB"
echo "File:     $FILE"
echo ""
read -p "This will overwrite data in '$DB'. Continue? (y/N) " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
  echo "Aborted."
  exit 0
fi

echo "Restoring $FILE → $DB..."
gunzip -c "$FILE" | docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T postgres \
  psql -U "$PG_USER" -d "$DB"

echo "=== Restore complete ==="
