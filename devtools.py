"""Утилита разработчика: посмотреть и почистить данные в базе.

Запуск из папки app/ (окружение активировано):
    python devtools.py show        # показать все операции
    python devtools.py clear-tx    # удалить ВСЕ операции (категории и бюджеты остаются)
    python devtools.py reset        # полный сброс: удалить операции, категории, пользователей
    python devtools.py reset-box    # сбросить ВСЕХ, кроме владельца, до нейтральной «коробки»

Совет: перед clear-tx / reset / reset-box лучше остановить бота (Ctrl+C), чтобы файл не был занят.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, func, select

from backend.config import settings
from backend.db import async_session
from backend.models import Budget, Category, Transaction, User
from backend.seed import seed_categories

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


async def reset_box() -> None:
    """Сбрасывает всех пользователей, КРОМЕ владельца, до нейтральной «коробки».

    Для каждого целевого пользователя удаляем его операции, бюджеты и категории,
    затем засеваем текущий нейтральный шаблон (backend/seed.py). Сам пользователь
    (его telegram_id/имя) сохраняется. Данные владельца не трогаем.
    """
    owner = settings.owner_telegram_id
    if not owner:
        print("⛔ Не задан owner_telegram_id — отказ (иначе сбросило бы всех). "
              "Задай OWNER_TELEGRAM_ID в .env и повтори.")
        return

    async with async_session() as session:
        users = (await session.execute(select(User))).scalars().all()
        targets = [u for u in users if u.telegram_id != owner]
        owner_present = any(u.telegram_id == owner for u in users)

        print(f"Владелец (tg {owner}): {'найден, не трогаем' if owner_present else 'НЕ найден в базе'}")
        print(f"К сбросу — пользователей: {len(targets)}")
        if not targets:
            print("Некого сбрасывать. Готово.")
            return

        for u in targets:
            ntx = (await session.execute(
                select(func.count()).select_from(Transaction).where(Transaction.user_id == u.id)
            )).scalar()
            await session.execute(delete(Transaction).where(Transaction.user_id == u.id))
            await session.execute(delete(Budget).where(Budget.user_id == u.id))
            await session.execute(delete(Category).where(Category.user_id == u.id))
            await session.flush()
            await seed_categories(session, u.id)
            who = u.name or f"user {u.id}"
            print(f"  ↻ [{who}] tg={u.telegram_id}: удалено операций {ntx}, категории/бюджеты пересозданы из коробки")

        await session.commit()
    print(f"✅ Готово. Сброшено пользователей: {len(targets)}. Данные владельца сохранены.")


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "show"
    if command == "show":
        await show()
    elif command == "clear-tx":
        await clear_tx()
    elif command == "reset":
        await reset()
    elif command == "reset-box":
        await reset_box()
    else:
        print("Команды: show | clear-tx | reset | reset-box")


if __name__ == "__main__":
    asyncio.run(main())
