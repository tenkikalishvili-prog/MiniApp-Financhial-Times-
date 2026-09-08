"""Платёжный календарь (направление D, S16 → V2 S16.1).

Отвечает на вопрос «хватит ли денег в каждой половине месяца на её платежи».
Месяц ВСЕГДА делится по 15-му числу на две плитки-отрезка:

* отрезок ①  «Оплатить до 15-го»          — дни 1…15;
* отрезок ②  «Оплатить до конца месяца»    — дни 16…конец.

Плановый доход (income-подкатегория) может приходить НЕСКОЛЬКО раз в месяц
(``categories.income_schedule`` = ``[{"day", "amount"}, …]``; фолбэк — одна выплата
``expected_day``/``expected_amount``). Каждая выплата и каждый платёж/долг попадают
в свой отрезок ПО ДАТЕ.

Просрочка (неоплаченное обязательство, чей отрезок уже в прошлом) ПЕРЕНОСИТСЯ вперёд
и показывается в текущем (ближайшем незакрытом) отрезке — с пометкой «просрочен» и
исходной датой. Ручной перенос платежа между двумя половинами — постоянный флаг
``segment_override`` (1|2|NULL).

Ответ строится за КОНКРЕТНЫЙ месяц (степпер на «Аналитике»); по умолчанию — текущий
месяц в часовом поясе пользователя. Расчёт — read-only агрегат поверх
``categories``/``bills``/``bill_marks``/``debts``. «Останется» считается ИЗОЛИРОВАННО
в каждой половине (доход половины − её платежи), без переноса остатка между плитками.
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

BOUNDARY = 15  # фиксированная граница половин месяца

_MONTHS_SHORT = [
    "", "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]


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


def _clamp_day(day: int, year: int, month: int) -> int:
    """Число-срок → фактический день месяца (клампим к последнему, напр. 31 → 30)."""
    last = monthrange(year, month)[1]
    return min(max(int(day), 1), last)


def _half(day: int) -> int:
    """Половина месяца по дню: 1 (1…15) или 2 (16…конец)."""
    return 1 if day <= BOUNDARY else 2


def _half_ord(year: int, month: int, half: int) -> int:
    """Глобальный порядковый номер половины — для сравнения «раньше/позже»."""
    return (year * 12 + (month - 1)) * 2 + (half - 1)


def _origin_label(year: int, month: int, day: int) -> str:
    """Человеческая метка исходной даты просрочки, напр. «с 28 авг»."""
    return f"с {day} {_MONTHS_SHORT[month]}"


def _income_slots(cat: Category, year: int, month: int) -> list[tuple[int, Decimal]]:
    """Список выплат дохода за месяц как (день, сумма).

    Источник — ``income_schedule``; фолбэк — легаси одна выплата
    ``expected_day``/``expected_amount``. Дни клампятся к длине месяца.
    """
    slots: list[tuple[int, Decimal]] = []
    sched = cat.income_schedule if isinstance(cat.income_schedule, list) else None
    if sched:
        for row in sched:
            try:
                day = _clamp_day(int(row["day"]), year, month)
                amount = Decimal(str(row["amount"]))
            except (KeyError, TypeError, ValueError):
                continue
            if amount > 0:
                slots.append((day, amount))
    elif cat.expected_day is not None and cat.expected_amount is not None:
        slots.append((_clamp_day(int(cat.expected_day), year, month), Decimal(str(cat.expected_amount))))
    return slots


@dataclass
class CashflowItem:
    kind: str                         # 'bill' | 'debt'
    id: int
    title: str
    emoji: Optional[str]
    day: int
    amount: Decimal
    category_name: Optional[str] = None
    counterparty: Optional[str] = None
    override: Optional[int] = None     # segment_override: 1 | 2 | None (авто)
    overdue: bool = False              # просрочен и перенесён вперёд
    origin_label: Optional[str] = None  # «с 28 авг» — исходная дата, если перенесён

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
    incomes: list[CashflowIncome] = field(default_factory=list)
    items: list[CashflowItem] = field(default_factory=list)

    @property
    def expected_income(self) -> Decimal:
        return sum((i.amount for i in self.incomes), Decimal("0"))

    @property
    def obligations(self) -> Decimal:
        return sum((it.amount for it in self.items), Decimal("0"))

    @property
    def coverage(self) -> Decimal:
        return self.expected_income - self.obligations


@dataclass
class CashflowPlan:
    month: str
    today: date
    boundary_day: int
    segments: list[CashflowSegment]


def _parse_month(month: Optional[str], today: date) -> tuple[int, int]:
    """'YYYY-MM' → (год, месяц); мусор/пусто → текущий месяц пользователя."""
    if month:
        try:
            year, mon = month.split("-")
            y, m = int(year), int(mon)
            if 1 <= m <= 12:
                return y, m
        except (ValueError, AttributeError):
            pass
    return today.year, today.month


async def build_plan(
    session: AsyncSession, user: User, month: Optional[str] = None
) -> CashflowPlan:
    """Собирает две плитки платёжного календаря за месяц ``month`` (по умолчанию — текущий)."""
    today = _user_today(user.timezone)
    year, mon = _parse_month(month, today)
    period = f"{year:04d}-{mon:02d}"

    cur_half = _half(today.day)
    cur_ord = _half_ord(today.year, today.month, cur_half)
    ord1 = _half_ord(year, mon, 1)
    ord2 = _half_ord(year, mon, 2)

    seg1 = CashflowSegment(index=1, label="Оплатить до 15-го")
    seg2 = CashflowSegment(index=2, label="Оплатить до конца месяца")

    def _place(display_ord: int) -> Optional[CashflowSegment]:
        """Сегмент этого месяца по display-порядку половины (или None — вне месяца)."""
        if display_ord == ord1:
            return seg1
        if display_ord == ord2:
            return seg2
        return None

    # ── 1. Плановые доходы: раскладываем выплаты по половинам ──────────────
    inc_rows = await session.execute(
        select(Category).where(
            Category.user_id == user.id,
            Category.article == "income",
            Category.is_archived == False,  # noqa: E712
        )
    )
    for c in inc_rows.scalars().all():
        for day, amount in _income_slots(c, year, mon):
            seg = seg1 if day <= BOUNDARY else seg2
            seg.incomes.append(
                CashflowIncome(name=c.name, group=c.group, emoji=c.emoji, day=day, amount=amount)
            )

    # ── 2. Кандидаты-обязательства (bills за окно [M−1; M], debts — все открытые) ──
    cats = {
        c.id: c
        for c in (
            await session.execute(select(Category).where(Category.user_id == user.id))
        ).scalars().all()
    }
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

    def _add_item(seg: CashflowSegment, item: CashflowItem) -> None:
        # Ручной перенос между половинами ЭТОГО месяца имеет приоритет над датой.
        target = seg
        if item.override == 1:
            target = seg1
        elif item.override == 2:
            target = seg2
        target.items.append(item)

    # bills — повторяющиеся: берём только инстанс просматриваемого месяца (пустой mark
    # прошлого месяца ≠ «не оплачено», иначе дублировали бы каждый платёж). Просрочка
    # внутри месяца: день уже прошёл → переносится вперёд в текущую половину.
    for b in bills:
        if b.id in marks:
            continue  # оплачен за этот месяц — пропускаем
        cat = cats.get(b.category_id)
        nday = _clamp_day(int(b.due_day), year, mon)
        nord = _half_ord(year, mon, _half(nday))
        disp = max(nord, cur_ord)
        seg = _place(disp)
        if seg is None:
            continue
        overdue = nord < cur_ord
        _add_item(
            seg,
            CashflowItem(
                kind="bill",
                id=b.id,
                title=b.title,
                emoji=cat.emoji if cat else None,
                day=nday,
                amount=Decimal(str(b.amount)),
                category_name=cat.name if cat else None,
                override=b.segment_override if b.segment_override in (1, 2) else None,
                overdue=overdue,
                origin_label=_origin_label(year, mon, nday) if overdue else None,
            ),
        )

    # debts: все открытые «я должен» со сроком; переносятся вперёд, пока открыты
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
        nord = _half_ord(due.year, due.month, _half(due.day))
        disp = max(nord, cur_ord)
        seg = _place(disp)
        if seg is None:
            continue
        overdue = nord < cur_ord
        _add_item(
            seg,
            CashflowItem(
                kind="debt",
                id=d.id,
                title=d.counterparty,
                emoji="🤝",
                day=due.day,
                amount=remaining,
                counterparty=d.counterparty,
                override=d.segment_override if d.segment_override in (1, 2) else None,
                overdue=overdue,
                origin_label=_origin_label(due.year, due.month, due.day) if overdue else None,
            ),
        )

    # ── 3. Сортировка: просрочка сверху, дальше по дню ────────────────────
    for seg in (seg1, seg2):
        seg.incomes.sort(key=lambda i: (i.day, i.name))
        seg.items.sort(key=lambda it: (not it.overdue, it.day, it.title))

    return CashflowPlan(
        month=period,
        today=today,
        boundary_day=BOUNDARY,
        segments=[seg1, seg2],
    )
