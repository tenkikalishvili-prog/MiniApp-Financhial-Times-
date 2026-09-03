"""Утренний лимит и вечерняя сводка: текст, команды по запросу и рассылка по расписанию."""

from __future__ import annotations

import logging
from datetime import date, datetime

try:  # zoneinfo — stdlib с Python 3.9; данные тайзон берём из пакета tzdata
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import Message

from backend.db import async_session
from backend.models import User
from backend.services.limits import (
    DailyLimit,
    EveningSummary,
    get_daily_limit,
    get_evening_summary,
)
from backend.services.reminders import build_reminders
from backend.services.users import get_all_users, get_or_create_user

router = Router()


def _now_in_tz(tz_name: str) -> datetime:
    """Текущее локальное время пользователя. При сбое тайзоны — как есть (сервер)."""
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:  # noqa: BLE001  (неизвестная зона / нет tzdata)
            logging.warning("Неизвестный часовой пояс %r — беру время сервера", tz_name)
    return datetime.now()

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


@router.message(Command("reminders"))
async def cmd_reminders(message: Message) -> None:
    """Напоминания о платежах и долгах по запросу (и для проверки до утреннего часа)."""
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name or "",
        )
        text = await build_reminders(session, user.id, _now_in_tz(user.timezone).date())
    await message.answer(text or "🔔 Ближайших платежей и долгов нет — всё под контролем.")


# ─── Рассылка по расписанию (вызывается планировщиком) ──────────────────

async def _safe_send(bot: Bot, telegram_id: int, text: str, tag: str) -> bool:
    """Шлёт сообщение, гася ошибки. True — доставлено, False — заблокирован/ошибка."""
    try:
        await bot.send_message(telegram_id, text)
        return True
    except TelegramForbiddenError:
        logging.info("%s: пользователь %s заблокировал бота", tag, telegram_id)
        return False
    except Exception as error:  # noqa: BLE001
        logging.warning("%s: не отправлено %s: %s", tag, telegram_id, error)
        return False


async def broadcast(bot: Bot, users: list[User], text: str, tag: str = "Рассылка") -> tuple[int, int]:
    """Рассылает один и тот же текст всем users.

    Возвращает (доставлено, не доставлено). Заблокировавшие бота и ошибки
    отправки не роняют рассылку — просто попадают во второе число.
    """
    delivered = 0
    for user in users:
        if await _safe_send(bot, user.telegram_id, text, tag):
            delivered += 1
    return delivered, len(users) - delivered


async def _send_morning(bot: Bot, session, user: User, on_date: date) -> None:
    daily = await get_daily_limit(session, user.id, on_date)
    await _safe_send(bot, user.telegram_id, format_morning(daily), "Утро")


async def _send_evening(bot: Bot, session, user: User, on_date: date) -> None:
    summary = await get_evening_summary(session, user.id, on_date)
    await _safe_send(bot, user.telegram_id, format_evening(summary, on_date), "Вечер")


async def _send_reminders(bot: Bot, session, user: User, on_date: date) -> None:
    """Шлёт напоминание о платежах/долгах, если есть о чём (иначе молчит)."""
    text = await build_reminders(session, user.id, on_date)
    if text:
        await _safe_send(bot, user.telegram_id, text, "Напоминания")


async def send_due_notifications(bot: Bot) -> None:
    """Почасовой тик: шлём тем, у кого настроенный час совпал с их локальным.

    Планировщик зовёт эту функцию в начале каждого часа. Время каждого
    пользователя (вкл/выкл и час) читается из БД здесь же — правки в настройках
    подхватываются без перезапуска бота.
    """
    async with async_session() as session:
        users = await get_all_users(session)
        for user in users:
            now = _now_in_tz(user.timezone)
            on_date = now.date()
            if user.morning_enabled and now.hour == user.morning_hour:
                await _send_morning(bot, session, user, on_date)
            # Напоминания о платежах/долгах — раз в день в утренний час (свой тумблер).
            if user.reminders_enabled and now.hour == user.morning_hour:
                await _send_reminders(bot, session, user, on_date)
            if user.evening_enabled and now.hour == user.evening_hour:
                await _send_evening(bot, session, user, on_date)
