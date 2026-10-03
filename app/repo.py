"""Lazy get-or-create accessors for the singleton settings row and the
fixed set of editable legal pages, shared between the admin and the
public-facing site routers."""

from datetime import datetime

from sqlalchemy.orm import Session

from .models import CONTENT_PAGE_DEFAULTS, ContentPage, OpeningHour, RestaurantSettings


def get_opening_hours_by_weekday(db: Session) -> dict:
    by_day = {weekday: [] for weekday in range(7)}
    for hour in db.query(OpeningHour).order_by(OpeningHour.weekday, OpeningHour.open_time).all():
        by_day[hour.weekday].append(hour)
    return by_day


def is_currently_open(hours_by_weekday: dict) -> bool:
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    for window in hours_by_weekday.get(now.weekday(), []):
        if window.open_time <= current_time <= window.close_time:
            return True
    return False


def get_settings(db: Session) -> RestaurantSettings:
    settings = db.get(RestaurantSettings, 1)
    if settings is None:
        settings = RestaurantSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_content_page(db: Session, slug: str) -> ContentPage:
    page = db.query(ContentPage).filter(ContentPage.slug == slug).first()
    if page is None:
        title, body = next(((t, b) for s, t, b in CONTENT_PAGE_DEFAULTS if s == slug), (slug, ""))
        page = ContentPage(slug=slug, title=title, body=body)
        db.add(page)
        db.commit()
        db.refresh(page)
    return page


def get_all_content_pages(db: Session):
    for slug, title, body in CONTENT_PAGE_DEFAULTS:
        if db.query(ContentPage).filter(ContentPage.slug == slug).first() is None:
            db.add(ContentPage(slug=slug, title=title, body=body))
    db.commit()
    return db.query(ContentPage).order_by(ContentPage.id).all()
