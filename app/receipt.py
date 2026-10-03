"""PDF receipt for an order (fpdf2, pure Python). Optional: if fpdf2 is not installed
on the server, build_receipt() returns None and the mail simply has no PDF."""

from . import timeutil
from .models import PAYMENT_LABELS, OrderType


def _t(text) -> str:
    """The built-in PDF fonts only know Latin-1: replace anything outside it."""
    return (
        str(text or "")
        .replace("–", "-").replace("—", "-").replace("’", "'").replace("„", '"').replace("“", '"')
        .encode("latin-1", "replace").decode("latin-1")
    )


def _rgb(hex_color: str):
    h = (hex_color or "#c8102e").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (200, 16, 46)


def build_receipt(order, settings):
    try:
        from fpdf import FPDF
    except ImportError:
        return None

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    accent = _rgb(settings.accent_color)
    shop = settings.name or "Restaurant"

    pdf.set_fill_color(*accent)
    pdf.rect(0, 0, 210, 26, "F")
    pdf.set_xy(15, 8)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 10, _t(shop))

    pdf.set_text_color(24, 19, 15)
    pdf.set_xy(15, 36)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 8, _t(f"Bestellbestätigung / Beleg  #{order.id}"))
    pdf.ln(9)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(107, 107, 107)
    how = "Lieferung" if order.order_type == OrderType.delivery else "Abholung"
    pay = "online bezahlt" if order.payment_method == "online" else f"{PAYMENT_LABELS.get(order.payment_method, '')} bei Übergabe"
    pdf.set_x(15)
    pdf.cell(0, 6, _t(f"Bestellt am {timeutil.fmt_local(order.created_at, '%d.%m.%Y um %H:%M Uhr')} | {how} | {pay}"))
    pdf.ln(10)

    pdf.set_text_color(24, 19, 15)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_x(15)
    pdf.cell(90, 6, "Kunde")
    if order.delivery_address:
        pdf.cell(0, 6, "Lieferadresse")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 10)
    top = pdf.get_y()
    pdf.set_x(15)
    pdf.multi_cell(88, 5, _t(f"{order.customer_name}\n{order.phone}\n{order.email}"))
    if order.delivery_address:
        pdf.set_xy(105, top)
        pdf.multi_cell(90, 5, _t(f"{order.delivery_address}\n{order.customer_zip} {order.customer_city}"))
    pdf.set_y(max(pdf.get_y(), top + 16) + 4)

    pdf.set_draw_color(227, 221, 212)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_x(15)
    pdf.cell(135, 7, "Artikel", border="B")
    pdf.cell(15, 7, "Menge", border="B", align="R")
    pdf.cell(30, 7, "Betrag (CHF)", border="B", align="R")
    pdf.ln(8)
    for line in order.items:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(15)
        pdf.cell(135, 6, _t(line.item_name))
        pdf.cell(15, 6, str(line.quantity), align="R")
        pdf.cell(30, 6, f"{line.unit_price * line.quantity:.2f}", align="R")
        pdf.ln(6)
        if line.options_summary:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(107, 107, 107)
            pdf.set_x(18)
            pdf.multi_cell(130, 4.5, _t(line.options_summary))
            pdf.set_text_color(24, 19, 15)
        pdf.ln(1.5)

    def total_row(label, amount, bold=False):
        pdf.set_font("Helvetica", "B" if bold else "", 10 if not bold else 11)
        pdf.set_x(15)
        pdf.cell(150, 7, _t(label), border="T" if bold else 0, align="R")
        pdf.cell(30, 7, f"{amount:.2f}", border="T" if bold else 0, align="R")
        pdf.ln(7)

    pdf.ln(2)
    if order.service_fee or order.delivery_fee_paid:
        total_row("Warenwert", order.goods_total)
    if order.delivery_fee_paid:
        total_row("Lieferung", order.delivery_fee_paid)
    if order.service_fee:
        total_row(order.service_fee_text or settings.service_fee_label or "Servicegebühr", order.service_fee)
    total_row("Total CHF (inkl. MwSt.)", order.total, bold=True)

    pdf.ln(8)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(107, 107, 107)
    pdf.set_x(15)
    note = "Dieser Beleg bestätigt den Eingang deiner Bestellung. Alle Preise in CHF inkl. MwSt."
    if settings.vat_number:
        note += f" MWST-Nr.: {settings.vat_number}"
    pdf.multi_cell(180, 4.8, _t(note))

    pdf.set_y(-32)
    pdf.set_draw_color(*accent)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    contact = " | ".join(
        x for x in (
            shop,
            f"{settings.address_street}, {settings.address_zip} {settings.address_city}" if settings.address_street else "",
            settings.phone,
            settings.email,
        ) if x
    )
    pdf.set_x(15)
    pdf.multi_cell(180, 4.8, _t(contact), align="C")
    return bytes(pdf.output())
