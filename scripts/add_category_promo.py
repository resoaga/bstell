"""Adds categories.is_promo (Aktions-Kategorie fuer den Hero). Safe to re-run.

    python3 scripts/add_category_promo.py [pfad/zur/bestellsystem.db]
"""
import sqlite3
import sys

con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "bestellsystem.db")
columns = [row[1] for row in con.execute("PRAGMA table_info(categories)")]
if not columns:
    sys.exit("Tabelle categories nicht gefunden - falsche Datenbank?")
if "is_promo" in columns:
    sys.exit("categories.is_promo gibt es schon, nichts zu tun.")
con.execute("ALTER TABLE categories ADD COLUMN is_promo BOOLEAN NOT NULL DEFAULT 0")
con.commit()
print("categories.is_promo angelegt.")
