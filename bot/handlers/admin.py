"""Скрытые админ-команды (только для владельца). В меню команд НЕ показываются.

- /reset_onboarding <telegram_id> — сбросить флаг онбординга пользователю.
- /reset_box [confirm] — сбросить ВСЕХ, кроме владельца, до нейтральной «коробки»
  (без аргумента показывает предпросмотр; с `confirm` — выполняет). Нужно, чтобы
  починить пользователей, которым старый бот засеял личный набор владельца.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db import async_session
from backend.models import Budget, Category, Transaction, User
from backend.seed import seed_categories

router = Router()


def _is_owner(message: Message) -> bool:
    return (
        settings.owner_telegram_id is not None
        and message.from_user is not None
        and message.from_user.id == settings.owner_telegram_id
    )


@router.message(Command("reset_onboarding"))
async def cmd_reset_onboarding(message: Message, command: CommandObject) -> None:
    """Владелец: /reset_onboarding <telegram_id> → снова показать мастер онбординга юзеру."""
    if not _is_owner(message):
        return  # молча игнорируем не-владельца (команда скрытая)

    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Использование: <code>/reset_onboarding &lt;telegram_id&gt;</code>")
        return
    target_id = int(arg)

    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == target_id))
        ).scalar_one_or_none()
        if user is None:
            await message.answer(f"⛔ Пользователь с telegram_id={target_id} не найден в базе.")
            return
        user.onboarded_at = None
        user.monthly_income = None
        user.discretionary_budget = None
        await session.commit()
        who = user.name or f"user {user.id}"

    await message.answer(
        f"✅ [{who}] tg={target_id}: онбординг сброшен.\n"
        "При следующем открытии Mini App снова покажется мастер первого входа."
    )


# ─── /reset_box — сброс всех, кроме владельца, до нейтральной «коробки» ──────

@dataclass
class BoxResetResult:
    count: int
    total_tx: int


async def _list_box_targets(session: AsyncSession, owner_id: int) -> list[User]:
    users = (await session.execute(select(User))).scalars().all()
    return [u for u in users if u.telegram_id != owner_id]


async def reset_non_owner_to_box(session: AsyncSession, owner_id: int) -> BoxResetResult:
    """Удаляет операции/бюджеты/категории у всех, кроме владельца, и пересевает коробку.

    Возвращает число затронутых пользователей и суммарно удалённых операций.
    Владелец (``owner_id``) не трогается. Вынесено отдельно — тестируется без бота.
    """
    targets = await _list_box_targets(session, owner_id)
    total_tx = 0
    for u in targets:
        ntx = (
            await session.execute(
                select(func.count()).select_from(Transaction).where(Transaction.user_id == u.id)
            )
        ).scalar() or 0
        total_tx += ntx
        await session.execute(delete(Transaction).where(Transaction.user_id == u.id))
        await session.execute(delete(Budget).where(Budget.user_id == u.id))
        await session.execute(delete(Category).where(Category.user_id == u.id))
        await session.flush()
        await seed_categories(session, u.id)
    await session.commit()
    return BoxResetResult(count=len(targets), total_tx=total_tx)


@router.message(Command("reset_box"))
async def cmd_reset_box(message: Message, command: CommandObject) -> None:
    """Владелец: /reset_box [confirm] — сбросить всех, кроме себя, до нейтральной коробки."""
    if not _is_owner(message):
        return  # скрытая команда — не-владельцу молчим

    owner = settings.owner_telegram_id
    if not owner:
        await message.answer("⛔ OWNER_TELEGRAM_ID не задан — отказ (иначе сбросило бы всех).")
        return

    confirm = (command.args or "").strip().lower() == "confirm"

    async with async_session() as session:
        if not confirm:
            targets = await _list_box_targets(session, owner)
            if not targets:
                await message.answer("Некого сбрасывать — кроме тебя в базе никого нет.")
                return
            shown = targets[:30]
            lines = "\n".join(
                f"• {u.name or ('user ' + str(u.id))} (tg {u.telegram_id})" for u in shown
            )
            more = f"\n…и ещё {len(targets) - len(shown)}" if len(targets) > len(shown) else ""
            await message.answer(
                f"⚠️ Сброс до нейтральной «коробки» затронет <b>{len(targets)}</b> польз. (кроме тебя):\n"
                f"{lines}{more}\n\n"
                "Удалит их операции и бюджеты, категории пересоздаст из коробки.\n"
                "Подтверди: <code>/reset_box confirm</code>"
            )
            return

        result = await reset_non_owner_to_box(session, owner)

    if result.count == 0:
        await message.answer("Некого сбрасывать — кроме тебя в базе никого нет.")
    else:
        await message.answer(
            f"✅ Сброшено пользователей: <b>{result.count}</b> "
            f"(удалено операций: {result.total_tx}). Твои данные не тронуты.\n"
            "Теперь у них — нейтральная «коробка»."
        )
