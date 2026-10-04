"""Audit trail: every successful change in the admin is logged with who, when and what."""
import base64
import re
from typing import Optional

from .database import SessionLocal
from .models import AuditLog

# (regex on the path after /admin, readable action). First match wins.
ACTIONS = [
    (r"^/quick/", "Schalter umgelegt"),
    (r"^/categories$", "Kategorie angelegt"),
    (r"^/categories/\d+/promo$", "Hero-Aktion einer Kategorie umgeschaltet"),
    (r"^/categories/\d+/delete$", "Kategorie gelöscht"),
    (r"^/items/new$", "Artikel angelegt"),
    (r"^/items/\d+/edit$", "Artikel geändert"),
    (r"^/items/\d+/image/delete$", "Artikelbild entfernt"),
    (r"^/items/\d+/delete$", "Artikel gelöscht"),
    (r"^/items/\d+/toggle$", "Ausverkauft umgeschaltet"),
    (r"^/items/\d+/toggle-new$", "Neu-Markierung umgeschaltet"),
    (r"^/items/\d+/option-links$", "Optionsgruppe einem Artikel zugewiesen"),
    (r"^/option-links/\d+/update$", "Zuweisung einer Optionsgruppe geändert"),
    (r"^/option-links/\d+/delete$", "Optionsgruppe bei Artikel entfernt"),
    (r"^/option-groups$", "Optionsgruppe angelegt"),
    (r"^/option-groups/\d+/rename$", "Optionsgruppe geändert"),
    (r"^/option-groups/\d+/delete$", "Optionsgruppe gelöscht"),
    (r"^/option-groups/\d+/assign$", "Optionsgruppe Artikeln zugewiesen"),
    (r"^/option-groups/\d+/options$", "Option hinzugefügt"),
    (r"^/options/\d+/delete$", "Option gelöscht"),
    (r"^/orders/\d+/advance$", "Bestellstatus weitergeschaltet"),
    (r"^/orders/\d+/cancel$", "Bestellung storniert"),
    (r"^/orders/\d+/resend$", "Beleg erneut per E-Mail gesendet"),
    (r"^/settings/general$", "Stammdaten geändert"),
    (r"^/settings/logo/delete$", "Logo entfernt"),
    (r"^/settings/website$", "Webseiten-Einstellungen geändert"),
    (r"^/settings/hero-image/delete$", "Hero-Bild entfernt"),
    (r"^/settings/legal/", "Rechtlicher Text geändert"),
    (r"^/settings/times", "Bestellzeiten geändert"),
    (r"^/settings/ordering$", "Bestellannahme geändert"),
    (r"^/settings/freiwirt-token/regenerate$", "Freiwirt-Token neu erzeugt"),
    (r"^/settings/(delivery|zones)", "Liefergebiet geändert"),
    (r"^/settings/payment$", "Zahlungseinstellungen geändert"),
    (r"^/settings/email/test$", "Test-Mail gesendet"),
    (r"^/settings/email$", "E-Mail-Einstellungen geändert"),
    (r"^/settings/hours", "Öffnungszeiten geändert"),
]
COMPILED = [(re.compile(p), label) for p, label in ACTIONS]


def action_for(path: str) -> str:
    for pattern, label in COMPILED:
        if pattern.search(path):
            return label
    return "Änderung"


def actor_of(request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            return base64.b64decode(header[6:]).decode("utf-8", "replace").split(":", 1)[0][:60]
        except Exception:
            return ""
    return ""


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def note(request, detail: str) -> None:
    """Handlers call this to add what exactly changed (name, number, reason)."""
    request.state.audit_detail = detail[:300]


def write(request, action: str, detail: Optional[str] = None) -> None:
    db = SessionLocal()
    try:
        db.add(AuditLog(
            actor=actor_of(request),
            ip=client_ip(request),
            action=action,
            detail=detail if detail is not None else getattr(request.state, "audit_detail", ""),
        ))
        db.commit()
    finally:
        db.close()
