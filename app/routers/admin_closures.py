"""Einstellungen → Betrieb: Ferien und spezielle Schliesstage."""
from datetime import date, datetime
from urllib.parse import quote_plus

from fastapi import Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ClosedPeriod


def _valid(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _back(error: str = ""):
    url = "/admin/settings/betrieb"
    if error:
        url += "?closure_error=" + quote_plus(error)
    return RedirectResponse(url=url + "#closures", status_code=303)


def setup(router, templates, mutating, views):
    def closures_view(request: Request, db: Session):
        today = date.today().isoformat()
        rows = db.query(ClosedPeriod).filter(ClosedPeriod.end_date >= today).order_by(ClosedPeriod.start_date).all()
        return templates.TemplateResponse(
            "admin/settings_closures.html",
            {"request": request, "rows": rows, "today": today, "error": request.query_params.get("closure_error", "")},
        )

    views["closures"] = closures_view

    @router.post("/settings/closures/new", dependencies=mutating)
    def create_closure(
        start_date: str = Form(""), end_date: str = Form(""), reason: str = Form(""),
        db: Session = Depends(get_db),
    ):
        end_date = end_date or start_date
        if not (_valid(start_date) and _valid(end_date)):
            return _back("Bitte Von- und Bis-Datum eintragen.")
        if end_date < start_date:
            return _back("Das Bis-Datum liegt vor dem Von-Datum.")
        db.add(ClosedPeriod(start_date=start_date, end_date=end_date, reason=reason.strip()[:60]))
        db.commit()
        return _back()

    @router.post("/settings/closures/{closure_id}/delete", dependencies=mutating)
    def delete_closure(closure_id: int, db: Session = Depends(get_db)):
        row = db.get(ClosedPeriod, closure_id)
        if row is not None:
            db.delete(row)
            db.commit()
        return _back()
