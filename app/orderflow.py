"""Order-side helpers: confirmation e-mail and abuse limits for the checkout."""

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .mailer import mail_configured, send_mail
from .models import PAYMENT_LABELS, Order, OrderStatus, OrderType

# In-memory sliding window per client IP (resets on restart, stores no personal data)
_ip_hits = defaultdict(deque)
MAX_ORDERS_PER_IP_PER_HOUR = 8
MAX_ORDERS_PER_PHONE_PER_HOUR = 4
MIN_SECONDS_ON_CHECKOUT = 3


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


def send_confirmation(order_id: int, base_url: str) -> None:
    """Background task: plain-text confirmation with a link to follow the order."""
    from .database import SessionLocal
    from .repo import get_settings

    db = SessionLocal()
    try:
        order = db.get(Order, order_id)
        settings = get_settings(db)
        if order is None or not order.email or not settings.send_order_confirmation or not mail_configured(settings):
            return
        shop = settings.name or "unser Restaurant"
        lines = [
            f"{i.quantity}x {i.item_name}" + (f" ({i.options_summary})" if i.options_summary else "") + f"  CHF {i.unit_price * i.quantity:.2f}"
            for i in order.items
        ]
        if order.service_fee:
            lines.append(f"{settings.service_fee_label}  CHF {order.service_fee:.2f}")
        if order.delivery_fee_paid:
            lines.append(f"Lieferung  CHF {order.delivery_fee_paid:.2f}")
        how = "Lieferung" if order.order_type == OrderType.delivery else "Abholung"
        where = f"\nAdresse: {order.delivery_address}, {order.customer_zip} {order.customer_city}" if order.delivery_address else ""
        pay = "online bezahlt" if order.payment_method == "online" else f"{PAYMENT_LABELS.get(order.payment_method, '')} bei Übergabe"
        minutes = settings.estimated_delivery_minutes if order.order_type == OrderType.delivery else settings.estimated_pickup_minutes
        body = (
            f"Danke für deine Bestellung bei {shop}!\n\n"
            f"Bestellung #{order.id} ({how}, {pay}){where}\n\n"
            + "\n".join(lines)
            + f"\n\nTotal: CHF {order.total:.2f} (inkl. MwSt.)\n\n"
            f"Voraussichtlich in ca. {minutes} Minuten (unverbindlicher Richtwert).\n"
            f"Bestellung verfolgen: {base_url}/verfolgen/{order.tracking_token}\n\n"
            f"Fragen? {settings.phone or settings.email or ''}\n"
        )
        send_mail(order.email, f"Deine Bestellung #{order.id} bei {shop}", body)
    finally:
        db.close()
