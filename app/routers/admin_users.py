"""Einstellungen → Benutzer: weitere Logins (Admin / Küche), Passwort ändern."""
import re
from urllib.parse import quote_plus

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import auth
from ..database import get_db
from ..models import AdminUser

ROLE_LABELS = {"admin": "Admin (sieht alles)", "kitchen": "Küche (ohne Einstellungen)"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
MIN_PASSWORD = 8


def _back(error: str = "", ok: str = ""):
    url = "/admin/settings/benutzer"
    if error:
        url += "?error=" + quote_plus(error)
    elif ok:
        url += "?ok=" + quote_plus(ok)
    return RedirectResponse(url=url + "#users", status_code=303)


def setup(router, templates, mutating, views):
    def users_view(request: Request, db: Session):
        rows = db.query(AdminUser).order_by(AdminUser.username).all()
        me = getattr(request.state, "username", "")
        return templates.TemplateResponse(
            "admin/settings_users.html",
            {
                "request": request, "users": rows, "me": me, "roles": ROLE_LABELS,
                "env_login": auth.ADMIN_USERNAME if not any(u.username == auth.ADMIN_USERNAME for u in rows) else "",
                "error": request.query_params.get("error", ""), "ok": request.query_params.get("ok", ""),
            },
        )

    views["users"] = users_view

    @router.post("/settings/users/new", dependencies=mutating)
    def create_user(
        username: str = Form(""), password: str = Form(""), role: str = Form("kitchen"),
        db: Session = Depends(get_db),
    ):
        username = username.strip()
        if not USERNAME_RE.match(username):
            return _back("Benutzername: 3 bis 30 Zeichen (Buchstaben, Zahlen, . _ -).")
        if len(password) < MIN_PASSWORD:
            return _back(f"Passwort: mindestens {MIN_PASSWORD} Zeichen.")
        if role not in ROLE_LABELS:
            role = "kitchen"
        if db.query(AdminUser).filter(AdminUser.username == username).first() or username == auth.ADMIN_USERNAME:
            return _back("Diesen Benutzernamen gibt es schon.")
        db.add(AdminUser(username=username, password_hash=auth.hash_password(password), role=role))
        db.commit()
        return _back(ok=f"Benutzer „{username}“ angelegt.")

    @router.post("/settings/users/my-password", dependencies=mutating)
    def change_my_password(
        request: Request, old_password: str = Form(""), new_password: str = Form(""),
        db: Session = Depends(get_db),
    ):
        me = request.state.username
        if auth.authenticate(me, old_password) is None:
            return _back("Das alte Passwort stimmt nicht.")
        if len(new_password) < MIN_PASSWORD:
            return _back(f"Neues Passwort: mindestens {MIN_PASSWORD} Zeichen.")
        user = db.query(AdminUser).filter(AdminUser.username == me).first()
        if user is None:  # Standard-Login aus der Serverkonfiguration wird jetzt in die Datenbank übernommen
            user = AdminUser(username=me, role="admin")
            db.add(user)
        user.password_hash = auth.hash_password(new_password)
        db.commit()
        return _back(ok="Passwort geändert. Beim nächsten Laden fragt der Browser nach dem neuen Passwort.")

    @router.post("/settings/users/{user_id}/password", dependencies=mutating)
    def reset_password(user_id: int, new_password: str = Form(""), db: Session = Depends(get_db)):
        user = db.get(AdminUser, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        if len(new_password) < MIN_PASSWORD:
            return _back(f"Neues Passwort: mindestens {MIN_PASSWORD} Zeichen.")
        user.password_hash = auth.hash_password(new_password)
        db.commit()
        return _back(ok=f"Passwort von „{user.username}“ geändert.")

    @router.post("/settings/users/{user_id}/role", dependencies=mutating)
    def change_role(user_id: int, request: Request, role: str = Form("kitchen"), db: Session = Depends(get_db)):
        user = db.get(AdminUser, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        if user.username == request.state.username:
            return _back("Die eigene Rolle lässt sich nicht ändern.")
        user.role = role if role in ROLE_LABELS else "kitchen"
        db.commit()
        return _back(ok=f"Rolle von „{user.username}“ geändert.")

    @router.post("/settings/users/{user_id}/delete", dependencies=mutating)
    def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
        user = db.get(AdminUser, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        if user.username == request.state.username:
            return _back("Den eigenen Benutzer kann man nicht löschen.")
        db.delete(user)
        db.commit()
        return _back(ok=f"Benutzer „{user.username}“ gelöscht.")
