"""Optional long-lived cookie that remembers a guest's contact details so
they don't have to retype name/phone/address on their next visit. Holds no
password or payment data, so a signed (not encrypted) cookie is enough."""

from itsdangerous import BadSignature, URLSafeSerializer

from .auth import SESSION_SECRET_KEY

COOKIE_NAME = "kontakt"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # 180 Tage

_serializer = URLSafeSerializer(SESSION_SECRET_KEY, salt="kontakt-cookie")


def encode_contact(data: dict) -> str:
    return _serializer.dumps(data)


def decode_contact(cookie_value: str) -> dict:
    try:
        data = _serializer.loads(cookie_value)
    except BadSignature:
        return {}
    return data if isinstance(data, dict) else {}
