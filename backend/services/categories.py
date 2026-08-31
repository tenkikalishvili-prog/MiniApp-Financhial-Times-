"""Логика по категориям: получение дерева Статья → Категория → Подкатегория."""

from __future__ import annotations

from sqlalchemy import select, update
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


async def rename_subcategory(
    session: AsyncSession, category: Category, new_name: str
) -> Category:
    """Переименовывает подкатегорию. Имя не пустое и уникальное в рамках
    (пользователь, статья, категория) — иначе ``ValueError``.

    Смена только названия: id подкатегории не меняется, поэтому история операций
    и бюджеты остаются привязанными (в транзакциях хранится category_id).
    """
    name = new_name.strip()
    if not name:
        raise ValueError("empty name")

    clash = await session.scalar(
        select(Category.id).where(
            Category.user_id == category.user_id,
            Category.article == category.article,
            Category.group == category.group,
            Category.name == name,
            Category.id != category.id,
        )
    )
    if clash is not None:
        raise ValueError("duplicate name")

    category.name = name
    await session.commit()
    await session.refresh(category)
    return category


async def rename_group(
    session: AsyncSession, user_id: int, article: str, old_group: str, new_group: str
) -> int:
    """Переименовывает КАТЕГОРИЮ (группу) — меняет ``group`` у всех её подкатегорий.

    Категория не отдельная таблица, а поле ``group`` в строках подкатегорий, поэтому
    переименование = массовый UPDATE. Имя не пустое; слияние с уже существующей группой
    запрещено (иначе конфликт уникальности и перемешивание). Возвращает число обновлённых
    подкатегорий. id подкатегорий не меняются — история и бюджеты сохраняются.
    """
    name = new_group.strip()
    if not name:
        raise ValueError("empty name")
    if name == old_group:
        return 0

    clash = await session.scalar(
        select(Category.id)
        .where(
            Category.user_id == user_id,
            Category.article == article,
            Category.group == name,
        )
        .limit(1)
    )
    if clash is not None:
        raise ValueError("group exists")

    result = await session.execute(
        update(Category)
        .where(
            Category.user_id == user_id,
            Category.article == article,
            Category.group == old_group,
        )
        .values(group=name)
    )
    await session.commit()
    return result.rowcount or 0
