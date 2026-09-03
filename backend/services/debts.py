"""Логика реестра долгов (направление C, S8).

Долг — отдельная карточка обязательства (кому/кто должен, сумма, срок, остаток),
не операция. Здесь — создание и выборка списка; правки/удаление тонко живут в роутах
(как у транзакций). Возвраты и пересчёт остатка появятся в S9.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Debt, DebtPayment

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


# ── Возвраты частями (S9) ────────────────────────────────────────────────
async def list_payments(session: AsyncSession, debt_id: int) -> list[DebtPayment]:
    """Все платежи долга — свежие сверху (по дате, затем по id)."""
    result = await session.execute(
        select(DebtPayment).where(DebtPayment.debt_id == debt_id)
    )
    payments = list(result.scalars().all())
    payments.sort(key=lambda p: (p.on_date, p.id), reverse=True)
    return payments


async def _recalc_paid(session: AsyncSession, debt: Debt) -> None:
    """Пересчитывает ``debt.paid`` = сумма платежей и авто-синхронит статус закрытия.

    Возврат до полной суммы → долг авто-закрывается; удаление платежа ниже полной
    суммы → авто-открывается. Ручной тумблер «возвращён» остаётся для долгов без
    платежей (напр. прощённых).
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(DebtPayment.amount), 0)).where(
            DebtPayment.debt_id == debt.id
        )
    )
    debt.paid = Decimal(str(total or 0))
    debt.is_closed = debt.paid >= debt.amount


async def add_payment(
    session: AsyncSession,
    debt: Debt,
    amount: Decimal,
    on_date: date,
) -> DebtPayment:
    """Записывает возврат по долгу и пересчитывает остаток/статус."""
    payment = DebtPayment(
        debt_id=debt.id,
        user_id=debt.user_id,
        amount=amount,
        on_date=on_date,
    )
    session.add(payment)
    await session.flush()  # получить id платежа до пересчёта
    await _recalc_paid(session, debt)
    await session.commit()
    return payment


async def delete_payment(session: AsyncSession, payment: DebtPayment, debt: Debt) -> None:
    """Удаляет возврат и пересчитывает остаток/статус долга."""
    await session.delete(payment)
    await session.flush()
    await _recalc_paid(session, debt)
    await session.commit()
