"""Recognises returning guests without passwords.

A signed "kunde" cookie carries a random device key (and, after the e-mail link
was used, the verified e-mail address). History = orders of this device key plus
orders placed with the verified e-mail. An e-mail merely typed at checkout never
grants access to anything - only a click on the link sent to that mailbox does."""

import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .auth import SESSION_SECRET_KEY
from .models import LoginLink, Order

COOKIE_NAME = "kunde"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
LINK_VALID_MINUTES = 30
MAX_MAILS_PER_EMAIL_PER_HOUR = 3
FLAG_REQUESTS_PER_EMAIL_PER_HOUR = 5
MAX_REQUESTS_PER_IP_PER_HOUR = 10
ACTIVE_STATUSES = ("received", "preparing", "ready")

_serializer = URLSafeSerializer(SESSION_SECRET_KEY, salt="kunde-cookie")


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def new_device_key() -> str:
    return secrets.token_urlsafe(16)


def new_tracking_token() -> str:
    return secrets.token_urlsafe(16)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def read_identity(request: Request) -> dict:
    """{'k': device_key or None, 'e': verified_email or None}"""
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        try:
            data = _serializer.loads(raw)
            if isinstance(data, dict):
                return {"k": data.get("k") or None, "e": data.get("e") or None}
        except BadSignature:
            pass
    return {"k": None, "e": None}


def set_identity_cookie(request: Request, response, key: str, verified_email: Optional[str] = None):
    response.set_cookie(
        COOKIE_NAME,
        _serializer.dumps({"k": key, "e": verified_email or ""}),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
    )


def orders_for_identity(db: Session, identity: dict, limit: int = 30) -> List[Order]:
    conditions = []
    if identity["k"]:
        conditions.append(Order.device_key == identity["k"])
    if identity["e"]:
        conditions.append(func.lower(Order.email) == identity["e"])
    if not conditions:
        return []
    return db.query(Order).filter(or_(*conditions)).order_by(Order.created_at.desc()).limit(limit).all()


def active_order(orders: List[Order]) -> Optional[Order]:
    for order in orders:
        if order.status.value in ACTIVE_STATUSES:
            return order
    return None


def client_ip(request: Request) -> str:
    # Behind the Plesk proxy the real client is the last entry the proxy appended.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()[:64]
    return request.client.host if request.client else ""


def public_base_url(request: Request) -> str:
    """Base URL for links in e-mails. Never taken from the Host header (header
    injection); comes from PUBLIC_BASE_URL or the first ALLOWED_ORIGINS entry."""
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if not configured:
        origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
        configured = origins[0] if origins else "http://127.0.0.1:8811"
    return configured.rstrip("/")


def request_login_link(db: Session, email: str, ip: str):
    """Applies the rules and logs the attempt. Returns (status, raw_token or None).
    A token is only issued (status 'sent') if the e-mail has orders and no limit hit."""
    now = datetime.utcnow()
    hour_ago = now - timedelta(hours=1)

    def log(status, token=None):
        entry = LoginLink(
            email=email,
            ip=ip,
            status=status,
            token_hash=hash_token(token) if token else "",
            expires_at=now + timedelta(minutes=LINK_VALID_MINUTES) if token else None,
        )
        db.add(entry)
        db.commit()

    per_ip = db.query(LoginLink).filter(LoginLink.ip == ip, LoginLink.created_at >= hour_ago).count()
    per_email = db.query(LoginLink).filter(LoginLink.email == email, LoginLink.created_at >= hour_ago).count()
    sent_for_email = (
        db.query(LoginLink)
        .filter(LoginLink.email == email, LoginLink.status == "sent", LoginLink.created_at >= hour_ago)
        .count()
    )
    if per_ip >= MAX_REQUESTS_PER_IP_PER_HOUR or per_email >= FLAG_REQUESTS_PER_EMAIL_PER_HOUR:
        log("blocked")
        return "blocked", None
    if sent_for_email >= MAX_MAILS_PER_EMAIL_PER_HOUR:
        log("rate_limited")
        return "rate_limited", None
    has_orders = db.query(Order).filter(func.lower(Order.email) == email).first() is not None
    if not has_orders:
        log("no_orders")
        return "no_orders", None
    token = secrets.token_urlsafe(32)
    log("sent", token)
    return "sent", token


def redeem_token(db: Session, token: str) -> Optional[str]:
    """Consumes a one-time token. Returns the verified e-mail or None."""
    entry = (
        db.query(LoginLink)
        .filter(LoginLink.token_hash == hash_token(token), LoginLink.status == "sent")
        .first()
    )
    if entry is None or entry.used_at is not None or entry.expires_at < datetime.utcnow():
        return None
    entry.used_at = datetime.utcnow()
    db.commit()
    return entry.email


def token_is_valid(db: Session, token: str) -> bool:
    entry = (
        db.query(LoginLink)
        .filter(LoginLink.token_hash == hash_token(token), LoginLink.status == "sent")
        .first()
    )
    return entry is not None and entry.used_at is None and entry.expires_at >= datetime.utcnow()
