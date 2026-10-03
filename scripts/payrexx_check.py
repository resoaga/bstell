"""Prueft, ob die in bstell gespeicherten Payrexx-Zugangsdaten funktionieren und
ob die Gateway-Schnittstelle (Online-Zahlung) freigeschaltet ist.

    python3 scripts/payrexx_check.py [pfad/zur/bestellsystem.db]

Es wird nichts abgebucht: es entsteht nur ein unbezahlter Zahlungslink ueber 1.00 CHF,
der nach 5 Minuten ablaeuft."""
import os
import sqlite3
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import payrexx  # noqa: E402

con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "bestellsystem.db")
row = con.execute("SELECT payrexx_instance, payrexx_api_key FROM restaurant_settings WHERE id = 1").fetchone()
if not row or not row[0] or not row[1]:
    sys.exit("Keine Payrexx-Instanz/API-Key im Admin gespeichert (Einstellungen -> Zahlungsdienstleister).")
settings = types.SimpleNamespace(payrexx_instance=row[0], payrexx_api_key=row[1])
print(f"Instanz: {settings.payrexx_instance}")

try:
    payrexx._call(settings, "GET", "SignatureCheck/")
    print("1) Zugangsdaten: OK (Instanz und API-Key stimmen)")
except payrexx.PayrexxError as exc:
    sys.exit(f"1) Zugangsdaten: FEHLER - {exc}\n   -> Instanz-Name (nur der Teil vor .payrexx.com) und API-Key pruefen.")

try:
    data = payrexx._call(settings, "POST", "Gateway/", {
        "amount": 100, "currency": "CHF", "referenceId": "bstell-check", "validity": 5,
    })
    print(f"2) Gateway (Online-Zahlung): OK - Testlink {data['link']}")
except payrexx.PayrexxError as exc:
    sys.exit(f"2) Gateway (Online-Zahlung): FEHLER - {exc}\n   -> Diese Funktion ist bei Payrexx fuer dein Konto nicht freigeschaltet. Bitte den Text an Payrexx-Support senden oder Tarif pruefen.")
