"""Time-based "not orderable" rules (see AvailabilityRule). Evaluated on the
server for the menu display, adding to the cart and placing the order."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from .models import AvailabilityRule


def load_rules(db: Session) -> List[dict]:
    rules = []
    for rule in db.query(AvailabilityRule).all():
        rules.append(
            {
                "days": {int(d) for d in rule.weekdays.split(",") if d.strip().isdigit()} or None,
                "start": rule.start_time,
                "end": rule.end_time,
                "cats": {c.id for c in rule.categories},
                "items": {i.id for i in rule.items},
            }
        )
    return rules


def blocked_until(rules: List[dict], category_id: int, item_id: int, now: Optional[datetime] = None) -> Optional[str]:
    """None if orderable right now, else the "HH:MM" from which it is orderable
    again (back-to-back rules are chained). "23:59" means: not any more today."""
    now = now or datetime.now()
    weekday = now.weekday()
    current = now.strftime("%H:%M")
    until = None
    for _ in range(10):
        active = [
            r
            for r in rules
            if (category_id in r["cats"] or item_id in r["items"])
            and (r["days"] is None or weekday in r["days"])
            and r["start"] <= current < r["end"]
        ]
        if not active:
            break
        current = until = max(r["end"] for r in active)
    return until


def describe(until: str) -> str:
    return "heute nicht mehr bestellbar" if until >= "23:59" else f"bestellbar ab {until}"
