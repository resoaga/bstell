"""Minimal SMTP sender using the credentials from Einstellungen -> E-Mail."""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from .database import SessionLocal
from .repo import get_settings


def mail_configured(settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from_email)


def send_mail_result(to: str, subject: str, body: str):
    """Sends a plain-text mail. Opens its own DB session so it can run as a
    background task. Returns (ok, error_text) and never raises."""
    db = SessionLocal()
    try:
        s = get_settings(db)
        if not mail_configured(s):
            return False, "SMTP-Host oder Absender-E-Mail fehlt"
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr((s.smtp_from_name or "", s.smtp_from_email))
        msg["To"] = to
        if s.email and s.email.strip().lower() != (s.smtp_from_email or "").strip().lower():
            msg["Reply-To"] = s.email.strip()  # customers' answers go to the contact address
        msg.set_content(body)
        port = s.smtp_port or 587
        context = ssl.create_default_context()
        if port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, port, context=context, timeout=15)
        else:
            server = smtplib.SMTP(s.smtp_host, port, timeout=15)
            server.starttls(context=context)
        with server:
            if s.smtp_username:
                server.login(s.smtp_username, s.smtp_password)
            server.send_message(msg)
        return True, ""
    except Exception as exc:  # network/auth errors must not break the request
        print(f"[mailer] Versand an {to} fehlgeschlagen: {exc}")
        return False, f"{type(exc).__name__}: {exc}"[:300]
    finally:
        db.close()


def send_mail(to: str, subject: str, body: str) -> bool:
    return send_mail_result(to, subject, body)[0]
