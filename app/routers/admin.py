import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_admin, verify_same_origin
from ..database import get_db
from ..models import (
    ORDER_STATUS_LABELS,
    WEEKDAY_LABELS,
    Category,
    MenuItem,
    OpeningHour,
    Option,
    OptionGroup,
    Order,
    OrderStatus,
    RestaurantSettings,
    SelectionType,
)

UPLOAD_DIR = "app/static/uploads"

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")
mutating = [Depends(verify_same_origin)]

ORDER_NEXT_STATUS = {
    OrderStatus.received: OrderStatus.preparing,
    OrderStatus.preparing: OrderStatus.ready,
    OrderStatus.ready: OrderStatus.completed,
}
ORDER_NEXT_STATUS_LABEL = {
    OrderStatus.received: "In Zubereitung nehmen",
    OrderStatus.preparing: "Als fertig markieren",
    OrderStatus.ready: "Abschliessen",
}
ORDER_TEMPLATE_EXTRAS = {
    "status_labels": ORDER_STATUS_LABELS,
    "next_status": ORDER_NEXT_STATUS,
    "next_status_label": ORDER_NEXT_STATUS_LABEL,
}


def get_settings(db: Session) -> RestaurantSettings:
    settings = db.get(RestaurantSettings, 1)
    if settings is None:
        settings = RestaurantSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_opening_hours_by_weekday(db: Session) -> dict:
    by_day = {weekday: [] for weekday in range(7)}
    for hour in db.query(OpeningHour).order_by(OpeningHour.weekday, OpeningHour.open_time).all():
        by_day[hour.weekday].append(hour)
    return by_day


SETTINGS_TABS = [
    ("general", "Stammdaten"),
    ("ordering", "Bestellannahme"),
    ("hours", "Öffnungszeiten"),
    ("delivery-zone", "Liefergebiet"),
    ("payment", "Zahlungsdienstleister"),
    ("email", "E-Mail"),
    ("customers", "Kunden"),
]


def settings_context(active_tab: str, db: Session) -> dict:
    return {
        "settings_tabs": SETTINGS_TABS,
        "active_settings_tab": active_tab,
        "settings": get_settings(db),
    }


@router.get("/menu")
def menu_list(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.sort_order, Category.id).all()
    return templates.TemplateResponse(
        "admin/menu_list.html", {"request": request, "categories": categories}
    )


@router.post("/categories", dependencies=mutating)
def create_category(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Category(name=name))
    db.commit()
    return RedirectResponse(url="/admin/menu", status_code=303)


@router.post("/categories/{category_id}/delete", dependencies=mutating)
def delete_category(category_id: int, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    db.delete(category)
    db.commit()
    return RedirectResponse(url="/admin/menu", status_code=303)


@router.get("/items/new")
def new_item_form(request: Request, category_id: Optional[int] = None, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.sort_order, Category.id).all()
    return templates.TemplateResponse(
        "admin/item_form.html",
        {
            "request": request,
            "categories": categories,
            "item": None,
            "preselected_category_id": category_id,
        },
    )


@router.post("/items/new", dependencies=mutating)
def create_item(
    name: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    category_id: int = Form(...),
    db: Session = Depends(get_db),
):
    item = MenuItem(name=name, description=description, price=price, category_id=category_id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return RedirectResponse(url=f"/admin/items/{item.id}/edit", status_code=303)


@router.get("/items/{item_id}/edit")
def edit_item_form(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    categories = db.query(Category).order_by(Category.sort_order, Category.id).all()
    return templates.TemplateResponse(
        "admin/item_form.html",
        {"request": request, "categories": categories, "item": item, "preselected_category_id": None},
    )


@router.post("/items/{item_id}/edit", dependencies=mutating)
def update_item(
    item_id: int,
    name: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    category_id: int = Form(...),
    db: Session = Depends(get_db),
):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    item.name = name
    item.description = description
    item.price = price
    item.category_id = category_id
    db.commit()
    return RedirectResponse(url=f"/admin/items/{item_id}/edit", status_code=303)


@router.post("/items/{item_id}/delete", dependencies=mutating)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    db.delete(item)
    db.commit()
    return RedirectResponse(url="/admin/menu", status_code=303)


@router.post("/items/{item_id}/toggle", dependencies=mutating)
def toggle_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    item.is_available = not item.is_available
    db.commit()
    return templates.TemplateResponse(
        "admin/_sold_out_button.html", {"request": request, "item": item}
    )


@router.post("/items/{item_id}/option-groups", dependencies=mutating)
def add_option_group(
    item_id: int,
    request: Request,
    name: str = Form(...),
    selection_type: SelectionType = Form(SelectionType.single),
    required: bool = Form(False),
    db: Session = Depends(get_db),
):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    db.add(
        OptionGroup(
            menu_item_id=item_id, name=name, selection_type=selection_type, required=required
        )
    )
    db.commit()
    return templates.TemplateResponse(
        "admin/_option_groups.html", {"request": request, "item": item}
    )


@router.post("/option-groups/{group_id}/delete", dependencies=mutating)
def delete_option_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    group = db.get(OptionGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Optionsgruppe nicht gefunden")
    item_id = group.menu_item_id
    db.delete(group)
    db.commit()
    item = db.get(MenuItem, item_id)
    return templates.TemplateResponse(
        "admin/_option_groups.html", {"request": request, "item": item}
    )


@router.post("/option-groups/{group_id}/options", dependencies=mutating)
def add_option(
    group_id: int,
    request: Request,
    name: str = Form(...),
    price_delta: float = Form(0.0),
    db: Session = Depends(get_db),
):
    group = db.get(OptionGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Optionsgruppe nicht gefunden")
    db.add(Option(option_group_id=group_id, name=name, price_delta=price_delta))
    db.commit()
    return templates.TemplateResponse(
        "admin/_option_group.html", {"request": request, "group": group}
    )


@router.post("/options/{option_id}/delete", dependencies=mutating)
def delete_option(option_id: int, request: Request, db: Session = Depends(get_db)):
    option = db.get(Option, option_id)
    if option is None:
        raise HTTPException(status_code=404, detail="Option nicht gefunden")
    group_id = option.option_group_id
    db.delete(option)
    db.commit()
    group = db.get(OptionGroup, group_id)
    return templates.TemplateResponse(
        "admin/_option_group.html", {"request": request, "group": group}
    )


@router.get("/orders")
def orders_list(request: Request, db: Session = Depends(get_db)):
    orders = db.query(Order).order_by(Order.created_at.desc()).all()
    return templates.TemplateResponse(
        "admin/orders_list.html",
        {"request": request, "orders": orders, **ORDER_TEMPLATE_EXTRAS},
    )


@router.post("/orders/{order_id}/advance", dependencies=mutating)
def advance_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    next_status = ORDER_NEXT_STATUS.get(order.status)
    if next_status is None:
        raise HTTPException(status_code=400, detail="Kein weiterer Status möglich")
    order.status = next_status
    db.commit()
    return templates.TemplateResponse(
        "admin/_order_row_actions.html",
        {"request": request, "order": order, **ORDER_TEMPLATE_EXTRAS},
    )


@router.post("/orders/{order_id}/cancel", dependencies=mutating)
def cancel_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    order.status = OrderStatus.cancelled
    db.commit()
    return templates.TemplateResponse(
        "admin/_order_row_actions.html",
        {"request": request, "order": order, **ORDER_TEMPLATE_EXTRAS},
    )


@router.get("/settings")
def settings_index():
    return RedirectResponse(url="/admin/settings/general")


@router.get("/settings/general")
def settings_general(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_general.html", {"request": request, **settings_context("general", db)}
    )


@router.post("/settings/general", dependencies=mutating)
async def update_general_settings(
    name: str = Form(""),
    address_street: str = Form(""),
    address_zip: str = Form(""),
    address_city: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    logo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.name = name
    settings.address_street = address_street
    settings.address_zip = address_zip
    settings.address_city = address_city
    settings.phone = phone
    settings.email = email
    if logo is not None and logo.filename:
        extension = os.path.splitext(logo.filename)[1].lower()
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename = f"logo-{uuid.uuid4().hex}{extension}"
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(await logo.read())
        settings.logo_filename = filename
    db.commit()
    return RedirectResponse(url="/admin/settings/general", status_code=303)


@router.get("/settings/ordering")
def settings_ordering(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_ordering.html", {"request": request, **settings_context("ordering", db)}
    )


@router.post("/settings/ordering", dependencies=mutating)
def update_ordering_settings(
    accepting_orders: bool = Form(False),
    pickup_enabled: bool = Form(False),
    delivery_enabled: bool = Form(False),
    minimum_order_value: float = Form(0.0),
    delivery_fee: float = Form(0.0),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.accepting_orders = accepting_orders
    settings.pickup_enabled = pickup_enabled
    settings.delivery_enabled = delivery_enabled
    settings.minimum_order_value = minimum_order_value
    settings.delivery_fee = delivery_fee
    db.commit()
    return RedirectResponse(url="/admin/settings/ordering", status_code=303)


@router.get("/settings/delivery-zone")
def settings_delivery_zone(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_delivery_zone.html",
        {"request": request, **settings_context("delivery-zone", db)},
    )


@router.post("/settings/delivery-zone", dependencies=mutating)
def update_delivery_zone_settings(
    delivery_zone_center: str = Form(""),
    delivery_zone_radius_km: float = Form(0.0),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.delivery_zone_center = delivery_zone_center
    settings.delivery_zone_radius_km = delivery_zone_radius_km
    db.commit()
    return RedirectResponse(url="/admin/settings/delivery-zone", status_code=303)


@router.get("/settings/payment")
def settings_payment(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_payment.html", {"request": request, **settings_context("payment", db)}
    )


@router.post("/settings/payment", dependencies=mutating)
def update_payment_settings(
    payrexx_instance: str = Form(""),
    payrexx_api_key: str = Form(""),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.payrexx_instance = payrexx_instance
    if payrexx_api_key:
        settings.payrexx_api_key = payrexx_api_key
    db.commit()
    return RedirectResponse(url="/admin/settings/payment", status_code=303)


@router.get("/settings/email")
def settings_email(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_email.html", {"request": request, **settings_context("email", db)}
    )


@router.post("/settings/email", dependencies=mutating)
def update_email_settings(
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from_email: str = Form(""),
    smtp_from_name: str = Form(""),
    send_order_confirmation: bool = Form(False),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.smtp_host = smtp_host
    settings.smtp_port = smtp_port
    settings.smtp_username = smtp_username
    if smtp_password:
        settings.smtp_password = smtp_password
    settings.smtp_from_email = smtp_from_email
    settings.smtp_from_name = smtp_from_name
    settings.send_order_confirmation = send_order_confirmation
    db.commit()
    return RedirectResponse(url="/admin/settings/email", status_code=303)


@router.get("/settings/customers")
def settings_customers(request: Request, db: Session = Depends(get_db)):
    orders = db.query(Order).order_by(Order.created_at.desc()).all()
    customers = {}
    for order in orders:
        entry = customers.setdefault(
            order.phone,
            {
                "name": order.customer_name,
                "phone": order.phone,
                "address": order.delivery_address,
                "order_count": 0,
                "last_order_at": order.created_at,
            },
        )
        entry["order_count"] += 1
    return templates.TemplateResponse(
        "admin/settings_customers.html",
        {
            "request": request,
            "customers": sorted(customers.values(), key=lambda c: c["last_order_at"], reverse=True),
            **settings_context("customers", db),
        },
    )


@router.get("/settings/hours")
def settings_hours(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_hours.html",
        {
            "request": request,
            "hours_by_weekday": get_opening_hours_by_weekday(db),
            "weekday_labels": WEEKDAY_LABELS,
            **settings_context("hours", db),
        },
    )


@router.post("/settings/hours/{weekday}/windows", dependencies=mutating)
def add_opening_hour_window(
    weekday: int,
    request: Request,
    open_time: str = Form("11:00"),
    close_time: str = Form("22:00"),
    db: Session = Depends(get_db),
):
    db.add(OpeningHour(weekday=weekday, open_time=open_time, close_time=close_time))
    db.commit()
    return templates.TemplateResponse(
        "admin/_opening_hour_day.html",
        {
            "request": request,
            "weekday": weekday,
            "weekday_label": WEEKDAY_LABELS[weekday],
            "windows": get_opening_hours_by_weekday(db)[weekday],
        },
    )


@router.post("/settings/hours/windows/{window_id}/delete", dependencies=mutating)
def delete_opening_hour_window(window_id: int, request: Request, db: Session = Depends(get_db)):
    window = db.get(OpeningHour, window_id)
    if window is None:
        raise HTTPException(status_code=404, detail="Zeitfenster nicht gefunden")
    weekday = window.weekday
    db.delete(window)
    db.commit()
    return templates.TemplateResponse(
        "admin/_opening_hour_day.html",
        {
            "request": request,
            "weekday": weekday,
            "weekday_label": WEEKDAY_LABELS[weekday],
            "windows": get_opening_hours_by_weekday(db)[weekday],
        },
    )
