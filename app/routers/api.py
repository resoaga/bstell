"""Machine-to-machine API for Freiwirt (the on-site order-acceptance software).
Freiwirt is the only caller, never the public website — auth is a single
shared token per installation, not the admin login, since there is no
browser/session involved."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
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


class DeliveryStatusIn(BaseModel):
    enabled: bool


@router.get("/status", dependencies=[Depends(require_freiwirt_token)])
def get_delivery_status(db: Session = Depends(get_db)):
    settings = get_settings(db)
    return {"accepting_orders": settings.accepting_orders}


@router.post("/lieferung", dependencies=[Depends(require_freiwirt_token)])
def set_delivery_status(payload: DeliveryStatusIn, db: Session = Depends(get_db)):
    settings = get_settings(db)
    settings.accepting_orders = payload.enabled
    db.commit()
    return {"accepting_orders": settings.accepting_orders}


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
def set_order_status(order_id: int, payload: OrderStatusIn, db: Session = Depends(get_db)):
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
    db.commit()
    return _order_json(order)
