from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import cart as cart_lib
from ..contact_cookie import COOKIE_MAX_AGE, COOKIE_NAME, decode_contact, encode_contact
from ..database import get_db
from ..models import (
    ORDER_STATUS_LABELS,
    ORDER_TYPE_LABELS,
    WEEKDAY_LABELS,
    Category,
    MenuItem,
    Order,
    OrderItem,
    OrderType,
)
from ..repo import (
    get_all_content_pages,
    get_content_page,
    get_opening_hours_by_weekday,
    get_settings,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def site_extra(request: Request, db: Session) -> dict:
    _, cart_total = cart_lib.resolve_cart_lines(request, db)
    return {
        "settings": get_settings(db),
        "cart_count": cart_lib.cart_item_count(request),
        "cart_total": cart_total,
        "legal_pages": get_all_content_pages(db),
    }


@router.get("/")
def homepage(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "site/index.html",
        {
            "request": request,
            "hours_by_weekday": get_opening_hours_by_weekday(db),
            "weekday_labels": WEEKDAY_LABELS,
            **site_extra(request, db),
        },
    )


@router.get("/speisekarte")
def menu_page(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.sort_order, Category.id).all()
    return templates.TemplateResponse(
        "site/menu.html",
        {"request": request, "categories": categories, **site_extra(request, db)},
    )


@router.post("/speisekarte/hinzufuegen")
async def add_to_cart(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    settings = get_settings(db)
    if not settings.accepting_orders:
        return templates.TemplateResponse(
            "site/_add_result.html",
            {
                "request": request,
                "error": "Wir nehmen aktuell keine Bestellungen an.",
                **site_extra(request, db),
            },
        )

    item_id = int(form.get("item_id"))
    item = db.get(MenuItem, item_id)
    if item is None or not item.is_available:
        return templates.TemplateResponse(
            "site/_add_result.html",
            {"request": request, "error": "Artikel nicht verfügbar.", **site_extra(request, db)},
        )

    try:
        quantity = int(form.get("qty") or 1)
    except ValueError:
        quantity = 1
    quantity = max(1, quantity)

    option_ids = []
    for group in item.option_groups:
        field_name = f"opt_{group.id}"
        if group.selection_type.value == "single":
            value = form.get(field_name)
            selected = [value] if value else []
        else:
            selected = form.getlist(field_name)
        if group.required and not selected:
            return templates.TemplateResponse(
                "site/_add_result.html",
                {
                    "request": request,
                    "error": f'Bitte "{group.name}" auswählen.',
                    **site_extra(request, db),
                },
            )
        option_ids.extend(int(v) for v in selected)

    cart_lib.add_to_cart(request, item_id, option_ids, quantity)
    return templates.TemplateResponse(
        "site/_add_result.html", {"request": request, "error": None, **site_extra(request, db)}
    )


@router.get("/warenkorb")
def cart_page(request: Request, db: Session = Depends(get_db)):
    lines, total = cart_lib.resolve_cart_lines(request, db)
    settings = get_settings(db)
    return templates.TemplateResponse(
        "site/cart.html",
        {
            "request": request,
            "lines": lines,
            "total": total,
            "below_minimum": total > 0 and total < settings.minimum_order_value,
            **site_extra(request, db),
        },
    )


@router.post("/warenkorb/menge")
async def update_cart_quantity(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    key = form.get("key", "")
    try:
        quantity = int(form.get("quantity") or 0)
    except ValueError:
        quantity = 0
    cart_lib.set_line_quantity(request, key, quantity)
    lines, total = cart_lib.resolve_cart_lines(request, db)
    settings = get_settings(db)
    return templates.TemplateResponse(
        "site/_cart_lines_response.html",
        {
            "request": request,
            "lines": lines,
            "total": total,
            "below_minimum": total > 0 and total < settings.minimum_order_value,
            **site_extra(request, db),
        },
    )


@router.post("/warenkorb/entfernen")
async def remove_cart_line(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    cart_lib.remove_line(request, form.get("key", ""))
    lines, total = cart_lib.resolve_cart_lines(request, db)
    settings = get_settings(db)
    return templates.TemplateResponse(
        "site/_cart_lines_response.html",
        {
            "request": request,
            "lines": lines,
            "total": total,
            "below_minimum": total > 0 and total < settings.minimum_order_value,
            **site_extra(request, db),
        },
    )


@router.get("/kasse")
def checkout_page(request: Request, db: Session = Depends(get_db)):
    lines, total = cart_lib.resolve_cart_lines(request, db)
    settings = get_settings(db)
    contact = {}
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value:
        contact = decode_contact(cookie_value)
    return templates.TemplateResponse(
        "site/checkout.html",
        {
            "request": request,
            "lines": lines,
            "total": total,
            "below_minimum": total > 0 and total < settings.minimum_order_value,
            "contact": contact,
            **site_extra(request, db),
        },
    )


@router.post("/kasse")
async def place_order(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    settings = get_settings(db)
    lines, total = cart_lib.resolve_cart_lines(request, db)

    def error_response(message: str):
        return templates.TemplateResponse(
            "site/checkout.html",
            {
                "request": request,
                "lines": lines,
                "total": total,
                "below_minimum": total > 0 and total < settings.minimum_order_value,
                "contact": {k: form.get(k, "") for k in ("customer_name", "phone", "customer_zip", "delivery_address")},
                "error": message,
                **site_extra(request, db),
            },
            status_code=400,
        )

    if not settings.accepting_orders:
        return error_response("Wir nehmen aktuell keine Bestellungen an.")
    if not lines:
        return error_response("Der Warenkorb ist leer.")
    if total < settings.minimum_order_value:
        return error_response(
            f"Mindestbestellwert ist CHF {settings.minimum_order_value:.2f}."
        )

    customer_name = (form.get("customer_name") or "").strip()
    phone = (form.get("phone") or "").strip()
    customer_zip = (form.get("customer_zip") or "").strip()
    delivery_address = (form.get("delivery_address") or "").strip()
    note = (form.get("note") or "").strip()
    if not customer_name or not phone or not customer_zip:
        return error_response("Name, Telefonnummer und PLZ sind erforderlich.")

    if settings.pickup_enabled and settings.delivery_enabled:
        order_type = OrderType.pickup if form.get("order_type") == "pickup" else OrderType.delivery
    elif settings.pickup_enabled:
        order_type = OrderType.pickup
    else:
        order_type = OrderType.delivery

    if order_type == OrderType.delivery and not delivery_address:
        return error_response("Bitte eine Lieferadresse angeben.")

    order_total = total + (settings.delivery_fee if order_type == OrderType.delivery else 0.0)
    order = Order(
        customer_name=customer_name,
        phone=phone,
        customer_zip=customer_zip,
        order_type=order_type,
        delivery_address=delivery_address if order_type == OrderType.delivery else "",
        note=note,
        total=order_total,
        items=[
            OrderItem(
                item_name=line["item"].name,
                options_summary=", ".join(o.name for o in line["options"]),
                unit_price=line["unit_price"],
                quantity=line["quantity"],
            )
            for line in lines
        ],
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    cart_lib.clear_cart(request)
    request.session["placed_order_ids"] = request.session.get("placed_order_ids", []) + [order.id]

    response = RedirectResponse(url=f"/bestellung/{order.id}/bestaetigung", status_code=303)
    if form.get("remember_contact"):
        response.set_cookie(
            COOKIE_NAME,
            encode_contact(
                {
                    "customer_name": customer_name,
                    "phone": phone,
                    "customer_zip": customer_zip,
                    "delivery_address": delivery_address,
                }
            ),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
    else:
        response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/bestellung/{order_id}/bestaetigung")
def order_confirmation(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is not None and order_id not in request.session.get("placed_order_ids", []):
        order = None
    if order is None:
        return templates.TemplateResponse(
            "site/confirmation.html",
            {"request": request, "order": None, **site_extra(request, db)},
            status_code=404,
        )
    return templates.TemplateResponse(
        "site/confirmation.html",
        {
            "request": request,
            "order": order,
            "status_label": ORDER_STATUS_LABELS[order.status],
            "type_label": ORDER_TYPE_LABELS[order.order_type],
            **site_extra(request, db),
        },
    )


@router.get("/verfolgen")
def track_order(
    request: Request,
    bestellnummer: Optional[str] = None,
    plz: Optional[str] = None,
    db: Session = Depends(get_db),
):
    order = None
    not_found = False
    remaining_minutes = None
    if bestellnummer and plz:
        not_found = True
        if bestellnummer.strip().isdigit():
            candidate = db.get(Order, int(bestellnummer.strip()))
            if candidate is not None and candidate.customer_zip.strip() == plz.strip():
                order = candidate
                not_found = False

    if order is not None and order.status.value in ("received", "preparing"):
        settings = get_settings(db)
        estimate = (
            settings.estimated_delivery_minutes
            if order.order_type == OrderType.delivery
            else settings.estimated_pickup_minutes
        )
        elapsed_minutes = (datetime.utcnow() - order.created_at).total_seconds() / 60
        remaining_minutes = max(0, round(estimate - elapsed_minutes))

    return templates.TemplateResponse(
        "site/tracking.html",
        {
            "request": request,
            "order": order,
            "not_found": not_found,
            "status_label": ORDER_STATUS_LABELS[order.status] if order else None,
            "type_label": ORDER_TYPE_LABELS[order.order_type] if order else None,
            "remaining_minutes": remaining_minutes,
            "bestellnummer": bestellnummer or "",
            "plz": plz or "",
            **site_extra(request, db),
        },
    )


@router.get("/rechtliches/{slug}")
def legal_page(slug: str, request: Request, db: Session = Depends(get_db)):
    page = get_content_page(db, slug)
    paragraphs = [p.strip() for p in page.body.split("\n\n") if p.strip()]
    return templates.TemplateResponse(
        "site/legal.html",
        {"request": request, "page": page, "paragraphs": paragraphs, **site_extra(request, db)},
    )
