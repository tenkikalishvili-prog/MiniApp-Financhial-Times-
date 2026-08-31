"""Планировщик уведомлений.

Один почасовой тик (в начале каждого часа) проходит по всем пользователям и шлёт
утренний лимит / вечернюю сводку тем, у кого настроенный час совпал с их локальным
временем. Время задаётся в приложении (экран «Настройки»), хранится в БД и читается
на каждом тике — правки подхватываются без перезапуска бота.
"""

from __future__ import annotations

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.handlers.notifications import send_due_notifications

# Опорный пояс для тика «в начале часа». Границы часов совпадают у всех целых
# смещений, поэтому сам per-user час считается по User.timezone в диспетчере.
TIMEZONE = "Europe/Moscow"


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Создаёт и запускает планировщик с почасовой рассылкой уведомлений."""
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        send_due_notifications,
        CronTrigger(minute=0, timezone=TIMEZONE),
        args=[bot],
        id="hourly_notifications",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
