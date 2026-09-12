import os
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me-now")


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
        return
    origin_host = origin.split("://", 1)[-1].split("/", 1)[0]
    if origin_host != request.url.netloc:
        raise HTTPException(status_code=403, detail="Ungültige Anfrage-Herkunft")
