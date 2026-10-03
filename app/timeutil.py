"""Orders are stored in UTC; everything shown to people is Swiss local time."""

from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("Europe/Zurich")
except Exception:  # tz database missing on the server: fall back to the server's own zone
    LOCAL_TZ = None


def to_local(dt: datetime) -> datetime:
    aware = dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(LOCAL_TZ) if LOCAL_TZ else aware.astimezone()


def fmt_local(dt: datetime, pattern: str = "%d.%m.%Y %H:%M") -> str:
    return to_local(dt).strftime(pattern) if dt else ""


def minutes_ago(dt: datetime) -> int:
    return max(0, int((datetime.utcnow() - dt).total_seconds() // 60))


def register(templates) -> None:
    templates.env.filters["localtime"] = fmt_local
    templates.env.filters["minutes_ago"] = minutes_ago
