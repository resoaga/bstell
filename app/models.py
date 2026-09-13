import enum
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .database import Base


class SelectionType(str, enum.Enum):
    single = "single"
    multiple = "multiple"


class OrderStatus(str, enum.Enum):
    received = "received"
    preparing = "preparing"
    ready = "ready"
    completed = "completed"
    cancelled = "cancelled"


ORDER_STATUS_LABELS = {
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
    sort_order = Column(Integer, default=0)

    category = relationship("Category", back_populates="items")
    option_groups = relationship(
        "OptionGroup",
        back_populates="menu_item",
        cascade="all, delete-orphan",
        order_by="OptionGroup.id",
    )


class OptionGroup(Base):
    __tablename__ = "option_groups"

    id = Column(Integer, primary_key=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    name = Column(String, nullable=False)
    selection_type = Column(Enum(SelectionType), default=SelectionType.single)
    required = Column(Boolean, default=False)

    menu_item = relationship("MenuItem", back_populates="option_groups")
    options = relationship(
        "Option",
        back_populates="option_group",
        cascade="all, delete-orphan",
        order_by="Option.id",
    )


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
    delivery_address = Column(String, default="")
    note = Column(String, default="")
    status = Column(Enum(OrderStatus), default=OrderStatus.received, nullable=False)
    total = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    items = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )


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
    phone = Column(String, default="")
    email = Column(String, default="")

    accepting_orders = Column(Boolean, default=True)
    pickup_enabled = Column(Boolean, default=True)
    delivery_enabled = Column(Boolean, default=True)
    minimum_order_value = Column(Float, default=0.0)
    delivery_fee = Column(Float, default=0.0)

    delivery_zone_center = Column(String, default="")
    delivery_zone_radius_km = Column(Float, default=0.0)


class OpeningHour(Base):
    __tablename__ = "opening_hours"

    id = Column(Integer, primary_key=True)
    weekday = Column(Integer, nullable=False, unique=True)
    closed = Column(Boolean, default=False)
    open_time = Column(String, default="11:00")
    close_time = Column(String, default="22:00")
