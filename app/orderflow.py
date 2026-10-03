"""Order-side helpers: confirmation e-mail and abuse limits for the checkout."""

import time
from html import escape
from collections import defaultdict, deque
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import mailhtml
from .mailer import mail_configured, send_mail
from .models import PAYMENT_LABELS, Order, OrderStatus, OrderType

# In-memory sliding window per client IP (resets on restart, stores no personal data)
_ip_hits = defaultdict(deque)
MAX_ORDERS_PER_IP_PER_HOUR = 8
MAX_ORDERS_PER_PHONE_PER_HOUR = 4
MIN_SECONDS_ON_CHECKOUT = 1  # prefilled forms can legitimately be sent within seconds


def ip_limit_reached(ip: str) -> bool:
    now = time.time()
    hits = _ip_hits[ip]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    return len(hits) >= MAX_ORDERS_PER_IP_PER_HOUR


def register_ip(ip: str) -> None:
    _ip_hits[ip].append(time.time())


def phone_or_device_limit_reached(db: Session, phone: str, device_key: str) -> bool:
    since = datetime.utcnow() - timedelta(hours=1)
    recent = db.query(Order).filter(Order.created_at >= since, Order.status != OrderStatus.cancelled)
    return (
        recent.filter(Order.phone == phone).count() >= MAX_ORDERS_PER_PHONE_PER_HOUR
        or recent.filter(Order.device_key == device_key).count() >= MAX_ORDERS_PER_PHONE_PER_HOUR + 2
    )


def _legal_links(db, base_url):
    from .repo import get_all_content_pages

    return [(page.title, f"{base_url}/rechtliches/{page.slug}") for page in get_all_content_pages(db) if page.body.strip()]


def send_confirmation(order_id: int, base_url: str) -> None:
    """Background task: confirmation e-mail (HTML + plain text, optional PDF receipt)."""
    from .database import SessionLocal
    from .receipt import build_receipt
    from .repo import get_settings
    from . import timeutil

    db = SessionLocal()
    try:
        order = db.get(Order, order_id)
        settings = get_settings(db)
        if order is None or not order.email or not settings.send_order_confirmation or not mail_configured(settings):
            return
        shop = settings.name or "unser Restaurant"
        accent = settings.accent_color
        track = f"{base_url}/verfolgen/{order.tracking_token}"
        pdf_url = f"{base_url}/verfolgen/{order.tracking_token}/beleg.pdf"
        delivery = order.order_type == OrderType.delivery
        how = "Lieferung" if delivery else "Abholung"
        if order.payment_method == "online":
            pay, pay_sentence = "Online bezahlt", "Die Zahlung ist bei uns eingegangen."
        elif order.payment_method == "card":
            pay, pay_sentence = "Karte / Twint bei Übergabe", "Du bezahlst bei der Übergabe mit Karte oder Twint."
        else:
            pay, pay_sentence = "Bar bei Übergabe", "Du bezahlst bei der Übergabe bar."
        where = f"{order.delivery_address}\n{order.customer_zip} {order.customer_city}" if order.delivery_address else ""
        when = timeutil.fmt_local(order.created_at, "%d.%m.%Y, %H:%M Uhr")
        contact_line = " · ".join(x for x in (settings.phone, settings.email) if x)

        lines = [(i.item_name, i.options_summary or "", i.quantity, f"CHF {i.unit_price * i.quantity:.2f}") for i in order.items]
        extra = []
        if order.delivery_fee_paid:
            extra.append(("Lieferung", f"CHF {order.delivery_fee_paid:.2f}"))
        if order.service_fee:
            extra.append((order.service_fee_text or settings.service_fee_label or "Servicegebühr", f"CHF {order.service_fee:.2f}"))

        # ---- HTML ----
        body = (
            mailhtml.paragraph(f"Hallo {order.customer_name},")
            + mailhtml.paragraph(
                f"vielen Dank für deine Bestellung bei {shop}! Wir haben sie erhalten und bereiten sie jetzt frisch für dich zu."
            )
            + mailhtml.paragraph(
                "Den aktuellen Stand siehst du jederzeit über den Knopf unten. Falls sich bei dir etwas ändert, "
                "ruf uns bitte so schnell wie möglich an, damit wir es noch berücksichtigen können.",
            )
            + mailhtml.info_box(
                [("Bestellung", f"#{int(order.id)}"), ("Bestellt am", when), ("Art", how),
                 ("Adresse" if delivery else "", where), ("Zahlung", pay)]
            )
            + mailhtml.order_lines(lines)
            + mailhtml.totals(extra, "Total (inkl. MwSt.)", f"CHF {order.total:.2f}")
            + mailhtml.paragraph(pay_sentence, muted=True)
            + mailhtml.button(track, "Bestellung verfolgen", accent)
            + f'<p style="margin:6px 0 0;font-size:13px;">{mailhtml.text_link(pdf_url, "Beleg als PDF herunterladen", accent)}</p>'
        )
        legal = " · ".join(mailhtml.text_link(u, t, "#7a746c") for t, u in _legal_links(db, base_url))
        footer = (
            (f"{escape(shop)}<br>" if shop else "")
            + (f"{escape(contact_line)}<br>" if contact_line else "")
            + (f"MWST-Nr.: {escape(settings.vat_number)}<br>" if settings.vat_number else "")
            + (f"<br>{legal}<br>" if legal else "")
            + "<br>Diese E-Mail wurde automatisch versendet."
        )
        html = mailhtml.wrap(shop, accent, "Danke für deine Bestellung!", body, footer, f"Bestellung #{order.id} ist bei uns eingegangen.")

        # ---- plain text ----
        text = [f"Hallo {order.customer_name},", "",
                f"vielen Dank für deine Bestellung bei {shop}! Wir haben sie erhalten und bereiten sie jetzt frisch für dich zu.", "",
                "Den aktuellen Stand siehst du jederzeit unter dem Link unten. Falls sich bei dir etwas ändert, ruf uns bitte so schnell wie möglich an.", "",
                f"Bestellung #{order.id} vom {when}", f"{how} · {pay}"]
        if where:
            text.append(where.replace("\n", ", "))
        text.append("")
        for name, options, qty, amount in lines:
            text.append(f"{qty}x {name}  {amount}")
            if options:
                text.append(f"   {options}")
        text += [f"{label}  {amount}" for label, amount in extra]
        text += ["", f"Total (inkl. MwSt.): CHF {order.total:.2f}", pay_sentence, "",
                 f"Bestellung verfolgen: {track}", f"Beleg als PDF: {pdf_url}", ""]
        text += [f"{t}: {u}" for t, u in _legal_links(db, base_url)]
        if contact_line:
            text += ["", contact_line]

        attachments = None
        if settings.attach_receipt_pdf:
            pdf = build_receipt(order, settings)
            if pdf:
                attachments = [(f"Beleg-Bestellung-{order.id}.pdf", pdf, "application/pdf")]
        send_mail(order.email, f"Deine Bestellung #{order.id} bei {shop}", "\n".join(text), html, attachments)
    finally:
        db.close()


def send_cancellation(order_id: int, base_url: str) -> None:
    """Background task: tell the customer that the order was cancelled (with the reason)."""
    from .database import SessionLocal
    from .repo import get_settings

    db = SessionLocal()
    try:
        order = db.get(Order, order_id)
        settings = get_settings(db)
        if order is None or not order.email or not settings.send_order_confirmation or not mail_configured(settings):
            return
        shop = settings.name or "unser Restaurant"
        contact_line = " · ".join(x for x in (settings.phone, settings.email) if x)
        refund = order.payment_method == "online"
        reason = order.cancel_reason or ""
        body_html = (
            mailhtml.paragraph(f"Hallo {order.customer_name},")
            + mailhtml.paragraph(f"leider mussten wir deine Bestellung #{int(order.id)} bei {shop} stornieren. Das tut uns leid.")
            + (mailhtml.info_box([("Grund", reason)]) if reason else "")
            + (mailhtml.paragraph("Du hast online bezahlt: Wir erstatten dir den Betrag. Er wird dir in den nächsten Tagen gutgeschrieben.") if refund else "")
            + mailhtml.paragraph(
                "Bei Fragen erreichst du uns gerne direkt" + (f" unter {contact_line}" if contact_line else "") + "."
            )
            + mailhtml.button(base_url + "/", "Neu bestellen", settings.accent_color)
        )
        footer = (escape(shop) + ("<br>" + escape(contact_line) if contact_line else "") + "<br><br>Diese E-Mail wurde automatisch versendet.")
        html = mailhtml.wrap(shop, settings.accent_color, "Bestellung storniert", body_html, footer, f"Bestellung #{order.id} wurde storniert.")
        text = [f"Hallo {order.customer_name},", "",
                f"leider mussten wir deine Bestellung #{order.id} bei {shop} stornieren."]
        if reason:
            text.append(f"Grund: {reason}")
        if refund:
            text.append("Du hast online bezahlt: Wir erstatten dir den Betrag in den nächsten Tagen.")
        text += ["", f"Bei Fragen: {contact_line}" if contact_line else "", "", base_url + "/"]
        send_mail(order.email, f"Deine Bestellung #{order.id} wurde storniert", "\n".join(text), html)
    finally:
        db.close()
