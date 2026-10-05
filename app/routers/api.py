"""Machine-to-machine API for Freiwirt (the on-site order-acceptance software).
Freiwirt is the only caller, never the public website — auth is a single
shared token per installation, not the admin login, since there is no
browser/session involved."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Category, MenuItem, Order, OrderStatus
from ..repo import get_settings

router = APIRouter(prefix="/api/freiwirt")


def require_freiwirt_token(x_freiwirt_token: str = Header(...), db: Session = Depends(get_db)):
    settings = get_settings(db)
    if not settings.freiwirt_api_token or x_freiwirt_token != settings.freiwirt_api_token:
        raise HTTPException(status_code=401, detail="Ungültiges Freiwirt-Token")


def _shop_json(db: Session) -> dict:
    """Everything the Freiwirt header needs: the three switches (green = customers can really order
    that way right now), the quoted waiting times and the cancel presets."""
    from ..models import DEFAULT_CANCEL_REASONS
    from ..repo import effective_shop, get_opening_hours_by_weekday

    settings = get_settings(db)
    shop = effective_shop(settings, get_opening_hours_by_weekday(db))
    live = bool(shop["can_order"])
    reasons = [l.strip() for l in (settings.cancel_reasons or "").splitlines() if l.strip()]
    return {
        "accepting_orders": live,
        "pickup": live and bool(settings.pickup_enabled),
        "delivery": live and bool(settings.delivery_enabled),
        "state": shop["state"],  # open | preorder | closed (opening hours)
        "opens_text": shop.get("opens_text", ""),
        "pickup_minutes": settings.estimated_pickup_minutes or 0,
        "delivery_minutes": settings.estimated_delivery_minutes or 0,
        "cancel_reasons": reasons or list(DEFAULT_CANCEL_REASONS),
    }


@router.get("/status", dependencies=[Depends(require_freiwirt_token)])
def get_shop_status(db: Session = Depends(get_db)):
    return _shop_json(db)


@router.post("/schalter/{field}", dependencies=[Depends(require_freiwirt_token)])
def toggle_switch(field: str, request: Request, db: Session = Depends(get_db)):
    """Same one-tap switches as the admin dashboard: bestellungen | lieferung | abholung."""
    from .. import audit
    from ..repo import apply_quick_switch, effective_shop, get_opening_hours_by_weekday

    fields = {"bestellungen": "accepting_orders", "lieferung": "delivery_enabled", "abholung": "pickup_enabled"}
    if field not in fields:
        raise HTTPException(status_code=404, detail="Unbekannter Schalter")
    settings = get_settings(db)
    shop = effective_shop(settings, get_opening_hours_by_weekday(db))
    text = apply_quick_switch(settings, shop, fields[field])
    db.commit()
    audit.note(request, "Freiwirt: " + text)
    return _shop_json(db)


class WaitTimesIn(BaseModel):
    pickup_minutes: Optional[int] = None
    delivery_minutes: Optional[int] = None


@router.post("/zeiten", dependencies=[Depends(require_freiwirt_token)])
def set_wait_times(payload: WaitTimesIn, db: Session = Depends(get_db)):
    """Quoted waiting time shown to customers (e.g. busy night: Lieferung 30 -> 50 min)."""
    settings = get_settings(db)
    if payload.pickup_minutes is not None:
        settings.estimated_pickup_minutes = max(0, min(payload.pickup_minutes, 240))
    if payload.delivery_minutes is not None:
        settings.estimated_delivery_minutes = max(0, min(payload.delivery_minutes, 240))
    db.commit()
    return _shop_json(db)


class AvailabilityIn(BaseModel):
    available: bool
    # Only with available=false. Neither given = sold out until the admin releases it.
    minutes: Optional[int] = None  # e.g. 45: orderable again in 45 minutes
    today: bool = False  # sold out for the rest of today


def _sold_out_until(payload: AvailabilityIn) -> Optional[datetime]:
    now = datetime.now()
    if payload.today:
        return now.replace(hour=23, minute=59, second=59, microsecond=0)
    if payload.minutes:
        return now + timedelta(minutes=max(1, min(payload.minutes, 24 * 60)))
    return None


def _apply_availability(item: MenuItem, payload: AvailabilityIn) -> None:
    if payload.available:
        item.is_available = True
        item.sold_out_until = None
        return
    until = _sold_out_until(payload)
    if until is None:
        item.is_available = False  # open-ended: stays sold out until released
        item.sold_out_until = None
    else:
        item.sold_out_until = until  # temporary: comes back by itself


def _item_json(item: MenuItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "category_id": item.category_id,
        "category": item.category.name,
        "price": item.price,
        "available": bool(item.is_available and not item.temp_sold_out),
        "sold_out_until": item.sold_out_until.isoformat() if item.temp_sold_out else None,
    }


@router.get("/artikel", dependencies=[Depends(require_freiwirt_token)])
def list_items(db: Session = Depends(get_db)):
    """Current state of every menu item, so Freiwirt can (re)sync at any time."""
    items = db.query(MenuItem).join(Category).order_by(Category.sort_order, Category.id, MenuItem.sort_order, MenuItem.id).all()
    return [_item_json(i) for i in items]


@router.post("/artikel/{item_id}/verfuegbar", dependencies=[Depends(require_freiwirt_token)])
def set_item_available(item_id: int, payload: AvailabilityIn, db: Session = Depends(get_db)):
    """Sold out (for N minutes / today / until released) or available again.
    Takes effect on the website immediately (same database)."""
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    _apply_availability(item, payload)
    db.commit()
    return _item_json(item)


@router.post("/kategorien/{category_id}/verfuegbar", dependencies=[Depends(require_freiwirt_token)])
def set_category_available(category_id: int, payload: AvailabilityIn, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    for item in category.items:
        _apply_availability(item, payload)
    db.commit()
    return {"category_id": category.id, "available": payload.available, "items": len(category.items)}


# ---- Orders: what Freiwirt shows on the kitchen screen ----

class OrderStatusIn(BaseModel):
    status: OrderStatus
    reason: Optional[str] = None  # only used with status=cancelled


ALLOWED_TRANSITIONS = {
    OrderStatus.received: {OrderStatus.preparing, OrderStatus.cancelled},
    OrderStatus.preparing: {OrderStatus.ready, OrderStatus.cancelled},
    OrderStatus.ready: {OrderStatus.completed, OrderStatus.cancelled},
}


def _order_json(order: Order) -> dict:
    return {
        "id": order.id,
        "status": order.status.value,
        "type": order.order_type.value,
        "created_at": order.created_at.isoformat(),
        "customer_name": order.customer_name,
        "phone": order.phone,
        "email": order.email,
        "address": order.delivery_address,
        "zip": order.customer_zip,
        "city": order.customer_city,
        "payment": order.payment_method,  # cash | card (collect at handover) | online (already paid)
        "paid": order.payment_method == "online",
        "cancel_reason": order.cancel_reason or "",
        "note": order.note,
        "total": order.total,
        "service_fee": order.service_fee or 0.0,
        "items": [
            {"name": i.item_name, "options": i.options_summary, "quantity": i.quantity, "unit_price": i.unit_price}
            for i in order.items
        ],
    }


@router.get("/bestellungen", dependencies=[Depends(require_freiwirt_token)])
def list_orders(since_id: int = 0, db: Session = Depends(get_db)):
    """Open orders (new, in preparation, ready) plus anything newer than since_id."""
    open_states = [OrderStatus.received, OrderStatus.preparing, OrderStatus.ready]
    orders = (
        db.query(Order)
        .filter(((Order.status.in_(open_states)) | (Order.id > since_id)) & (Order.status != OrderStatus.awaiting_payment))
        .order_by(Order.id)
        .all()
    )
    return [_order_json(o) for o in orders]


@router.post("/bestellungen/{order_id}/status", dependencies=[Depends(require_freiwirt_token)])
def set_order_status(order_id: int, payload: OrderStatusIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Accept (preparing), mark ready/on the way (ready), complete, or cancel.
    The customer's tracking page shows the new status on its next refresh."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    if payload.status == order.status:
        return _order_json(order)
    if payload.status not in ALLOWED_TRANSITIONS.get(order.status, set()):
        raise HTTPException(status_code=409, detail=f"Wechsel {order.status.value} -> {payload.status.value} nicht erlaubt")
    order.status = payload.status
    if payload.status == OrderStatus.cancelled:
        order.cancel_reason = (payload.reason or "").strip()[:200]
    db.commit()
    if payload.status == OrderStatus.cancelled:
        from .. import customer as cust, orderflow
        background.add_task(orderflow.send_cancellation, order.id, cust.public_base_url(request))
    return _order_json(order)
