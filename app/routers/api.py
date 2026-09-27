"""Machine-to-machine API for Freiwirt (the on-site order-acceptance software).
Freiwirt is the only caller, never the public website — auth is a single
shared token per installation, not the admin login, since there is no
browser/session involved."""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
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
