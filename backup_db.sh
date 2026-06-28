#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="${1:-$SCRIPT_DIR/shtab.db}"
BACKUP_DIR="$SCRIPT_DIR/backups"

if [ ! -f "$DB_PATH" ]; then
  echo "БД не найдена: $DB_PATH"
  exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y-%m-%d_%H%M%S)
DEST="$BACKUP_DIR/shtab_${STAMP}.db"
cp "$DB_PATH" "$DEST"
echo "Бэкап: $DEST ($(du -h "$DEST" | cut -f1))"
