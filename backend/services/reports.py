"""Агрегации для Mini App: месячные итоги, аналитика, строки бюджета, история.

Вся логика чтения для HTTP-API живёт здесь (в слое сервисов), чтобы роуты
оставались тонкими, а запросы — переиспользуемыми и тестируемыми.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Budget, Category, Transaction


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """(первое число месяца, первое число следующего месяца)."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


@dataclass
class MonthTotals:
    income: Decimal
    expense: Decimal


async def month_totals(
    session: AsyncSession, user_id: int, year: int, month: int
) -> MonthTotals:
    """Сумма доходов и расходов за месяц (по статье операции)."""
    start, end = month_bounds(year, month)
    result = await session.execute(
        select(
            Transaction.article,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.article.in_(("income", "expense")),
        )
        .group_by(Transaction.article)
    )
    totals = {row[0]: Decimal(str(row[1])) for row in result.all()}
    return MonthTotals(
        income=totals.get("income", Decimal("0")),
        expense=totals.get("expense", Decimal("0")),
    )


@dataclass
class GroupSlice:
    group: str
    amount: Decimal


async def expense_by_group(
    session: AsyncSession, user_id: int, year: int, month: int
) -> list[GroupSlice]:
    """Расходы за месяц, сгруппированные по категории (для donut). По убыванию."""
    start, end = month_bounds(year, month)
    result = await session.execute(
        select(
            Category.group,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .join(Category, Category.id == Transaction.category_id)
        .where(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.article == "expense",
        )
        .group_by(Category.group)
        .order_by(func.sum(Transaction.amount).desc())
    )
    return [GroupSlice(group=row[0], amount=Decimal(str(row[1]))) for row in result.all()]


@dataclass
class BudgetLine:
    category_id: int
    group: str
    name: str
    emoji: str | None
    spent: Decimal
    limit: Decimal


async def budget_lines(
    session: AsyncSession,
    user_id: int,
    year: int,
    month: int,
    group: str | None = None,
) -> list[BudgetLine]:
    """Строки бюджета: подкатегория → план (шаблон) и факт за месяц.

    Берём расходные подкатегории, у которых задан плановый бюджет (> 0).
    Если указан ``group`` — только внутри неё (напр. «Траты» для дневного лимита).
    """
    start, end = month_bounds(year, month)

    # Факт за месяц по каждой подкатегории
    spent_subq = (
        select(
            Transaction.category_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("spent"),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date < end,
        )
        .group_by(Transaction.category_id)
        .subquery()
    )

    conditions = [
        Category.user_id == user_id,
        Category.article == "expense",
        Category.is_archived == False,  # noqa: E712
        Budget.amount > 0,
        Budget.period_month.is_(None),
    ]
    if group is not None:
        conditions.append(Category.group == group)

    result = await session.execute(
        select(
            Category.id,
            Category.group,
            Category.name,
            Category.emoji,
            func.coalesce(spent_subq.c.spent, 0),
            Budget.amount,
        )
        .join(Budget, Budget.category_id == Category.id)
        .outerjoin(spent_subq, spent_subq.c.category_id == Category.id)
        .where(*conditions)
        .order_by(Category.sort_order)
    )
    return [
        BudgetLine(
            category_id=row[0],
            group=row[1],
            name=row[2],
            emoji=row[3],
            spent=Decimal(str(row[4])),
            limit=Decimal(str(row[5])),
        )
        for row in result.all()
    ]


@dataclass
class BudgetSub:
    category_id: int
    name: str
    emoji: str | None
    spent: Decimal
    limit: Decimal


@dataclass
class BudgetGroupView:
    group: str
    emoji: str | None
    spent: Decimal
    limit: Decimal
    subcategories: list[BudgetSub]


async def budget_overview(
    session: AsyncSession,
    user_id: int,
    year: int,
    month: int,
    article: str = "expense",
) -> list[BudgetGroupView]:
    """Полный бюджет для экрана «Бюджет»: ВСЕ категории → ВСЕ подкатегории.

    В отличие от ``budget_lines`` (только подкатегории с заданным лимитом, для дневного
    лимита/overview), здесь показываем и подкатегории без лимита (limit=0) — чтобы можно
    было листать все категории каруселью и задавать лимиты с нуля. Группировка — в Python
    с сохранением порядка ``sort_order``.
    """
    start, end = month_bounds(year, month)

    spent_subq = (
        select(
            Transaction.category_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("spent"),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date < end,
        )
        .group_by(Transaction.category_id)
        .subquery()
    )
    budget_subq = (
        select(Budget.category_id, Budget.amount.label("amount"))
        .where(Budget.user_id == user_id, Budget.period_month.is_(None))
        .subquery()
    )

    result = await session.execute(
        select(
            Category.id,
            Category.group,
            Category.name,
            Category.emoji,
            func.coalesce(spent_subq.c.spent, 0),
            func.coalesce(budget_subq.c.amount, 0),
        )
        .outerjoin(spent_subq, spent_subq.c.category_id == Category.id)
        .outerjoin(budget_subq, budget_subq.c.category_id == Category.id)
        .where(
            Category.user_id == user_id,
            Category.article == article,
            Category.is_archived == False,  # noqa: E712
        )
        .order_by(Category.sort_order)
    )

    groups: list[BudgetGroupView] = []
    index: dict[str, BudgetGroupView] = {}
    for cid, group, name, emoji, spent_raw, limit_raw in result.all():
        spent = Decimal(str(spent_raw))
        limit = Decimal(str(limit_raw))
        view = index.get(group)
        if view is None:
            # эмодзи группы — от первой её подкатегории (как на экране «Добавить»)
            view = BudgetGroupView(
                group=group, emoji=emoji, spent=Decimal("0"), limit=Decimal("0"), subcategories=[]
            )
            index[group] = view
            groups.append(view)
        view.subcategories.append(
            BudgetSub(category_id=cid, name=name, emoji=emoji, spent=spent, limit=limit)
        )
        view.spent += spent
        view.limit += limit
    return groups


async def recent_transactions(
    session: AsyncSession,
    user_id: int,
    limit: int = 30,
    offset: int = 0,
    year: int | None = None,
    month: int | None = None,
    article: str | None = None,
    group: str | None = None,
    query: str | None = None,
) -> list[Transaction]:
    """Операции с подгруженной категорией — для Главной и экрана «История».

    Все фильтры опциональны и комбинируются:
    - ``year``/``month`` — период (пара, иначе игнорируются);
    - ``article`` — статья (income/expense/debt);
    - ``group`` — имя категории (группы);
    - ``query`` — поиск по описанию операции и названию подкатегории;
    - ``offset``/``limit`` — постраничная выдача (сортировка: сначала новые).
    """
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .options(selectinload(Transaction.category))
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    )
    if year is not None and month is not None:
        start, end = month_bounds(year, month)
        stmt = stmt.where(Transaction.date >= start, Transaction.date < end)
    if article:
        stmt = stmt.where(Transaction.article == article)
    # Фильтр по группе и поиск требуют присоединить Category (связь 1:1 — без дублей).
    if group or query:
        stmt = stmt.join(Category, Category.id == Transaction.category_id)
    if group:
        stmt = stmt.where(Category.group == group)
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Transaction.description.ilike(pattern),
                Category.name.ilike(pattern),
            )
        )

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars())
