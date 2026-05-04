#!/usr/bin/env bash
# Dumps the SMI PostgreSQL database to backups/ and prunes files older than RETAIN_DAYS.
# Reads credentials from .env in the project root (same file docker-compose uses).
# Usage: ./data-pipeline/backup_db.sh
#        PROJECT_DIR=/other/path ./data-pipeline/backup_db.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(dirname "$SCRIPT_DIR")}"
ENV_FILE="$PROJECT_DIR/.env"
BACKUP_DIR="$PROJECT_DIR/backups"
RETAIN_DAYS=7
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTFILE="$BACKUP_DIR/smi_${TIMESTAMP}.sql.gz"

# Load credentials from .env (skip comments and blank lines)
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2
  exit 1
fi
while IFS='=' read -r key value; do
  [[ "$key" =~ ^[A-Z_]+$ ]] && [[ -n "$value" ]] && export "$key=$value"
done < <(grep -E '^[A-Z_]+=' "$ENV_FILE")

: "${POSTGRES_USER:?POSTGRES_USER not set in .env}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set in .env}"
: "${POSTGRES_DB:?POSTGRES_DB not set in .env}"

mkdir -p "$BACKUP_DIR"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting backup → $OUTFILE"

# pg_dump runs inside the postgres container; output is gzip-compressed on the host.
# --no-owner / --no-acl make the dump portable (restore works under any superuser).
PGPASSWORD="$POSTGRES_PASSWORD" docker compose \
  --project-directory "$PROJECT_DIR" \
  exec -T postgres \
  pg_dump \
    --username "$POSTGRES_USER" \
    --no-owner \
    --no-acl \
    "$POSTGRES_DB" \
  | gzip > "$OUTFILE"

SIZE="$(du -sh "$OUTFILE" | cut -f1)"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup complete — $SIZE"

# Prune backups older than RETAIN_DAYS days
find "$BACKUP_DIR" -name "smi_*.sql.gz" -mtime +"$RETAIN_DAYS" -print -delete

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Done. Backups older than ${RETAIN_DAYS}d pruned."
