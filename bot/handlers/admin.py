"""Скрытые админ-команды (только для владельца). В меню команд НЕ показываются.

Сейчас: /reset_onboarding <telegram_id> — сбросить флаг онбординга пользователю,
чтобы при следующем входе снова показался мастер первого входа. Удобно, чтобы
самому пройти онбординг на тест-аккаунте, не трогая боевую БД снаружи.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from sqlalchemy import select

from backend.config import settings
from backend.db import async_session
from backend.models import User

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
