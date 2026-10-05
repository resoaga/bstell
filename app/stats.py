"""Numbers about orders: statistics for the owner, accounting figures, monthly report.
Orders are stored in UTC; everything is bucketed in Swiss local time."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from . import timeutil, zones as zones_lib
from .models import PAYMENT_LABELS, Order, OrderStatus, OrderType

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
PERIOD_LABELS = {
    "today": "Heute", "7": "7 Tage", "30": "30 Tage", "month": "Dieser Monat", "lastmonth": "Letzter Monat",
    "quarter": "Dieses Quartal", "lastquarter": "Letztes Quartal", "year": "Dieses Jahr", "lastyear": "Letztes Jahr",
    "all": "Alles",
}


def _local_midnight_to_utc(day: date) -> datetime:
    local = datetime(day.year, day.month, day.day, tzinfo=timeutil.LOCAL_TZ) if timeutil.LOCAL_TZ else datetime(day.year, day.month, day.day).astimezone()
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def local_today() -> date:
    return timeutil.to_local(datetime.utcnow()).date()


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def resolve_period(key: str, start: str = "", end: str = "") -> Tuple[date, date, str]:
    """(first day, last day inclusive, label) in local dates. Custom dates win over the key."""
    today = local_today()
    try:
        if start and end:
            a, b = date.fromisoformat(start), date.fromisoformat(end)
            if b < a:
                a, b = b, a
            return a, b, f"{a:%d.%m.%Y} bis {b:%d.%m.%Y}"
    except ValueError:
        pass
    if key == "today":
        a, b = today, today
    elif key == "7":
        a, b = today - timedelta(days=6), today
    elif key == "month":
        a, b = today.replace(day=1), today
    elif key == "lastmonth":
        b = today.replace(day=1) - timedelta(days=1)
        a = b.replace(day=1)
    elif key == "quarter":
        a, b = _quarter_start(today), today
    elif key == "lastquarter":
        b = _quarter_start(today) - timedelta(days=1)
        a = _quarter_start(b)
    elif key == "year":
        a, b = today.replace(month=1, day=1), today
    elif key == "lastyear":
        a, b = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    elif key == "all":
        a, b = date(2000, 1, 1), today
    else:
        key = "30"
        a, b = today - timedelta(days=29), today
    label = PERIOD_LABELS.get(key, "30 Tage")
    if key in ("month", "lastmonth", "quarter", "lastquarter", "year", "lastyear"):
        label += f" ({a:%d.%m.%Y} bis {b:%d.%m.%Y})"
    return a, b, label


def load_orders(db: Session, first: date, last: date):
    start_utc = _local_midnight_to_utc(first)
    end_utc = _local_midnight_to_utc(last + timedelta(days=1))
    return (
        db.query(Order).options(joinedload(Order.items))
        .filter(Order.created_at >= start_utc, Order.created_at < end_utc)
        .order_by(Order.created_at).all()
    )


def _money(x: float) -> float:
    return round(x or 0.0, 2)


def _customer_key(o) -> str:
    return (o.email or "").strip().lower() or (o.phone or "").replace(" ", "")


def summarize(orders) -> dict:
    """Totals of non-cancelled orders plus cancelled ones counted separately."""
    valid = [o for o in orders if o.status != OrderStatus.cancelled]
    cancelled = [o for o in orders if o.status == OrderStatus.cancelled]
    count = len(valid)
    revenue = sum(o.total for o in valid)
    return {
        "count": count,
        "revenue": _money(revenue),
        "goods": _money(sum(o.goods_total for o in valid)),
        "delivery_fees": _money(sum(o.delivery_fee_paid for o in valid)),
        "service_fees": _money(sum(o.service_fee or 0 for o in valid)),
        "average": _money(revenue / count) if count else 0.0,
        "cancelled": len(cancelled),
        "cancelled_value": _money(sum(o.total for o in cancelled)),
        "delivery_count": sum(1 for o in valid if o.order_type == OrderType.delivery),
        "pickup_count": sum(1 for o in valid if o.order_type == OrderType.pickup),
    }


def by_payment(orders) -> list:
    groups = defaultdict(lambda: [0, 0.0])
    for o in orders:
        if o.status == OrderStatus.cancelled:
            continue
        g = groups[o.payment_method or "cash"]
        g[0] += 1
        g[1] += o.total
    return [
        {"key": k, "label": PAYMENT_LABELS.get(k, k), "count": v[0], "revenue": _money(v[1])}
        for k, v in sorted(groups.items(), key=lambda kv: -kv[1][1])
    ]


def by_day(orders) -> list:
    groups = defaultdict(lambda: [0, 0.0])
    for o in orders:
        if o.status == OrderStatus.cancelled:
            continue
        d = timeutil.to_local(o.created_at).date()
        groups[d][0] += 1
        groups[d][1] += o.total
    return [{"day": d, "count": v[0], "revenue": _money(v[1])} for d, v in sorted(groups.items())]


def detailed(db: Session, first: date, last: date) -> dict:
    orders = load_orders(db, first, last)
    valid = [o for o in orders if o.status != OrderStatus.cancelled]
    zone_list = zones_lib.load_zones(db)

    hours = [0] * 24
    weekdays = [[0, 0.0] for _ in range(7)]
    zones: Dict[str, list] = defaultdict(lambda: [0, 0.0, 0.0])  # count, revenue, delivery fees
    zips: Dict[str, list] = defaultdict(lambda: [0, 0.0])
    items: Dict[str, list] = defaultdict(lambda: [0, 0.0])
    for o in valid:
        local = timeutil.to_local(o.created_at)
        hours[local.hour] += 1
        weekdays[local.weekday()][0] += 1
        weekdays[local.weekday()][1] += o.total
        if o.order_type == OrderType.delivery:
            zone = zones_lib.zone_for(zone_list, o.customer_zip)
            name = zone.name if zone else ("Ohne Zone" if zone_list else "Lieferung")
            z = zones[name]
            z[0] += 1
            z[1] += o.total
            z[2] += o.delivery_fee_paid
            plz = (o.customer_zip or "").strip() or "?"
            label = f"{plz} {o.customer_city}".strip() if o.customer_city else plz
            zips[label][0] += 1
            zips[label][1] += o.total
        for line in o.items:
            it = items[line.item_name]
            it[0] += line.quantity
            it[1] += line.unit_price * line.quantity

    # New vs. returning customers: "new" = no earlier non-cancelled order before this period
    keys = {_customer_key(o) for o in valid if _customer_key(o)}
    earlier = set()
    if keys:
        start_utc = _local_midnight_to_utc(first)
        for o in db.query(Order).filter(Order.created_at < start_utc, Order.status != OrderStatus.cancelled).all():
            earlier.add(_customer_key(o))
    returning = len(keys & earlier)

    reasons: Dict[str, int] = defaultdict(int)
    for o in orders:
        if o.status == OrderStatus.cancelled:
            reasons[o.cancel_reason or "ohne Grund"] += 1

    summary = summarize(orders)
    return {
        "summary": summary,
        "days": by_day(orders),
        "hours": hours,
        "weekdays": [
            {"label": WEEKDAYS[i], "count": c, "revenue": _money(r)} for i, (c, r) in enumerate(weekdays)
        ],
        "zones": sorted(
            ({"name": n, "count": v[0], "revenue": _money(v[1]), "average": _money(v[1] / v[0]), "fees": _money(v[2])} for n, v in zones.items()),
            key=lambda r: -r["revenue"],
        ),
        "zips": sorted(
            ({"name": n, "count": v[0], "revenue": _money(v[1])} for n, v in zips.items()), key=lambda r: -r["count"]
        )[:15],
        "payments": by_payment(orders),
        "top_items": sorted(
            ({"name": n, "qty": v[0], "revenue": _money(v[1])} for n, v in items.items()), key=lambda r: (-r["qty"], r["name"])
        )[:15],
        "customers": {"total": len(keys), "returning": returning, "new": len(keys) - returning},
        "cancel_reasons": sorted(reasons.items(), key=lambda kv: -kv[1]),
    }


def vat_part(gross: float, rate: float) -> float:
    """VAT contained in a gross amount."""
    return _money(gross * rate / (100.0 + rate)) if rate else 0.0
