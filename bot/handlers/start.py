"""Команда /start: регистрирует пользователя (с засевом категорий) и показывает меню."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from backend.db import async_session
from backend.services.users import get_or_create_user
from bot.keyboards import main_menu_kb

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name or "",
        )

    greeting = (
        f"Привет, {user.name or 'друг'}! 👋\n\n"
        "Это <b>Financial Times</b> — помощник по личным финансам.\n"
        "Нажми «➕ Добавить операцию», чтобы записать доход, расход или долг."
    )
    await message.answer(greeting, reply_markup=main_menu_kb())
