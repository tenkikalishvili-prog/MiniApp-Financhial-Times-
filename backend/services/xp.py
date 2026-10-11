"""XP и «Финансовый уровень» (геймификация G3).

XP награждает ДИСЦИПЛИНУ, а не суммы: ведение учёта, закрытые дни, дни «в рамках» дневного
ориентира, платежи/взносы. Как и серия, XP **выводится из истории** (операции, отметки дней,
бюджет), а не хранится по событиям: нечему «разойтись» с реальностью, а правка/удаление
операции сразу пересчитывает очки. Хранится только ``users.xp_spent`` — потрачено XP (G4:
заморозки). Баланс = заработано − потрачено; уровень зависит от заработанного.

Анти-абуз (дневные капы): за день не больше ``OPS_CAP`` обычных операций и ``EVENTS_CAP``
«дисциплинарных» событий приносят XP; день закрывается/«в рамках» только один раз в сутки.
Сутки — по ``User.timezone`` (по ``created_at``, момент ввода — как у серии).
"""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import DailyActivity, Transaction, User
from backend.services import dayring as dayring_svc
from backend.services import streak as streak_svc
from backend.services.limits import _discretionary_budget, _discretionary_ids

XP_OP = 5            # обычная операция (кап за день — OPS_CAP)
XP_EVENT = 8         # платёж по долгу / пополнение цели / оплата платежа / досрочка
XP_DAY_CLOSED = 10   # день закрыт (операция или «Трат не было»)
XP_DAY_WITHIN = 15   # день закрыт и в рамках дневного ориентира «Трат»
OPS_CAP = 3
EVENTS_CAP = 5

# Названия уровней; дальше 10-го — «Легенда» с продолжением нумерации.
LEVEL_NAMES = [
    "Новичок", "Ученик", "Бережливый", "Планировщик", "Стратег",
    "Мастер бюджета", "Эксперт", "Финансист", "Гуру", "Легенда",
]


def xp_for_level(level: int) -> int:
    """Накопленный XP, с которого начинается уровень: 1→0, 2→100, 3→300, 4→600, 5→1000…"""
    return 50 * level * (level - 1)


def level_for_xp(xp: int) -> int:
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


def level_name(level: int) -> str:
    return LEVEL_NAMES[min(level, len(LEVEL_NAMES)) - 1]


@dataclass
class XpInfo:
    earned: int          # заработано всего
    spent: int           # потрачено (заморозки)
    balance: int         # доступно для трат
    today: int           # заработано сегодня
    level: int
    level_name: str
    level_start: int     # XP, с которого начался текущий уровень
    next_at: int         # XP, на котором наступит следующий уровень
    progress: int        # прогресс внутри уровня, %


class FreezeError(Exception):
    """Покупка заморозки невозможна: ``max_freezes`` | ``not_enough_xp``."""


async def buy_freeze(session: AsyncSession, user: User) -> None:
    """Покупка заморозки серии за XP (G4). Не больше ``MAX_FREEZES``; XP списывается из баланса."""
    if (user.streak_freezes or 0) >= streak_svc.MAX_FREEZES:
        raise FreezeError("max_freezes")
    info = await compute_xp(session, user)
    if info.balance < streak_svc.FREEZE_COST_XP:
        raise FreezeError("not_enough_xp")
    user.xp_spent = (user.xp_spent or 0) + streak_svc.FREEZE_COST_XP
    user.streak_freezes = (user.streak_freezes or 0) + 1
    await session.commit()


def _is_event(tx_row) -> bool:
    """Платёж / взнос: оплата платежа, возврат долга, пополнение цели, досрочка кредита."""
    source, goal_id, debt_id, debt_role, credit_id = tx_row
    return bool(
        source == "bill"
        or goal_id is not None
        or (debt_id is not None and debt_role == "payment")
        or credit_id is not None
    )


async def compute_xp(session: AsyncSession, user: User) -> XpInfo:
    tz = streak_svc.get_tz(user)
    today = streak_svc.local_today(user)

    # Операции по локальным дням: считаем обычные и «дисциплинарные» отдельно.
    ops: dict[date, int] = defaultdict(int)
    events: dict[date, int] = defaultdict(int)
    closed: set[date] = set()
    rows = await session.execute(
        select(
            Transaction.created_at,
            Transaction.source,
            Transaction.goal_id,
            Transaction.debt_id,
            Transaction.debt_role,
            Transaction.credit_id,
        ).where(Transaction.user_id == user.id)
    )
    for created_at, *flags in rows.all():
        if created_at is None:
            continue
        day = streak_svc._to_local_date(created_at, tz)
        closed.add(day)
        if _is_event(tuple(flags)):
            events[day] += 1
        else:
            ops[day] += 1

    marks = await session.execute(
        select(DailyActivity.local_date).where(
            DailyActivity.user_id == user.id, DailyActivity.source != "freeze"
        )
    )
    closed.update(d for (d,) in marks.all())

    # «В рамках»: траты по группе «Траты» за день ≤ дневного ориентира (нужен бюджет).
    within_days: set[date] = set()
    ids = await _discretionary_ids(session, user.id)
    budget = await _discretionary_budget(session, user.id, ids)
    if budget > 0 and closed:
        spent = await dayring_svc._spent_by_day(
            session, user.id, ids, min(closed), today + timedelta(days=1)
        )
        for day in closed:
            target = (budget / monthrange(day.year, day.month)[1]).quantize(Decimal("1"))
            if spent.get(day, Decimal("0")) <= target:
                within_days.add(day)

    per_day: dict[date, int] = {}
    for day in closed:
        xp = XP_DAY_CLOSED
        xp += XP_OP * min(ops.get(day, 0), OPS_CAP)
        xp += XP_EVENT * min(events.get(day, 0), EVENTS_CAP)
        if day in within_days:
            xp += XP_DAY_WITHIN
        per_day[day] = xp

    earned = sum(per_day.values())
    spent_xp = user.xp_spent or 0
    level = level_for_xp(earned)
    start, nxt = xp_for_level(level), xp_for_level(level + 1)
    progress = int((earned - start) * 100 / (nxt - start))
    return XpInfo(
        earned=earned,
        spent=spent_xp,
        balance=max(0, earned - spent_xp),
        today=per_day.get(today, 0),
        level=level,
        level_name=level_name(level),
        level_start=start,
        next_at=nxt,
        progress=progress,
    )
