import enum
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import relationship

from .database import Base


class SelectionType(str, enum.Enum):
    single = "single"
    multiple = "multiple"


class OrderStatus(str, enum.Enum):
    awaiting_payment = "awaiting_payment"  # online payment started, not yet confirmed - hidden from the kitchen
    received = "received"
    preparing = "preparing"
    ready = "ready"
    completed = "completed"
    cancelled = "cancelled"


class OrderType(str, enum.Enum):
    pickup = "pickup"
    delivery = "delivery"


DEFAULT_CANCEL_REASONS = [
    "Ein Artikel ist leider ausverkauft",
    "Wir sind aktuell überlastet und können nicht liefern",
    "Die Adresse liegt ausserhalb unseres Liefergebiets",
    "Wir konnten dich telefonisch nicht erreichen",
    "Doppelte Bestellung",
    "Auf deinen Wunsch storniert",
]

PAYMENT_LABELS = {
    "online": "Online bezahlen",
    "cash": "Bar",
    "card": "Karte / Twint",
}
PAYMENT_HINTS = {
    "online": "Karte, Twint …",
    "cash": "bei Übergabe",
    "card": "bei Übergabe",
}

ORDER_TYPE_LABELS = {
    OrderType.pickup: "Abholung",
    OrderType.delivery: "Lieferung",
}


ORDER_STATUS_LABELS = {
    OrderStatus.awaiting_payment: "Zahlung ausstehend",
    OrderStatus.received: "Neu",
    OrderStatus.preparing: "In Zubereitung",
    OrderStatus.ready: "Fertig",
    OrderStatus.completed: "Abgeschlossen",
    OrderStatus.cancelled: "Storniert",
}


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)
    # Items of a promo category are featured in the homepage hero (and are
    # still listed as a normal category in the menu).
    is_promo = Column(Boolean, default=False, nullable=False)

    items = relationship(
        "MenuItem", back_populates="category", cascade="all, delete-orphan"
    )


class MenuItem(Base):
    __tablename__ = "menu_items"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    price = Column(Float, nullable=False)
    is_available = Column(Boolean, default=True)
    is_new = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    image_filename = Column(String, default="")
    # Temporary sold-out set from the shop (Freiwirt): orderable again after this moment
    sold_out_until = Column(DateTime, nullable=True)

    @property
    def temp_sold_out(self) -> bool:
        return bool(self.sold_out_until and self.sold_out_until > datetime.now())

    category = relationship("Category", back_populates="items")
    option_links = relationship(
        "ItemOptionGroup",
        back_populates="menu_item",
        cascade="all, delete-orphan",
        order_by="ItemOptionGroup.sort_order, ItemOptionGroup.id",
    )


class OptionGroup(Base):
    """Library entry: defined once (name, single/multiple, options + prices) and
    then assigned to any number of menu items via ItemOptionGroup."""

    __tablename__ = "option_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    selection_type = Column(Enum(SelectionType), default=SelectionType.single)

    options = relationship(
        "Option",
        back_populates="option_group",
        cascade="all, delete-orphan",
        order_by="Option.id",
    )
    links = relationship(
        "ItemOptionGroup", back_populates="option_group", cascade="all, delete-orphan"
    )


class ItemOptionGroup(Base):
    """Assignment of a library group to one item. Whether the group is mandatory
    (and how many options may be picked) is decided per assignment, so the same
    group can be required on one item and optional on another."""

    __tablename__ = "item_option_groups"

    id = Column(Integer, primary_key=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    option_group_id = Column(Integer, ForeignKey("option_groups.id"), nullable=False)
    required = Column(Boolean, default=False, nullable=False)
    max_selections = Column(Integer, nullable=True)
    sort_order = Column(Integer, default=0)

    menu_item = relationship("MenuItem", back_populates="option_links")
    option_group = relationship("OptionGroup", back_populates="links")


class Option(Base):
    __tablename__ = "options"

    id = Column(Integer, primary_key=True)
    option_group_id = Column(Integer, ForeignKey("option_groups.id"), nullable=False)
    name = Column(String, nullable=False)
    price_delta = Column(Float, default=0.0)

    option_group = relationship("OptionGroup", back_populates="options")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    customer_name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    customer_zip = Column(String, default="")
    customer_city = Column(String, default="")
    payment_method = Column(String, default="cash")  # cash | card (at handover) | online (Payrexx)
    payrexx_gateway_id = Column(String, default="")
    email = Column(String, default="")
    # Random id of the visitor's browser (signed "kunde" cookie) and an unguessable
    # code for the tracking link; both replace the guessable sequential order number.
    device_key = Column(String, default="", index=True)
    tracking_token = Column(String, default="", index=True)
    order_type = Column(Enum(OrderType), default=OrderType.delivery, nullable=False)
    delivery_address = Column(String, default="")
    note = Column(String, default="")
    status = Column(Enum(OrderStatus), default=OrderStatus.received, nullable=False)
    total = Column(Float, nullable=False, default=0.0)
    service_fee = Column(Float, default=0.0)
    service_fee_text = Column(String, default="")  # e.g. "Servicegebühr (2 % + CHF 0.30)" as it was when ordered
    cancel_reason = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    items = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )

    @property
    def customer_status_label(self) -> str:
        """What the customer sees: a finished order is 'unterwegs' (delivery) or 'abholbereit' (pickup)."""
        if self.status == OrderStatus.ready:
            return "Unterwegs" if self.order_type == OrderType.delivery else "Abholbereit"
        if self.status == OrderStatus.received:
            return "Eingegangen"
        return ORDER_STATUS_LABELS[self.status]

    @property
    def goods_total(self) -> float:
        return sum(i.unit_price * i.quantity for i in self.items)

    @property
    def delivery_fee_paid(self) -> float:
        return max(0.0, round(self.total - self.goods_total - (self.service_fee or 0.0), 2))


class LoginLink(Base):
    """One request for an order-history link by e-mail. Doubles as the rate-limit
    counter and the log the admin reviews to spot misuse. Only the hash of the
    one-time token is stored."""

    __tablename__ = "login_links"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, index=True)
    ip = Column(String, default="", index=True)
    # sent | no_orders | rate_limited | blocked | mail_off | failed
    status = Column(String, nullable=False)
    token_hash = Column(String, default="", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    item_name = Column(String, nullable=False)
    options_summary = Column(String, default="")
    unit_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)

    order = relationship("Order", back_populates="items")


WEEKDAY_LABELS = {
    0: "Montag",
    1: "Dienstag",
    2: "Mittwoch",
    3: "Donnerstag",
    4: "Freitag",
    5: "Samstag",
    6: "Sonntag",
}


class RestaurantSettings(Base):
    __tablename__ = "restaurant_settings"

    id = Column(Integer, primary_key=True, default=1)

    name = Column(String, default="")
    address_street = Column(String, default="")
    address_zip = Column(String, default="")
    address_city = Column(String, default="")
    hours_note = Column(String, default="")
    footer_credit = Column(String, default="")
    vat_number = Column(String, default="")  # UID / MWST-Nr., printed on the receipt when set
    attach_receipt_pdf = Column(Boolean, default=False, nullable=False)
    order_limit_per_hour = Column(Integer, default=5)  # abuse protection: max orders per customer and hour
    cancel_reasons = Column(Text, default="")  # one preset per line; empty = built-in defaults  # free text under the opening hours (e.g. special days)
    phone = Column(String, default="")
    email = Column(String, default="")

    accepting_orders = Column(Boolean, default=True)
    pickup_enabled = Column(Boolean, default=True)
    delivery_enabled = Column(Boolean, default=True)
    # Pre-orders: how many minutes before opening the shop already takes orders
    preorder_minutes = Column(Integer, default=60)
    minimum_order_value = Column(Float, default=0.0)
    delivery_fee = Column(Float, default=0.0)
    # Optional surcharge for every order: a percentage of the goods total or a fixed amount
    service_fee_enabled = Column(Boolean, default=False, nullable=False)
    service_fee_mode = Column(String, default="percent")  # "percent" | "fixed"
    service_fee_percent = Column(Float, default=2.0)
    service_fee_fixed = Column(Float, default=0.0)
    service_fee_label = Column(String, default="Servicegebühr")

    # Postcodes we deliver to (comma/space separated). Empty = no restriction.
    delivery_zips = Column(String, default="")
    delivery_zone_center = Column(String, default="")
    delivery_zone_radius_km = Column(Float, default=0.0)

    logo_filename = Column(String, default="")
    accent_color = Column(String, default="#c8102e")

    hero_headline = Column(String, default="")
    hero_subheadline = Column(String, default="")
    hero_image_filename = Column(String, default="")
    promo_banner_enabled = Column(Boolean, default=False)
    promo_banner_text = Column(String, default="")
    estimated_pickup_minutes = Column(Integer, default=15)
    estimated_delivery_minutes = Column(Integer, default=30)
    badge_1 = Column(String, default="")
    badge_2 = Column(String, default="")
    badge_3 = Column(String, default="")
    rating_text = Column(String, default="")

    # Customers only see "Online bezahlen" when this is on AND the Payrexx credentials are set
    online_payment_enabled = Column(Boolean, default=False, nullable=False)
    payrexx_instance = Column(String, default="")
    payrexx_api_key = Column(String, default="")

    freiwirt_api_token = Column(String, default="")

    smtp_host = Column(String, default="")
    smtp_port = Column(Integer, default=587)
    smtp_username = Column(String, default="")
    smtp_password = Column(String, default="")
    smtp_from_email = Column(String, default="")
    smtp_from_name = Column(String, default="")
    send_order_confirmation = Column(Boolean, default=True)


class ContentPage(Base):
    """Editable public-facing text (legal pages) with a fixed set of slugs
    seeded on first access, similar to RestaurantSettings' singleton row."""

    __tablename__ = "content_pages"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False)
    title = Column(String, default="")
    body = Column(Text, default="")


FAQ_DEFAULT_BODY = """## Wie bestelle ich?
Wähle auf der Startseite deine Gerichte aus, lege sie in den Warenkorb und gehe zur Kasse. Dort gibst du Name, Telefonnummer, E-Mail und bei Lieferung deine Adresse an.

## Wie lange dauert es bis zur Abholung oder Lieferung?
Die Zeiten auf der Startseite sind Durchschnittswerte und unverbindlich. Bei grossem Andrang kann es länger dauern.

## Wo sehe ich, wie weit meine Bestellung ist?
Unter «Meine Bestellungen» im Menü oben. Auf demselben Handy erscheinen deine letzten Bestellungen automatisch. Auf einem anderen Gerät kannst du dir mit deiner E-Mail-Adresse einen Link schicken lassen.

## Gibt es einen Mindestbestellwert?
Falls ja, steht er im Warenkorb. Liegst du darunter, siehst du dort einen Hinweis.

## Wie bezahle ich?
Die möglichen Zahlungsarten siehst du an der Kasse.

## Ich habe eine Allergie oder einen Sonderwunsch.
Schreibe es bei der Bestellung in das Feld «Anmerkung» oder rufe uns kurz an.

## Ich möchte meine Bestellung ändern oder stornieren.
Bitte ruf uns so schnell wie möglich an. Sobald die Zubereitung begonnen hat, ist eine Änderung nicht mehr in jedem Fall möglich.
"""

CONTENT_PAGE_DEFAULTS = [
    ("faq", "Häufige Fragen", FAQ_DEFAULT_BODY),
    ("datenschutz", "Datenschutz", ""),
    ("agb", "AGB", ""),
    ("widerruf", "Widerrufsrecht", ""),
    ("impressum", "Impressum", ""),
]


rule_categories = Table(
    "rule_categories",
    Base.metadata,
    Column("rule_id", Integer, ForeignKey("availability_rules.id"), primary_key=True),
    Column("category_id", Integer, ForeignKey("categories.id"), primary_key=True),
)
rule_items = Table(
    "rule_items",
    Base.metadata,
    Column("rule_id", Integer, ForeignKey("availability_rules.id"), primary_key=True),
    Column("item_id", Integer, ForeignKey("menu_items.id"), primary_key=True),
)


class AvailabilityRule(Base):
    """"Not orderable" window, defined once and assigned to any number of
    categories / single items (e.g. 14:00-17:00 for the pizza category because
    the oven is off). Outside the window the item is orderable as usual."""

    __tablename__ = "availability_rules"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, default="Nicht bestellbar")
    weekdays = Column(String, default="")  # "0,1,2" (Mon=0); empty = every day
    start_time = Column(String, nullable=False, default="14:00")
    end_time = Column(String, nullable=False, default="17:00")

    categories = relationship("Category", secondary=rule_categories)
    items = relationship("MenuItem", secondary=rule_items)


class OpeningHour(Base):
    """One time window on one weekday. A weekday with no rows is closed;
    multiple rows per weekday (e.g. lunch + dinner) are allowed."""

    __tablename__ = "opening_hours"

    id = Column(Integer, primary_key=True)
    weekday = Column(Integer, nullable=False)
    open_time = Column(String, nullable=False, default="11:00")
    close_time = Column(String, nullable=False, default="22:00")
