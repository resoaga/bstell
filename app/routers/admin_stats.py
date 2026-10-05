"""Einstellungen → Statistik und Zahlen (mit PDF-/CSV-Export für die Buchhaltung)."""
import csv
import io
from urllib.parse import urlencode

from fastapi import Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import report_pdf, sounds, stats
from ..database import get_db
from ..models import PAYMENT_LABELS
from ..repo import get_settings

def _csv_safe(value) -> str:
    """Excel treats cells starting with = + - @ as formulas: customer input must not run as one."""
    text = "" if value is None else str(value)
    return "'" + text if text and text[0] in ("=", "+", "-", "@", "\t", "\r") else text


STAT_CHIPS = ["today", "7", "30", "month", "lastmonth", "year", "all"]
FIGURE_CHIPS = ["month", "lastmonth", "quarter", "lastquarter", "year", "lastyear"]


def _vat_rate(db: Session) -> float:
    try:
        return max(0.0, min(30.0, float(sounds.get_prefs(db).get("vat_rate") or 0)))
    except ValueError:
        return 0.0


def _period(request: Request, default: str):
    q = request.query_params
    key = q.get("p") or default
    first, last, label = stats.resolve_period(key, q.get("from", ""), q.get("to", ""))
    custom = bool(q.get("from") and q.get("to"))
    return key, first, last, label, custom


def setup(router, templates, mutating, views):
    def statistics_view(request: Request, db: Session):
        key, first, last, label, custom = _period(request, "30")
        data = stats.detailed(db, first, last)
        hours = data["hours"]
        used = [h for h, c in enumerate(hours) if c]
        lo, hi = (min(used), max(used)) if used else (10, 22)
        return templates.TemplateResponse(
            "admin/settings_statistics.html",
            {
                "request": request, "d": data, "label": label, "key": "" if custom else key, "first": first, "last": last,
                "chips": [(k, stats.PERIOD_LABELS[k]) for k in STAT_CHIPS],
                "hour_rows": [(h, hours[h]) for h in range(lo, hi + 1)],
                "max_hour": max(hours) or 1,
                "max_weekday": max((w["count"] for w in data["weekdays"]), default=0) or 1,
                "max_day": max((x["count"] for x in data["days"]), default=0) or 1,
                "max_zone": max((z["revenue"] for z in data["zones"]), default=0) or 1,
                "max_item": max((i["qty"] for i in data["top_items"]), default=0) or 1,
            },
        )

    def figures_view(request: Request, db: Session):
        key, first, last, label, custom = _period(request, "lastmonth")
        orders = stats.load_orders(db, first, last)
        summary = stats.summarize(orders)
        rate = _vat_rate(db)
        vat = stats.vat_part(summary["revenue"], rate)
        return templates.TemplateResponse(
            "admin/settings_figures.html",
            {
                "request": request, "label": label, "key": "" if custom else key, "first": first, "last": last,
                "chips": [(k, stats.PERIOD_LABELS[k]) for k in FIGURE_CHIPS],
                "summary": summary, "payments": stats.by_payment(orders), "days": stats.by_day(orders),
                "vat_rate": rate, "vat": vat, "net": round(summary["revenue"] - vat, 2),
                "query": urlencode({"from": first.isoformat(), "to": last.isoformat()}),
            },
        )

    views["statistics"] = statistics_view
    views["figures"] = figures_view

    @router.post("/settings/figures/vat", dependencies=mutating)
    def save_vat(request: Request, vat_rate: str = Form(""), db: Session = Depends(get_db)):
        try:
            value = max(0.0, min(30.0, float(vat_rate.replace(",", ".")))) if vat_rate.strip() else 0.0
        except ValueError:
            value = 0.0
        sounds.set_pref(db, "vat_rate", f"{value:g}")
        db.commit()
        return RedirectResponse(url="/admin/settings/zahlen#figures", status_code=303)

    @router.get("/settings/figures/export.pdf")
    def pdf_download(
        from_: str = Query("", alias="from"), to: str = Query(""), db: Session = Depends(get_db)
    ):
        first, last, label = stats.resolve_period("30", from_, to)
        orders = stats.load_orders(db, first, last)
        data = report_pdf.build_figures_pdf(
            get_settings(db), label, stats.summarize(orders), stats.by_payment(orders), stats.by_day(orders), _vat_rate(db)
        )
        if data is None:
            return Response("PDF ist auf dem Server nicht verfügbar (fpdf2 fehlt).", status_code=503)
        name = f"umsatz_{first.isoformat()}_{last.isoformat()}.pdf"
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @router.get("/settings/figures/export.csv")
    def csv_download(
        from_: str = Query("", alias="from"), to: str = Query(""), db: Session = Depends(get_db)
    ):
        first, last, _label = stats.resolve_period("30", from_, to)
        orders = [o for o in stats.load_orders(db, first, last)]
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(["Nr", "Datum", "Zeit", "Art", "Zahlung", "Status", "PLZ", "Warenwert", "Lieferung", "Service", "Total"])
        for o in orders:
            local = stats.timeutil.to_local(o.created_at)
            w.writerow([_csv_safe(x) for x in [
                o.id, local.strftime("%d.%m.%Y"), local.strftime("%H:%M"),
                "Lieferung" if o.order_type.value == "delivery" else "Abholung",
                PAYMENT_LABELS.get(o.payment_method, o.payment_method), o.status.value, o.customer_zip or "",
                f"{o.goods_total:.2f}", f"{o.delivery_fee_paid:.2f}", f"{(o.service_fee or 0):.2f}", f"{o.total:.2f}",
            ]])
        name = f"bestellungen_{first.isoformat()}_{last.isoformat()}.csv"
        return Response(
            "\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
