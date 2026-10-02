"""One-off migration: per-item option groups -> shared option-group library.

Run once from the project directory (where bestellsystem.db lives), with the
app stopped or idle:

    python3 scripts/migrate_option_library.py [pfad/zur/bestellsystem.db]

Identical groups (same name, type and options with prices) are merged into one
library group; every old group becomes an assignment (item_option_groups) that
keeps its required / max_selections values. A backup copy is written first.
Safe to re-run: does nothing once option_groups has no menu_item_id column.
"""
import shutil
import sqlite3
import sys
import time

path = sys.argv[1] if len(sys.argv) > 1 else "bestellsystem.db"
con = sqlite3.connect(path, isolation_level=None)
columns = [row[1] for row in con.execute("PRAGMA table_info(option_groups)")]
if not columns:
    sys.exit("Tabelle option_groups nicht gefunden - falsche Datenbank?")
if "menu_item_id" not in columns:
    sys.exit("Schon migriert, nichts zu tun.")

backup = f"{path}.bak-vor-optionsbibliothek-{int(time.time())}"
shutil.copyfile(path, backup)
print("Backup:", backup)

con.execute("PRAGMA foreign_keys=OFF")
con.execute("BEGIN")
try:
    con.execute(
        """CREATE TABLE IF NOT EXISTS item_option_groups (
            id INTEGER NOT NULL PRIMARY KEY,
            menu_item_id INTEGER NOT NULL REFERENCES menu_items (id),
            option_group_id INTEGER NOT NULL REFERENCES option_groups (id),
            required BOOLEAN NOT NULL DEFAULT 0,
            max_selections INTEGER,
            sort_order INTEGER DEFAULT 0
        )"""
    )

    groups = con.execute(
        "SELECT id, menu_item_id, name, selection_type, required, max_selections "
        "FROM option_groups ORDER BY id"
    ).fetchall()
    options = {}
    for option_id, group_id, name, price in con.execute(
        "SELECT id, option_group_id, name, price_delta FROM options ORDER BY id"
    ):
        options.setdefault(group_id, []).append((name, price or 0.0))

    canonical = {}  # signature -> kept group id
    merged_away = []
    seen_pairs = set()
    for gid, item_id, name, sel_type, required, max_sel in groups:
        signature = (name, sel_type, tuple(options.get(gid, [])))
        keep = canonical.setdefault(signature, gid)
        if keep != gid:
            merged_away.append(gid)
        if (item_id, keep) in seen_pairs:
            continue
        seen_pairs.add((item_id, keep))
        con.execute(
            "INSERT INTO item_option_groups (menu_item_id, option_group_id, required, max_selections, sort_order) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_id, keep, 1 if required else 0, max_sel, gid),
        )

    for gid in merged_away:
        con.execute("DELETE FROM options WHERE option_group_id = ?", (gid,))

    con.execute(
        "CREATE TABLE option_groups_new (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR NOT NULL, selection_type VARCHAR(8))"
    )
    con.execute(
        "INSERT INTO option_groups_new (id, name, selection_type) "
        "SELECT id, name, selection_type FROM option_groups WHERE id NOT IN (%s)"
        % ",".join(str(g) for g in merged_away or [-1])
    )
    con.execute("DROP TABLE option_groups")
    con.execute("ALTER TABLE option_groups_new RENAME TO option_groups")
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

print(f"{len(groups)} alte Gruppen -> {len(canonical)} Bibliotheks-Gruppen, {len(seen_pairs)} Zuweisungen.")
