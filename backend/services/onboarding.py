"""Онбординг: лёгкий мастер первого входа (доход + общий лимит трат).

Пишет плановые цифры прямо в модель User и помечает пользователя пройденным
(``onboarded_at``), чтобы мастер больше не показывался. Общий лимит трат питает
дневной лимит (см. backend/services/limits.py) до тех пор, пока человек не задаст
лимиты по подкатегориям вручную.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import User


def _to_decimal(value: Optional[float]) -> Optional[Decimal]:
    if value is None:
        return None
    dec = Decimal(str(value))
    return dec if dec > 0 else None


async def complete_onboarding(
    session: AsyncSession,
    user: User,
    monthly_income: Optional[float] = None,
    monthly_spending: Optional[float] = None,
) -> User:
    """Сохраняет плановый доход и общий лимит трат, отмечает онбординг пройденным.

    Любое из полей можно пропустить (None) — тогда просто не задаём его. Мастер
    в любом случае считается пройденным: повторно не показываем.
    """
    user.monthly_income = _to_decimal(monthly_income)
    user.discretionary_budget = _to_decimal(monthly_spending)
    user.onboarded_at = datetime.utcnow()
    await session.commit()
    await session.refresh(user)
    return user
