import hashlib
import io
from datetime import datetime, timedelta
import os
import re
import xml.etree.ElementTree as ET
import secrets
import uuid
from typing import List, Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit
from .. import customer as cust
from .. import zones as zones_lib
from .. import orderflow
from ..auth import require_admin, verify_same_origin
from .. import timeutil
from ..assets import admin_version, css_version
from ..database import get_db
from ..models import (
    ORDER_STATUS_LABELS,
    DEFAULT_CANCEL_REASONS,
    PAYMENT_LABELS,
    WEEKDAY_LABELS,
    Category,
    ItemOptionGroup,
    MenuItem,
    OpeningHour,
    Option,
    OptionGroup,
    AuditLog,
    DeliveryZone,
    AvailabilityRule,
    LoginLink,
    Order,
    OrderStatus,
    SelectionType,
)
from ..repo import top_seller_ids, effective_shop, get_all_content_pages, get_content_page, get_opening_hours_by_weekday, get_settings

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


def shrink_to_webp(data: bytes, max_side: int) -> Optional[bytes]:
    """Rotates per EXIF, scales down to max_side and re-encodes as WebP so phone
    photos (several MB) become ~50 KB. Returns None if Pillow is missing or the
    file can't be processed - the caller then stores the original unchanged."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        Image.MAX_IMAGE_PIXELS = 50_000_000
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_side, max_side), Image.LANCZOS)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "transparency" in img.info else "RGB")
            out = io.BytesIO()
            img.save(out, "WEBP", quality=80, method=6)
            return out.getvalue()
    except Exception:
        return None


async def save_uploaded_image(upload: UploadFile, prefix: str, max_side: Optional[int] = None) -> str:
    data = await upload.read()
    extension = detect_logo_extension(data)
    if extension is None:
        raise HTTPException(
            status_code=400, detail="Bild muss ein PNG-, JPEG-, GIF-, WebP- oder (sicheres) SVG-Bild sein"
        )
    # Photos are scaled down; logos (SVG/GIF/transparent PNG) are stored as uploaded.
    if max_side and extension in (".jpg", ".png", ".webp"):
        small = shrink_to_webp(data, max_side)
        if small is not None and len(small) < len(data):
            data, extension = small, ".webp"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{prefix}-{uuid.uuid4().hex}{extension}"
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        f.write(data)
    return filename

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["css_version"] = css_version
templates.env.globals["admin_version"] = admin_version
timeutil.register(templates)
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


# (slug, label, sections). Every section keeps its own template and view function; a tab page
# renders its sections one below the other. Hidden tabs have no entry in the navigation.
SETTINGS_TABS = [
    ("betrieb", "Betrieb", ["general", "hours"]),
    ("bestellung", "Bestellung", ["ordering", "times", "delivery-zone", "payment"]),
    ("mitteilungen", "Benachrichtigung", ["email"]),
    ("legal", "Rechtliches", ["legal"]),
    ("customers", "Kunden", ["customers"]),
    ("benutzer", "Benutzer", ["users"]),
    ("system", "System", ["security", "audit"]),
]
HIDDEN_SETTINGS_TABS = [("website", "Webseite", ["website"])]


def settings_context(active_tab: str, db: Session) -> dict:
    return {
        "settings_tabs": SETTINGS_TABS,
        "active_settings_tab": active_tab,
        "settings": get_settings(db),
    }


DASH_PERIODS = {"today": ("Heute", 1), "7": ("7 Tage", 7), "30": ("30 Tage", 30)}
WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


QUICK_SWITCHES = [
    ("accepting_orders", "Bestellungen"),
    ("delivery_enabled", "Lieferung"),
    ("pickup_enabled", "Abholung"),
]


def _switches_response(request: Request, db: Session):
    return templates.TemplateResponse("admin/_quick_switches.html", {"request": request, "rows": _switch_rows(db)})


def _switch_rows(db: Session) -> list:
    """Shown green only while customers can really order that way right now."""
    settings = get_settings(db)
    live = effective_shop(settings, get_opening_hours_by_weekday(db))["can_order"]
    return [
        ("accepting_orders", "Bestellungen", live),
        ("delivery_enabled", "Lieferung", live and bool(settings.delivery_enabled)),
        ("pickup_enabled", "Abholung", live and bool(settings.pickup_enabled)),
    ]


def _force_orders(settings, shop: dict, open_: bool) -> None:
    """Manual override of the opening hours until they change state (opening / closing time)."""
    settings.order_override = "open" if open_ else "closed"
    settings.order_override_base = "closed" if shop["state"] == "closed" else "open"


@router.post("/quick/{field}", dependencies=mutating)
def quick_switch(field: str, request: Request, db: Session = Depends(get_db)):
    """One-tap on/off for the website. Bestellungen is the master: off turns everything off,
    on turns both ways on; Lieferung / Abholung switch alone and wake or stop the master."""
    if field not in dict(QUICK_SWITCHES):
        raise HTTPException(status_code=404, detail="Unbekannter Schalter")
    settings = get_settings(db)
    shop = effective_shop(settings, get_opening_hours_by_weekday(db))
    live = shop["can_order"]
    if field == "accepting_orders":
        turn_on = not live
        settings.pickup_enabled = settings.delivery_enabled = turn_on
        _force_orders(settings, shop, turn_on)
        text = "Bestellungen: " + ("an (Lieferung + Abholung)" if turn_on else "aus (alles aus)")
    else:
        other = "pickup_enabled" if field == "delivery_enabled" else "delivery_enabled"
        turn_on = not (live and getattr(settings, field))
        if turn_on and not live:
            setattr(settings, other, False)  # waking the shop with one way only
        setattr(settings, field, turn_on)
        if turn_on:
            _force_orders(settings, shop, True)
        elif not getattr(settings, other):
            _force_orders(settings, shop, False)
        text = f"{dict(QUICK_SWITCHES)[field]}: {'an' if turn_on else 'aus'}"
    db.commit()
    audit.note(request, text)
    return _switches_response(request, db)


@router.get("")
def dashboard(request: Request, p: str = "7", db: Session = Depends(get_db)):
    """Start page: how busy was it, when, and what sells. No money figures on purpose."""
    if p not in DASH_PERIODS:
        p = "7"
    days = DASH_PERIODS[p][1]
    today = timeutil.to_local(datetime.utcnow()).date()
    first_day = today - timedelta(days=days - 1)
    start_utc = timeutil.local_midnight_utc() - timedelta(days=days - 1)
    orders = (
        db.query(Order)
        .filter(Order.created_at >= start_utc, Order.status != OrderStatus.awaiting_payment)
        .all()
    )
    valid = [o for o in orders if o.status != OrderStatus.cancelled]
    cancelled = len(orders) - len(valid)

    per_day = {first_day + timedelta(days=i): 0 for i in range(days)}
    per_hour = [0] * 24
    per_weekday = [0] * 7
    item_counts = {}
    pay = {"cash": 0, "card": 0, "online": 0}
    delivery = 0
    for o in valid:
        local = timeutil.to_local(o.created_at)
        per_day[local.date()] = per_day.get(local.date(), 0) + 1
        per_hour[local.hour] += 1
        per_weekday[local.weekday()] += 1
        pay[o.payment_method] = pay.get(o.payment_method, 0) + 1
        if o.order_type.value == "delivery":
            delivery += 1
        for line in o.items:
            item_counts[line.item_name] = item_counts.get(line.item_name, 0) + line.quantity
    top_items = sorted(item_counts.items(), key=lambda kv: -kv[1])[:8]

    # Returning customers: phone number seen on an earlier order than this period
    phones = {o.phone for o in valid}
    returning = 0
    if phones:
        seen_before = {
            row[0] for row in db.query(Order.phone).filter(Order.created_at < start_utc, Order.phone.in_(phones)).distinct()
        }
        returning = len({o.phone for o in valid if o.phone in seen_before})
    total = len(valid)
    open_now = db.query(Order).filter(Order.status.in_(OPEN_STATES)).count()
    sold_out = db.query(MenuItem).filter(MenuItem.is_available == False).count()  # noqa: E712
    peak_hour = max(range(24), key=lambda h: per_hour[h]) if total else None
    busiest_day = max(per_day.items(), key=lambda kv: kv[1]) if total else None
    day_rows = [{"label": d.strftime("%d.%m."), "wd": WEEKDAYS_SHORT[d.weekday()], "n": n} for d, n in per_day.items()]
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "p": p,
            "rows": _switch_rows(db),
            "periods": {k: v[0] for k, v in DASH_PERIODS.items()},
            "total": total,
            "cancelled": cancelled,
            "open_now": open_now,
            "sold_out": sold_out,
            "delivery_pct": round(100 * delivery / total) if total else 0,
            "returning": returning,
            "customers": len(phones),
            "day_rows": day_rows,
            "day_max": max([r["n"] for r in day_rows] + [1]),
            "hours": [(h, per_hour[h]) for h in range(8, 24)],
            "hour_max": max(per_hour + [1]),
            "weekdays": list(zip(WEEKDAYS_SHORT, per_weekday)),
            "weekday_max": max(per_weekday + [1]),
            "top_items": top_items,
            "top_max": top_items[0][1] if top_items else 1,
            "pay": [(PAYMENT_LABELS[k], v) for k, v in pay.items() if k in PAYMENT_LABELS],
            "peak_hour": peak_hour,
            "busiest_day": busiest_day,
        },
    )


@router.get("/menu")
def menu_list(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.sort_order, Category.id).all()
    return templates.TemplateResponse(
        "admin/menu_list.html",
        {"request": request, "categories": categories, "top_ids": top_seller_ids(db)},
    )


@router.post("/categories", dependencies=mutating)
def create_category(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
    audit.note(request, name)
    db.add(Category(name=name))
    db.commit()
    return RedirectResponse(url="/admin/menu", status_code=303)


@router.post("/categories/{category_id}/delete", dependencies=mutating)
def delete_category(category_id: int, request: Request, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    audit.note(request, f"{category.name} ({len(category.items)} Artikel)")
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
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    category_id: int = Form(...),
    is_vegetarian: bool = Form(False),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    audit.note(request, f"{name}, CHF {price:.2f}")
    item = MenuItem(name=name, description=description, price=price, category_id=category_id, is_vegetarian=is_vegetarian)
    if image is not None and image.filename:
        item.image_filename = await save_uploaded_image(image, "item", max_side=720)
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
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    category_id: int = Form(...),
    is_vegetarian: bool = Form(False),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    changes = []
    if item.name != name:
        changes.append(f"Name {item.name} -> {name}")
    if abs(item.price - price) > 0.001:
        changes.append(f"Preis {item.price:.2f} -> {price:.2f}")
    audit.note(request, f"{name}: " + ", ".join(changes) if changes else name)
    item.name = name
    item.description = description
    item.price = price
    item.category_id = category_id
    item.is_vegetarian = is_vegetarian
    if image is not None and image.filename:
        old = item.image_filename
        item.image_filename = await save_uploaded_image(image, "item", max_side=720)
        _remove_upload(old)
    db.commit()
    return RedirectResponse(url=f"/admin/items/{item_id}/edit", status_code=303)


def _remove_upload(filename: str) -> None:
    """Deletes an uploaded file (basename only, so nothing outside the upload folder is touched)."""
    if filename:
        try:
            os.remove(os.path.join(UPLOAD_DIR, os.path.basename(filename)))
        except OSError:
            pass


@router.post("/items/{item_id}/image/delete", dependencies=mutating)
def delete_item_image(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    audit.note(request, item.name)
    _remove_upload(item.image_filename)
    item.image_filename = ""
    db.commit()
    return RedirectResponse(url=f"/admin/items/{item_id}/edit", status_code=303)


@router.post("/settings/logo/delete", dependencies=mutating)
def delete_logo(db: Session = Depends(get_db)):
    settings = get_settings(db)
    _remove_upload(settings.logo_filename)
    settings.logo_filename = ""
    db.commit()
    return RedirectResponse(url="/admin/settings/general", status_code=303)


@router.post("/settings/hero-image/delete", dependencies=mutating)
def delete_hero_image(db: Session = Depends(get_db)):
    settings = get_settings(db)
    _remove_upload(settings.hero_image_filename)
    settings.hero_image_filename = ""
    db.commit()
    return RedirectResponse(url="/admin/settings/website", status_code=303)


@router.post("/items/{item_id}/delete", dependencies=mutating)
def delete_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    audit.note(request, item.name)
    db.delete(item)
    db.commit()
    return RedirectResponse(url="/admin/menu", status_code=303)


@router.post("/items/{item_id}/toggle", dependencies=mutating)
def toggle_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    audit.note(request, item.name)
    if item.temp_sold_out:
        item.sold_out_until = None  # "Wieder verfügbar" also ends a temporary sold-out
    else:
        item.is_available = not item.is_available
    db.commit()
    return templates.TemplateResponse(
        "admin/_sold_out_button.html", {"request": request, "item": item}
    )


@router.post("/items/{item_id}/toggle-hot", dependencies=mutating)
def toggle_hot(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    audit.note(request, item.name)
    item.is_hot = not item.is_hot
    db.commit()
    return templates.TemplateResponse("admin/_hot_button.html", {"request": request, "item": item})


@router.post("/items/{item_id}/toggle-new", dependencies=mutating)
def toggle_new(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    audit.note(request, item.name)
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
def link_requests():
    return RedirectResponse(url="/admin/settings/security", status_code=303)


def settings_security(request: Request, db: Session = Depends(get_db)):
    entries = db.query(LoginLink).order_by(LoginLink.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "admin/settings_security.html",
        {"request": request, "entries": entries, "labels": LINK_STATUS_LABELS, **settings_context("security", db)},
    )


def settings_audit(request: Request, q: str = "", page: int = 1, db: Session = Depends(get_db)):
    per_page = 50
    query = db.query(AuditLog)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            AuditLog.action.like(like) | AuditLog.detail.like(like) | AuditLog.actor.like(like) | AuditLog.ip.like(like)
        )
    page = max(1, page)
    entries = query.order_by(AuditLog.id.desc()).offset((page - 1) * per_page).limit(per_page + 1).all()
    return templates.TemplateResponse(
        "admin/settings_audit.html",
        {
            "request": request,
            "entries": entries[:per_page],
            "has_more": len(entries) > per_page,
            "page": page,
            "q": q,
            **settings_context("audit", db),
        },
    )


OPEN_STATES = (OrderStatus.received, OrderStatus.preparing, OrderStatus.ready)


def _cancel_reasons(db: Session) -> list:
    lines = [l.strip() for l in (get_settings(db).cancel_reasons or "").splitlines() if l.strip()]
    return lines or list(DEFAULT_CANCEL_REASONS)


def _orders_context(db: Session) -> dict:
    now = datetime.utcnow()
    done_limit = max(0, get_settings(db).orders_done_limit if get_settings(db).orders_done_limit is not None else 3)
    open_orders = (
        db.query(Order).filter(Order.status.in_(OPEN_STATES)).order_by(Order.created_at.asc()).all()
    )
    done_orders = (
        db.query(Order)
        .filter(Order.status.in_((OrderStatus.completed, OrderStatus.cancelled)), Order.created_at >= now - timedelta(hours=24))
        .order_by(Order.created_at.desc())
        .limit(done_limit)
        .all()
    )
    max_id = db.query(func.max(Order.id)).scalar() or 0
    # Changes whenever an order appears, changes status, or a minute passes (elapsed times)
    signature = hashlib.md5(
        ("|".join(f"{o.id}:{o.status.value}" for o in open_orders + done_orders) + f"@{now:%Y%m%d%H%M}").encode()
    ).hexdigest()[:12]
    return {
        "open_orders": open_orders,
        "done_orders": done_orders,
        "max_id": max_id,
        "signature": signature,
        "done_limit": done_limit,
        "new_count": sum(1 for o in open_orders if o.status == OrderStatus.received),
        "payment_labels": PAYMENT_LABELS,
        "cancel_reasons": _cancel_reasons(db),
        "estimate": {"pickup": 15, "delivery": 30},
        **ORDER_TEMPLATE_EXTRAS,
    }


@router.get("/orders")
def orders_list(request: Request, db: Session = Depends(get_db)):
    settings = get_settings(db)
    ctx = _orders_context(db)
    ctx["estimate"] = {"pickup": settings.estimated_pickup_minutes, "delivery": settings.estimated_delivery_minutes}
    return templates.TemplateResponse("admin/orders_list.html", {"request": request, **ctx})


PERIODS = {"today": "Heute", "7": "7 Tage", "30": "30 Tage", "all": "Alle"}


@router.get("/orders/history")
def orders_history(
    request: Request,
    q: str = "",
    status: str = "",
    kind: str = "",
    pay: str = "",
    period: str = "7",
    page: int = 1,
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    per_page = 25
    query = db.query(Order).filter(Order.status != OrderStatus.awaiting_payment)
    if period == "today":
        query = query.filter(Order.created_at >= timeutil.local_midnight_utc())
    elif period in ("7", "30"):
        query = query.filter(Order.created_at >= datetime.utcnow() - timedelta(days=int(period)))
    if status in {s_.value for s_ in OrderStatus}:
        query = query.filter(Order.status == OrderStatus(status))
    if kind in ("pickup", "delivery"):
        query = query.filter(Order.order_type == kind)
    if pay in PAYMENT_LABELS:
        query = query.filter(Order.payment_method == pay)
    term = q.strip().lstrip("#")
    if term:
        like = f"%{term}%"
        cond = Order.customer_name.like(like) | Order.phone.like(like)
        if term.isdigit():
            cond = cond | (Order.id == int(term))
        query = query.filter(cond)
    page = max(1, page)
    orders = query.order_by(Order.id.desc()).offset((page - 1) * per_page).limit(per_page + 1).all()
    return templates.TemplateResponse(
        "admin/orders_history.html",
        {
            "request": request,
            "orders": orders[:per_page],
            "has_more": len(orders) > per_page,
            "page": page,
            "f": {"q": q, "status": status, "kind": kind, "pay": pay, "period": period},
            "periods": PERIODS,
            "estimate": {"pickup": settings.estimated_pickup_minutes, "delivery": settings.estimated_delivery_minutes},
            "cancel_reasons": _cancel_reasons(db),
            **ORDER_TEMPLATE_EXTRAS,
        },
    )


@router.get("/orders/feed")
def orders_feed(request: Request, sig: str = "", db: Session = Depends(get_db)):
    """Polled by the orders page. 204 (no swap) while nothing changed."""
    settings = get_settings(db)
    ctx = _orders_context(db)
    if sig and sig == ctx["signature"]:
        return Response(status_code=204)
    ctx["estimate"] = {"pickup": settings.estimated_pickup_minutes, "delivery": settings.estimated_delivery_minutes}
    return templates.TemplateResponse("admin/_orders_feed.html", {"request": request, **ctx})


@router.get("/orders/badge")
def orders_badge(db: Session = Depends(get_db)):
    count = db.query(Order).filter(Order.status == OrderStatus.received).count()
    return HTMLResponse(f'<span class="nav-badge">{count}</span>' if count else "")


@router.post("/orders/{order_id}/advance", dependencies=mutating)
def advance_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    next_status = ORDER_NEXT_STATUS.get(order.status)
    if next_status is None:
        raise HTTPException(status_code=400, detail="Kein weiterer Status möglich")
    order.status = next_status
    audit.note(request, f"#{order.id} -> {ORDER_STATUS_LABELS[next_status]}")
    db.commit()
    response = templates.TemplateResponse(
        "admin/_order_row_actions.html",
        {"request": request, "order": order, "cancel_reasons": _cancel_reasons(db), **ORDER_TEMPLATE_EXTRAS},
    )
    response.headers["HX-Trigger"] = "ordersChanged"
    return response


@router.post("/orders/{order_id}/resend", dependencies=mutating)
def resend_receipt(order_id: int, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Sends the order confirmation (with receipt link) to the customer again."""
    from ..mailer import mail_configured

    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    if not order.email:
        raise HTTPException(status_code=400, detail="Bei dieser Bestellung ist keine E-Mail-Adresse hinterlegt.")
    if not mail_configured(get_settings(db)):
        raise HTTPException(status_code=400, detail="E-Mail-Versand ist nicht eingerichtet (Einstellungen → E-Mail).")
    audit.note(request, f"#{order.id} an {order.email}")
    background.add_task(orderflow.send_confirmation, order.id, cust.public_base_url(request), True)
    return Response(status_code=204)


@router.post("/orders/{order_id}/cancel", dependencies=mutating)
def cancel_order(
    order_id: int,
    request: Request,
    background: BackgroundTasks,
    preset: str = Form(""),
    custom: str = Form(""),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    if order.status == OrderStatus.cancelled:
        return templates.TemplateResponse(
            "admin/_order_row_actions.html",
            {"request": request, "order": order, "cancel_reasons": _cancel_reasons(db), **ORDER_TEMPLATE_EXTRAS},
        )
    order.status = OrderStatus.cancelled
    order.cancel_reason = (custom.strip() or preset.strip())[:200]
    audit.note(request, f"#{order.id}: {order.cancel_reason or 'ohne Grund'}")
    db.commit()
    background.add_task(orderflow.send_cancellation, order.id, cust.public_base_url(request))
    response = templates.TemplateResponse(
        "admin/_order_row_actions.html",
        {"request": request, "order": order, "cancel_reasons": _cancel_reasons(db), **ORDER_TEMPLATE_EXTRAS},
    )
    response.headers["HX-Trigger"] = "ordersChanged"
    return response


@router.get("/settings")
def settings_index():
    return RedirectResponse(url="/admin/settings/betrieb")


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
    vat_number: str = Form(""),
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
    settings.vat_number = vat_number.strip()[:40]
    if logo is not None and logo.filename:
        old = settings.logo_filename
        settings.logo_filename = await save_uploaded_image(logo, "logo")
        _remove_upload(old)
    db.commit()
    return RedirectResponse(url="/admin/settings/general", status_code=303)


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
    footer_credit: str = Form(""),
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
    settings.footer_credit = footer_credit.strip()[:140]
    if hero_image is not None and hero_image.filename:
        old = settings.hero_image_filename
        settings.hero_image_filename = await save_uploaded_image(hero_image, "hero", max_side=1600)
        _remove_upload(old)
    db.commit()
    return RedirectResponse(url="/admin/settings/website", status_code=303)


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


def _valid_time(value: str) -> bool:
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value or ""))


def settings_times(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_times.html",
        {
            "request": request,
            "rules": db.query(AvailabilityRule).order_by(AvailabilityRule.id).all(),
            "categories": db.query(Category).order_by(Category.sort_order, Category.id).all(),
            "weekday_labels": WEEKDAY_LABELS,
            "error": request.query_params.get("error"),
            **settings_context("times", db),
        },
    )


@router.post("/settings/times/new", dependencies=mutating)
def create_time_rule(db: Session = Depends(get_db)):
    db.add(AvailabilityRule(name="Nicht bestellbar", weekdays="", start_time="14:00", end_time="17:00"))
    db.commit()
    return RedirectResponse(url="/admin/settings/times", status_code=303)


@router.post("/settings/times/{rule_id}", dependencies=mutating)
async def update_time_rule(rule_id: int, request: Request, db: Session = Depends(get_db)):
    rule = db.get(AvailabilityRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    form = await request.form()
    start, end = (form.get("start_time") or "").strip(), (form.get("end_time") or "").strip()
    if not (_valid_time(start) and _valid_time(end) and start < end):
        return RedirectResponse(
            url="/admin/settings/times?error=Bitte+Von+und+Bis+als+Uhrzeit+eintragen+(Von+vor+Bis,+Ende+z.B.+23:59)",
            status_code=303,
        )
    rule.name = (form.get("name") or "").strip()[:60] or "Nicht bestellbar"
    rule.weekdays = ",".join(sorted({d for d in form.getlist("weekday") if d in "0123456" and len(d) == 1}))
    rule.start_time, rule.end_time = start, end
    cat_ids = {int(v) for v in form.getlist("category") if v.isdigit()}
    item_ids = {int(v) for v in form.getlist("item") if v.isdigit()}
    rule.categories = db.query(Category).filter(Category.id.in_(cat_ids)).all() if cat_ids else []
    rule.items = db.query(MenuItem).filter(MenuItem.id.in_(item_ids)).all() if item_ids else []
    db.commit()
    return RedirectResponse(url="/admin/settings/times", status_code=303)


@router.post("/settings/times/{rule_id}/delete", dependencies=mutating)
def delete_time_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(AvailabilityRule, rule_id)
    if rule is not None:
        rule.categories = []
        rule.items = []
        db.delete(rule)
        db.commit()
    return RedirectResponse(url="/admin/settings/times", status_code=303)


def settings_ordering(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_ordering.html",
        {"request": request, "cancel_reasons_text": "\n".join(_cancel_reasons(db)), **settings_context("ordering", db)},
    )


@router.post("/settings/ordering", dependencies=mutating)
def update_ordering_settings(
    minimum_order_value: float = Form(0.0),
    delivery_fee: float = Form(0.0),
    preorder_minutes: int = Form(60),
    cancel_reasons: str = Form(""),
    order_limit_per_hour: int = Form(5),
    orders_done_limit: int = Form(3),
    service_fee_enabled: bool = Form(False),
    service_fee_percent: float = Form(0.0),
    service_fee_fixed: float = Form(0.0),
    service_fee_label: str = Form("Servicegebühr"),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.preorder_minutes = max(0, min(preorder_minutes, 240))
    settings.order_limit_per_hour = max(1, min(order_limit_per_hour, 50))
    settings.orders_done_limit = max(0, min(orders_done_limit, 30))
    cleaned = [l.strip()[:120] for l in cancel_reasons.splitlines() if l.strip()]
    settings.cancel_reasons = "" if cleaned == DEFAULT_CANCEL_REASONS else "\n".join(cleaned)[:2000]
    settings.service_fee_enabled = service_fee_enabled
    percent = max(0.0, min(service_fee_percent, 20.0))
    fixed = max(0.0, min(service_fee_fixed, 50.0))
    settings.service_fee_percent, settings.service_fee_fixed = percent, fixed
    settings.service_fee_mode = "mixed" if (percent and fixed) else ("fixed" if fixed else "percent")
    settings.service_fee_label = service_fee_label.strip() or "Servicegebühr"
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


def settings_delivery_zone(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_delivery_zone.html",
        {
            "request": request,
            "zones": zones_lib.load_zones(db),
            "error": request.query_params.get("error", ""),
            **settings_context("delivery-zone", db),
        },
    )


def _zones_back(error: str = ""):
    suffix = f"?error={quote_plus(error)}" if error else ""
    return RedirectResponse(url="/admin/settings/delivery-zone" + suffix, status_code=303)


def _parse_zips(raw: str, db: Session, own_zone_id: Optional[int] = None):
    """(new valid zips, error text). A postcode may belong to one zone only."""
    tokens = [z for z in re.split(r"[\s,;]+", raw) if z]
    valid = [z for z in tokens if re.fullmatch(r"\d{4}", z)]
    problems = []
    if len(valid) != len(tokens):
        problems.append("Keine gültige PLZ (4 Ziffern): " + ", ".join(z for z in tokens if z not in valid))
    taken = {}
    for zone in zones_lib.load_zones(db):
        if zone.id != own_zone_id:
            for z in zone.zip_list:
                taken[z] = zone.name
    clash = [f"{z} (schon in {taken[z]})" for z in valid if z in taken]
    if clash:
        problems.append("Gehört schon zu einer anderen Zone: " + ", ".join(clash))
    return [z for z in valid if z not in taken], " – ".join(problems)


@router.post("/settings/zones/new", dependencies=mutating)
def create_zone(
    request: Request,
    name: str = Form(""),
    delivery_fee: float = Form(0.0),
    min_order: float = Form(0.0),
    zips: str = Form(""),
    db: Session = Depends(get_db),
):
    valid, error = _parse_zips(zips, db)
    zone = DeliveryZone(
        name=name.strip()[:40] or "Neue Zone",
        zips=", ".join(sorted(set(valid))),
        delivery_fee=max(0.0, delivery_fee),
        min_order=max(0.0, min_order),
        sort_order=len(zones_lib.load_zones(db)),
    )
    db.add(zone)
    db.commit()
    audit.note(request, f"{zone.name}: Lieferkosten {zone.delivery_fee:.2f}, ab {zone.min_order:.2f}, PLZ {zone.zips or '-'}")
    return _zones_back(error)


@router.post("/settings/zones/{zone_id}", dependencies=mutating)
def update_zone(
    zone_id: int,
    request: Request,
    name: str = Form(""),
    delivery_fee: float = Form(0.0),
    min_order: float = Form(0.0),
    db: Session = Depends(get_db),
):
    zone = db.get(DeliveryZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone nicht gefunden")
    zone.name = name.strip()[:40] or zone.name
    zone.delivery_fee = max(0.0, delivery_fee)
    zone.min_order = max(0.0, min_order)
    db.commit()
    audit.note(request, f"{zone.name}: Lieferkosten {zone.delivery_fee:.2f}, ab {zone.min_order:.2f}")
    return _zones_back()


@router.post("/settings/zones/{zone_id}/zips/add", dependencies=mutating)
def add_zone_zips(zone_id: int, request: Request, zips: str = Form(""), db: Session = Depends(get_db)):
    zone = db.get(DeliveryZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone nicht gefunden")
    valid, error = _parse_zips(zips, db, own_zone_id=zone.id)
    zone.zips = ", ".join(sorted(set(zone.zip_list) | set(valid)))
    db.commit()
    audit.note(request, f"{zone.name}: PLZ {', '.join(valid) or '-'} hinzugefügt")
    return _zones_back(error)


@router.post("/settings/zones/{zone_id}/zips/remove", dependencies=mutating)
def remove_zone_zip(zone_id: int, request: Request, zip: str = Form(""), db: Session = Depends(get_db)):
    zone = db.get(DeliveryZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone nicht gefunden")
    zone.zips = ", ".join(z for z in zone.zip_list if z != zip.strip())
    db.commit()
    audit.note(request, f"{zone.name}: PLZ {zip.strip()} entfernt")
    return _zones_back()


@router.post("/settings/zones/{zone_id}/delete", dependencies=mutating)
def delete_zone(zone_id: int, request: Request, db: Session = Depends(get_db)):
    zone = db.get(DeliveryZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone nicht gefunden")
    audit.note(request, f"{zone.name} (PLZ {zone.zips or '-'})")
    db.delete(zone)
    db.commit()
    return _zones_back()


def settings_payment(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_payment.html",
        {"request": request, "webhook_url": cust.public_base_url(request) + "/payrexx/webhook", **settings_context("payment", db)},
    )


@router.post("/settings/payment", dependencies=mutating)
def update_payment_settings(
    payrexx_instance: str = Form(""),
    payrexx_api_key: str = Form(""),
    online_payment_enabled: bool = Form(False),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.online_payment_enabled = online_payment_enabled
    settings.payrexx_instance = payrexx_instance
    if payrexx_api_key:
        settings.payrexx_api_key = payrexx_api_key
    db.commit()
    return RedirectResponse(url="/admin/settings/payment", status_code=303)


@router.post("/settings/email/test", dependencies=mutating)
def send_test_mail(to: str = Form(""), db: Session = Depends(get_db)):
    from urllib.parse import quote
    from ..mailer import mail_configured, send_mail_result
    settings = get_settings(db)
    if not mail_configured(settings):
        return RedirectResponse(url="/admin/settings/email?test=missing", status_code=303)
    ok, error = send_mail_result(to.strip(), "Test-E-Mail vom Bestellsystem", "Wenn du das liest, funktioniert der E-Mail-Versand.")
    return RedirectResponse(url=f"/admin/settings/email?test={'ok' if ok else 'failed'}&err={quote(error)}", status_code=303)


def settings_email(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/settings_email.html",
        {"request": request, "test": request.query_params.get("test"), "test_error": request.query_params.get("err", ""), **settings_context("email", db)}
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
    attach_receipt_pdf: bool = Form(False),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    settings.attach_receipt_pdf = attach_receipt_pdf
    clean = lambda v: "" if v.strip().lower() in ("none", "null") else v.strip()
    settings.smtp_host = clean(smtp_host)
    settings.smtp_port = smtp_port
    settings.smtp_username = clean(smtp_username)
    if smtp_password:
        settings.smtp_password = smtp_password
    settings.smtp_from_email = clean(smtp_from_email)
    settings.smtp_from_name = clean(smtp_from_name)
    settings.send_order_confirmation = send_order_confirmation
    db.commit()
    return RedirectResponse(url="/admin/settings/email", status_code=303)


def _phone_key(phone: str, email: str = "") -> str:
    """Same person = same phone number, however it was typed (spaces, +41, 0041)."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0041"):
        digits = "0" + digits[4:]
    elif digits.startswith("41") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits or (email or "").strip().lower()


def settings_customers(request: Request, db: Session = Depends(get_db)):
    orders = db.query(Order).filter(Order.status != OrderStatus.awaiting_payment).order_by(Order.created_at.desc()).all()
    customers = {}
    for order in orders:  # newest first, so the first order seen carries the latest details
        entry = customers.setdefault(
            _phone_key(order.phone, order.email),
            {
                "name": order.customer_name,
                "other_names": [],
                "phone": order.phone,
                "emails": [],
                "address": order.delivery_address,
                "order_count": 0,
                "total": 0.0,
                "last_order_at": order.created_at,
            },
        )
        entry["order_count"] += 1
        if order.status != OrderStatus.cancelled:
            entry["total"] += order.total
        if order.customer_name != entry["name"] and order.customer_name not in entry["other_names"]:
            entry["other_names"].append(order.customer_name)
        if order.email and order.email.lower() not in entry["emails"]:
            entry["emails"].append(order.email.lower())
    return templates.TemplateResponse(
        "admin/settings_customers.html",
        {
            "request": request,
            "customers": sorted(customers.values(), key=lambda c: c["last_order_at"], reverse=True),
            **settings_context("customers", db),
        },
    )


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


# ---- Einstellungen: Reiter aus mehreren Abschnitten ----

def _section_views() -> dict:
    return {
        "general": settings_general, "hours": settings_hours, "times": settings_times,
        "ordering": settings_ordering, "delivery-zone": settings_delivery_zone,
        "payment": settings_payment, "email": settings_email, "website": settings_website,
        "legal": settings_legal, "customers": settings_customers,
        "security": settings_security, "audit": settings_audit,
        **EXTRA_SECTION_VIEWS,
    }


# Filled by the later settings modules (users, statistics, ...): slug -> view function
EXTRA_SECTION_VIEWS: dict = {}


def _render_tab(request: Request, db: Session, tab) -> HTMLResponse:
    slug, label, sections = tab
    views = _section_views()
    params = request.query_params
    html = []
    for sec in sections:
        view = views[sec]
        kwargs = {}
        if sec == "audit":
            kwargs = {"q": params.get("q", ""), "page": int(params["page"]) if params.get("page", "").isdigit() else 1}
        resp = view(request=request, db=db, **kwargs)
        html.append((sec, Markup(resp.template.render(**resp.context))))
    return templates.TemplateResponse(
        "admin/settings_page.html",
        {"request": request, "sections": html, "tab_label": label, **settings_context(slug, db)},
    )


def _register_settings_routes():
    for tab in SETTINGS_TABS + HIDDEN_SETTINGS_TABS:
        def view(request: Request, db: Session = Depends(get_db), _tab=tab):
            return _render_tab(request, db, _tab)
        router.add_api_route(f"/settings/{tab[0]}", view, methods=["GET"])
        for sec in tab[2]:
            if sec == tab[0]:
                continue
            def old(request: Request, _tab=tab[0], _sec=sec):
                query = ("?" + request.url.query) if request.url.query else ""
                return RedirectResponse(url=f"/admin/settings/{_tab}{query}#{_sec}", status_code=303)
            router.add_api_route(f"/settings/{sec}", old, methods=["GET"])


from . import admin_users  # noqa: E402

admin_users.setup(router, templates, mutating, EXTRA_SECTION_VIEWS)
_register_settings_routes()
