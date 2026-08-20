"""Утренний лимит и вечерняя сводка: текст, команды по запросу и рассылка по расписанию."""

from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import Message

from backend.db import async_session
from backend.services.limits import (
    DailyLimit,
    EveningSummary,
    get_daily_limit,
    get_evening_summary,
)
from backend.services.users import get_all_users, get_or_create_user

router = Router()

STATUS_TEXT = {
    "good": ("🟢", "День прошёл хорошо"),
    "medium": ("🟡", "Средне — чуть выше дневного ориентира"),
    "bad": ("🔴", "Перерасход по повседневным тратам"),
}


def _fmt(amount) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def format_morning(dl: DailyLimit) -> str:
    if not dl.has_budget:
        return (
            "☀️ <b>Доброе утро!</b>\n"
            "По повседневным тратам ещё не задан бюджет — дневной лимит посчитать не из чего.\n"
            "Задать лимиты можно будет в приложении."
        )
    if dl.remaining <= 0:
        return (
            "☀️ <b>Доброе утро!</b>\n"
            f"🔴 Бюджет на повседневные траты в этом месяце уже исчерпан "
            f"(потрачено {_fmt(dl.spent_month)} ₽ из {_fmt(dl.monthly_budget)} ₽).\n"
            "Сегодня лучше не тратить."
        )
    return (
        "☀️ <b>Доброе утро!</b>\n"
        f"Сегодня можно потратить ≈ <b>{_fmt(dl.per_day)} ₽</b> на повседневные траты.\n\n"
        f"📅 До конца месяца: {dl.days_left} дн.\n"
        f"💰 Осталось {_fmt(dl.remaining)} ₽ из {_fmt(dl.monthly_budget)} ₽ "
        f"(потрачено {_fmt(dl.spent_month)} ₽)"
    )


def format_evening(es: EveningSummary, on_date: date) -> str:
    if not es.has_budget:
        return (
            f"🌙 <b>Итоги дня — {on_date:%d.%m.%Y}</b>\n"
            f"Потрачено сегодня: <b>{_fmt(es.today_spent)} ₽</b>\n"
            "Бюджет по повседневным тратам не задан — оценку дня не считаю."
        )
    emoji, verdict = STATUS_TEXT[es.status]
    return (
        f"🌙 <b>Итоги дня — {on_date:%d.%m.%Y}</b>\n"
        f"Потрачено сегодня: <b>{_fmt(es.today_spent)} ₽</b>\n"
        f"Дневной ориентир: ≈{_fmt(es.daily_target)} ₽\n"
        f"{emoji} {verdict}\n\n"
        f"За месяц: {_fmt(es.spent_month)} из {_fmt(es.monthly_budget)} ₽"
    )


# ─── Команды по запросу (и для проверки, не дожидаясь 9:00 / 23:00) ──────

@router.message(Command("limit"))
async def cmd_limit(message: Message) -> None:
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name or "",
        )
        daily = await get_daily_limit(session, user.id)
    await message.answer(format_morning(daily))


@router.message(Command("day"))
async def cmd_day(message: Message) -> None:
    today = date.today()
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name or "",
        )
        summary = await get_evening_summary(session, user.id, today)
    await message.answer(format_evening(summary, today))


# ─── Рассылка по расписанию (вызывается планировщиком) ──────────────────

async def send_morning_all(bot: Bot) -> None:
    async with async_session() as session:
        users = await get_all_users(session)
        for user in users:
            daily = await get_daily_limit(session, user.id)
            text = format_morning(daily)
            try:
                await bot.send_message(user.telegram_id, text)
            except TelegramForbiddenError:
                logging.info("Утро: пользователь %s заблокировал бота", user.telegram_id)
            except Exception as error:  # noqa: BLE001
                logging.warning("Утро: не отправлено %s: %s", user.telegram_id, error)


async def send_evening_all(bot: Bot) -> None:
    today = date.today()
    async with async_session() as session:
        users = await get_all_users(session)
        for user in users:
            summary = await get_evening_summary(session, user.id, today)
            text = format_evening(summary, today)
            try:
                await bot.send_message(user.telegram_id, text)
            except TelegramForbiddenError:
                logging.info("Вечер: пользователь %s заблокировал бота", user.telegram_id)
            except Exception as error:  # noqa: BLE001
                logging.warning("Вечер: не отправлено %s: %s", user.telegram_id, error)
