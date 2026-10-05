"""Monatsbericht per Mail (fuer den Betreiber der Plattform): Bestellungen, Umsatz und
Provision des Vormonats, mit Umsatz-PDF im Anhang.

In Plesk als "Geplante Aufgabe" (Cron) am 1. jedes Monats, z.B. um 06:00:
    cd ~/httpdocs && python3 scripts/monthly_report.py --to hallo@resitterzi.ch

Optionen:
    --to ADRESSE       Empfaenger (oder Umgebungsvariable REPORT_EMAIL)
    --rate 5           Provision in Prozent vom Brutto-Umsatz (Standard 5)
    --month 2026-09    anderer Monat statt Vormonat
    --yearly-fee 100   Jahresgebuehr in CHF, wird im Januar (oder mit --month) mit ausgewiesen
    --dry-run          nur ausgeben, nichts senden
"""
import argparse
import os
import sys
from datetime import date

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault("ADMIN_USERNAME", "x")
os.environ.setdefault("ADMIN_PASSWORD", "x")
os.environ.setdefault("SESSION_SECRET_KEY", "x")

from app import mailer, report_pdf, stats  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.repo import get_settings  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--to", default=os.environ.get("REPORT_EMAIL", ""))
parser.add_argument("--rate", type=float, default=5.0)
parser.add_argument("--month", default="")
parser.add_argument("--yearly-fee", type=float, default=0.0)
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
if not args.to and not args.dry_run:
    sys.exit("Empfaenger fehlt: --to ADRESSE")

if args.month:
    year, month = (int(x) for x in args.month.split("-"))
    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1)
    last = date.fromordinal(last.toordinal() - 1)
else:
    first, last, _ = stats.resolve_period("lastmonth")

db = SessionLocal()
try:
    settings = get_settings(db)
    orders = stats.load_orders(db, first, last)
    summary = stats.summarize(orders)
    label = f"{first:%d.%m.%Y} bis {last:%d.%m.%Y}"
    commission = round(summary["revenue"] * args.rate / 100.0, 2)
    shop = settings.name or "Restaurant"
    lines = [
        f"Monatsbericht {shop}: {first:%m/%Y}",
        f"Zeitraum: {label}",
        "",
        f"Bestellungen:     {summary['count']}   (storniert: {summary['cancelled']})",
        f"Umsatz brutto:    CHF {summary['revenue']:.2f}",
        f"  davon Waren:    CHF {summary['goods']:.2f}",
        f"  Lieferkosten:   CHF {summary['delivery_fees']:.2f}",
        f"  Servicegebuehr: CHF {summary['service_fees']:.2f}",
        "",
        f"Provision {args.rate:g} % vom Brutto-Umsatz: CHF {commission:.2f}",
    ]
    if args.yearly_fee and (first.month == 1 or args.month):
        lines.append(f"Jahresgebuehr: CHF {args.yearly_fee:.2f}")
        lines.append(f"Total zu verrechnen: CHF {commission + args.yearly_fee:.2f}")
    lines += ["", "Die Umsatz-Uebersicht liegt als PDF bei."]
    body = "\n".join(lines)
    print(body)
    if args.dry_run:
        sys.exit(0)
    pdf = report_pdf.build_figures_pdf(settings, label, summary, stats.by_payment(orders), stats.by_day(orders))
    attachments = [(f"umsatz_{first:%Y-%m}.pdf", pdf, "application/pdf")] if pdf else []
    ok, error = mailer.send_mail_result(args.to, f"Monatsbericht {shop} {first:%m/%Y}", body, attachments=attachments)
    print("Mail gesendet." if ok else f"FEHLER beim Senden: {error}")
    sys.exit(0 if ok else 1)
finally:
    db.close()
