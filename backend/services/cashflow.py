"""Платёжный календарь (направление D, S16).

Отвечает на вопрос «сколько нужно заплатить до и после даты дохода» для ТЕКУЩЕГО
месяца. Граница отрезков G = день поступления самого крупного планового дохода
(тай-брейк — более ранний день). Всегда два отрезка «до G» и «после G» + отдельная
корзина «Просрочено» (неоплаченное за прошлый месяц и обязательства этого месяца,
чей день уже прошёл). Долги — только ``owe`` со сроком внутри текущего месяца.

Расчёт — read-only агрегат поверх ``bills``/``bill_marks``/``debts``/``categories``.
Ручной перенос платежа/долга между отрезками — постоянный флаг ``segment_override``
(1|2|NULL); на просрочку он не влияет. Долгосрочные долги — открытый вопрос 7.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Bill, BillMark, Category, Debt, User


def _user_today(tz_name: Optional[str]) -> date:
    """«Сегодня» в часовом поясе пользователя (fallback — локальная дата сервера)."""
    if tz_name:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz_name)).date()
        except Exception:  # noqa: BLE001  (нет zoneinfo / неизвестная зона)
            pass
    return date.today()


def _prev_period(year: int, month: int) -> str:
    """'YYYY-MM' предыдущего месяца."""
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def _clamp_day(day: int, year: int, month: int) -> int:
    """Число-срок → фактический день месяца (клампим к последнему, напр. 31 → 30)."""
    last = monthrange(year, month)[1]
    return min(max(day, 1), last)


@dataclass
class CashflowItem:
    kind: str                        # 'bill' | 'debt'
    id: int
    title: str
    emoji: Optional[str]
    day: int
    amount: Decimal
    category_name: Optional[str] = None
    counterparty: Optional[str] = None
    override: Optional[int] = None    # segment_override: 1 | 2 | None (авто)

    @property
    def overridden(self) -> bool:
        return self.override in (1, 2)


@dataclass
class CashflowIncome:
    name: str
    group: str
    emoji: Optional[str]
    day: int
    amount: Decimal


@dataclass
class CashflowSegment:
    index: int
    label: str
    boundary_day: Optional[int]
    expected_income: Decimal = Decimal("0")
    obligations: Decimal = Decimal("0")
    items: list[CashflowItem] = field(default_factory=list)

    @property
    def coverage(self) -> Decimal:
        return self.expected_income - self.obligations


@dataclass
class CashflowPlan:
    month: str
    today: date
    boundary_day: Optional[int]
    incomes: list[CashflowIncome]
    overdue_items: list[CashflowItem]
    segments: list[CashflowSegment]

    @property
    def overdue_total(self) -> Decimal:
        return sum((it.amount for it in self.overdue_items), Decimal("0"))


async def build_plan(session: AsyncSession, user: User) -> CashflowPlan:
    """Собирает платёжный календарь пользователя за текущий месяц (в его tz)."""
    today = _user_today(user.timezone)
    year, month = today.year, today.month
    period = f"{year:04d}-{month:02d}"

    # ── 1. Плановые доходы и граница G ────────────────────────────────────
    inc_rows = await session.execute(
        select(Category).where(
            Category.user_id == user.id,
            Category.article == "income",
            Category.is_archived == False,  # noqa: E712
            Category.expected_day.is_not(None),
            Category.expected_amount.is_not(None),
        )
    )
    incomes: list[CashflowIncome] = []
    for c in inc_rows.scalars().all():
        day = _clamp_day(int(c.expected_day), year, month)
        incomes.append(
            CashflowIncome(
                name=c.name,
                group=c.group,
                emoji=c.emoji,
                day=day,
                amount=Decimal(str(c.expected_amount)),
            )
        )
    incomes.sort(key=lambda i: (i.day, -float(i.amount)))

    boundary_day: Optional[int] = None
    if incomes:
        # G = день дохода с максимальной суммой; тай-брейк — меньший день.
        top = max(incomes, key=lambda i: (i.amount, -i.day))
        boundary_day = top.day

    # ── 2. Обязательства текущего месяца ──────────────────────────────────
    bills = (
        await session.execute(
            select(Bill).where(Bill.user_id == user.id, Bill.is_active.is_(True))
        )
    ).scalars().all()
    marks = {
        m.bill_id
        for m in (
            await session.execute(
                select(BillMark).where(
                    BillMark.user_id == user.id, BillMark.period == period
                )
            )
        ).scalars().all()
    }
    prev_marks = {
        m.bill_id
        for m in (
            await session.execute(
                select(BillMark).where(
                    BillMark.user_id == user.id,
                    BillMark.period == _prev_period(year, month),
                )
            )
        ).scalars().all()
    }

    cats = {
        c.id: c
        for c in (
            await session.execute(
                select(Category).where(Category.user_id == user.id)
            )
        ).scalars().all()
    }

    overdue: list[CashflowItem] = []
    current: list[CashflowItem] = []  # непросроченные обязательства текущего месяца

    for b in bills:
        cat = cats.get(b.category_id)
        day = _clamp_day(int(b.due_day), year, month)
        item = CashflowItem(
            kind="bill",
            id=b.id,
            title=b.title,
            emoji=cat.emoji if cat else None,
            day=day,
            amount=Decimal(str(b.amount)),
            category_name=cat.name if cat else None,
            override=b.segment_override if b.segment_override in (1, 2) else None,
        )
        # Просрочка за прошлый месяц: активный платёж, не отмеченный в M−1.
        if b.id not in prev_marks:
            prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
            prev_day = _clamp_day(int(b.due_day), prev_year, prev_month)
            overdue.append(
                CashflowItem(
                    kind="bill",
                    id=b.id,
                    title=b.title,
                    emoji=cat.emoji if cat else None,
                    day=prev_day,
                    amount=Decimal(str(b.amount)),
                    category_name=cat.name if cat else None,
                    override=None,  # просрочку override не переносит
                )
            )
        # Текущий месяц: пропускаем уже оплаченные.
        if b.id in marks:
            continue
        if day < today.day:
            overdue.append(item)  # день этого месяца уже прошёл
        else:
            current.append(item)

    debts = (
        await session.execute(
            select(Debt).where(
                Debt.user_id == user.id,
                Debt.direction == "owe",
                Debt.is_closed.is_(False),
                Debt.due_date.is_not(None),
            )
        )
    ).scalars().all()
    for d in debts:
        remaining = Decimal(str(d.amount)) - Decimal(str(d.paid))
        if remaining <= 0:
            continue
        due = d.due_date
        if due.year != year or due.month != month:
            continue  # срок вне текущего месяца — вне блока (открытый вопрос 7)
        item = CashflowItem(
            kind="debt",
            id=d.id,
            title=d.counterparty,
            emoji="🤝",
            day=due.day,
            amount=remaining,
            counterparty=d.counterparty,
            override=d.segment_override if d.segment_override in (1, 2) else None,
        )
        if due.day < today.day:
            overdue.append(item)
        else:
            current.append(item)

    # ── 3. Раскладка непросроченных обязательств по отрезкам ──────────────
    seg1 = CashflowSegment(index=1, label="До поступления дохода", boundary_day=boundary_day)
    seg2 = CashflowSegment(index=2, label="После поступления дохода", boundary_day=boundary_day)

    if boundary_day is None:
        # Нет доходов с датой — один список «Весь месяц» (кладём в seg1, seg2 пуст).
        seg1.label = "Весь месяц"
        for it in current:
            seg1.items.append(it)
            seg1.obligations += it.amount
    else:
        for it in current:
            if it.override == 1:
                seg = seg1
            elif it.override == 2:
                seg = seg2
            else:
                seg = seg1 if it.day <= boundary_day else seg2
            seg.items.append(it)
            seg.obligations += it.amount
        seg1.expected_income = sum(
            (i.amount for i in incomes if i.day <= boundary_day), Decimal("0")
        )
        seg2.expected_income = sum(
            (i.amount for i in incomes if i.day > boundary_day), Decimal("0")
        )

    for seg in (seg1, seg2):
        seg.items.sort(key=lambda it: (it.day, it.title))
    overdue.sort(key=lambda it: (it.day, it.title))

    segments = [seg1] if boundary_day is None else [seg1, seg2]
    return CashflowPlan(
        month=period,
        today=today,
        boundary_day=boundary_day,
        incomes=incomes,
        overdue_items=overdue,
        segments=segments,
    )
