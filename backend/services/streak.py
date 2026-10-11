"""Серия «Финансовая серия» (геймификация G1).

Единица привычки — **«день закрыт»**: за локальный день пользователя внесена хотя бы одна
операция (любая: доход, расход, платёж, цель, долг, досрочка — из приложения или бота) ИЛИ
нажата «Трат не было» (``DailyActivity.source='manual_empty'``) ИЛИ день спасла заморозка
(``source='freeze'``, G4). Сумма и «уложился ли в лимит» — НЕ критерий (это G2).

Серия не хранится, а **считается** из этих данных. Так её не нужно вешать на каждую точку
ввода и она не может «разойтись» с реальностью. «Локальный день» — по ``User.timezone``
(дата операции берётся из ``created_at`` — момент ввода, а не пользовательская ``date``:
иначе можно было бы «закрывать» прошлые дни задним числом).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

try:  # zoneinfo — stdlib с Python 3.9
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import DailyActivity, Transaction, User

# Глубина просмотра истории для подсчёта серии/рекорда.
WINDOW_DAYS = 400
DEFAULT_TZ = "Europe/Moscow"


def get_tz(user: User):
    """Часовой пояс пользователя; при сбое — Москва, затем UTC."""
    if ZoneInfo is not None:
        for name in (user.timezone, DEFAULT_TZ):
            try:
                return ZoneInfo(name)
            except Exception:  # noqa: BLE001
                continue
    return timezone.utc


def local_now(user: User, now_utc: Optional[datetime] = None) -> datetime:
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc.astimezone(get_tz(user))


def local_today(user: User, now_utc: Optional[datetime] = None) -> date:
    return local_now(user, now_utc).date()


def _to_local_date(created_at: datetime, tz) -> date:
    """created_at хранится как наивный UTC (server_default now()) → локальная дата."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(tz).date()


async def closed_days(
    session: AsyncSession, user: User, today: Optional[date] = None
) -> dict[date, str]:
    """Все закрытые локальные дни за окно: ``{дата: источник}`` (tx | manual_empty | freeze)."""
    tz = get_tz(user)
    today = today or local_today(user)
    since_date = today - timedelta(days=WINDOW_DAYS)
    since_dt = datetime.combine(since_date, datetime.min.time()) - timedelta(days=1)

    days: dict[date, str] = {}
    rows = await session.execute(
        select(Transaction.created_at).where(
            Transaction.user_id == user.id, Transaction.created_at >= since_dt
        )
    )
    for (created_at,) in rows.all():
        if created_at is not None:
            days[_to_local_date(created_at, tz)] = "tx"

    marks = await session.execute(
        select(DailyActivity.local_date, DailyActivity.source).where(
            DailyActivity.user_id == user.id, DailyActivity.local_date >= since_date
        )
    )
    for local_date, source in marks.all():
        days.setdefault(local_date, source)  # операция «сильнее» ручной отметки
    return days


def run_ending_at(closed: set[date], end: date) -> int:
    """Длина непрерывной цепочки закрытых дней, заканчивающейся датой ``end``."""
    n, d = 0, end
    while d in closed:
        n += 1
        d -= timedelta(days=1)
    return n


def longest_run(closed: set[date]) -> int:
    starts = [d for d in closed if (d - timedelta(days=1)) not in closed]  # начала цепочек
    return max((_run_from(closed, d) for d in starts), default=0)


def _run_from(closed: set[date], start: date) -> int:
    n, d = 0, start
    while d in closed:
        n += 1
        d += timedelta(days=1)
    return n


@dataclass
class StreakInfo:
    current: int
    longest: int
    closed_today: bool
    at_risk: bool          # серия идёт, но сегодня день ещё не закрыт
    today: date
    last_closed: Optional[date]


def compute(closed_map: dict[date, str], today: date, stored_longest: int = 0) -> StreakInfo:
    """Серия и рекорд по множеству закрытых дней."""
    closed = set(closed_map)
    closed_today = today in closed
    # Если сегодня ещё не закрыт — серия «держится» до конца дня по вчерашнему.
    anchor = today if closed_today else today - timedelta(days=1)
    current = run_ending_at(closed, anchor)
    longest = max(stored_longest, longest_run(closed), current)
    return StreakInfo(
        current=current,
        longest=longest,
        closed_today=closed_today,
        at_risk=current > 0 and not closed_today,
        today=today,
        last_closed=max(closed) if closed else None,
    )


async def get_streak(session: AsyncSession, user: User) -> StreakInfo:
    """Серия пользователя; рекорд при росте сохраняется в ``users.longest_streak``."""
    today = local_today(user)
    info = compute(await closed_days(session, user, today), today, user.longest_streak or 0)
    if info.longest > (user.longest_streak or 0):
        user.longest_streak = info.longest
        await session.commit()
    return info


async def close_day_manual(session: AsyncSession, user: User) -> StreakInfo:
    """«Трат не было»: закрывает сегодняшний локальный день без операции (идемпотентно)."""
    today = local_today(user)
    existing = await closed_days(session, user, today)
    if today not in existing:
        session.add(DailyActivity(user_id=user.id, local_date=today, source="manual_empty"))
        await session.commit()
    return await get_streak(session, user)


def broken_run_length(closed_map: dict[date, str], today: date) -> int:
    """Если серия оборвалась ВЧЕРА (вчера не закрыт, позавчера закрыт) — длина оборвавшейся серии, иначе 0."""
    closed = set(closed_map)
    if today in closed or (today - timedelta(days=1)) in closed:
        return 0
    return run_ending_at(closed, today - timedelta(days=2))
