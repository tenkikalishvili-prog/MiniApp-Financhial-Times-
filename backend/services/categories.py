"""Логика по категориям: получение дерева Статья → Категория → Подкатегория."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Category


async def get_groups(session: AsyncSession, user_id: int, article: str) -> list[str]:
    """Список категорий (групп) для статьи, в порядке сортировки, без дублей."""
    result = await session.execute(
        select(Category)
        .where(
            Category.user_id == user_id,
            Category.article == article,
            Category.is_archived == False,  # noqa: E712
        )
        .order_by(Category.sort_order)
    )
    groups: list[str] = []
    for category in result.scalars():
        if category.group not in groups:
            groups.append(category.group)
    return groups


async def get_subcategories(
    session: AsyncSession, user_id: int, article: str, group: str
) -> list[Category]:
    """Подкатегории внутри группы."""
    result = await session.execute(
        select(Category)
        .where(
            Category.user_id == user_id,
            Category.article == article,
            Category.group == group,
            Category.is_archived == False,  # noqa: E712
        )
        .order_by(Category.sort_order)
    )
    return list(result.scalars())


async def get_category(session: AsyncSession, category_id: int) -> Category | None:
    """Одна подкатегория по её id."""
    return await session.get(Category, category_id)
