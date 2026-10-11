"""Логика кредитов и досрочного погашения — на едином реестре «Операции».

Кредит — карточка (банк, тело, ежемесячный платёж). Досрочное погашение — НЕ отдельная
таблица, а обычный РАСХОД: ``Transaction`` с ``article='expense'``, категорией «Досрочное
погашение кредитов» (отдельная группа, отдельная статья в Аналитике) и ссылкой ``credit_id``.
Поэтому оно один раз попадает в «Расходы», «Остаток» и «Свободно за месяц» (а значит и в
дневной лимит, который считается от «Свободно»), и видно в «Истории».

``Credit.paid`` — кэш суммы таких операций (как ``Goal.saved``). Кредит закрыт, когда
``paid >= amount`` — неважно, как набрано: одним «погасить полностью» или частичными взносами.
Платёж закрытого кредита «высвобождается» (плитка «Высвободившиеся платежи»).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Category, Credit, Transaction

# Категория досрочки: своя группа, чтобы в донате Аналитики это была отдельная статья
# и НЕ часть группы «Траты» (по ней считается лимит трат).
PREPAY_GROUP = "Досрочное погашение"
PREPAY_NAME = "Досрочное погашение кредитов"
PREPAY_EMOJI = "💳"


async def get_prepay_category(session: AsyncSession, user_id: int) -> Category:
    """Категория досрочки пользователя; создаётся лениво (старым пользователям seed не шёл)."""
    cat = await session.scalar(
        select(Category).where(
            Category.user_id == user_id,
            Category.article == "expense",
            Category.group == PREPAY_GROUP,
            Category.name == PREPAY_NAME,
        )
    )
    if cat is None:
        top = await session.scalar(
            select(func.coalesce(func.max(Category.sort_order), 0)).where(
                Category.user_id == user_id
            )
        )
        cat = Category(
            user_id=user_id,
            article="expense",
            group=PREPAY_GROUP,
            name=PREPAY_NAME,
            emoji=PREPAY_EMOJI,
            sort_order=int(top or 0) + 1,
        )
        session.add(cat)
        await session.flush()
    elif cat.is_archived:
        cat.is_archived = False
    return cat


async def list_credits(session: AsyncSession, user_id: int) -> list[Credit]:
    result = await session.execute(
        select(Credit).where(Credit.user_id == user_id).order_by(Credit.id)
    )
    return list(result.scalars().all())


async def create_credit(
    session: AsyncSession,
    user_id: int,
    kind: str,
    bank: str,
    amount: Decimal,
    monthly_payment: Decimal,
    note: Optional[str] = None,
) -> Credit:
    credit = Credit(
        user_id=user_id,
        kind=kind,
        bank=bank,
        amount=amount,
        paid=Decimal("0"),
        monthly_payment=monthly_payment,
        note=note,
    )
    session.add(credit)
    await session.commit()
    return credit


async def recalc_paid(session: AsyncSession, credit: Credit) -> None:
    """``paid`` = сумма операций-погашений; ``is_closed`` — по факту. Коммит — на вызывающем."""
    total = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.credit_id == credit.id
        )
    )
    credit.paid = Decimal(str(total or 0))
    credit.is_closed = credit.paid >= credit.amount


def remaining(credit: Credit) -> Decimal:
    return max(Decimal("0"), credit.amount - credit.paid)


async def list_repayments(session: AsyncSession, credit_id: int) -> list[Transaction]:
    result = await session.execute(
        select(Transaction)
        .where(Transaction.credit_id == credit_id)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    )
    return list(result.scalars().all())


async def add_repayment(
    session: AsyncSession,
    credit: Credit,
    amount: Decimal,
    on_date: date,
    source: str = "manual_app",
) -> Transaction:
    """Досрочное погашение: расход по категории досрочки + пересчёт факта/статуса."""
    cat = await get_prepay_category(session, credit.user_id)
    tx = Transaction(
        user_id=credit.user_id,
        date=on_date,
        article="expense",
        category_id=cat.id,
        amount=amount,
        source=source,
        credit_id=credit.id,
        description=f"{credit.bank} · досрочно",
    )
    session.add(tx)
    await session.flush()
    await recalc_paid(session, credit)
    await session.commit()
    return tx


async def delete_repayment(session: AsyncSession, tx: Transaction, credit: Credit) -> None:
    await session.delete(tx)
    await session.flush()
    await recalc_paid(session, credit)
    await session.commit()


async def reset_repayments(session: AsyncSession, credit: Credit) -> None:
    """Сбрасывает факт: удаляет ВСЕ погашения кредита (снятие галочки «погашен полностью»)."""
    await session.execute(delete(Transaction).where(Transaction.credit_id == credit.id))
    await session.flush()
    await recalc_paid(session, credit)
    await session.commit()


async def delete_credit(session: AsyncSession, credit: Credit) -> None:
    """Удаляет карточку вместе с её операциями (нет каскада FK — иначе останутся сироты)."""
    await session.execute(delete(Transaction).where(Transaction.credit_id == credit.id))
    await session.delete(credit)
    await session.commit()


@dataclass
class CreditsSummary:
    total_debt: Decimal       # сумма остатков по всем кредитам
    freed_payments: Decimal   # ежемесячные платежи закрытых кредитов
    paid_total: Decimal       # факт досрочки по всем кредитам


async def month_paid_total(
    session: AsyncSession, user_id: int, year: int, month: int
) -> Decimal:
    """Сумма досрочных погашений (по всем кредитам) за календарный месяц."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    total = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            Transaction.credit_id.is_not(None),
            Transaction.date >= start,
            Transaction.date < end,
        )
    )
    return Decimal(str(total or 0))


def summarize(credits: list[Credit]) -> CreditsSummary:
    return CreditsSummary(
        total_debt=sum((remaining(c) for c in credits), Decimal("0")),
        freed_payments=sum((c.monthly_payment for c in credits if c.is_closed), Decimal("0")),
        paid_total=sum((c.paid for c in credits), Decimal("0")),
    )
