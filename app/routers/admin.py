from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_admin, verify_same_origin
from ..database import get_db
from ..models import Category, MenuItem, Option, OptionGroup, SelectionType

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")
mutating = [Depends(verify_same_origin)]


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
