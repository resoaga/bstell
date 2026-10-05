"""Notfall: setzt das Passwort eines Admin-Benutzers (legt ihn als Admin an, falls es ihn nicht gibt).

    python3 scripts/reset_admin_password.py BENUTZER NEUESPASSWORT [pfad/zur/bestellsystem.db]
"""
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("ADMIN_USERNAME", "x")
os.environ.setdefault("ADMIN_PASSWORD", "x")
os.environ.setdefault("SESSION_SECRET_KEY", "x")
from app.auth import hash_password  # noqa: E402

if len(sys.argv) < 3 or len(sys.argv[2]) < 8:
    sys.exit("Aufruf: python3 scripts/reset_admin_password.py BENUTZER NEUESPASSWORT(mind. 8 Zeichen) [db]")
user, password = sys.argv[1], sys.argv[2]
con = sqlite3.connect(sys.argv[3] if len(sys.argv) > 3 else "bestellsystem.db")
con.execute(
    "CREATE TABLE IF NOT EXISTS admin_users (id INTEGER PRIMARY KEY, username VARCHAR NOT NULL UNIQUE, "
    "password_hash VARCHAR NOT NULL, role VARCHAR NOT NULL DEFAULT 'admin', created_at DATETIME NOT NULL)"
)
row = con.execute("SELECT id FROM admin_users WHERE username = ?", (user,)).fetchone()
if row:
    con.execute("UPDATE admin_users SET password_hash = ?, role = 'admin' WHERE id = ?", (hash_password(password), row[0]))
else:
    con.execute(
        "INSERT INTO admin_users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
        (user, hash_password(password), datetime.utcnow().isoformat(sep=" ")),
    )
con.commit()
print(f"Passwort fuer '{user}' gesetzt (Rolle Admin).")
