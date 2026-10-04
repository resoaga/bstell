import json
import re
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import cart as cart_lib
from .. import availability
from .. import customer as cust
from .. import zones as zones_lib
from .. import timeutil
from ..assets import css_version
from .. import orderflow, payrexx
from ..mailer import mail_configured, send_mail
from ..database import get_db
from ..models import (
    CONTENT_PAGE_DEFAULTS,
    ORDER_STATUS_LABELS,
    PAYMENT_HINTS,
    PAYMENT_LABELS,
    OrderStatus,
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
    is_currently_open,
    effective_shop,
    shop_status,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["css_version"] = css_version
timeutil.register(templates)

ALLOWED_LEGAL_SLUGS = {slug for slug, _, _ in CONTENT_PAGE_DEFAULTS}

_ISO_WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def _restaurant_schema_json(settings, hours_by_weekday: dict) -> str:
    opening_hours = [
        {
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": f"https://schema.org/{_ISO_WEEKDAYS[weekday]}",
            "opens": window.open_time,
            "closes": window.close_time,
        }
        for weekday, windows in hours_by_weekday.items()
        for window in windows
    ]
    data = {
        "@context": "https://schema.org",
        "@type": "Restaurant",
        "name": settings.name or "",
        "telephone": settings.phone or "",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": settings.address_street or "",
            "postalCode": settings.address_zip or "",
            "addressLocality": settings.address_city or "",
            "addressCountry": "CH",
        },
        "openingHoursSpecification": opening_hours,
    }
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def closed_message(settings, shop: dict) -> str:
    """Why ordering is not possible right now (shop closed, outside pre-order window)."""
    if shop.get("paused"):
        return "Wir nehmen aktuell keine Bestellungen an."
    when = f" Wir öffnen {shop['opens_text']}." if shop["opens_text"] else ""
    contact = f" Für frühere Bestellungen bitte anrufen: {settings.phone}." if settings.phone else ""
    return f"Wir haben geschlossen.{when} Vorbestellungen sind {settings.preorder_minutes or 0} Minuten vor Öffnung möglich.{contact}"


def footer_hours(hours_by_weekday: dict) -> list:
    """Compact opening hours for the footer: consecutive days with identical
    hours share one line (Mo–Fr, Sa, So). Returns [(label, text), ...]."""

    def text(day: int) -> str:
        windows = hours_by_weekday.get(day)
        if not windows:
            return "geschlossen"
        return ", ".join(f"{w.open_time}–{w.close_time}" for w in windows)

    rows = []
    day = 0
    while day <= 6:
        end = day
        while end < 6 and text(end + 1) == text(day):
            end += 1
        first, last = WEEKDAY_LABELS[day][:2], WEEKDAY_LABELS[end][:2]
        rows.append((first if end == day else f"{first}–{last}", text(day)))
        day = end + 1
    return rows


def site_extra(request: Request, db: Session) -> dict:
    _, cart_total = cart_lib.resolve_cart_lines(request, db)
    settings = get_settings(db)
    hours_by_weekday = get_opening_hours_by_weekday(db)
    identity = cust.read_identity(request)
    my_orders = cust.orders_for_identity(db, identity, limit=5)
    cart_fee = cart_lib.service_fee_for(settings, cart_total)
    shop = effective_shop(settings, hours_by_weekday)
    base_url = cust.public_base_url(request)
    removed = [] if request.headers.get("HX-Request") else request.session.pop("cart_removed", [])
    return {
        "site_base_url": base_url,
        "canonical_url": base_url + request.url.path,
        "cart_removed": removed,
        "shop": shop,
        "cart_min": zones_lib.cart_minimum(settings, zones_lib.load_zones(db)),
        "can_order": shop["can_order"],
        "service_fee": cart_fee,
        "service_fee_text": cart_lib.service_fee_text(settings),
        "cart_total_with_fee": cart_total + cart_fee,
        "my_active_order": cust.active_order(my_orders),
        "has_history": bool(my_orders),
        "settings": settings,
        "cart_count": cart_lib.cart_item_count(request),
        "cart_total": cart_total,
        "legal_pages": get_all_content_pages(db),
        "hours_by_weekday": hours_by_weekday,
        "footer_hours": footer_hours(hours_by_weekday),
        "weekday_labels": WEEKDAY_LABELS,
        "is_open": is_currently_open(hours_by_weekday),
        "restaurant_schema_json": _restaurant_schema_json(settings, hours_by_weekday),
    }


@router.get("/")
def homepage(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.sort_order, Category.id).all()
    rules = availability.load_rules(db)
    item_block = {}
    cat_block = {}
    for category in categories:
        times = []
        for item in category.items:
            until = availability.combined_block(rules, item)
            if not item.is_available:
                item_block[item.id] = "ausverkauft"
                until = until or "sold"
            elif until:
                item_block[item.id] = availability.describe(until)
            times.append(until)
        if times and all(times):
            timed = [t for t in times if t != "sold"]
            if timed and len(timed) == len(times):
                cat_block[category.id] = availability.describe(max(timed))
    promo_items = [
        item
        for category in categories
        if category.is_promo
        for item in category.items
        if item.is_available and item.id not in item_block
    ][:5]
    return templates.TemplateResponse(
        "site/index.html",
        {
            "request": request,
            "item_block": item_block,
            "cat_block": cat_block,
            "categories": categories,
            "promo_items": promo_items,
            **site_extra(request, db),
        },
    )


@router.get("/robots.txt", include_in_schema=False)
def robots_txt(request: Request):
    base = cust.public_base_url(request)
    body = (
        "User-agent: *\n"
        "Disallow: /admin\nDisallow: /api\nDisallow: /kasse\nDisallow: /warenkorb\n"
        "Disallow: /verfolgen\nDisallow: /verlauf\nDisallow: /bestellung\nDisallow: /payrexx\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return Response(body, media_type="text/plain")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request, db: Session = Depends(get_db)):
    base = cust.public_base_url(request)
    urls = [f"{base}/"] + [f"{base}/rechtliches/{p.slug}" for p in get_all_content_pages(db) if p.body.strip()]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n"
    return Response(xml, media_type="application/xml")


@router.get("/speisekarte")
def menu_page_redirect():
    return RedirectResponse(url="/", status_code=301)


@router.post("/speisekarte/hinzufuegen")
async def add_to_cart(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    settings = get_settings(db)
    extra = site_extra(request, db)
    if not extra["can_order"]:
        return templates.TemplateResponse(
            "site/_add_result.html",
            {"request": request, "error": closed_message(settings, extra["shop"]), **extra},
        )

    item_id = int(form.get("item_id"))
    item = db.get(MenuItem, item_id)
    if item is None or not item.is_available:
        return templates.TemplateResponse(
            "site/_add_result.html",
            {"request": request, "error": "Artikel nicht verfügbar.", **site_extra(request, db)},
        )

    until = availability.combined_block(availability.load_rules(db), item)
    if until:
        return templates.TemplateResponse(
            "site/_add_result.html",
            {"request": request, "error": f"{item.name}: {availability.describe(until)}.", **site_extra(request, db)},
        )

    try:
        quantity = int(form.get("qty") or 1)
    except ValueError:
        quantity = 1
    quantity = max(1, quantity)

    option_ids = []
    for link in item.option_links:
        group = link.option_group
        field_name = f"opt_{group.id}"
        if group.selection_type.value == "single":
            value = form.get(field_name)
            selected = [value] if value else []
        else:
            selected = form.getlist(field_name)
        if link.required and not selected:
            return templates.TemplateResponse(
                "site/_add_result.html",
                {
                    "request": request,
                    "error": f'Bitte "{group.name}" auswählen.',
                    **site_extra(request, db),
                },
            )
        if link.max_selections and len(selected) > link.max_selections:
            return templates.TemplateResponse(
                "site/_add_result.html",
                {
                    "request": request,
                    "error": f'Bei "{group.name}" sind maximal {link.max_selections} Auswahlen möglich.',
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
            "hide_cart": True,
            "lines": lines,
            "total": total,
            "below_minimum": total > 0 and total < zones_lib.cart_minimum(settings, zones_lib.load_zones(db)),
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
            "below_minimum": total > 0 and total < zones_lib.cart_minimum(settings, zones_lib.load_zones(db)),
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
            "below_minimum": total > 0 and total < zones_lib.cart_minimum(settings, zones_lib.load_zones(db)),
            **site_extra(request, db),
        },
    )


def last_contact(request: Request, db: Session) -> dict:
    """Prefill for the checkout from this visitor's most recent order, so nobody
    has to retype name/phone/address (no extra cookie or consent box needed)."""
    orders = cust.orders_for_identity(db, cust.read_identity(request), limit=1)
    if not orders:
        return {}
    o = orders[0]
    return {
        "customer_name": o.customer_name,
        "phone": o.phone,
        "email": o.email,
        "delivery_address": o.delivery_address,
        "customer_zip": o.customer_zip,
        "customer_city": o.customer_city,
        "order_type": o.order_type.value,
        "payment": o.payment_method,
    }


def checkout_context(request: Request, db: Session, contact: dict, **extra) -> dict:
    lines, total = cart_lib.resolve_cart_lines(request, db)
    settings = get_settings(db)
    return {
        "request": request,
        "hide_cart": True,
        "lines": lines,
        "total": total,
        "below_minimum": total > 0 and total < zones_lib.cart_minimum(settings, zones_lib.load_zones(db)),
        "contact": contact,
        "t0": int(time.time()),
        "zones_json": json.dumps(zones_lib.zones_for_js(zones_lib.load_zones(db))),
        "has_zones": bool(zones_lib.load_zones(db)),
        "payment_options": [
            (k, v, PAYMENT_HINTS[k]) for k, v in PAYMENT_LABELS.items() if k != "online" or payrexx.available(settings)
        ],
        **extra,
        **site_extra(request, db),
    }


@router.get("/kasse")
def checkout_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("site/checkout.html", checkout_context(request, db, last_contact(request, db)))


@router.post("/kasse")
async def place_order(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    form = await request.form()
    settings = get_settings(db)
    lines, total = cart_lib.resolve_cart_lines(request, db)

    def error_response(message: str):
        keys = ("customer_name", "phone", "email", "customer_zip", "customer_city", "delivery_address", "order_type", "payment", "note")
        return templates.TemplateResponse(
            "site/checkout.html",
            checkout_context(request, db, {k: form.get(k, "") for k in keys}, error=message),
            status_code=400,
        )

    shop = effective_shop(settings, get_opening_hours_by_weekday(db))
    if not shop["can_order"]:
        return error_response(closed_message(settings, shop))
    if not lines:
        return error_response("Der Warenkorb ist leer.")
    blocked = [l for l in lines if l["blocked_until"]]
    if blocked:
        names = ", ".join(f'{l["item"].name} ({availability.describe(l["blocked_until"])})' for l in blocked)
        return error_response(f"Aktuell nicht bestellbar: {names}. Bitte im Warenkorb entfernen.")

    customer_name = (form.get("customer_name") or "").strip()[:100]
    phone = (form.get("phone") or "").strip()[:30]
    email = cust.normalize_email(form.get("email") or "")
    customer_zip = (form.get("customer_zip") or "").strip()[:10]
    customer_city = (form.get("customer_city") or "").strip()[:60]
    delivery_address = (form.get("delivery_address") or "").strip()[:120]
    note = (form.get("note") or "").strip()[:500]
    payment = form.get("payment") if form.get("payment") in PAYMENT_LABELS else ""
    if payment == "online" and not payrexx.available(settings):
        payment = ""
    if not customer_name or not phone:
        return error_response("Bitte Name und Telefonnummer angeben.")
    if not email or "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
        return error_response("Bitte eine gültige E-Mail-Adresse angeben.")
    if not payment:
        return error_response("Bitte eine Zahlungsart wählen.")

    if settings.pickup_enabled and settings.delivery_enabled:
        order_type = OrderType.pickup if form.get("order_type") == "pickup" else OrderType.delivery
    elif settings.pickup_enabled:
        order_type = OrderType.pickup
    else:
        order_type = OrderType.delivery

    if order_type == OrderType.delivery:
        if not delivery_address or not customer_zip or not customer_city:
            return error_response("Bitte Strasse, PLZ und Ort für die Lieferung angeben.")
        deliverable, delivery_fee, minimum, _zone = zones_lib.delivery_terms(
            settings, zones_lib.load_zones(db), customer_zip
        )
        if not deliverable:
            return error_response(f"Leider liefern wir nicht nach {customer_zip}. Abholung ist möglich.")
        if total < minimum:
            return error_response(f"Für die Lieferung nach {customer_zip} beträgt der Mindestbestellwert CHF {minimum:.2f}.")
    else:
        customer_zip = customer_city = ""
        delivery_fee = 0.0
        if total < (settings.minimum_order_value or 0.0):
            return error_response(f"Mindestbestellwert ist CHF {settings.minimum_order_value:.2f}.")

    identity = cust.read_identity(request)
    device_key = identity["k"] or cust.new_device_key()
    ip = cust.client_ip(request)
    too_fast = False
    try:
        too_fast = time.time() - float(form.get("t0")) < orderflow.MIN_SECONDS_ON_CHECKOUT
    except (TypeError, ValueError):
        too_fast = True
    if form.get("hp_trap") or too_fast:
        return error_response("Das ging zu schnell. Bitte kurz prüfen und noch einmal absenden.")
    limit = settings.order_limit_per_hour or orderflow.DEFAULT_ORDERS_PER_HOUR
    if orderflow.ip_limit_reached(ip, limit) or orderflow.phone_or_device_limit_reached(db, phone, device_key, limit):
        return error_response("Zu viele Bestellungen in kurzer Zeit. Bitte rufe uns kurz an" + (f": {settings.phone}" if settings.phone else "") + ".")
    orderflow.register_ip(ip)
    service_fee = cart_lib.service_fee_for(settings, total)
    order_total = total + service_fee + delivery_fee
    order = Order(
        customer_name=customer_name,
        phone=phone,
        customer_zip=customer_zip,
        customer_city=customer_city,
        payment_method=payment,
        status=OrderStatus.awaiting_payment if payment == "online" else OrderStatus.received,
        email=email,
        device_key=device_key,
        tracking_token=cust.new_tracking_token(),
        order_type=order_type,
        delivery_address=delivery_address if order_type == OrderType.delivery else "",
        note=note,
        total=order_total,
        service_fee=service_fee,
        service_fee_text=cart_lib.service_fee_text(settings) if service_fee else "",
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
    request.session["placed_order_ids"] = request.session.get("placed_order_ids", []) + [order.id]

    if payment == "online":
        # The order only reaches the kitchen once Payrexx confirms the payment.
        try:
            gateway = payrexx.create_gateway(settings, order, cust.public_base_url(request))
        except payrexx.PayrexxError as exc:
            print(f"[payrexx] Gateway fehlgeschlagen für Bestellung {order.id}: {exc}")
            order.status = OrderStatus.cancelled
            db.commit()
            return error_response("Die Online-Zahlung ist gerade nicht verfügbar. Bitte Bar oder Karte bei Übergabe wählen.")
        order.payrexx_gateway_id = str(gateway["id"])
        db.commit()
        response = RedirectResponse(url=gateway["link"], status_code=303)
    else:
        cart_lib.clear_cart(request)
        background.add_task(orderflow.send_confirmation, order.id, cust.public_base_url(request))
        response = RedirectResponse(url=f"/bestellung/{order.id}/bestaetigung", status_code=303)
    cust.set_identity_cookie(request, response, device_key, identity["e"])
    response.delete_cookie("kontakt")  # legacy cookie from the old checkout
    return response


def _settle_online_order(db: Session, order: Order) -> str:
    """Re-reads the payment from Payrexx and updates the order. Returns paid | pending | failed."""
    if order.status != OrderStatus.awaiting_payment:
        return "paid" if order.status != OrderStatus.cancelled else "failed"
    try:
        state = payrexx.gateway_state(get_settings(db), order)
    except payrexx.PayrexxError as exc:
        print(f"[payrexx] Statusabfrage Bestellung {order.id}: {exc}")
        return "pending"
    if state == "paid":
        order.status = OrderStatus.received
        db.commit()
    return state


@router.get("/bestellung/{token}/zahlung")
def payment_return(token: str, request: Request, background: BackgroundTasks, r: str = "ok", db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.tracking_token == token).first() if len(token) >= 16 else None
    if order is None or order.payment_method != "online":
        raise HTTPException(status_code=404)
    was_waiting = order.status == OrderStatus.awaiting_payment
    state = _settle_online_order(db, order)
    if state == "paid":
        if was_waiting:
            background.add_task(orderflow.send_confirmation, order.id, cust.public_base_url(request))
        cart_lib.clear_cart(request)
        request.session["placed_order_ids"] = request.session.get("placed_order_ids", []) + [order.id]
        return RedirectResponse(url=f"/bestellung/{order.id}/bestaetigung", status_code=303)
    return templates.TemplateResponse(
        "site/payment_status.html",
        {"request": request, "order": order, "state": state if r == "ok" else "failed", "r": r, **site_extra(request, db)},
    )


@router.post("/bestellung/{token}/zahlung/neu")
def payment_retry(token: str, request: Request, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.tracking_token == token).first() if len(token) >= 16 else None
    if order is None or order.status != OrderStatus.awaiting_payment:
        raise HTTPException(status_code=404)
    try:
        gateway = payrexx.create_gateway(get_settings(db), order, cust.public_base_url(request))
    except payrexx.PayrexxError:
        return RedirectResponse(url=f"/bestellung/{token}/zahlung?r=failed", status_code=303)
    order.payrexx_gateway_id = str(gateway["id"])
    db.commit()
    return RedirectResponse(url=gateway["link"], status_code=303)


def _find_reference(node):
    """Looks for a referenceId like 'order-123' anywhere in the webhook payload."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "referenceId" and isinstance(value, str) and re.fullmatch(r"order-\d+", value):
                return value
            found = _find_reference(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_reference(value)
            if found:
                return found
    return None


@router.post("/payrexx/webhook")
async def payrexx_webhook(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Payrexx calls this on payment events. The payload is NOT trusted (webhooks
    are unsigned): it only tells us which order to re-check against the API."""
    body = await request.body()
    try:
        payload = json.loads(body)
    except ValueError:
        payload = {k: v for k, v in (await request.form()).items()}
    reference = _find_reference(payload)
    if reference:
        order = db.get(Order, int(reference.split("-")[1]))
        if order is not None and order.payment_method == "online":
            was_waiting = order.status == OrderStatus.awaiting_payment
            if _settle_online_order(db, order) == "paid" and was_waiting:
                background.add_task(orderflow.send_confirmation, order.id, cust.public_base_url(request))
    return {"ok": True}


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
            "payment_labels": PAYMENT_LABELS,
            **site_extra(request, db),
        },
    )


def _remaining_minutes(order: Order, settings) -> Optional[int]:
    if order.status.value not in ("received", "preparing"):
        return None
    estimate = (
        settings.estimated_delivery_minutes
        if order.order_type == OrderType.delivery
        else settings.estimated_pickup_minutes
    )
    elapsed_minutes = (datetime.utcnow() - order.created_at).total_seconds() / 60
    return max(0, round(estimate - elapsed_minutes))


def _tracking_response(request: Request, db: Session, status_code: int = 200, **ctx):
    return templates.TemplateResponse(
        "site/tracking.html",
        {"request": request, "status_labels": ORDER_STATUS_LABELS, "type_labels": ORDER_TYPE_LABELS, **ctx, **site_extra(request, db)},
        status_code=status_code,
    )


@router.get("/verfolgen")
def order_history(request: Request, db: Session = Depends(get_db)):
    """Hub: the visitor's own orders (recognised by cookie), otherwise the
    e-mail form to get a link for a new device."""
    identity = cust.read_identity(request)
    orders = cust.orders_for_identity(db, identity)
    settings = get_settings(db)
    return _tracking_response(
        request, db,
        mode="history",
        orders=orders,
        remaining={o.id: _remaining_minutes(o, settings) for o in orders},
        refresh=cust.active_order(orders) is not None,
        link_state=request.query_params.get("link"),
    )


@router.get("/verfolgen/{token}/beleg.pdf")
def receipt_pdf(token: str, db: Session = Depends(get_db)):
    from ..receipt import build_receipt

    order = db.query(Order).filter(Order.tracking_token == token).first() if len(token) >= 16 else None
    if order is None or order.status == OrderStatus.awaiting_payment:
        raise HTTPException(status_code=404)
    pdf = build_receipt(order, get_settings(db))
    if pdf is None:
        raise HTTPException(status_code=503, detail="PDF-Beleg ist auf diesem Server noch nicht aktiv (fpdf2 fehlt im Python der App; deploy.sh neu ausführen)")
    return Response(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Beleg-Bestellung-{order.id}.pdf"', "X-Robots-Tag": "noindex"},
    )


@router.get("/verfolgen/{token}")
def track_order(token: str, request: Request, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.tracking_token == token).first() if len(token) >= 16 else None
    settings = get_settings(db)
    return _tracking_response(
        request, db,
        mode="single",
        orders=[order] if order else [],
        status_code=200 if order else 404,
        not_found=order is None,
        remaining={order.id: _remaining_minutes(order, settings)} if order else {},
        refresh=bool(order and order.status.value in cust.ACTIVE_STATUSES),
    )


@router.post("/verlauf/anfordern")
async def request_history_link(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    form = await request.form()
    email = cust.normalize_email(form.get("email") or "")
    # Honeypot field: real visitors never fill it
    if form.get("hp_trap") or not email or "@" not in email or len(email) > 254:
        return RedirectResponse(url="/verfolgen?link=invalid", status_code=303)
    settings = get_settings(db)
    if not mail_configured(settings):
        return RedirectResponse(url="/verfolgen?link=off", status_code=303)
    ip = cust.client_ip(request)
    status, token = cust.request_login_link(db, email, ip)
    if status == "sent":
        link = f"{cust.public_base_url(request)}/verlauf/anmelden?t={token}"
        shop = settings.name or "unser Restaurant"
        body = (
            f"Gewünschter Link für Ihren Bestellverlauf bei {shop}:\n\n"
            f"{link}\n\n"
            f"Der Link ist {cust.LINK_VALID_MINUTES} Minuten gültig und funktioniert nur einmal.\n\n"
            "Sicherheitshinweis:\n"
            "Falls Sie diese E-Mail nicht angefordert haben - das kann auch aus Versehen passiert sein - "
            "müssen Sie nichts unternehmen. Ohne Klick auf den Link passiert nichts.\n"
            "Wenn das aber mehrmals vorkommt, kontaktieren Sie bitte "
            f"{settings.email or settings.smtp_from_email}.\n"
        )
        from html import escape as html_escape
        from .. import mailhtml
        contact = settings.email or settings.smtp_from_email
        inner = (
            mailhtml.paragraph("Guten Tag,")
            + mailhtml.paragraph(
                f"Sie haben den Link zu Ihrem Bestellverlauf bei {shop} angefordert. Mit einem Klick auf den Knopf sehen Sie "
                "Ihre bisherigen Bestellungen auf diesem Gerät."
            )
            + mailhtml.button(link, "Bestellverlauf öffnen", settings.accent_color)
            + mailhtml.paragraph(f"Der Link ist {cust.LINK_VALID_MINUTES} Minuten gültig und funktioniert nur einmal.", muted=True)
        )
        legal = " · ".join(
            mailhtml.text_link(f"{cust.public_base_url(request)}/rechtliches/{p.slug}", p.title, "#7a746c")
            for p in get_all_content_pages(db) if p.body.strip()
        )
        footer = (
            "<strong>Sicherheitshinweis:</strong> Falls Sie diese E-Mail nicht angefordert haben - das kann auch aus Versehen "
            "passiert sein - müssen Sie nichts unternehmen. Ohne Klick auf den Link passiert nichts. Wenn das aber mehrmals "
            f"vorkommt, kontaktieren Sie bitte {html_escape(contact or '')}."
            + (f"<br><br>{legal}" if legal else "")
        )
        html = mailhtml.wrap(shop, settings.accent_color, "Ihr Bestellverlauf", inner, footer, "Ihr Link zum Bestellverlauf")
        background.add_task(send_mail, email, f"Ihr Bestellverlauf bei {shop}", body, html)
    # Same answer for every outcome, so nobody can probe which addresses are customers
    return RedirectResponse(url="/verfolgen?link=sent", status_code=303)


@router.get("/verlauf/anmelden")
def history_login_page(request: Request, t: str = "", db: Session = Depends(get_db)):
    """Shows a confirm button instead of consuming the token on GET, so mail
    scanners that pre-open links don't burn it."""
    return _tracking_response(request, db, mode="confirm", orders=[], token=t, token_ok=cust.token_is_valid(db, t) if t else False)


@router.post("/verlauf/anmelden")
async def history_login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    email = cust.redeem_token(db, form.get("t") or "")
    if email is None:
        return RedirectResponse(url="/verfolgen?link=expired", status_code=303)
    identity = cust.read_identity(request)
    response = RedirectResponse(url="/verfolgen", status_code=303)
    cust.set_identity_cookie(request, response, identity["k"] or cust.new_device_key(), email)
    return response


def parse_sections(body: str):
    """Splits an editable text into (intro_paragraphs, [(heading, paragraphs)]).
    A line starting with "## " opens a new collapsible section; everything
    before the first one is the intro. Text without any "## " stays plain."""
    intro, sections, current = [], [], None
    for block in body.replace("\r\n", "\n").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        while lines and lines[0].startswith("## "):
            current = (lines.pop(0)[3:].strip(), [])
            sections.append(current)
        rest = "\n".join(lines).strip()
        if rest:
            (current[1] if current else intro).append(rest)
    return intro, sections


@router.get("/rechtliches/{slug}")
def legal_page(slug: str, request: Request, db: Session = Depends(get_db)):
    if slug not in ALLOWED_LEGAL_SLUGS:
        raise HTTPException(status_code=404)
    page = get_content_page(db, slug)
    intro, sections = parse_sections(page.body)
    faq_json = None
    if slug == "faq" and sections:
        faq_json = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": " ".join(a)}}
                    for q, a in sections
                ],
            },
            ensure_ascii=False,
        ).replace("</", "<\\/")
    return templates.TemplateResponse(
        "site/legal.html",
        {"request": request, "page": page, "intro": intro, "sections": sections, "faq_json": faq_json, **site_extra(request, db)},
    )
