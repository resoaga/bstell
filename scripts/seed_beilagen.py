"""Adds the Just-Eat Doener option groups (Fleisch, Salat, Sauce, Extras) to the
option library. Run after migrate_option_library.py:

    python3 scripts/seed_beilagen.py [pfad/zur/bestellsystem.db]

Only creates groups; assign them to items in the admin (Optionen -> Gruppe ->
"Artikeln zuweisen"). Groups whose name already exists are skipped, so it is
safe to re-run.
"""
import sqlite3
import sys

SAUCES = [
    "Cocktailsauce", "Joghurtsauce", "Knoblauchsauce", "Algereien Sauce (scharf)",
    "Barbecue-Sauce (scharf)", "Samurai Sauce (scharf)", "Chili-Sauce-Sriracha",
    "Curry-Sauce", "Scharfes Pulver", "Chili-Sauce-Sriracha Mayonnaise",
    "Ketchup", "Mayonnaise",
]

GROUPS = [
    ("Fleisch", "single", [("Kalbfleisch", 0), ("Pouletfleisch", 0), ("Ohne Fleisch", 0)]),
    (
        "Salat & Zutaten",
        "multiple",
        [(n, 0) for n in [
            "Eisbergsalat", "Weisskohl", "Rotkohl", "Zwiebeln", "Tomaten", "Mais",
            "Bauernsalat (Tomaten und Gurke)",
        ]],
    ),
    ("Sauce", "multiple", [(n, 0) for n in SAUCES] + [("Ohne Sauce", 0)]),
    ("Extra-Sauce", "multiple", [(n, 1.5) for n in SAUCES]),
    (
        "Extra-Zutaten",
        "multiple",
        [("Feta", 2.0), ("Oliven", 2.0), ("Extra Poulet", 2.0), ("Extra Kalbfleisch", 2.0), ("Dönerbrot", 2.5)],
    ),
]

def main():
    con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "bestellsystem.db")
    for name, sel_type, options in GROUPS:
        if con.execute("SELECT 1 FROM option_groups WHERE name = ?", (name,)).fetchone():
            print(f"übersprungen (gibt es schon): {name}")
            continue
        gid = con.execute(
            "INSERT INTO option_groups (name, selection_type) VALUES (?, ?)", (name, sel_type)
        ).lastrowid
        con.executemany(
            "INSERT INTO options (option_group_id, name, price_delta) VALUES (?, ?, ?)",
            [(gid, n, p) for n, p in options],
        )
        print(f"angelegt: {name} ({len(options)} Optionen)")
    con.commit()


if __name__ == "__main__":
    main()
