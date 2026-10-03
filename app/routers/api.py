"""Machine-to-machine API for Freiwirt (the on-site order-acceptance software).
Freiwirt is the only caller, never the public website — auth is a single
shared token per installation, not the admin login, since there is no
browser/session involved."""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Category, MenuItem
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


def _item_json(item: MenuItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "category_id": item.category_id,
        "category": item.category.name,
        "price": item.price,
        "available": bool(item.is_available),
    }


@router.get("/artikel", dependencies=[Depends(require_freiwirt_token)])
def list_items(db: Session = Depends(get_db)):
    """Current state of every menu item, so Freiwirt can (re)sync at any time."""
    items = db.query(MenuItem).join(Category).order_by(Category.sort_order, Category.id, MenuItem.sort_order, MenuItem.id).all()
    return [_item_json(i) for i in items]


@router.post("/artikel/{item_id}/verfuegbar", dependencies=[Depends(require_freiwirt_token)])
def set_item_available(item_id: int, payload: AvailabilityIn, db: Session = Depends(get_db)):
    """Sold out / available again. Takes effect on the website immediately
    (same database); the admin menu list shows the same state."""
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    item.is_available = payload.available
    db.commit()
    return _item_json(item)


@router.post("/kategorien/{category_id}/verfuegbar", dependencies=[Depends(require_freiwirt_token)])
def set_category_available(category_id: int, payload: AvailabilityIn, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    for item in category.items:
        item.is_available = payload.available
    db.commit()
    return {"category_id": category.id, "available": payload.available, "items": len(category.items)}
