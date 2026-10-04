"""Liefergebiete: legt die Tabelle an und macht aus der bisherigen PLZ-Liste eine erste Zone
(mit den bisherigen Lieferkosten und dem bisherigen Mindestbestellwert). Wiederholbar.

    python3 scripts/migrate_zones.py [pfad/zur/bestellsystem.db]
"""
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bestellsystem.db"
con = sqlite3.connect(path)
con.execute(
    "CREATE TABLE IF NOT EXISTS delivery_zones (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR NOT NULL, "
    "zips VARCHAR, delivery_fee FLOAT, min_order FLOAT, sort_order INTEGER)"
)
have = con.execute("SELECT COUNT(*) FROM delivery_zones").fetchone()[0]
row = None
try:
    row = con.execute("SELECT delivery_zips, delivery_fee, minimum_order_value FROM restaurant_settings LIMIT 1").fetchone()
except sqlite3.OperationalError:
    pass
if have == 0 and row and (row[0] or "").strip():
    zips = ", ".join(sorted({z for z in row[0].replace(",", " ").split() if z}))
    con.execute(
        "INSERT INTO delivery_zones (name, zips, delivery_fee, min_order, sort_order) VALUES (?, ?, ?, ?, 0)",
        ("Liefergebiet", zips, row[1] or 0.0, row[2] or 0.0),
    )
    con.execute("UPDATE restaurant_settings SET delivery_zips = ''")
    print("Zone 'Liefergebiet' aus der bisherigen PLZ-Liste angelegt")
else:
    print("Zonen: nichts zu tun")
con.commit()
