"""Логика реестра долгов (направление C, S8).

Долг — отдельная карточка обязательства (кому/кто должен, сумма, срок, остаток),
не операция. Здесь — создание и выборка списка; правки/удаление тонко живут в роутах
(как у транзакций). Возвраты и пересчёт остатка появятся в S9.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Debt

# Допустимые направления долга.
DIRECTIONS = ("owe", "lent")  # owe — я должен; lent — мне должны


async def list_debts(
    session: AsyncSession, user_id: int, include_closed: bool = False
) -> list[Debt]:
    """Все долги пользователя.

    Сортировка: сначала открытые, затем — по сроку (без срока — в конце), затем
    свежие сверху. Так ближайшие к дедлайну обязательства оказываются выше.
    """
    stmt = select(Debt).where(Debt.user_id == user_id)
    if not include_closed:
        stmt = stmt.where(Debt.is_closed.is_(False))
    result = await session.execute(stmt)
    debts = list(result.scalars().all())

    far = date.max
    debts.sort(key=lambda d: (d.is_closed, d.due_date or far, -d.id))
    return debts


async def create_debt(
    session: AsyncSession,
    user_id: int,
    direction: str,
    counterparty: str,
    amount: Decimal,
    due_date: date | None = None,
    note: str | None = None,
) -> Debt:
    """Заводит новую карточку долга. paid = 0 (остаток = amount)."""
    debt = Debt(
        user_id=user_id,
        direction=direction,
        counterparty=counterparty,
        amount=amount,
        paid=Decimal("0"),
        due_date=due_date,
        note=note,
    )
    session.add(debt)
    await session.commit()
    return debt
