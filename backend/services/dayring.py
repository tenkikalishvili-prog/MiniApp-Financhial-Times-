"""«Кольцо дня» (геймификация G2): траты за сегодня против дневного ориентира.

Дневной ориентир = месячный бюджет группы «Траты» ÷ дней в месяце (как в вечерней сводке).
День считается «в рамках», если он закрыт (G1) и траты по группе «Траты» за него не выше
ориентира. Всё выводится из операций и бюджета — отдельно ничего не храним, поэтому
кольцо не расходится с реальностью (правка/удаление операции сразу пересчитывает дни).

Если бюджет «Трат» не задан — кольцо не строится (``has_budget=False``), «в рамках»
оценить не из чего.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Transaction, User
from backend.services import streak as streak_svc
from backend.services.limits import (
    MEDIUM_FACTOR,
    _discretionary_budget,
    _discretionary_ids,
)


@dataclass
class WeekDay:
    day: date
    state: str  # within | over | open  (open — день не закрыт / ещё идёт)


@dataclass
class DayRing:
    has_budget: bool
    today: date
    today_spent: Decimal
    daily_target: Decimal
    status: str          # good | medium | bad (как в вечерней сводке)
    pct: int             # доля ориентира, % (может быть > 100)
    days_closed: int     # закрытых дней в этом месяце
    days_within: int     # из них «в рамках»
    week: list[WeekDay] = field(default_factory=list)  # последние 7 дней, старые → новые


async def _spent_by_day(
    session: AsyncSession, user_id: int, category_ids: list[int], start: date, end: date
) -> dict[date, Decimal]:
    """Траты по группе «Траты» по дням (по пользовательской дате операции)."""
    if not category_ids:
        return {}
    rows = await session.execute(
        select(Transaction.date, func.coalesce(func.sum(Transaction.amount), 0))
        .where(
            Transaction.user_id == user_id,
            Transaction.category_id.in_(category_ids),
            Transaction.date >= start,
            Transaction.date < end,
        )
        .group_by(Transaction.date)
    )
    return {d: Decimal(str(v)) for d, v in rows.all()}


def _status(spent: Decimal, target: Decimal) -> str:
    if target <= 0 or spent <= target:
        return "good"
    if spent <= target * MEDIUM_FACTOR:
        return "medium"
    return "bad"


async def get_day_ring(session: AsyncSession, user: User) -> DayRing:
    today = streak_svc.local_today(user)
    days_in_month = monthrange(today.year, today.month)[1]
    month_start = date(today.year, today.month, 1)

    ids = await _discretionary_ids(session, user.id)
    budget = await _discretionary_budget(session, user.id, ids)
    target = (budget / days_in_month).quantize(Decimal("1")) if budget > 0 else Decimal("0")
    has_budget = budget > 0

    # Траты за последние 7 дней и за месяц одним запросом (берём более раннюю границу).
    week_start = today - timedelta(days=6)
    since = min(month_start, week_start)
    spent = await _spent_by_day(session, user.id, ids, since, today + timedelta(days=1))
    closed = await streak_svc.closed_days(session, user, today)

    def within(day: date) -> bool:
        return (not has_budget) or spent.get(day, Decimal("0")) <= target

    days_closed = days_within = 0
    d = month_start
    while d <= today:
        if d in closed:
            days_closed += 1
            if has_budget and within(d):
                days_within += 1
        d += timedelta(days=1)

    week: list[WeekDay] = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        if day not in closed or not has_budget:
            state = "open"
        else:
            state = "within" if within(day) else "over"
        week.append(WeekDay(day=day, state=state))

    today_spent = spent.get(today, Decimal("0"))
    pct = int((today_spent / target * 100).to_integral_value()) if target > 0 else 0
    return DayRing(
        has_budget=has_budget,
        today=today,
        today_spent=today_spent,
        daily_target=target,
        status=_status(today_spent, target),
        pct=pct,
        days_closed=days_closed,
        days_within=days_within,
        week=week,
    )
