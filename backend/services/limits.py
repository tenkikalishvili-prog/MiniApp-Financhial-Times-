"""Расчёт дневного лимита и вечерней оценки дня.

Считаем по «повседневным тратам» — группе категорий DISCRETIONARY_GROUP
(кредиты, подписки, жильё в дневной лимит не входят: их не тратят понемногу
каждый день). Все суммы — за текущий месяц.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Budget, Category, Transaction

DISCRETIONARY_GROUP = "Траты"
# Порог «жёлтой зоны»: до 1.5× дневного ориентира — средне, выше — перерасход.
MEDIUM_FACTOR = Decimal("1.5")


@dataclass
class DailyLimit:
    monthly_budget: Decimal
    spent_month: Decimal
    remaining: Decimal
    days_left: int
    per_day: Decimal
    has_budget: bool


@dataclass
class EveningSummary:
    today_spent: Decimal
    daily_target: Decimal
    status: str  # good | medium | bad
    spent_month: Decimal
    monthly_budget: Decimal
    has_budget: bool


def _month_bounds(on_date: date) -> tuple[date, date, int]:
    """Возвращает (первое число месяца, первое число следующего, дней в месяце)."""
    days_in_month = monthrange(on_date.year, on_date.month)[1]
    month_start = date(on_date.year, on_date.month, 1)
    if on_date.month == 12:
        next_month = date(on_date.year + 1, 1, 1)
    else:
        next_month = date(on_date.year, on_date.month + 1, 1)
    return month_start, next_month, days_in_month


async def _discretionary_ids(session: AsyncSession, user_id: int) -> list[int]:
    result = await session.execute(
        select(Category.id).where(
            Category.user_id == user_id,
            Category.article == "expense",
            Category.group == DISCRETIONARY_GROUP,
            Category.is_archived == False,  # noqa: E712
        )
    )
    return [row[0] for row in result.all()]


async def _sum_budget(
    session: AsyncSession, user_id: int, category_ids: list[int]
) -> Decimal:
    if not category_ids:
        return Decimal("0")
    result = await session.scalar(
        select(func.coalesce(func.sum(Budget.amount), 0)).where(
            Budget.user_id == user_id,
            Budget.category_id.in_(category_ids),
            Budget.period_month.is_(None),
        )
    )
    return Decimal(str(result))


async def _sum_spent(
    session: AsyncSession,
    user_id: int,
    category_ids: list[int],
    start: date,
    end: date,
) -> Decimal:
    if not category_ids:
        return Decimal("0")
    result = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            Transaction.category_id.in_(category_ids),
            Transaction.date >= start,
            Transaction.date < end,
        )
    )
    return Decimal(str(result))


async def get_daily_limit(
    session: AsyncSession, user_id: int, on_date: date | None = None
) -> DailyLimit:
    """Сколько можно потратить сегодня: остаток бюджета «Траты» ÷ оставшиеся дни месяца."""
    on_date = on_date or date.today()
    ids = await _discretionary_ids(session, user_id)
    month_start, next_month, days_in_month = _month_bounds(on_date)

    monthly_budget = await _sum_budget(session, user_id, ids)
    spent_month = await _sum_spent(session, user_id, ids, month_start, next_month)
    remaining = monthly_budget - spent_month
    days_left = days_in_month - on_date.day + 1  # включая сегодня

    if remaining > 0 and days_left > 0:
        per_day = (remaining / days_left).quantize(Decimal("1"))
    else:
        per_day = Decimal("0")

    return DailyLimit(
        monthly_budget=monthly_budget,
        spent_month=spent_month,
        remaining=remaining,
        days_left=days_left,
        per_day=per_day,
        has_budget=bool(ids) and monthly_budget > 0,
    )


async def get_evening_summary(
    session: AsyncSession, user_id: int, on_date: date | None = None
) -> EveningSummary:
    """Оценка дня: траты сегодня vs равномерный дневной ориентир (бюджет ÷ дней в месяце)."""
    on_date = on_date or date.today()
    ids = await _discretionary_ids(session, user_id)
    month_start, next_month, days_in_month = _month_bounds(on_date)

    monthly_budget = await _sum_budget(session, user_id, ids)
    spent_month = await _sum_spent(session, user_id, ids, month_start, next_month)
    today_spent = await _sum_spent(
        session, user_id, ids, on_date, on_date + timedelta(days=1)
    )

    if monthly_budget > 0:
        daily_target = (monthly_budget / days_in_month).quantize(Decimal("1"))
    else:
        daily_target = Decimal("0")

    if daily_target <= 0 or today_spent <= daily_target:
        status = "good"
    elif today_spent <= daily_target * MEDIUM_FACTOR:
        status = "medium"
    else:
        status = "bad"

    return EveningSummary(
        today_spent=today_spent,
        daily_target=daily_target,
        status=status,
        spent_month=spent_month,
        monthly_budget=monthly_budget,
        has_budget=bool(ids) and monthly_budget > 0,
    )
