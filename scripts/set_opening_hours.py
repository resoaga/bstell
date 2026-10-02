"""Replaces ALL opening hours with the hours below (einmalig, mit Backup).

    python3 scripts/set_opening_hours.py [pfad/zur/bestellsystem.db]

Weekday numbers: 0=Montag ... 6=Sonntag. "00:00" is entered as 23:59, because
the open-check compares plain HH:MM strings. A day without a window = closed.
"""
import shutil
import sqlite3
import sys
import time

HOURS = {
    0: [("10:00", "23:59")],  # Montag
    1: [("10:00", "23:59")],  # Dienstag
    2: [("10:00", "23:59")],  # Mittwoch
    3: [("10:00", "23:59")],  # Donnerstag
    4: [("10:00", "23:59")],  # Freitag
    5: [("11:30", "23:59")],  # Samstag
    6: [],                    # Sonntag geschlossen
}

path = sys.argv[1] if len(sys.argv) > 1 else "bestellsystem.db"
backup = f"{path}.bak-vor-oeffnungszeiten-{int(time.time())}"
shutil.copyfile(path, backup)
print("Backup:", backup)

con = sqlite3.connect(path)
con.execute("DELETE FROM opening_hours")
for weekday, windows in HOURS.items():
    for open_time, close_time in windows:
        con.execute(
            "INSERT INTO opening_hours (weekday, open_time, close_time) VALUES (?, ?, ?)",
            (weekday, open_time, close_time),
        )
con.commit()
print("Oeffnungszeiten gesetzt:", con.execute("SELECT COUNT(*) FROM opening_hours").fetchone()[0], "Zeitfenster")
