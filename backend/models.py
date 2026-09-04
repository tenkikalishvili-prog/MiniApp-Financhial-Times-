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
    """Факт: одна операция — единый реестр ВСЕХ движений ДС.

    Кроме обычных доход/расход (привязка к ``category_id``) здесь же живут движения
    по целям и долгам (направление D, единый реестр):
    - **пополнение цели** — ``goal_id`` задан, категории нет, ``flow='out'``;
    - **движение по долгу** — ``debt_id`` задан, категории нет, ``debt_role`` =
      ``principal`` (тело долга: занял/дал) либо ``payment`` (возврат), ``flow`` —
      направление ДС (``in`` приток / ``out`` отток).

    ``flow`` заполняется ТОЛЬКО у операций по целям/долгам (у доход/расход знак ясен
    из статьи). «Остаток» = доход − расход ± нетто этих операций; в аналитику трат и в
    дневной лимит цели/долги НЕ попадают (у них нет категории). Один источник правды:
    прогресс цели (``Goal.saved``) и долга (``Debt.paid``) считается из этих операций.
    """

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    article: Mapped[str] = mapped_column(String(16))  # income | expense | debt | goal
    # Категория есть у обычных доход/расход; у операций по целям/долгам — NULL.
    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id"), index=True, nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # источник ввода: manual_app | bot_buttons | bot_text | bot_voice | bot_photo | bill
    source: Mapped[str] = mapped_column(String(24), default="bot_buttons")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # ── Привязка к цели/долгу (единый реестр) ────────────────────────────
    goal_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("goals.id"), index=True, nullable=True
    )
    debt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("debts.id"), index=True, nullable=True
    )
    # Для операций по долгу: principal (тело — занял/дал) | payment (возврат/платёж).
    debt_role: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # Направление ДС для операций по целям/долгам: in (приток) | out (отток). NULL у доход/расход.
    flow: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)

    user: Mapped[User] = relationship(back_populates="transactions")
    category: Mapped[Optional[Category]] = relationship()
    goal: Mapped[Optional["Goal"]] = relationship()
    debt: Mapped[Optional["Debt"]] = relationship()


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
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # изначальная сумма долга (тело)
    # Погашено на данный момент = сумма операций-возвратов. Остаток = amount − paid.
    paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # срок возврата
    # Дата, когда деньги реально перешли (для операции-тела в реестре). Старый долг →
    # прошлая дата, и текущий месяц не раздувается. NULL до бэкфилла.
    started_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)  # закрыт (возвращён)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship()


class DebtPayment(Base):
    """LEGACY (S9): один возврат по долгу. Больше НЕ используется для новых записей.

    С переходом на единый реестр «Операции» возвраты долга — это операции
    (``Transaction`` с ``debt_id`` и ``debt_role='payment'``). Таблица сохраняется
    только для разового БЭКФИЛЛА старых данных в реестр (см. ``db._backfill_ledger``);
    после успешной миграции — пустая, не пишется.
    """

    __tablename__ = "debt_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    debt_id: Mapped[int] = mapped_column(ForeignKey("debts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # сумма возврата
    on_date: Mapped[date] = mapped_column(Date)  # дата возврата
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    debt: Mapped[Debt] = relationship()


class Goal(Base):
    """Финансовая цель / накопление (направление D, S13).

    Отдельная сущность-карточка: на что копим, сколько нужно (``target_amount``),
    к какому сроку (``deadline``). ``saved`` — кэш суммы всех пополнений (см.
    ``GoalContribution``), пересчитывается при добавлении/удалении пополнения;
    остаток = ``target_amount − saved``. Нужный ежемесячный темп фронт считает
    из остатка и срока. Пополнения хранятся внутри цели и НЕ создают расходную
    операцию (это откладывание, а не трата) — так же, как возвраты долга.
    """

    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))  # на что копим
    target_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # сколько нужно всего
    # Накоплено на данный момент = сумма пополнений. Остаток = target_amount − saved.
    saved: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # срок цели
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)  # цель достигнута
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship()


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
