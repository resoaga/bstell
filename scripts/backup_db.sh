#!/bin/sh
# Taegliche Sicherung der Datenbank (SQLite) - als Plesk "Geplante Aufgabe", z.B. taeglich 03:30:
#     sh ~/httpdocs/scripts/backup_db.sh
# Legt konsistente Kopien in ~/backups/bstell an und behaelt die letzten 30.
set -e
cd "$(dirname "$0")/.."
DEST="$HOME/backups/bstell"
mkdir -p "$DEST"
STAMP=$(date +%Y-%m-%d_%H%M)
python3 - "$DEST/bestellsystem_$STAMP.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect("bestellsystem.db")
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
dst.close(); src.close()
PY
gzip -f "$DEST/bestellsystem_$STAMP.db"
ls -1t "$DEST"/bestellsystem_*.db.gz | tail -n +31 | xargs -r rm --
tar -czf "$DEST/uploads_$STAMP.tar.gz" app/static/uploads 2>/dev/null || true
ls -1t "$DEST"/uploads_*.tar.gz | tail -n +8 | xargs -r rm --
echo "Backup ok: $DEST/bestellsystem_$STAMP.db.gz"
