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
    Integer,
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

    # ── Онбординг (лёгкий мастер первого входа) ──────────────────────────
    # NULL → мастер ещё не пройден (показываем при первом входе в Mini App).
    onboarded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Плановый доход в месяц (с онбординга) — контекст, для будущих целей/сводок.
    monthly_income: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    # Общий месячный лимит на группу «Траты» (с онбординга). Питает дневной лимит,
    # пока пользователь не задал лимиты по подкатегориям вручную (тогда берётся их сумма).
    discretionary_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)

    # ── Настройки уведомлений бота (экран «Настройки» в Mini App) ────────
    # Час — по часовому поясу пользователя (User.timezone). Раньше был хардкод
    # 9:00 / 23:00 в планировщике; теперь каждый настраивает под себя.
    morning_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    morning_hour: Mapped[int] = mapped_column(Integer, default=9)
    evening_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    evening_hour: Mapped[int] = mapped_column(Integer, default=23)
    # Напоминания о платежах и долгах (S11): шлются раз в день в утренний час.
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

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


class Debt(Base):
    """Долг в реестре обязательств (направление C, S8).

    Отдельная сущность, НЕ операция со статьёй ``debt``: это карточка обязательства —
    кому/кто должен, сумма, срок, остаток. Возвраты и пересчёт остатка — S9 (пока
    ``paid`` = 0, остаток = ``amount``). Модель сразу заложена под оба направления.
    """

    __tablename__ = "debts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # owe  → я должен кому-то;  lent → мне должны.
    direction: Mapped[str] = mapped_column(String(8))
    counterparty: Mapped[str] = mapped_column(String(128))  # кому / кто
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # изначальная сумма долга
    # Погашено на данный момент (наполнится в S9). Остаток = amount − paid.
    paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # срок возврата
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)  # закрыт (возвращён)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship()


class DebtPayment(Base):
    """Один возврат по долгу частями (направление C, S9).

    История платежей: каждый частичный возврат — отдельная запись (сумма + дата).
    ``Debt.paid`` = сумма всех платежей долга (кэш пересчитывается при добавлении/
    удалении платежа), остаток = ``amount − paid``. Отдельная таблица заводится через
    ``create_all`` — ALTER-миграция не нужна (целиком новая таблица).
    """

    __tablename__ = "debt_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    debt_id: Mapped[int] = mapped_column(ForeignKey("debts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # сумма возврата
    on_date: Mapped[date] = mapped_column(Date)  # дата возврата
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    debt: Mapped[Debt] = relationship()


class Bill(Base):
    """Регулярный обязательный платёж (направление C, S10): аренда, кредит, подписка…

    Календарь ежемесячных обязательств. У платежа — число месяца-срок (``due_day``) и
    привязка к расходной подкатегории (``category_id``): отметка «оплачено» за месяц
    создаёт расходную операцию по этой категории (см. ``BillMark``).
    """

    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))  # название платежа
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # сумма к оплате
    due_day: Mapped[int] = mapped_column(Integer)  # число месяца-срок (1–31)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    category: Mapped[Category] = relationship()


class BillMark(Base):
    """Отметка оплаты платежа за конкретный месяц (S10).

    Наличие строки = платёж оплачен в этом месяце. ``transaction_id`` — созданная при
    отметке расходная операция (снятие отметки её удаляет, чтобы не задваивать учёт).
    """

    __tablename__ = "bill_marks"
    __table_args__ = (
        UniqueConstraint("bill_id", "period", name="uq_bill_mark"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))  # 'YYYY-MM'
    transaction_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
