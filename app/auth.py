import base64
import hashlib
import os
import secrets
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

try:
    ADMIN_USERNAME = os.environ["ADMIN_USERNAME"]
    ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
except KeyError as exc:
    raise RuntimeError(
        "ADMIN_USERNAME und ADMIN_PASSWORD müssen als Umgebungsvariablen gesetzt sein"
    ) from exc

try:
    SESSION_SECRET_KEY = os.environ["SESSION_SECRET_KEY"]
except KeyError as exc:
    raise RuntimeError(
        "SESSION_SECRET_KEY muss als Umgebungsvariable gesetzt sein (signiert Warenkorb- "
        "und Kontakt-Cookies der öffentlichen Webseite)"
    ) from exc

ALLOWED_ORIGINS = {
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
} | {"http://127.0.0.1:8811", "http://localhost:8811"}


def _origin_key(url: str) -> str:
    parsed = urlparse(url)
    port_suffix = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port_suffix}"


KITCHEN_PATHS = (
    "/admin/orders", "/admin/menu", "/admin/items", "/admin/categories",
    "/admin/option-groups", "/admin/options", "/admin/option-links", "/admin/quick",
)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2**14, r=8, p=1)
        return secrets.compare_digest(test, base64.b64decode(digest))
    except Exception:
        return False


def authenticate(username: str, password: str):
    """Returns the role ("admin" / "kitchen") for valid credentials, otherwise None."""
    from .database import SessionLocal
    from .models import AdminUser

    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.username == username).first()
    finally:
        db.close()
    if user is not None:
        return user.role if check_password(password, user.password_hash) else None
    if secrets.compare_digest(username, ADMIN_USERNAME) and secrets.compare_digest(password, ADMIN_PASSWORD):
        return "admin"
    return None


def require_admin(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> str:
    role = authenticate(credentials.username, credentials.password)
    if role is None:
        raise HTTPException(
            status_code=401,
            detail="Falscher Benutzername oder Passwort",
            headers={"WWW-Authenticate": "Basic"},
        )
    request.state.role = role
    request.state.username = credentials.username
    path = request.url.path.rstrip("/")
    if role != "admin" and path != "/admin" and not path.startswith(KITCHEN_PATHS):
        raise HTTPException(status_code=403, detail="Dafür fehlt die Berechtigung")
    return credentials.username


def verify_same_origin(request: Request) -> None:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is None:
        raise HTTPException(status_code=403, detail="Fehlende Origin-Angabe")
    if _origin_key(origin) not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Ungültige Anfrage-Herkunft")
