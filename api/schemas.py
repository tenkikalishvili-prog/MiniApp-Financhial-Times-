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


class SettingsUpdate(CamelModel):
    timezone: Optional[str] = Field(default=None)
    morning_enabled: Optional[bool] = Field(default=None, validation_alias="morningEnabled")
    morning_hour: Optional[int] = Field(default=None, ge=0, le=23, validation_alias="morningHour")
    evening_enabled: Optional[bool] = Field(default=None, validation_alias="eveningEnabled")
    evening_hour: Optional[int] = Field(default=None, ge=0, le=23, validation_alias="eveningHour")


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
    category_id: int = Field(serialization_alias="categoryId")
    category_name: str = Field(serialization_alias="categoryName")
    subcategory_name: str = Field(serialization_alias="subcategoryName")
    emoji: Optional[str]
    amount: float
    date: date
    comment: Optional[str]


class TransactionCreate(CamelModel):
    category_id: int = Field(validation_alias="categoryId")
    amount: float
    date: Optional[date] = None
    comment: Optional[str] = None


# ── Изменение бюджета подкатегории ───────────────────────────────────────
class BudgetSet(CamelModel):
    amount: float
