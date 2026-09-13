from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ORDER_STATUS_LABELS, Category, MenuItem, Order, OrderItem

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/order")
def order_form(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.sort_order, Category.id).all()
    return templates.TemplateResponse(
        "order_form.html", {"request": request, "categories": categories}
    )


@router.post("/order")
async def place_order(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    customer_name = (form.get("customer_name") or "").strip()
    phone = (form.get("phone") or "").strip()
    delivery_address = (form.get("delivery_address") or "").strip()
    note = (form.get("note") or "").strip()
    if not customer_name or not phone:
        raise HTTPException(status_code=400, detail="Name und Telefonnummer sind erforderlich")

    available_items = db.query(MenuItem).filter(MenuItem.is_available.is_(True)).all()
    order_items = []
    total = 0.0
    for item in available_items:
        try:
            quantity = int(form.get(f"item_{item.id}_qty") or 0)
        except ValueError:
            quantity = 0
        if quantity <= 0:
            continue

        unit_price = item.price
        option_names = []
        for group in item.option_groups:
            field_name = f"item_{item.id}_opt_{group.id}"
            if group.selection_type.value == "single":
                selected_value = form.get(field_name)
                selected_ids = [selected_value] if selected_value else []
            else:
                selected_ids = form.getlist(field_name)
            if group.required and not selected_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Bitte '{group.name}' für '{item.name}' auswählen",
                )
            for option in group.options:
                if str(option.id) in selected_ids:
                    unit_price += option.price_delta
                    option_names.append(option.name)

        order_items.append(
            OrderItem(
                item_name=item.name,
                options_summary=", ".join(option_names),
                unit_price=unit_price,
                quantity=quantity,
            )
        )
        total += unit_price * quantity

    if not order_items:
        raise HTTPException(status_code=400, detail="Die Bestellung enthält keine Artikel")

    order = Order(
        customer_name=customer_name,
        phone=phone,
        delivery_address=delivery_address,
        note=note,
        total=total,
        items=order_items,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return RedirectResponse(url=f"/order/{order.id}/confirmation", status_code=303)


@router.get("/order/{order_id}/confirmation")
def order_confirmation(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    return templates.TemplateResponse(
        "order_confirmation.html",
        {"request": request, "order": order, "status_label": ORDER_STATUS_LABELS[order.status]},
    )
