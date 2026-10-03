import os
import re
import xml.etree.ElementTree as ET
import secrets
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_admin, verify_same_origin
from ..assets import css_version
from ..database import get_db
from ..models import (
    ORDER_STATUS_LABELS,
    WEEKDAY_LABELS,
    Category,
    ItemOptionGroup,
    MenuItem,
    OpeningHour,
    Option,
    OptionGroup,
    LoginLink,
    Order,
    OrderStatus,
    SelectionType,
)
from ..repo import get_all_content_pages, get_content_page, get_opening_hours_by_weekday, get_settings

UPLOAD_DIR = "app/static/uploads"

# Signature (magic bytes) -> file extension, checked against actual upload content
# rather than the client-supplied filename, so only real raster images can be saved.
LOGO_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
]


SVG_NS = "{http://www.w3.org/2000/svg}"
SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "defs", "lineargradient", "radialgradient", "stop", "clippath", "mask", "title",
    "desc", "text", "tspan", "style",
}
SVG_ATTR_BAD_VALUE = re.compile(r"javascript:|data:|expression\(|@import|url\(\s*[\"']?\s*(?!#)", re.I)


def is_safe_svg(data: bytes) -> bool:
    """Allowlist check: only plain drawing elements, no scripts, handlers or external refs."""
    if len(data) > 200_000 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        return False
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return False
    if root.tag != SVG_NS + "svg":
        return False
    for el in root.iter():
        if not isinstance(el.tag, str) or not el.tag.startswith(SVG_NS):
            return False
        if el.tag[len(SVG_NS):].lower() not in SVG_TAGS:
            return False
        if el.tag.endswith("style") and el.text and SVG_ATTR_BAD_VALUE.search(el.text):
            return False
        for name, value in el.attrib.items():
            local = name.rsplit("}", 1)[-1].lower()
            if local.startswith("on"):
                return False
            if local == "href" and not value.startswith("#"):
                return False
            if SVG_ATTR_BAD_VALUE.search(value):
                return False
    return True


def detect_logo_extension(data: bytes) -> Optional[str]:
    if is_safe_svg(data):
        return ".svg"
    for signature, extension in LOGO_SIGNATURES:
        if data.startswith(signature):
            return extension
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


async def save_uploaded_image(upload: UploadFile, prefix: str) -> str:
    data = await upload.read()
    extension = detect_logo_extension(data)
    if extension is None:
        raise HTTPException(
            status_code=400, detail="Bild muss ein PNG-, JPEG-, GIF-, WebP- oder (sicheres) SVG-Bild sein"
        )
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{prefix}-{uuid.uuid4().hex}{extension}"
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        f.write(data)
    return filename

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["css_version"] = css_version
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


SETTINGS_TABS = [
    ("general", "Stammdaten"),
    ("website", "Webseite"),
    ("ordering", "Bestellannahme"),
    ("hours", "Öffnungszeiten"),
    ("delivery-zone", "Liefergebiet"),
    ("payment", "Zahlungsdienstleister"),
    ("email", "E-Mail"),
    ("legal", "Rechtliches"),
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


@router.post("/categories/{category_id}/promo", dependencies=mutating)
def toggle_category_promo(category_id: int, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    category.is_promo = not category.is_promo
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
async def create_item(
    name: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    category_id: int = Form(...),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    item = MenuItem(name=name, description=description, price=price, category_id=category_id)
    if image is not None and image.filename:
        item.image_filename = await save_uploaded_image(image, "item")
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
        {
            "request": request,
            "categories": categories,
            "item": item,
            "preselected_category_id": None,
            "library": db.query(OptionGroup).order_by(OptionGroup.name).all(),
        },
    )


@router.post("/items/{item_id}/edit", dependencies=mutating)
async def update_item(
    item_id: int,
    name: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    category_id: int = Form(...),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    item.name = name
    item.description = description
    item.price = price
    item.category_id = category_id
    if image is not None and image.filename:
        item.image_filename = await save_uploaded_image(image, "item")
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


@router.post("/items/{item_id}/toggle-new", dependencies=mutating)
def toggle_new(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    item.is_new = not item.is_new
    db.commit()
    return templates.TemplateResponse(
        "admin/_new_button.html", {"request": request, "item": item}
    )


def _item_links_response(request: Request, db: Session, item_id: int):
    item = db.get(MenuItem, item_id)
    return templates.TemplateResponse(
        "admin/_item_option_links.html",
        {
            "request": request,
            "item": item,
            "library": db.query(OptionGroup).order_by(OptionGroup.name).all(),
        },
    )


def _clean_max(selection_type: SelectionType, max_selections: Optional[int]) -> Optional[int]:
    if selection_type == SelectionType.multiple and max_selections and max_selections > 0:
        return max_selections
    return None


@router.get("/option-groups")
def option_library(request: Request, db: Session = Depends(get_db)):
    groups = db.query(OptionGroup).order_by(OptionGroup.name).all()
    return templates.TemplateResponse(
        "admin/option_library.html", {"request": request, "groups": groups}
    )


@router.post("/option-groups", dependencies=mutating)
def create_option_group(
    name: str = Form(...),
    selection_type: SelectionType = Form(SelectionType.single),
    db: Session = Depends(get_db),
):
    group = OptionGroup(name=name.strip(), selection_type=selection_type)
    db.add(group)
    db.commit()
    db.refresh(group)
    return RedirectResponse(url=f"/admin/option-groups/{group.id}", status_code=303)


@router.get("/option-groups/{group_id}")
def option_group_page(group_id: int, request: Request, db: Session = Depends(get_db)):
    group = db.get(OptionGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Optionsgruppe nicht gefunden")
    categories = db.query(Category).order_by(Category.sort_order, Category.id).all()
    links_by_item = {link.menu_item_id: link for link in group.links}
    return templates.TemplateResponse(
        "admin/option_group_page.html",
        {
            "request": request,
            "group": group,
            "categories": categories,
            "links_by_item": links_by_item,
        },
    )


@router.post("/option-groups/{group_id}/rename", dependencies=mutating)
def rename_option_group(
    group_id: int,
    name: str = Form(...),
    selection_type: SelectionType = Form(SelectionType.single),
    db: Session = Depends(get_db),
):
    group = db.get(OptionGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Optionsgruppe nicht gefunden")
    group.name = name.strip()
    group.selection_type = selection_type
    if selection_type == SelectionType.single:
        for link in group.links:
            link.max_selections = None
    db.commit()
    return RedirectResponse(url=f"/admin/option-groups/{group_id}", status_code=303)


@router.post("/option-groups/{group_id}/delete", dependencies=mutating)
def delete_option_group(group_id: int, db: Session = Depends(get_db)):
    group = db.get(OptionGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Optionsgruppe nicht gefunden")
    db.delete(group)
    db.commit()
    return RedirectResponse(url="/admin/option-groups", status_code=303)


@router.post("/option-groups/{group_id}/assign", dependencies=mutating)
def assign_option_group(
    group_id: int,
    item_ids: List[int] = Form([]),
    required: bool = Form(False),
    max_selections: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """Bulk assignment: the checked items get this group (existing links are
    updated); unchecked items lose it."""
    group = db.get(OptionGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Optionsgruppe nicht gefunden")
    wanted = set(item_ids)
    existing = {link.menu_item_id: link for link in group.links}
    max_value = _clean_max(group.selection_type, max_selections)
    for link in list(group.links):
        if link.menu_item_id not in wanted:
            db.delete(link)
    for item_id in wanted:
        if db.get(MenuItem, item_id) is None:
            continue
        link = existing.get(item_id)
        if link is None:
            link = ItemOptionGroup(menu_item_id=item_id, option_group_id=group_id)
            db.add(link)
        link.required = required
        link.max_selections = max_value
    db.commit()
    return RedirectResponse(url=f"/admin/option-groups/{group_id}", status_code=303)


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


@router.post("/items/{item_id}/option-links", dependencies=mutating)
def add_item_option_link(
    item_id: int,
    request: Request,
    option_group_id: int = Form(...),
    required: bool = Form(False),
    max_selections: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    item = db.get(MenuItem, item_id)
    group = db.get(OptionGroup, option_group_id)
    if item is None or group is None:
        raise HTTPException(status_code=404, detail="Artikel oder Optionsgruppe nicht gefunden")
    if not any(link.option_group_id == option_group_id for link in item.option_links):
        db.add(
            ItemOptionGroup(
                menu_item_id=item_id,
                option_group_id=option_group_id,
                required=required,
                max_selections=_clean_max(group.selection_type, max_selections),
            )
        )
        db.commit()
    return _item_links_response(request, db, item_id)


@router.post("/option-links/{link_id}/update", dependencies=mutating)
def update_item_option_link(
    link_id: int,
    request: Request,
    required: bool = Form(False),
    max_selections: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    link = db.get(ItemOptionGroup, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Zuweisung nicht gefunden")
    link.required = required
    link.max_selections = _clean_max(link.option_group.selection_type, max_selections)
    db.commit()
    return _item_links_response(request, db, link.menu_item_id)


@router.post("/option-links/{link_id}/delete", dependencies=mutating)
def delete_item_option_link(link_id: int, request: Request, db: Session = Depends(get_db)):
    link = db.get(ItemOptionGroup, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Zuweisung nicht gefunden")
    item_id = link.menu_item_id
    db.delete(link)
    db.commit()
    return _item_links_response(request, db, item_id)


LINK_STATUS_LABELS = {
    "sent": "Link gesendet",
    "no_orders": "Keine Bestellungen zu dieser E-Mail (nichts gesendet)",
    "rate_limited": "Zu viele Links (nichts gesendet)",
    "blocked": "GESPERRT – auffällig viele Anfragen",
    "mail_off": "E-Mail-Versand aus",
    "failed": "Versand fehlgeschlagen",
}


@router.get("/link-requests")
def link_requests(request: Request, db: Session = Depends(get_db)):
    entries = db.query(LoginLink).order_by(LoginLink.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "admin/link_requests.html",
        {"request": request, "entries": entries, "labels": LINK_STATUS_LABELS},
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
        settings.logo_filename = await save_uploaded_image(logo, "logo")
    db.commit()
    return RedirectResponse(url="/admin/settings/general", status_code=303)


@router.get("/settings/website")
def settings_website(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_website.html",
        {
            "request": request,
            **settings_context("website", db),
        },
    )


@router.post("/settings/website", dependencies=mutating)
async def update_website_settings(
    hero_headline: str = Form(""),
    hero_subheadline: str = Form(""),
    promo_banner_enabled: bool = Form(False),
    promo_banner_text: str = Form(""),
    estimated_pickup_minutes: int = Form(15),
    estimated_delivery_minutes: int = Form(30),
    badge_1: str = Form(""),
    badge_2: str = Form(""),
    badge_3: str = Form(""),
    rating_text: str = Form(""),
    accent_color: str = Form("#c8102e"),
    hero_image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.hero_headline = hero_headline
    settings.hero_subheadline = hero_subheadline
    settings.promo_banner_enabled = promo_banner_enabled
    settings.promo_banner_text = promo_banner_text
    settings.estimated_pickup_minutes = estimated_pickup_minutes
    settings.estimated_delivery_minutes = estimated_delivery_minutes
    settings.badge_1 = badge_1
    settings.badge_2 = badge_2
    settings.badge_3 = badge_3
    settings.rating_text = rating_text
    settings.accent_color = accent_color or "#c8102e"
    if hero_image is not None and hero_image.filename:
        settings.hero_image_filename = await save_uploaded_image(hero_image, "hero")
    db.commit()
    return RedirectResponse(url="/admin/settings/website", status_code=303)


@router.get("/settings/legal")
def settings_legal(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_legal.html",
        {
            "request": request,
            "pages": get_all_content_pages(db),
            **settings_context("legal", db),
        },
    )


@router.post("/settings/legal/{slug}", dependencies=mutating)
def update_legal_page(
    slug: str,
    title: str = Form(""),
    body: str = Form(""),
    db: Session = Depends(get_db),
):
    page = get_content_page(db, slug)
    page.title = title
    page.body = body
    db.commit()
    return RedirectResponse(url="/admin/settings/legal", status_code=303)


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


@router.post("/settings/freiwirt-token/regenerate", dependencies=mutating)
def regenerate_freiwirt_token(db: Session = Depends(get_db)):
    settings = get_settings(db)
    settings.freiwirt_api_token = secrets.token_hex(24)
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


@router.post("/settings/hours-note", dependencies=mutating)
def update_hours_note(hours_note: str = Form(""), db: Session = Depends(get_db)):
    settings = get_settings(db)
    settings.hours_note = hours_note.strip()
    db.commit()
    return RedirectResponse(url="/admin/settings/hours", status_code=303)


@router.post("/settings/hours/{weekday}/windows", dependencies=mutating)
def add_opening_hour_window(
    weekday: int,
    request: Request,
    open_time: str = Form("11:00"),
    close_time: str = Form("22:00"),
    db: Session = Depends(get_db),
):
    if weekday not in WEEKDAY_LABELS:
        raise HTTPException(status_code=404, detail="Wochentag nicht gefunden")
    db.add(OpeningHour(weekday=weekday, open_time=open_time, close_time=close_time))
    db.commit()
    if "hx-request" not in request.headers:  # plain form post (htmx not running)
        return RedirectResponse(url="/admin/settings/hours", status_code=303)
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
    if "hx-request" not in request.headers:  # plain form post (htmx not running)
        return RedirectResponse(url="/admin/settings/hours", status_code=303)
    return templates.TemplateResponse(
        "admin/_opening_hour_day.html",
        {
            "request": request,
            "weekday": weekday,
            "weekday_label": WEEKDAY_LABELS[weekday],
            "windows": get_opening_hours_by_weekday(db)[weekday],
        },
    )
