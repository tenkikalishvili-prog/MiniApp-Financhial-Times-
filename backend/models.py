"""Модели данных (таблицы БД).

Иерархия учёта: Статья (income/expense/debt) → Категория (group) →
Подкатегория (Category.name) → Сумма. Так же, как в Excel.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


class User(Base):
    """Пользователь Telegram. Все данные привязаны к нему (мультипользовательность)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    theme: Mapped[str] = mapped_column(String(16), default="dark")
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    categories: Mapped[list[Category]] = relationship(back_populates="user")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="user")


class Category(Base):
    """Категория/подкатегория пользователя. Свой набор у каждого (засев из шаблона)."""

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "article", "group", "name", name="uq_category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    article: Mapped[str] = mapped_column(String(16))  # income | expense | debt
    group: Mapped[str] = mapped_column(String(64))  # Категория (напр. «Траты»)
    name: Mapped[str] = mapped_column(String(128))  # Подкатегория (напр. «Рестораны»)
    emoji: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="categories")


class Budget(Base):
    """Плановый бюджет по подкатегории. period_month = NULL — шаблон по умолчанию."""

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    period_month: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # YYYY-MM


class Transaction(Base):
    """Факт: одна операция (доход/расход/долг)."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    article: Mapped[str] = mapped_column(String(16))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # источник ввода: manual_app | bot_buttons | bot_text | bot_voice | bot_photo
    source: Mapped[str] = mapped_column(String(24), default="bot_buttons")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="transactions")
    category: Mapped[Category] = relationship()
