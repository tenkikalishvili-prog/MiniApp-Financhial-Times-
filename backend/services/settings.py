"""Настройки пользователя: сейчас — уведомления бота (утро/вечер).

Храним прямо в модели User (как и поля онбординга) — отдельная таблица Settings
пока избыточна. Час указывается по часовому поясу пользователя (User.timezone).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import User


@dataclass
class NotificationSettings:
    timezone: str
    morning_enabled: bool
    morning_hour: int
    evening_enabled: bool
    evening_hour: int
    reminders_enabled: bool


def get_notification_settings(user: User) -> NotificationSettings:
    """Текущие настройки уведомлений пользователя."""
    return NotificationSettings(
        timezone=user.timezone,
        morning_enabled=user.morning_enabled,
        morning_hour=user.morning_hour,
        evening_enabled=user.evening_enabled,
        evening_hour=user.evening_hour,
        reminders_enabled=user.reminders_enabled,
    )


def _clamp_hour(hour: Optional[int]) -> Optional[int]:
    if hour is None:
        return None
    return max(0, min(23, int(hour)))


def is_valid_timezone(tz_name: str) -> bool:
    """Проверяет, что строка — существующий часовой пояс IANA (напр. Europe/Moscow)."""
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz_name)
        return True
    except Exception:  # noqa: BLE001  (нет zoneinfo / неизвестная зона)
        return False


async def update_notification_settings(
    session: AsyncSession,
    user: User,
    timezone: Optional[str] = None,
    morning_enabled: Optional[bool] = None,
    morning_hour: Optional[int] = None,
    evening_enabled: Optional[bool] = None,
    evening_hour: Optional[int] = None,
    reminders_enabled: Optional[bool] = None,
) -> User:
    """Обновляет настройки уведомлений. Любое поле можно пропустить (None → не меняем).

    Часы приводятся к диапазону 0–23. Часовой пояс должен быть провалидирован вызывающим
    (см. is_valid_timezone). Изменения подхватывает планировщик бота (он читает время и
    пояс из БД на каждом часовом тике — рестарт не нужен).
    """
    if timezone is not None:
        user.timezone = timezone
    if morning_enabled is not None:
        user.morning_enabled = morning_enabled
    if morning_hour is not None:
        user.morning_hour = _clamp_hour(morning_hour)
    if evening_enabled is not None:
        user.evening_enabled = evening_enabled
    if evening_hour is not None:
        user.evening_hour = _clamp_hour(evening_hour)
    if reminders_enabled is not None:
        user.reminders_enabled = reminders_enabled
    await session.commit()
    await session.refresh(user)
    return user
