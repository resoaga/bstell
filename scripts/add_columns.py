"""Adds columns that newer app versions expect (SQLAlchemy create_all never
alters existing tables). Idempotent: skips columns that already exist.

    python3 scripts/add_columns.py [pfad/zur/bestellsystem.db]

To add a column in the future, append one line to COLUMNS.
"""
import sqlite3
import sys

COLUMNS = [
    ("categories", "is_promo", "BOOLEAN NOT NULL DEFAULT 0"),
    ("restaurant_settings", "hours_note", "VARCHAR DEFAULT ''"),
]

con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "bestellsystem.db")
for table, column, ddl in COLUMNS:
    existing = [row[1] for row in con.execute(f"PRAGMA table_info({table})")]
    if not existing:
        print(f"Tabelle {table} nicht gefunden - uebersprungen")
    elif column in existing:
        print(f"{table}.{column}: schon da")
    else:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        print(f"{table}.{column}: angelegt")
con.commit()
