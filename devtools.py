"""Утилита разработчика: посмотреть и почистить данные в базе.

Запуск из папки app/ (окружение активировано):
    python devtools.py show        # показать все операции
    python devtools.py clear-tx    # удалить ВСЕ операции (категории и бюджеты остаются)
    python devtools.py reset        # полный сброс: удалить операции, категории, пользователей

Совет: перед clear-tx / reset лучше остановить бота (Ctrl+C), чтобы файл не был занят.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, select

from backend.db import async_session
from backend.models import Budget, Category, Transaction, User

ARTICLE_RU = {"income": "Доход", "expense": "Расход", "debt": "Долг"}


async def show() -> None:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Transaction, Category, User)
                .join(Category, Transaction.category_id == Category.id)
                .join(User, Transaction.user_id == User.id)
                .order_by(User.id, Transaction.created_at)
            )
        ).all()

    if not rows:
        print("Операций пока нет.")
        return

    total = 0
    print(f"Всего операций: {len(rows)}\n")
    for tx, cat, user in rows:
        article = ARTICLE_RU.get(tx.article, tx.article)
        emoji = f"{cat.emoji} " if cat.emoji else ""
        who = user.name or f"user {user.id}"
        print(
            f"  [{who}] {tx.date.strftime('%d.%m.%Y')} · {article} · "
            f"{cat.group} · {emoji}{cat.name} · {tx.amount:.0f} ₽"
        )
        total += float(tx.amount)
    print(f"\n  Сумма всех операций: {total:,.0f} ₽".replace(",", " "))


async def clear_tx() -> None:
    async with async_session() as session:
        await session.execute(delete(Transaction))
        await session.commit()
    print("✅ Все операции удалены. Категории и бюджеты сохранены.")


async def reset() -> None:
    async with async_session() as session:
        await session.execute(delete(Transaction))
        await session.execute(delete(Budget))
        await session.execute(delete(Category))
        await session.execute(delete(User))
        await session.commit()
    print("✅ Полный сброс. При следующем /start категории засеются заново.")


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "show"
    if command == "show":
        await show()
    elif command == "clear-tx":
        await clear_tx()
    elif command == "reset":
        await reset()
    else:
        print("Команды: show | clear-tx | reset")


if __name__ == "__main__":
    asyncio.run(main())
