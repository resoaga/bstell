"""Session-backed shopping cart. No customer login exists, so the cart lives
in the signed session cookie (item_id + chosen option_ids + quantity only —
never a price), and prices/availability are always re-resolved from the
database when the cart is displayed or checked out."""

from decimal import ROUND_HALF_UP, Decimal
from typing import List

from fastapi import Request
from sqlalchemy.orm import Session

from . import availability
from .models import MenuItem


def _line_key(item_id: int, option_ids: List[int]) -> str:
    return f"{item_id}:{'-'.join(str(i) for i in sorted(option_ids))}"


def add_to_cart(request: Request, item_id: int, option_ids: List[int], quantity: int) -> None:
    if quantity <= 0:
        return
    cart = dict(request.session.get("cart", {}))
    key = _line_key(item_id, option_ids)
    line = cart.get(key, {"item_id": item_id, "option_ids": sorted(option_ids), "quantity": 0})
    line["quantity"] += quantity
    cart[key] = line
    request.session["cart"] = cart


def set_line_quantity(request: Request, key: str, quantity: int) -> None:
    cart = dict(request.session.get("cart", {}))
    if quantity <= 0:
        cart.pop(key, None)
    elif key in cart:
        cart[key]["quantity"] = quantity
    request.session["cart"] = cart


def remove_line(request: Request, key: str) -> None:
    cart = dict(request.session.get("cart", {}))
    cart.pop(key, None)
    request.session["cart"] = cart


def clear_cart(request: Request) -> None:
    request.session["cart"] = {}


def cart_item_count(request: Request) -> int:
    cart = request.session.get("cart", {})
    return sum(line["quantity"] for line in cart.values())


def resolve_cart_lines(request: Request, db: Session):
    """Re-resolves every cart line against the current menu. Lines whose
    item was deleted or marked sold out since being added are dropped."""
    cart = dict(request.session.get("cart", {}))
    lines = []
    total = 0.0
    changed = False
    rules = availability.load_rules(db)
    for key, line in list(cart.items()):
        item = db.get(MenuItem, line["item_id"])
        if item is None or not item.is_available:
            cart.pop(key, None)
            changed = True
            continue

        option_ids = set(line.get("option_ids", []))
        selected_options = []
        unit_price = item.price
        for link in item.option_links:
            for option in link.option_group.options:
                if option.id in option_ids:
                    selected_options.append(option)
                    unit_price += option.price_delta

        quantity = line["quantity"]
        line_total = unit_price * quantity
        lines.append(
            {
                "key": key,
                "item": item,
                "options": selected_options,
                "unit_price": unit_price,
                "quantity": quantity,
                "line_total": line_total,
                "blocked_until": availability.combined_block(rules, item),
            }
        )
        total += line_total

    if changed:
        request.session["cart"] = cart
    return lines, total


def round_to_5_rappen(amount: float) -> float:
    """Swiss cash rounding to the nearest 0.05 (x.025 and up rounds up)."""
    cents = (Decimal(str(round(amount, 4))) * 20).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(cents / 20)


def service_fee_for(settings, goods_total: float) -> float:
    """Surcharge on every order (cash and card alike): a percentage of the
    goods total, a fixed amount, or both added together. Computed from the settings only, never from
    anything the browser sends; 0 when off or the cart is empty. No VAT is added.
    The fee is rounded so that goods + fee lands on a 5-Rappen amount, which is
    what the customer pays; delivery fees are entered in 0.05 steps in the admin.
    This one function feeds both the displayed totals and the stored order."""
    if not settings.service_fee_enabled or goods_total <= 0:
        return 0.0
    mode = settings.service_fee_mode
    percent = 0.0 if mode == "fixed" else (settings.service_fee_percent or 0.0)
    fixed = 0.0 if mode == "percent" else (settings.service_fee_fixed or 0.0)
    raw = goods_total * percent / 100.0 + fixed
    fee = round(round_to_5_rappen(goods_total + raw) - goods_total, 2)
    return max(0.0, fee)
