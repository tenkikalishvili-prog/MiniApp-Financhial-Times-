"""Планировщик уведомлений: утренний лимит и вечерняя сводка по времени.

MVP: время и часовой пояс — константы. Позже вынесем в настройки пользователя.
"""

from __future__ import annotations

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.handlers.notifications import send_evening_all, send_morning_all

TIMEZONE = "Europe/Moscow"
MORNING_HOUR = 9
EVENING_HOUR = 23


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Создаёт и запускает планировщик с утренней и вечерней рассылками."""
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        send_morning_all,
        CronTrigger(hour=MORNING_HOUR, minute=0, timezone=TIMEZONE),
        args=[bot],
        id="morning_limit",
        replace_existing=True,
    )
    scheduler.add_job(
        send_evening_all,
        CronTrigger(hour=EVENING_HOUR, minute=0, timezone=TIMEZONE),
        args=[bot],
        id="evening_summary",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
