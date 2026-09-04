"""Логика реестра долгов (направление C) — на едином реестре «Операции».

Долг — карточка обязательства (кому/кто должен, сумма-тело, срок, остаток). Движения
денег по долгу — операции реестра (``Transaction`` с ``debt_id``, без категории,
``article='debt'``):

- **тело** (``debt_role='principal'``) — заняли/дали в долг. Дата = ``started_on``
  (когда деньги реально перешли). ``flow`` = приток для «я должен» (занял → денег
  больше), отток для «мне должны» (дал → денег меньше).
- **возврат** (``debt_role='payment'``) — частичное погашение. ``flow`` противоположный
  телу. ``Debt.paid`` = сумма возвратов (кэш), остаток = ``amount − paid``.

Долги/цели в доход-расход и аналитику трат НЕ попадают (нет категории) — только в
«остаток» через ``flow``. Один источник правды: движение видно в «Истории» и в карточке.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Debt, Transaction

# Допустимые направления долга.
DIRECTIONS = ("owe", "lent")  # owe — я должен; lent — мне должны


def principal_flow(direction: str) -> str:
    """Направление ДС для тела долга: занял (owe) → приток; дал (lent) → отток."""
    return "in" if direction == "owe" else "out"


def payment_flow(direction: str) -> str:
    """Направление ДС для возврата: противоположно телу."""
    return "out" if direction == "owe" else "in"


async def list_debts(
    session: AsyncSession, user_id: int, include_closed: bool = False
) -> list[Debt]:
    """Все долги пользователя (открытые вперёд/по сроку/свежие сверху)."""
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
    started_on: date | None = None,
    source: str = "manual_app",
) -> Debt:
    """Заводит долг и операцию-тело в реестре (движение ДС на всю сумму)."""
    start = started_on or date.today()
    debt = Debt(
        user_id=user_id,
        direction=direction,
        counterparty=counterparty,
        amount=amount,
        paid=Decimal("0"),
        due_date=due_date,
        started_on=start,
        note=note,
    )
    session.add(debt)
    await session.flush()  # получить debt.id

    session.add(
        Transaction(
            user_id=user_id,
            date=start,
            article="debt",
            category_id=None,
            amount=amount,
            source=source,
            debt_id=debt.id,
            debt_role="principal",
            flow=principal_flow(direction),
            description=counterparty,
        )
    )
    await session.commit()
    return debt


async def _get_principal(session: AsyncSession, debt_id: int) -> Transaction | None:
    result = await session.execute(
        select(Transaction).where(
            Transaction.debt_id == debt_id, Transaction.debt_role == "principal"
        )
    )
    return result.scalar_one_or_none()


async def sync_principal(session: AsyncSession, debt: Debt) -> None:
    """Приводит операцию-тело в соответствие карточке (сумма/дата/направление/имя).

    Вызывается после правки долга. Если тела нет (старые данные) — создаёт его.
    Коммит — на вызывающей стороне.
    """
    principal = await _get_principal(session, debt.id)
    start = debt.started_on or date.today()
    if principal is None:
        session.add(
            Transaction(
                user_id=debt.user_id,
                date=start,
                article="debt",
                category_id=None,
                amount=debt.amount,
                source="manual_app",
                debt_id=debt.id,
                debt_role="principal",
                flow=principal_flow(debt.direction),
                description=debt.counterparty,
            )
        )
        return
    principal.amount = debt.amount
    principal.date = start
    principal.flow = principal_flow(debt.direction)
    principal.description = debt.counterparty


async def delete_debt_ledger(session: AsyncSession, debt_id: int) -> None:
    """Удаляет ВСЕ операции долга (тело + возвраты). Коммит — на вызывающей стороне."""
    await session.execute(delete(Transaction).where(Transaction.debt_id == debt_id))


# ── Возвраты = операции реестра ───────────────────────────────────────────
async def list_payments(session: AsyncSession, debt_id: int) -> list[Transaction]:
    """Операции-возвраты долга — свежие сверху (по дате, затем по id)."""
    result = await session.execute(
        select(Transaction)
        .where(Transaction.debt_id == debt_id, Transaction.debt_role == "payment")
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    )
    return list(result.scalars().all())


async def _recalc_paid(session: AsyncSession, debt: Debt) -> None:
    """Пересчитывает ``debt.paid`` = сумма возвратов и авто-синхронит статус закрытия."""
    total = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.debt_id == debt.id, Transaction.debt_role == "payment"
        )
    )
    debt.paid = Decimal(str(total or 0))
    debt.is_closed = debt.paid >= debt.amount


async def add_payment(
    session: AsyncSession,
    debt: Debt,
    amount: Decimal,
    on_date: date,
    source: str = "manual_app",
) -> Transaction:
    """Возврат по долгу: операция реестра (противоположный телу flow) + пересчёт."""
    tx = Transaction(
        user_id=debt.user_id,
        date=on_date,
        article="debt",
        category_id=None,
        amount=amount,
        source=source,
        debt_id=debt.id,
        debt_role="payment",
        flow=payment_flow(debt.direction),
        description=debt.counterparty,
    )
    session.add(tx)
    await session.flush()
    await _recalc_paid(session, debt)
    await session.commit()
    return tx


async def delete_payment(session: AsyncSession, tx: Transaction, debt: Debt) -> None:
    """Удаляет операцию-возврат и пересчитывает остаток/статус долга."""
    await session.delete(tx)
    await session.flush()
    await _recalc_paid(session, debt)
    await session.commit()
