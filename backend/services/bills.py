"""Логика календаря обязательных платежей (направление C, S10).

Платёж — регулярное ежемесячное обязательство (аренда, кредит, подписка) с числом-
сроком и привязкой к расходной подкатегории. Отметка «оплачено» за месяц создаёт
расходную операцию по этой категории; снятие отметки — удаляет её (чтобы не задваивать).
Создание/правка/удаление самих платежей — тонко в роутах (как у долгов).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Bill, BillMark, Transaction
from backend.services.transactions import create_transaction


async def list_bills(
    session: AsyncSession, user_id: int, active_only: bool = True
) -> list[Bill]:
    """Платежи пользователя, отсортированные по числу-сроку (затем по id)."""
    stmt = select(Bill).where(Bill.user_id == user_id)
    if active_only:
        stmt = stmt.where(Bill.is_active.is_(True))
    result = await session.execute(stmt)
    bills = list(result.scalars().all())
    bills.sort(key=lambda b: (b.due_day, b.id))
    return bills


async def marks_for_period(
    session: AsyncSession, user_id: int, period: str
) -> dict[int, BillMark]:
    """Отметки оплаты за месяц ('YYYY-MM') — словарь bill_id → отметка."""
    result = await session.execute(
        select(BillMark).where(
            BillMark.user_id == user_id, BillMark.period == period
        )
    )
    return {m.bill_id: m for m in result.scalars().all()}


async def create_bill(
    session: AsyncSession,
    user_id: int,
    title: str,
    amount: Decimal,
    due_day: int,
    category_id: int,
    note: str | None = None,
) -> Bill:
    """Заводит новый обязательный платёж."""
    bill = Bill(
        user_id=user_id,
        title=title,
        amount=amount,
        due_day=due_day,
        category_id=category_id,
        note=note,
    )
    session.add(bill)
    await session.commit()
    return bill


async def set_paid(
    session: AsyncSession,
    user_id: int,
    bill: Bill,
    period: str,
    paid: bool,
) -> BillMark | None:
    """Ставит/снимает отметку оплаты платежа за месяц.

    Отметка «оплачено» создаёт расходную операцию по категории платежа (дата — сегодня,
    сумма — из платежа), снятие — удаляет ранее созданную операцию. Операция идемпотентна.
    """
    existing = await session.scalar(
        select(BillMark).where(
            BillMark.bill_id == bill.id, BillMark.period == period
        )
    )

    if paid:
        if existing is not None:
            return existing  # уже оплачено — ничего не делаем
        # Создаём расходную операцию по категории платежа.
        tx = await create_transaction(
            session,
            user_id=user_id,
            category_id=bill.category_id,
            article="expense",
            amount=bill.amount,
            source="bill",
            description=bill.title,
            on_date=date.today(),
        )
        mark = BillMark(
            bill_id=bill.id,
            user_id=user_id,
            period=period,
            transaction_id=tx.id,
        )
        session.add(mark)
        await session.commit()
        return mark

    # Снятие отметки — удаляем связанную операцию и саму отметку.
    if existing is None:
        return None
    if existing.transaction_id is not None:
        tx = await session.get(Transaction, existing.transaction_id)
        if tx is not None and tx.user_id == user_id:
            await session.delete(tx)
    await session.delete(existing)
    await session.commit()
    return None
