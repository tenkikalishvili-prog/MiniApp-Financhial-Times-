"""Агрегации для Mini App: месячные итоги, аналитика, строки бюджета, история.

Вся логика чтения для HTTP-API живёт здесь (в слое сервисов), чтобы роуты
оставались тонкими, а запросы — переиспользуемыми и тестируемыми.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Budget, Category, Transaction


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """(первое число месяца, первое число следующего месяца)."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


@dataclass
class MonthTotals:
    income: Decimal
    expense: Decimal


async def month_totals(
    session: AsyncSession, user_id: int, year: int, month: int
) -> MonthTotals:
    """Сумма доходов и расходов за месяц (по статье операции)."""
    start, end = month_bounds(year, month)
    result = await session.execute(
        select(
            Transaction.article,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.article.in_(("income", "expense")),
        )
        .group_by(Transaction.article)
    )
    totals = {row[0]: Decimal(str(row[1])) for row in result.all()}
    return MonthTotals(
        income=totals.get("income", Decimal("0")),
        expense=totals.get("expense", Decimal("0")),
    )


@dataclass
class GroupSlice:
    group: str
    amount: Decimal


async def expense_by_group(
    session: AsyncSession, user_id: int, year: int, month: int
) -> list[GroupSlice]:
    """Расходы за месяц, сгруппированные по категории (для donut). По убыванию."""
    start, end = month_bounds(year, month)
    result = await session.execute(
        select(
            Category.group,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .join(Category, Category.id == Transaction.category_id)
        .where(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.article == "expense",
        )
        .group_by(Category.group)
        .order_by(func.sum(Transaction.amount).desc())
    )
    return [GroupSlice(group=row[0], amount=Decimal(str(row[1]))) for row in result.all()]


@dataclass
class BudgetLine:
    category_id: int
    group: str
    name: str
    emoji: str | None
    spent: Decimal
    limit: Decimal


async def budget_lines(
    session: AsyncSession,
    user_id: int,
    year: int,
    month: int,
    group: str | None = None,
) -> list[BudgetLine]:
    """Строки бюджета: подкатегория → план (шаблон) и факт за месяц.

    Берём расходные подкатегории, у которых задан плановый бюджет (> 0).
    Если указан ``group`` — только внутри неё (напр. «Траты» для дневного лимита).
    """
    start, end = month_bounds(year, month)

    # Факт за месяц по каждой подкатегории
    spent_subq = (
        select(
            Transaction.category_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("spent"),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date < end,
        )
        .group_by(Transaction.category_id)
        .subquery()
    )

    conditions = [
        Category.user_id == user_id,
        Category.article == "expense",
        Category.is_archived == False,  # noqa: E712
        Budget.amount > 0,
        Budget.period_month.is_(None),
    ]
    if group is not None:
        conditions.append(Category.group == group)

    result = await session.execute(
        select(
            Category.id,
            Category.group,
            Category.name,
            Category.emoji,
            func.coalesce(spent_subq.c.spent, 0),
            Budget.amount,
        )
        .join(Budget, Budget.category_id == Category.id)
        .outerjoin(spent_subq, spent_subq.c.category_id == Category.id)
        .where(*conditions)
        .order_by(Category.sort_order)
    )
    return [
        BudgetLine(
            category_id=row[0],
            group=row[1],
            name=row[2],
            emoji=row[3],
            spent=Decimal(str(row[4])),
            limit=Decimal(str(row[5])),
        )
        for row in result.all()
    ]


async def recent_transactions(
    session: AsyncSession,
    user_id: int,
    limit: int = 30,
    year: int | None = None,
    month: int | None = None,
) -> list[Transaction]:
    """Последние операции с подгруженной категорией. Опционально — за месяц."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .options(selectinload(Transaction.category))
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(limit)
    )
    if year is not None and month is not None:
        start, end = month_bounds(year, month)
        stmt = stmt.where(Transaction.date >= start, Transaction.date < end)

    result = await session.execute(stmt)
    return list(result.scalars())
