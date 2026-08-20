"""Логика по пользователям."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import User
from backend.seed import seed_categories


async def get_or_create_user(session: AsyncSession, telegram_id: int, name: str = "") -> User:
    """Находит пользователя по Telegram ID или создаёт нового.

    При первом создании засеваем ему набор категорий и бюджетов из шаблона.
    """
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(telegram_id=telegram_id, name=name)
    session.add(user)
    await session.flush()  # получаем user.id
    await seed_categories(session, user.id)
    await session.commit()
    return user


async def get_all_users(session: AsyncSession) -> list[User]:
    """Все пользователи — для рассылки уведомлений (утро/вечер)."""
    result = await session.execute(select(User))
    return list(result.scalars())
