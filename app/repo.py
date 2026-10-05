"""Lazy get-or-create accessors for the singleton settings row and the
fixed set of editable legal pages, shared between the admin and the
public-facing site routers."""

from datetime import datetime, timedelta

import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import (
    CONTENT_PAGE_DEFAULTS,
    WEEKDAY_LABELS,
    ContentPage,
    MenuItem,
    ClosedPeriod,
    OpeningHour,
    Order,
    OrderItem,
    OrderStatus,
    RestaurantSettings,
)


class HoursMap(dict):
    """weekday -> opening windows, plus the current/upcoming closures (holidays, special days)."""

    closures: list = []

    def closure_on(self, day):
        iso = day.isoformat()
        return next((c for c in self.closures if c.start_date <= iso <= c.end_date), None)


def get_opening_hours_by_weekday(db: Session) -> dict:
    by_day = HoursMap({weekday: [] for weekday in range(7)})
    by_day.closures = (
        db.query(ClosedPeriod)
        .filter(ClosedPeriod.end_date >= datetime.now().date().isoformat())
        .order_by(ClosedPeriod.start_date)
        .all()
    )
    for hour in db.query(OpeningHour).order_by(OpeningHour.weekday, OpeningHour.open_time).all():
        by_day[hour.weekday].append(hour)
    return by_day


def _closure_on(hours_by_weekday, day):
    return hours_by_weekday.closure_on(day) if isinstance(hours_by_weekday, HoursMap) else None


def is_currently_open(hours_by_weekday: dict) -> bool:
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    if _closure_on(hours_by_weekday, now.date()):
        return False
    for window in hours_by_weekday.get(now.weekday(), []):
        if window.open_time <= current_time <= window.close_time:
            return True
    return False


def effective_shop(settings, hours_by_weekday: dict) -> dict:
    """shop_status plus the manual dashboard switch. can_order: customers may order right now.
    paused: shop is shut by hand / by the master switch (not just outside hours)."""
    shop = shop_status(hours_by_weekday, settings.preorder_minutes)
    hours_state = shop["state"]  # what the opening hours say, before any manual override
    base = "closed" if hours_state == "closed" else "open"
    override = settings.order_override if settings.order_override_base == base else ""
    if override == "open":
        shop = {"state": "open", "opens_text": ""}
        shop["paused"], shop["can_order"] = False, True
    elif override == "closed":
        shop["paused"], shop["can_order"] = True, False
    else:
        shop["paused"] = not settings.accepting_orders
        shop["can_order"] = bool(settings.accepting_orders and shop["state"] in ("open", "preorder"))
    if not (settings.pickup_enabled or settings.delivery_enabled):
        shop["paused"], shop["can_order"] = True, False
    shop["hours_state"] = hours_state
    return shop


def shop_status(hours_by_weekday: dict, preorder_minutes: int, now: datetime = None) -> dict:
    """state: open | preorder (within preorder_minutes before opening) | closed.
    opens_text says when the shop opens next ("12:00", "morgen 11:00", "Mo 11:00")."""
    now = now or datetime.now()
    current = now.strftime("%H:%M")
    windows = sorted(hours_by_weekday.get(now.weekday(), []), key=lambda w: w.open_time)
    closure = _closure_on(hours_by_weekday, now.date())
    if closure:
        windows = []
    for w in windows:
        if w.open_time <= current <= w.close_time:
            return {"state": "open", "opens_text": ""}
    for w in windows:
        if current < w.open_time:
            opens = datetime.combine(now.date(), datetime.strptime(w.open_time, "%H:%M").time())
            state = "preorder" if opens - now <= timedelta(minutes=preorder_minutes or 0) else "closed"
            return {"state": state, "opens_text": w.open_time}
    reason = {"closure_reason": closure.reason or "Betriebsferien"} if closure else {}
    for offset in range(1, 90):
        date = now.date() + timedelta(days=offset)
        day = date.weekday()
        next_windows = sorted(hours_by_weekday.get(day, []), key=lambda w: w.open_time)
        if next_windows and not _closure_on(hours_by_weekday, date):
            if offset == 1:
                when = "morgen"
            elif offset < 7:
                when = WEEKDAY_LABELS[day][:2]
            else:
                when = date.strftime("%d.%m.")
            return {"state": "closed", "opens_text": f"{when} {next_windows[0].open_time}", **reason}
    return {"state": "closed", "opens_text": "", **reason}


def get_settings(db: Session) -> RestaurantSettings:
    settings = db.get(RestaurantSettings, 1)
    if settings is None:
        settings = RestaurantSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_content_page(db: Session, slug: str) -> ContentPage:
    page = db.query(ContentPage).filter(ContentPage.slug == slug).first()
    if page is None:
        title, body = next(((t, b) for s, t, b in CONTENT_PAGE_DEFAULTS if s == slug), (slug, ""))
        page = ContentPage(slug=slug, title=title, body=body)
        db.add(page)
        db.commit()
        db.refresh(page)
    return page


def get_all_content_pages(db: Session):
    for slug, title, body in CONTENT_PAGE_DEFAULTS:
        if db.query(ContentPage).filter(ContentPage.slug == slug).first() is None:
            db.add(ContentPage(slug=slug, title=title, body=body))
    db.commit()
    return db.query(ContentPage).order_by(ContentPage.id).all()


TOP_DAYS = 7
TOP_COUNT = 3
TOP_MIN_QTY = 3  # an article must have sold at least this often to count as "most sold"
_top_cache = {"at": 0.0, "rows": []}


def seller_ranking(db: Session) -> list:
    """[(item_id, quantity)] of available articles sold in the last 7 days, best first.
    Cancelled / unpaid orders do not count. Cached for a few minutes."""
    if time.time() - _top_cache["at"] < 300:
        return _top_cache["rows"]
    since = datetime.utcnow() - timedelta(days=TOP_DAYS)
    rows = (
        db.query(OrderItem.item_name, func.sum(OrderItem.quantity).label("qty"))
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.created_at >= since, Order.status.notin_((OrderStatus.cancelled, OrderStatus.awaiting_payment)))
        .group_by(OrderItem.item_name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .all()
    )
    by_name = {i.name: i.id for i in db.query(MenuItem).filter(MenuItem.is_available == True).all()}  # noqa: E712
    ranking = [(by_name[name], int(qty)) for name, qty in rows if name in by_name]
    _top_cache.update(at=time.time(), rows=ranking)
    return ranking


def top_seller_ids(db: Session) -> list:
    """The "Meistverkauft" articles: top 3 of the week, each sold at least TOP_MIN_QTY times."""
    return [i for i, qty in seller_ranking(db) if qty >= TOP_MIN_QTY][:TOP_COUNT]


QUICK_SWITCH_LABELS = {
    "accepting_orders": "Bestellungen",
    "delivery_enabled": "Lieferung",
    "pickup_enabled": "Abholung",
}


def force_orders(settings, shop: dict, open_: bool) -> None:
    """Manual override of the opening hours until they change state (opening / closing time)."""
    settings.order_override = "open" if open_ else "closed"
    settings.order_override_base = "closed" if shop.get("hours_state", shop["state"]) == "closed" else "open"


def apply_quick_switch(settings, shop: dict, field: str) -> str:
    """Toggle one of the three dashboard switches (shared by the admin and the Freiwirt API).
    Bestellungen is the master: off turns everything off, on turns both ways on; Lieferung /
    Abholung switch alone and wake or stop the master. Returns the audit text."""
    live = shop["can_order"]
    if field == "accepting_orders":
        turn_on = not live
        settings.pickup_enabled = settings.delivery_enabled = turn_on
        force_orders(settings, shop, turn_on)
        return "Bestellungen: " + ("an (Lieferung + Abholung)" if turn_on else "aus (alles aus)")
    other = "pickup_enabled" if field == "delivery_enabled" else "delivery_enabled"
    turn_on = not (live and getattr(settings, field))
    if turn_on and not live:
        setattr(settings, other, False)  # waking the shop with one way only
    setattr(settings, field, turn_on)
    if turn_on:
        force_orders(settings, shop, True)
    elif not getattr(settings, other):
        force_orders(settings, shop, False)
    return f"{QUICK_SWITCH_LABELS[field]}: {'an' if turn_on else 'aus'}"
