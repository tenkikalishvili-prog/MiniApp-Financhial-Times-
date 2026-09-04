"""Логика финансовых целей (направление D) — на едином реестре «Операции».

Цель — карточка накопления (на что копим, сколько нужно, к какому сроку). Пополнения
НЕ отдельная таблица, а операции реестра: ``Transaction`` с ``goal_id`` (без категории,
``article='goal'``, ``flow='out'`` — отложил деньги). ``Goal.saved`` = сумма таких
операций (кэш), остаток = ``target_amount − saved``. Один источник правды: пополнение
видно и в «Истории», и в карточке цели; удаление в любом месте пересчитывает прогресс.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Goal, Transaction


async def list_goals(
    session: AsyncSession, user_id: int, include_done: bool = False
) -> list[Goal]:
    """Все цели пользователя.

    Сортировка: сначала активные, затем — по сроку (без срока — в конце), затем
    свежие сверху. Так ближайшие к дедлайну цели оказываются выше.
    """
    stmt = select(Goal).where(Goal.user_id == user_id)
    if not include_done:
        stmt = stmt.where(Goal.is_done.is_(False))
    result = await session.execute(stmt)
    goals = list(result.scalars().all())

    far = date.max
    goals.sort(key=lambda g: (g.is_done, g.deadline or far, -g.id))
    return goals


async def create_goal(
    session: AsyncSession,
    user_id: int,
    title: str,
    target_amount: Decimal,
    deadline: date | None = None,
    note: str | None = None,
) -> Goal:
    """Заводит новую цель. saved = 0 (у цели нет «тела» — только пополнения)."""
    goal = Goal(
        user_id=user_id,
        title=title,
        target_amount=target_amount,
        saved=Decimal("0"),
        deadline=deadline,
        note=note,
    )
    session.add(goal)
    await session.commit()
    return goal


# ── Пополнения = операции реестра ─────────────────────────────────────────
async def list_contributions(session: AsyncSession, goal_id: int) -> list[Transaction]:
    """Операции-пополнения цели — свежие сверху (по дате, затем по id)."""
    result = await session.execute(
        select(Transaction)
        .where(Transaction.goal_id == goal_id)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    )
    return list(result.scalars().all())


async def _recalc_saved(session: AsyncSession, goal: Goal) -> None:
    """Пересчитывает ``goal.saved`` = сумма операций-пополнений и статус достижения."""
    total = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.goal_id == goal.id
        )
    )
    goal.saved = Decimal(str(total or 0))
    goal.is_done = goal.saved >= goal.target_amount


async def add_contribution(
    session: AsyncSession,
    goal: Goal,
    amount: Decimal,
    on_date: date,
    source: str = "manual_app",
) -> Transaction:
    """Пополнение цели: операция реестра (отток) + пересчёт накопленного/статуса."""
    tx = Transaction(
        user_id=goal.user_id,
        date=on_date,
        article="goal",
        category_id=None,
        amount=amount,
        source=source,
        goal_id=goal.id,
        flow="out",
        description=goal.title,
    )
    session.add(tx)
    await session.flush()  # получить id до пересчёта
    await _recalc_saved(session, goal)
    await session.commit()
    return tx


async def delete_contribution(
    session: AsyncSession, tx: Transaction, goal: Goal
) -> None:
    """Удаляет операцию-пополнение и пересчитывает прогресс цели."""
    await session.delete(tx)
    await session.flush()
    await _recalc_saved(session, goal)
    await session.commit()
