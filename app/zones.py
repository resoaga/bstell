"""Delivery zones: postcode groups with their own delivery fee and minimum order value."""
from typing import List, Optional

from sqlalchemy.orm import Session

from .models import DeliveryZone


def load_zones(db: Session) -> List[DeliveryZone]:
    return db.query(DeliveryZone).order_by(DeliveryZone.sort_order, DeliveryZone.id).all()


def zone_for(zones: List[DeliveryZone], plz: str) -> Optional[DeliveryZone]:
    plz = (plz or "").strip()
    for zone in zones:
        if plz in zone.zip_list:
            return zone
    return None


def delivery_terms(settings, zones: List[DeliveryZone], plz: str):
    """(deliverable, fee, minimum, zone) for a postcode. No zones = everywhere at the default terms."""
    if not zones:
        return True, settings.delivery_fee or 0.0, settings.minimum_order_value or 0.0, None
    zone = zone_for(zones, plz)
    if zone is None:
        return False, 0.0, 0.0, None
    return True, zone.delivery_fee or 0.0, zone.min_order or 0.0, zone


def cart_minimum(settings, zones: List[DeliveryZone]) -> float:
    """Lowest minimum over everything the customer can still choose; the cart only blocks
    when even that is not reached (the exact value follows from postcode at checkout)."""
    options = []
    if settings.pickup_enabled:
        options.append(settings.minimum_order_value or 0.0)
    if settings.delivery_enabled:
        options.extend([z.min_order or 0.0 for z in zones] if zones else [settings.minimum_order_value or 0.0])
    return min(options) if options else (settings.minimum_order_value or 0.0)


def zones_for_js(zones: List[DeliveryZone]) -> dict:
    return {
        z_: {"name": zone.name, "fee": zone.delivery_fee or 0.0, "min": zone.min_order or 0.0}
        for zone in zones
        for z_ in zone.zip_list
    }
