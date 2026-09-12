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

ALLOWED_ORIGINS = {
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
} | {"http://127.0.0.1:8811", "http://localhost:8811"}


def _origin_key(url: str) -> str:
    parsed = urlparse(url)
    port_suffix = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port_suffix}"


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    valid_user = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    valid_pass = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=401,
            detail="Falscher Benutzername oder Passwort",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def verify_same_origin(request: Request) -> None:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is None:
        raise HTTPException(status_code=403, detail="Fehlende Origin-Angabe")
    if _origin_key(origin) not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Ungültige Anfrage-Herkunft")
