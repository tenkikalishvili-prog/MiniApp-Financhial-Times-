"""Логика по категориям: получение дерева Статья → Категория → Подкатегория."""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Budget, Category, Transaction


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


async def create_subcategory(
    session: AsyncSession,
    user_id: int,
    article: str,
    group: str,
    name: str,
    emoji: str | None = None,
) -> Category:
    """Создаёт подкатегорию в категории (группе). Если группы ещё нет — она
    появляется автоматически (категория = поле ``group``, отдельной таблицы нет),
    так что этой же функцией создаётся и новая категория (её первая подкатегория).

    Правила: имена не пустые; дубль в рамках (пользователь, статья, категория)
    запрещён. Если такая подкатегория уже есть, но архивная — «оживляем» её
    (снимаем архив, при желании обновляем эмодзи), чтобы вернулась история операций.
    Иначе ``ValueError``. Новая запись встаёт в конец своей категории.
    """
    group_name = group.strip()
    sub_name = name.strip()
    emoji = (emoji or "").strip() or None
    if not group_name:
        raise ValueError("empty group")
    if not sub_name:
        raise ValueError("empty name")

    existing = await session.scalar(
        select(Category).where(
            Category.user_id == user_id,
            Category.article == article,
            Category.group == group_name,
            Category.name == sub_name,
        )
    )
    if existing is not None:
        if existing.is_archived:
            existing.is_archived = False
            if emoji:
                existing.emoji = emoji
            await session.commit()
            await session.refresh(existing)
            return existing
        raise ValueError("duplicate name")

    # Порядок сортировки: в конец группы, если она существует; иначе новая
    # категория целиком уходит в конец списка статьи.
    max_in_group = await session.scalar(
        select(func.max(Category.sort_order)).where(
            Category.user_id == user_id,
            Category.article == article,
            Category.group == group_name,
        )
    )
    if max_in_group is None:
        max_global = await session.scalar(
            select(func.max(Category.sort_order)).where(
                Category.user_id == user_id,
                Category.article == article,
            )
        )
        next_order = (max_global or 0) + 1
    else:
        next_order = max_in_group + 1

    category = Category(
        user_id=user_id,
        article=article,
        group=group_name,
        name=sub_name,
        emoji=emoji,
        sort_order=next_order,
    )
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


async def _has_transactions(session: AsyncSession, category_id: int) -> bool:
    """Есть ли хоть одна операция по этой подкатегории."""
    found = await session.scalar(
        select(Transaction.id).where(Transaction.category_id == category_id).limit(1)
    )
    return found is not None


async def delete_subcategory(session: AsyncSession, category: Category) -> str:
    """Удаляет подкатегорию, если по ней НЕТ операций; иначе архивирует.

    История операций ссылается на ``category_id`` (FK), поэтому физически удалять
    подкатегорию с операциями нельзя — прячем её (``is_archived``), а данные храним.
    Пустую подкатегорию удаляем совсем (вместе с её шаблонным лимитом).
    Возвращает ``'deleted'`` или ``'archived'``.
    """
    if await _has_transactions(session, category.id):
        category.is_archived = True
        await session.commit()
        return "archived"

    await session.execute(delete(Budget).where(Budget.category_id == category.id))
    await session.delete(category)
    await session.commit()
    return "deleted"


async def delete_group(
    session: AsyncSession, user_id: int, article: str, group: str
) -> dict[str, int]:
    """Удаляет категорию (группу) целиком: применяет к каждой её подкатегории
    правило ``delete_subcategory`` (пустые — удаляет, с историей — архивирует).

    Возвращает ``{'deleted': N, 'archived': M}``. ``ValueError('group not found')``,
    если активных подкатегорий в группе нет.
    """
    subs = list(
        await session.scalars(
            select(Category).where(
                Category.user_id == user_id,
                Category.article == article,
                Category.group == group,
                Category.is_archived == False,  # noqa: E712
            )
        )
    )
    if not subs:
        raise ValueError("group not found")

    deleted = archived = 0
    for sub in subs:
        if await delete_subcategory(session, sub) == "deleted":
            deleted += 1
        else:
            archived += 1
    return {"deleted": deleted, "archived": archived}
