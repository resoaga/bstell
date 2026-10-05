"""PDF with the accounting figures of a period (for the bookkeeper). fpdf2, built-in fonts."""
from . import timeutil
from .models import PAYMENT_LABELS
from .receipt import _rgb, _t
from .stats import vat_part


def _chf(x: float) -> str:
    return f"{x:,.2f}".replace(",", "'")


def build_figures_pdf(settings, label: str, summary: dict, payments: list, days: list, vat_rate: float = 0.0):
    try:
        from fpdf import FPDF
    except ImportError:
        return None
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    accent = _rgb(settings.accent_color)
    pdf.set_fill_color(*accent)
    pdf.rect(0, 0, 210, 22, "F")
    pdf.set_xy(15, 7)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 9, _t(settings.name or "Restaurant"))

    pdf.set_text_color(24, 19, 15)
    pdf.set_xy(15, 30)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _t("Umsatzübersicht"))
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(107, 107, 107)
    pdf.set_x(15)
    pdf.cell(0, 6, _t(f"Zeitraum: {label}   |   erstellt am {timeutil.fmt_local(__import__('datetime').datetime.utcnow(), '%d.%m.%Y %H:%M')}"))
    pdf.ln(10)
    pdf.set_text_color(24, 19, 15)

    def row(left, right, bold=False, border=0, w=(130, 50)):
        pdf.set_font("Helvetica", "B" if bold else "", 10)
        pdf.set_x(15)
        pdf.cell(w[0], 7, _t(left), border=border)
        pdf.cell(w[1], 7, _t(right), border=border, align="R")
        pdf.ln(7)

    def heading(text):
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_x(15)
        pdf.cell(0, 7, _t(text))
        pdf.ln(8)

    pdf.set_draw_color(227, 221, 212)
    heading("Zusammenfassung (ohne stornierte Bestellungen)")
    row("Anzahl Bestellungen", str(summary["count"]))
    row("Warenwert (Artikel)", f"CHF {_chf(summary['goods'])}")
    row("Lieferkosten", f"CHF {_chf(summary['delivery_fees'])}")
    row("Servicegebühr", f"CHF {_chf(summary['service_fees'])}")
    row("Gesamtumsatz (brutto, inkl. MwSt.)", f"CHF {_chf(summary['revenue'])}", bold=True, border="T")
    if vat_rate:
        vat = vat_part(summary["revenue"], vat_rate)
        row(f"davon MwSt ({vat_rate:g} %)", f"CHF {_chf(vat)}")
        row("Umsatz netto (ohne MwSt.)", f"CHF {_chf(summary['revenue'] - vat)}")
    row("Durchschnitt pro Bestellung", f"CHF {_chf(summary['average'])}")
    if summary["cancelled"]:
        row(f"Storniert (nicht enthalten): {summary['cancelled']}", f"CHF {_chf(summary['cancelled_value'])}")

    heading("Nach Zahlungsart")
    for p in payments:
        row(f"{PAYMENT_LABELS.get(p['key'], p['label'])} ({p['count']} Bestellungen)", f"CHF {_chf(p['revenue'])}")
    if not payments:
        row("keine Bestellungen", "")

    heading("Pro Tag")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_x(15)
    pdf.cell(60, 7, "Datum", border="B")
    pdf.cell(50, 7, "Bestellungen", border="B", align="R")
    pdf.cell(70, 7, "Umsatz (CHF)", border="B", align="R")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    for d in days:
        pdf.set_x(15)
        pdf.cell(60, 6, d["day"].strftime("%d.%m.%Y"))
        pdf.cell(50, 6, str(d["count"]), align="R")
        pdf.cell(70, 6, _chf(d["revenue"]), align="R")
        pdf.ln(6)

    pdf.ln(6)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(107, 107, 107)
    pdf.set_x(15)
    pdf.multi_cell(180, 4.5, _t(
        "Auswertung aus dem Bestellsystem. Die Zahlen sind Bestellwerte, keine Buchhaltung; "
        "Bar-/Kartenzahlungen bei Übergabe sind erst mit dem Kassenabschluss bestätigt."
        + (f" MWST-Nr.: {settings.vat_number}" if settings.vat_number else "")
    ))
    return bytes(pdf.output())
