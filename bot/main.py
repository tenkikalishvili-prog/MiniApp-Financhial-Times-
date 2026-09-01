"""Точка запуска бота.

Запуск (из папки app/):
    python -m bot.main
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from backend.config import settings
from backend.db import init_db
from bot.handlers import (
    add_transaction,
    admin,
    notifications,
    receipt,
    smart_add,
    start,
    voice,
)
from bot.scheduler import TIMEZONE, setup_scheduler


async def _set_commands(bot: Bot) -> None:
    """Меню команд, которое показывает Telegram по кнопке «/»."""
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск и меню"),
            BotCommand(command="limit", description="☀️ Лимит на сегодня"),
            BotCommand(command="day", description="🌙 Итоги дня"),
        ]
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Создаём таблицы в БД, если их ещё нет
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(admin.router)  # скрытые команды владельца (/reset_onboarding) — до мастера
    dp.include_router(notifications.router)  # команды /limit, /day — до мастера ввода
    dp.include_router(add_transaction.router)
    dp.include_router(receipt.router)  # S7: фото чека → операция (F.photo, не задевает текст)
    dp.include_router(voice.router)  # S6: голос → операция (F.voice, не задевает текст)
    # Умный ввод (свободный текст) — ПОСЛЕДНИМ: его catch-all по тексту не должен
    # перехватывать кнопку меню и шаги мастера (их роутеры идут раньше).
    dp.include_router(smart_add.router)

    await _set_commands(bot)
    setup_scheduler(bot)
    logging.info(
        "Бот запущен. Уведомления — почасовой тик, время у каждого своё (опорный пояс %s).",
        TIMEZONE,
    )
    logging.info("Ожидаю сообщения…")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
