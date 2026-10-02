"""Replaces the old single-choice "Sauce" group (from the first import) by the
Just-Eat sauce list (13 options, multiple choice). Every item that had the old
group gets the new one as Pflicht, max. 2 Saucen.

    python3 scripts/upgrade_sauce.py [pfad/zur/bestellsystem.db]

Safe to re-run: does nothing once "Sauce" is already multiple choice.
"""
import shutil
import sqlite3
import sys
import time

from seed_beilagen import GROUPS

path = sys.argv[1] if len(sys.argv) > 1 else "bestellsystem.db"
con = sqlite3.connect(path)
old = con.execute("SELECT id, selection_type FROM option_groups WHERE name = 'Sauce'").fetchone()
if old is None:
    sys.exit("Keine Gruppe 'Sauce' gefunden.")
if old[1] == "multiple":
    sys.exit("Sauce ist schon Mehrfachauswahl, nichts zu tun.")

backup = f"{path}.bak-vor-sauce-{int(time.time())}"
shutil.copyfile(path, backup)
print("Backup:", backup)

old_id = old[0]
sauce = next(g for g in GROUPS if g[0] == "Sauce")
new_id = con.execute(
    "INSERT INTO option_groups (name, selection_type) VALUES (?, ?)", ("Sauce NEU", "multiple")
).lastrowid
con.executemany(
    "INSERT INTO options (option_group_id, name, price_delta) VALUES (?, ?, ?)",
    [(new_id, n, p) for n, p in sauce[2]],
)
items = [r[0] for r in con.execute(
    "SELECT menu_item_id FROM item_option_groups WHERE option_group_id = ?", (old_id,)
)]
con.executemany(
    "INSERT INTO item_option_groups (menu_item_id, option_group_id, required, max_selections, sort_order) "
    "VALUES (?, ?, 1, 2, 0)",
    [(i, new_id) for i in items],
)
con.execute("DELETE FROM item_option_groups WHERE option_group_id = ?", (old_id,))
con.execute("DELETE FROM options WHERE option_group_id = ?", (old_id,))
con.execute("DELETE FROM option_groups WHERE id = ?", (old_id,))
con.execute("UPDATE option_groups SET name = 'Sauce' WHERE id = ?", (new_id,))
con.commit()
print(f"Sauce ersetzt: {len(sauce[2])} Optionen, Mehrfachauswahl, Pflicht, max. 2, bei {len(items)} Artikeln.")
