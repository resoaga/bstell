import enum

from sqlalchemy import Boolean, Column, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .database import Base


class SelectionType(str, enum.Enum):
    single = "single"
    multiple = "multiple"


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
