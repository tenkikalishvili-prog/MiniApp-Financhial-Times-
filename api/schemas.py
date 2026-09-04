"""Схемы запросов и ответов HTTP-API.

Суммы отдаём числом (рубли) — фронту удобно считать/форматировать.
Ключи в camelCase, чтобы совпадали с типами во фронте (webapp/src/types.ts).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# ── Пользователь ─────────────────────────────────────────────────────────
class MeOut(CamelModel):
    id: int
    telegram_id: int = Field(serialization_alias="telegramId")
    name: str
    currency: str
    theme: str
    needs_onboarding: bool = Field(serialization_alias="needsOnboarding")
    planned_income: Optional[float] = Field(default=None, serialization_alias="plannedIncome")
    planned_spending: Optional[float] = Field(default=None, serialization_alias="plannedSpending")


# ── Онбординг (лёгкий мастер: доход + общий лимит трат) ───────────────────
class OnboardingIn(CamelModel):
    monthly_income: Optional[float] = Field(default=None, validation_alias="monthlyIncome")
    monthly_spending: Optional[float] = Field(default=None, validation_alias="monthlySpending")


# ── Настройки уведомлений бота ───────────────────────────────────────────
class SettingsOut(CamelModel):
    timezone: str
    morning_enabled: bool = Field(serialization_alias="morningEnabled")
    morning_hour: int = Field(serialization_alias="morningHour")
    evening_enabled: bool = Field(serialization_alias="eveningEnabled")
    evening_hour: int = Field(serialization_alias="eveningHour")
    reminders_enabled: bool = Field(serialization_alias="remindersEnabled")


class SettingsUpdate(CamelModel):
    timezone: Optional[str] = Field(default=None)
    morning_enabled: Optional[bool] = Field(default=None, validation_alias="morningEnabled")
    morning_hour: Optional[int] = Field(default=None, ge=0, le=23, validation_alias="morningHour")
    evening_enabled: Optional[bool] = Field(default=None, validation_alias="eveningEnabled")
    evening_hour: Optional[int] = Field(default=None, ge=0, le=23, validation_alias="eveningHour")
    reminders_enabled: Optional[bool] = Field(default=None, validation_alias="remindersEnabled")


# ── Обзор месяца ─────────────────────────────────────────────────────────
class TopSpendOut(CamelModel):
    category_id: int = Field(serialization_alias="subcategoryId")
    name: str
    emoji: Optional[str]
    spent: float
    limit: float


class OverviewOut(CamelModel):
    month: str  # YYYY-MM
    income: float
    expense: float
    remaining: float
    daily_limit: float = Field(serialization_alias="dailyLimit")
    days_left: int = Field(serialization_alias="daysLeft")
    has_budget: bool = Field(serialization_alias="hasBudget")
    top_spend: list[TopSpendOut] = Field(serialization_alias="topSpend")


# ── Аналитика (donut) ────────────────────────────────────────────────────
class SliceOut(CamelModel):
    name: str
    value: float


class AnalyticsOut(CamelModel):
    month: str
    total: float
    slices: list[SliceOut]


# ── Бюджет ───────────────────────────────────────────────────────────────
class BudgetLineOut(CamelModel):
    category_id: int = Field(serialization_alias="subcategoryId")
    group: str
    name: str
    emoji: Optional[str]
    spent: float
    limit: float


# ── Бюджет: полный обзор всех категорий каруселью ─────────────────────────
class BudgetSubOut(CamelModel):
    subcategory_id: int = Field(serialization_alias="subcategoryId")
    name: str
    emoji: Optional[str]
    spent: float
    limit: float


class BudgetGroupViewOut(CamelModel):
    group: str
    emoji: Optional[str]
    spent: float
    limit: float
    subcategories: list[BudgetSubOut]


# ── Переименование подкатегории ───────────────────────────────────────────
class CategoryRename(CamelModel):
    name: str


# ── Переименование категории (группы) ─────────────────────────────────────
class GroupRename(CamelModel):
    old_name: str = Field(validation_alias="oldName")
    new_name: str = Field(validation_alias="newName")
    article: str = "expense"


class GroupRenameOut(CamelModel):
    group: str
    renamed: int


# ── Создание подкатегории / категории ────────────────────────────────────
class SubcategoryCreate(CamelModel):
    article: str = "expense"
    group: str
    name: str
    emoji: Optional[str] = None


class CreatedSubcategoryOut(CamelModel):
    id: int
    name: str
    emoji: Optional[str]
    group: str
    article: str


# ── Удаление подкатегории / категории ────────────────────────────────────
class DeleteResultOut(CamelModel):
    # 'deleted' — удалено физически; 'archived' — скрыто (по подкатегории есть операции)
    action: str
    id: int


class GroupDeleteResultOut(CamelModel):
    deleted: int
    archived: int


# ── Категории (для экрана «Добавить») ────────────────────────────────────
class SubcategoryOut(CamelModel):
    id: int
    name: str
    emoji: Optional[str]


class CategoryGroupOut(CamelModel):
    group: str
    emoji: Optional[str]
    subcategories: list[SubcategoryOut]


# ── Операции ─────────────────────────────────────────────────────────────
class TransactionOut(CamelModel):
    id: int
    article: str
    # kind — тип движения для рендера: expense | income | goal | debt.
    kind: str
    # flow — знак движения ДС для операций по целям/долгам: in | out. NULL у доход/расход.
    flow: Optional[str] = None
    category_id: Optional[int] = Field(default=None, serialization_alias="categoryId")
    category_name: str = Field(serialization_alias="categoryName")
    subcategory_name: str = Field(serialization_alias="subcategoryName")
    emoji: Optional[str]
    amount: float
    date: date
    comment: Optional[str]
    # Привязка к цели/долгу — чтобы из «Истории» открыть карточку.
    goal_id: Optional[int] = Field(default=None, serialization_alias="goalId")
    debt_id: Optional[int] = Field(default=None, serialization_alias="debtId")
    debt_role: Optional[str] = Field(default=None, serialization_alias="debtRole")


class TransactionCreate(CamelModel):
    # ``on_date`` (alias ``date``) намеренно НЕ называется ``date``: одноимённое поле
    # в связке с ``from __future__ import annotations`` затеняет тип ``date`` собственным
    # значением по умолчанию, и pydantic резолвит тип в NoneType (см. TransactionUpdate).
    category_id: int = Field(validation_alias="categoryId")
    amount: float
    on_date: Optional[date] = Field(default=None, validation_alias="date")
    comment: Optional[str] = None


class TransactionUpdate(CamelModel):
    """Частичное изменение операции. Любое поле опционально — меняем присланные.

    ``on_date`` (alias ``date``) намеренно НЕ называется ``date``: одноимённое поле
    в связке с ``from __future__ import annotations`` затеняет тип ``date`` собственным
    значением по умолчанию, и pydantic резолвит тип в NoneType.
    """

    amount: Optional[float] = Field(default=None, gt=0)
    category_id: Optional[int] = Field(default=None, validation_alias="categoryId")
    on_date: Optional[date] = Field(default=None, validation_alias="date")
    comment: Optional[str] = None


# ── Изменение бюджета подкатегории ───────────────────────────────────────
class BudgetSet(CamelModel):
    amount: float


# ── Умный ввод (S5 в приложении): «кофе 350» → предзаполнение ─────────────
class SmartParseIn(CamelModel):
    text: str


class SmartParseOut(CamelModel):
    amount: Optional[float]          # None — сумму распознать не удалось
    description: str                 # очищенное описание (без суммы/валюты)
    article: str                     # предполагаемая статья: expense | income | debt
    matched: bool                    # угадана ли подкатегория
    category_id: Optional[int] = Field(default=None, serialization_alias="categoryId")
    group: Optional[str] = None      # категория (группа) подобранной подкатегории
    subcategory_name: Optional[str] = Field(default=None, serialization_alias="subcategoryName")
    emoji: Optional[str] = None


# ── Долги (направление C, S8) ────────────────────────────────────────────
class DebtOut(CamelModel):
    id: int
    direction: str                   # owe — я должен; lent — мне должны
    counterparty: str                # кому / кто должен
    amount: float                    # изначальная сумма
    paid: float                      # погашено (S9; пока 0)
    remaining: float                 # остаток = amount − paid
    due_date: Optional[date] = Field(default=None, serialization_alias="dueDate")
    # Дата движения тела (когда деньги перешли) — операция-тело в реестре.
    started_on: Optional[date] = Field(default=None, serialization_alias="startedOn")
    note: Optional[str] = None
    is_closed: bool = Field(serialization_alias="isClosed")


class DebtCreate(CamelModel):
    direction: str
    counterparty: str
    amount: float
    # ``due_date`` (alias ``dueDate``) намеренно НЕ называется ``date`` — см. TransactionCreate.
    due_date: Optional[date] = Field(default=None, validation_alias="dueDate")
    # Дата движения тела долга (по умолчанию сегодня). Старый долг → прошлая дата.
    started_on: Optional[date] = Field(default=None, validation_alias="startedOn")
    note: Optional[str] = None


class DebtUpdate(CamelModel):
    """Частичное изменение долга. Любое поле опционально — меняем присланные."""

    direction: Optional[str] = None
    counterparty: Optional[str] = None
    amount: Optional[float] = Field(default=None, gt=0)
    due_date: Optional[date] = Field(default=None, validation_alias="dueDate")
    started_on: Optional[date] = Field(default=None, validation_alias="startedOn")
    note: Optional[str] = None
    is_closed: Optional[bool] = Field(default=None, validation_alias="isClosed")


class DebtPaymentOut(CamelModel):
    id: int
    amount: float
    # сериализуем как ``date`` наружу; внутри поле зовётся on_date (см. TransactionCreate).
    on_date: date = Field(serialization_alias="date")


class DebtPaymentCreate(CamelModel):
    amount: float = Field(gt=0)
    # ``on_date`` (alias ``date``) намеренно НЕ называется ``date`` — см. TransactionCreate.
    on_date: Optional[date] = Field(default=None, validation_alias="date")


# ── Финансовые цели (направление D, S13) ─────────────────────────────────
class GoalOut(CamelModel):
    id: int
    title: str                       # на что копим
    target_amount: float = Field(serialization_alias="targetAmount")  # сколько нужно
    saved: float                     # накоплено (сумма пополнений)
    remaining: float                 # остаток = target − saved
    deadline: Optional[date] = None  # срок цели
    note: Optional[str] = None
    is_done: bool = Field(serialization_alias="isDone")


class GoalCreate(CamelModel):
    title: str
    target_amount: float = Field(gt=0, validation_alias="targetAmount")
    deadline: Optional[date] = None
    note: Optional[str] = None


class GoalUpdate(CamelModel):
    """Частичное изменение цели. Любое поле опционально — меняем присланные."""

    title: Optional[str] = None
    target_amount: Optional[float] = Field(default=None, gt=0, validation_alias="targetAmount")
    deadline: Optional[date] = None
    note: Optional[str] = None
    is_done: Optional[bool] = Field(default=None, validation_alias="isDone")


class GoalContributionOut(CamelModel):
    id: int
    amount: float
    # сериализуем как ``date`` наружу; внутри — on_date (см. TransactionCreate).
    on_date: date = Field(serialization_alias="date")


class GoalContributionCreate(CamelModel):
    amount: float = Field(gt=0)
    on_date: Optional[date] = Field(default=None, validation_alias="date")


# ── Обязательные платежи (направление C, S10) ────────────────────────────
class BillOut(CamelModel):
    id: int
    title: str
    amount: float
    due_day: int = Field(serialization_alias="dueDay")
    category_id: int = Field(serialization_alias="categoryId")
    category_name: str = Field(serialization_alias="categoryName")  # подкатегория
    group: str                                                       # категория (группа)
    emoji: Optional[str] = None
    note: Optional[str] = None
    is_active: bool = Field(serialization_alias="isActive")
    paid: bool  # оплачен ли за выбранный месяц


class BillCreate(CamelModel):
    title: str
    amount: float = Field(gt=0)
    due_day: int = Field(ge=1, le=31, validation_alias="dueDay")
    category_id: int = Field(validation_alias="categoryId")
    note: Optional[str] = None


class BillUpdate(CamelModel):
    title: Optional[str] = None
    amount: Optional[float] = Field(default=None, gt=0)
    due_day: Optional[int] = Field(default=None, ge=1, le=31, validation_alias="dueDay")
    category_id: Optional[int] = Field(default=None, validation_alias="categoryId")
    note: Optional[str] = None
    is_active: Optional[bool] = Field(default=None, validation_alias="isActive")


class BillPaidUpdate(CamelModel):
    month: str          # 'YYYY-MM'
    paid: bool
