"""Ring for new orders: which sound plays for which order (default / pickup / delivery zone)."""
from typing import Dict

from sqlalchemy.orm import Session

from . import zones as zones_lib
from .models import CustomSound, AppPref

BUILTINS = [("klingel", "Klingel"), ("glocke", "Glocke"), ("pling", "Pling"), ("sirene", "Sirene"), ("fanfare", "Fanfare")]
DEFAULTS = {"ring_seconds": "5", "pause_seconds": "5", "volume": "80", "default": "b:klingel", "pickup": ""}


def get_prefs(db: Session) -> Dict[str, str]:
    prefs = dict(DEFAULTS)
    for row in db.query(AppPref).all():
        prefs[row.key] = row.value or ""
    return prefs


def set_pref(db: Session, key: str, value: str) -> None:
    row = db.get(AppPref, key)
    if row is None:
        db.add(AppPref(key=key, value=value))
    else:
        row.value = value


def _int(prefs: dict, key: str, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(prefs.get(key) or DEFAULTS[key])))
    except ValueError:
        return int(DEFAULTS[key])


def ring_config(db: Session) -> dict:
    prefs = get_prefs(db)
    return {
        "ring": _int(prefs, "ring_seconds", 1, 60),
        "pause": _int(prefs, "pause_seconds", 1, 120),
        "volume": _int(prefs, "volume", 0, 100),
    }


def _spec(value: str, customs: dict) -> str:
    """"b:klingel" | "f:/static/uploads/x.mp3" | "n" - what the browser plays."""
    if value == "n":
        return "n"
    if value.startswith("f:"):
        sound = customs.get(value[2:])
        return "f:/static/uploads/" + sound.filename if sound else ""
    if value.startswith("b:") and value[2:] in dict(BUILTINS):
        return value
    return ""


def specs_for_orders(db: Session, orders) -> Dict[int, str]:
    """order id -> sound spec for the orders that still wait for an answer (status received)."""
    prefs = get_prefs(db)
    customs = {str(c.id): c for c in db.query(CustomSound).all()}
    zone_list = zones_lib.load_zones(db)
    fallback = _spec(prefs["default"], customs) or "b:klingel"
    result = {}
    for order in orders:
        value = ""
        if order.order_type.value == "delivery":
            zone = zones_lib.zone_for(zone_list, order.customer_zip)
            if zone is not None:
                value = prefs.get(f"zone_{zone.id}", "")
        else:
            value = prefs.get("pickup", "")
        result[order.id] = _spec(value, customs) or fallback
    return result
