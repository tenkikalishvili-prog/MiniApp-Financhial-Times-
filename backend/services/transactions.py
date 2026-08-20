"""Логика по транзакциям: сохранение операции и подсчёт остатка бюджета."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Budget, Transaction


async def create_transaction(
    session: AsyncSession,
    user_id: int,
    category_id: int,
    article: str,
    amount: Decimal,
    source: str = "bot_buttons",
    description: str | None = None,
    on_date: date | None = None,
) -> Transaction:
    """Сохраняет одну операцию."""
    transaction = Transaction(
        user_id=user_id,
        category_id=category_id,
        article=article,
        amount=amount,
        source=source,
        description=description,
        date=on_date or date.today(),
    )
    session.add(transaction)
    await session.commit()
    return transaction


async def get_budget_amount(
    session: AsyncSession, user_id: int, category_id: int
) -> Decimal | None:
    """Плановый бюджет подкатегории (шаблон по умолчанию, period_month = NULL)."""
    result = await session.execute(
        select(Budget.amount).where(
            Budget.user_id == user_id,
            Budget.category_id == category_id,
            Budget.period_month.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_month_spent(
    session: AsyncSession, user_id: int, category_id: int, year: int, month: int
) -> Decimal:
    """Сумма трат по подкатегории за конкретный месяц."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    result = await session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            Transaction.category_id == category_id,
            Transaction.date >= start,
            Transaction.date < end,
        )
    )
    return Decimal(str(result.scalar_one()))
