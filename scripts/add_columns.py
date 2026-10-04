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
    ("restaurant_settings", "service_fee_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("restaurant_settings", "service_fee_mode", "VARCHAR DEFAULT 'percent'"),
    ("restaurant_settings", "service_fee_percent", "FLOAT DEFAULT 2.0"),
    ("restaurant_settings", "service_fee_fixed", "FLOAT DEFAULT 0"),
    ("restaurant_settings", "service_fee_label", "VARCHAR DEFAULT 'Servicegebühr'"),
    ("orders", "service_fee", "FLOAT DEFAULT 0"),
    ("menu_items", "sold_out_until", "DATETIME"),
    ("restaurant_settings", "online_payment_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("restaurant_settings", "footer_credit", "VARCHAR DEFAULT ''"),
    ("restaurant_settings", "vat_number", "VARCHAR DEFAULT ''"),
    ("restaurant_settings", "cancel_reasons", "TEXT DEFAULT ''"),
    ("restaurant_settings", "order_limit_per_hour", "INTEGER DEFAULT 5"),
    ("restaurant_settings", "orders_done_limit", "INTEGER DEFAULT 3"),
    ("restaurant_settings", "order_override", "VARCHAR DEFAULT ''"),
    ("restaurant_settings", "order_override_base", "VARCHAR DEFAULT ''"),
    ("orders", "service_fee_text", "VARCHAR DEFAULT ''"),
    ("orders", "cancel_reason", "VARCHAR DEFAULT ''"),
    ("restaurant_settings", "attach_receipt_pdf", "BOOLEAN NOT NULL DEFAULT 0"),
    ("orders", "customer_city", "VARCHAR DEFAULT ''"),
    ("orders", "payment_method", "VARCHAR DEFAULT 'cash'"),
    ("orders", "payrexx_gateway_id", "VARCHAR DEFAULT ''"),
    ("restaurant_settings", "delivery_zips", "VARCHAR DEFAULT ''"),
    ("restaurant_settings", "preorder_minutes", "INTEGER DEFAULT 60"),
    ("orders", "email", "VARCHAR DEFAULT ''"),
    ("orders", "device_key", "VARCHAR DEFAULT ''"),
    ("orders", "tracking_token", "VARCHAR DEFAULT ''"),
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

# Alte Bestellungen bekommen einen Tracking-Code (neue bekommen ihn beim Bestellen)
import secrets

if any(row[1] == "tracking_token" for row in con.execute("PRAGMA table_info(orders)")):
    missing = [r[0] for r in con.execute("SELECT id FROM orders WHERE tracking_token IS NULL OR tracking_token = ''")]
    for order_id in missing:
        con.execute("UPDATE orders SET tracking_token = ? WHERE id = ?", (secrets.token_urlsafe(16), order_id))
    print(f"orders.tracking_token: {len(missing)} alte Bestellungen aufgefuellt")
    con.execute("CREATE INDEX IF NOT EXISTS ix_orders_tracking_token ON orders (tracking_token)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_orders_device_key ON orders (device_key)")
con.commit()
