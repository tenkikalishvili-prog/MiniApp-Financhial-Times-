"""Эндпоинты Mini App. Тонкий слой поверх backend/services."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from backend.models import Budget, Category, Transaction
from backend.models import User
from backend.services import categories as categories_svc
from backend.services import onboarding as onboarding_svc
from backend.services import reports
from backend.services import settings as settings_svc
from backend.services import transactions as tx_svc
from backend.services.limits import DISCRETIONARY_GROUP, get_daily_limit

from .deps import CurrentUser, SessionDep
from .schemas import (
    AnalyticsOut,
    BudgetGroupViewOut,
    BudgetLineOut,
    BudgetSet,
    BudgetSubOut,
    CategoryGroupOut,
    CategoryRename,
    CreatedSubcategoryOut,
    DeleteResultOut,
    GroupDeleteResultOut,
    GroupRename,
    GroupRenameOut,
    MeOut,
    OnboardingIn,
    OverviewOut,
    SettingsOut,
    SettingsUpdate,
    SliceOut,
    SubcategoryCreate,
    SubcategoryOut,
    TopSpendOut,
    TransactionCreate,
    TransactionOut,
)

router = APIRouter(prefix="/api")


def _parse_month(month: Optional[str]) -> tuple[int, int, str]:
    """'YYYY-MM' → (year, month, 'YYYY-MM'). По умолчанию — текущий месяц."""
    today = date.today()
    if not month:
        return today.year, today.month, f"{today.year:04d}-{today.month:02d}"
    try:
        year_s, month_s = month.split("-")
        year, mon = int(year_s), int(month_s)
        if not 1 <= mon <= 12:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "month must be YYYY-MM") from exc
    return year, mon, f"{year:04d}-{mon:02d}"


def _tx_out(t: Transaction) -> TransactionOut:
    return TransactionOut(
        id=t.id,
        article=t.article,
        category_id=t.category_id,
        category_name=t.category.group if t.category else "",
        subcategory_name=t.category.name if t.category else "",
        emoji=t.category.emoji if t.category else None,
        amount=float(t.amount),
        date=t.date,
        comment=t.description,
    )


# ── Пользователь ─────────────────────────────────────────────────────────
def _me_out(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        telegram_id=user.telegram_id,
        name=user.name,
        currency=user.currency,
        theme=user.theme,
        needs_onboarding=user.onboarded_at is None,
        planned_income=float(user.monthly_income) if user.monthly_income is not None else None,
        planned_spending=(
            float(user.discretionary_budget) if user.discretionary_budget is not None else None
        ),
    )


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser) -> MeOut:
    return _me_out(user)


@router.post("/onboarding", response_model=MeOut)
async def submit_onboarding(
    body: OnboardingIn,
    user: CurrentUser,
    session: SessionDep,
) -> MeOut:
    """Лёгкий мастер первого входа: сохраняет доход и общий лимит трат."""
    updated = await onboarding_svc.complete_onboarding(
        session,
        user,
        monthly_income=body.monthly_income,
        monthly_spending=body.monthly_spending,
    )
    return _me_out(updated)


# ── Настройки уведомлений ────────────────────────────────────────────────
def _settings_out(user: User) -> SettingsOut:
    s = settings_svc.get_notification_settings(user)
    return SettingsOut(
        timezone=s.timezone,
        morning_enabled=s.morning_enabled,
        morning_hour=s.morning_hour,
        evening_enabled=s.evening_enabled,
        evening_hour=s.evening_hour,
    )


@router.get("/settings", response_model=SettingsOut)
async def get_settings(user: CurrentUser) -> SettingsOut:
    return _settings_out(user)


@router.patch("/settings", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> SettingsOut:
    """Меняет настройки уведомлений (часовой пояс, вкл/выкл и час утра/вечера)."""
    if body.timezone is not None and not settings_svc.is_valid_timezone(body.timezone):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown timezone")
    updated = await settings_svc.update_notification_settings(
        session,
        user,
        timezone=body.timezone,
        morning_enabled=body.morning_enabled,
        morning_hour=body.morning_hour,
        evening_enabled=body.evening_enabled,
        evening_hour=body.evening_hour,
    )
    return _settings_out(updated)


# ── Обзор месяца (Главная) ───────────────────────────────────────────────
@router.get("/overview", response_model=OverviewOut)
async def overview(
    user: CurrentUser,
    session: SessionDep,
    month: Optional[str] = Query(default=None),
) -> OverviewOut:
    year, mon, month_str = _parse_month(month)

    totals = await reports.month_totals(session, user.id, year, mon)
    daily = await get_daily_limit(session, user.id)

    lines = await reports.budget_lines(
        session, user.id, year, mon, group=DISCRETIONARY_GROUP
    )
    top = sorted(lines, key=lambda l: l.spent, reverse=True)[:3]

    return OverviewOut(
        month=month_str,
        income=float(totals.income),
        expense=float(totals.expense),
        remaining=float(totals.income - totals.expense),
        daily_limit=float(daily.per_day),
        days_left=daily.days_left,
        has_budget=daily.has_budget,
        top_spend=[
            TopSpendOut(
                category_id=l.category_id,
                name=l.name,
                emoji=l.emoji,
                spent=float(l.spent),
                limit=float(l.limit),
            )
            for l in top
        ],
    )


# ── Аналитика (donut) ────────────────────────────────────────────────────
@router.get("/analytics", response_model=AnalyticsOut)
async def analytics(
    user: CurrentUser,
    session: SessionDep,
    month: Optional[str] = Query(default=None),
) -> AnalyticsOut:
    year, mon, month_str = _parse_month(month)
    groups = await reports.expense_by_group(session, user.id, year, mon)
    total = sum((g.amount for g in groups), Decimal("0"))
    return AnalyticsOut(
        month=month_str,
        total=float(total),
        slices=[SliceOut(name=g.group, value=float(g.amount)) for g in groups],
    )


# ── Бюджет ───────────────────────────────────────────────────────────────
@router.get("/budget/overview", response_model=list[BudgetGroupViewOut])
async def budget_overview(
    user: CurrentUser,
    session: SessionDep,
    month: Optional[str] = Query(default=None),
) -> list[BudgetGroupViewOut]:
    """Полный бюджет каруселью: все расходные категории → все подкатегории (лимит опционален)."""
    year, mon, _ = _parse_month(month)
    groups = await reports.budget_overview(session, user.id, year, mon)
    return [
        BudgetGroupViewOut(
            group=g.group,
            emoji=g.emoji,
            spent=float(g.spent),
            limit=float(g.limit),
            subcategories=[
                BudgetSubOut(
                    subcategory_id=s.category_id,
                    name=s.name,
                    emoji=s.emoji,
                    spent=float(s.spent),
                    limit=float(s.limit),
                )
                for s in g.subcategories
            ],
        )
        for g in groups
    ]


@router.get("/budget", response_model=list[BudgetLineOut])
async def budget(
    user: CurrentUser,
    session: SessionDep,
    month: Optional[str] = Query(default=None),
    group: str = Query(default=DISCRETIONARY_GROUP, description="имя группы или 'all'"),
) -> list[BudgetLineOut]:
    year, mon, _ = _parse_month(month)
    group_filter = None if group == "all" else group
    lines = await reports.budget_lines(session, user.id, year, mon, group=group_filter)
    return [
        BudgetLineOut(
            category_id=l.category_id,
            group=l.group,
            name=l.name,
            emoji=l.emoji,
            spent=float(l.spent),
            limit=float(l.limit),
        )
        for l in lines
    ]


@router.patch("/budget/{category_id}", response_model=BudgetLineOut)
async def set_budget(
    category_id: int,
    body: BudgetSet,
    user: CurrentUser,
    session: SessionDep,
) -> BudgetLineOut:
    category = await session.get(Category, category_id)
    if category is None or category.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")

    result = await session.execute(
        select(Budget).where(
            Budget.user_id == user.id,
            Budget.category_id == category_id,
            Budget.period_month.is_(None),
        )
    )
    budget_row = result.scalar_one_or_none()
    if budget_row is None:
        budget_row = Budget(
            user_id=user.id,
            category_id=category_id,
            amount=Decimal(str(body.amount)),
            period_month=None,
        )
        session.add(budget_row)
    else:
        budget_row.amount = Decimal(str(body.amount))
    await session.commit()

    now = date.today()
    spent = await tx_svc.get_month_spent(session, user.id, category_id, now.year, now.month)
    return BudgetLineOut(
        category_id=category.id,
        group=category.group,
        name=category.name,
        emoji=category.emoji,
        spent=float(spent),
        limit=float(budget_row.amount),
    )


# ── Категории (экран «Добавить») ─────────────────────────────────────────
@router.get("/categories", response_model=list[CategoryGroupOut])
async def categories(
    user: CurrentUser,
    session: SessionDep,
    article: str = Query(default="expense"),
) -> list[CategoryGroupOut]:
    if article not in ("income", "expense", "debt"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad article")

    groups = await categories_svc.get_groups(session, user.id, article)
    out: list[CategoryGroupOut] = []
    for group in groups:
        subs = await categories_svc.get_subcategories(session, user.id, article, group)
        out.append(
            CategoryGroupOut(
                group=group,
                emoji=subs[0].emoji if subs else None,
                subcategories=[
                    SubcategoryOut(id=s.id, name=s.name, emoji=s.emoji) for s in subs
                ],
            )
        )
    return out


@router.post("/categories", response_model=CreatedSubcategoryOut, status_code=201)
async def create_category(
    body: SubcategoryCreate,
    user: CurrentUser,
    session: SessionDep,
) -> CreatedSubcategoryOut:
    """Создаёт подкатегорию. Если её категории (группы) ещё нет — создаётся и она
    (передаётся имя новой группы). Так добавляется и новая категория целиком.
    """
    if body.article not in ("income", "expense", "debt"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad article")
    try:
        created = await categories_svc.create_subcategory(
            session, user.id, body.article, body.group, body.name, body.emoji
        )
    except ValueError as exc:
        detail = {
            "empty group": "group name required",
            "empty name": "name required",
            "duplicate name": "duplicate name",
        }.get(str(exc), str(exc))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail) from exc
    return CreatedSubcategoryOut(
        id=created.id,
        name=created.name,
        emoji=created.emoji,
        group=created.group,
        article=created.article,
    )


# DELETE /categories/group объявлен ДО /categories/{category_id}: literal-путь
# должен перехватить запрос раньше, чем параметрический (иначе "group" → int).
@router.delete("/categories/group", response_model=GroupDeleteResultOut)
async def delete_group_route(
    user: CurrentUser,
    session: SessionDep,
    article: str = Query(default="expense"),
    name: str = Query(..., description="имя категории (группы)"),
) -> GroupDeleteResultOut:
    """Удаляет категорию (группу) целиком. Подкатегории без операций удаляются,
    с историей — архивируются. Служебную группу «Траты» удалять нельзя (по ней
    считается дневной лимит)."""
    if article not in ("income", "expense", "debt"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad article")
    if name.strip() == DISCRETIONARY_GROUP:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot delete service group")
    try:
        result = await categories_svc.delete_group(session, user.id, article, name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found") from exc
    return GroupDeleteResultOut(**result)


@router.delete("/categories/{category_id}", response_model=DeleteResultOut)
async def delete_category(
    category_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> DeleteResultOut:
    """Удаляет подкатегорию, если по ней нет операций; иначе архивирует (историю храним)."""
    category = await session.get(Category, category_id)
    if category is None or category.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
    action = await categories_svc.delete_subcategory(session, category)
    return DeleteResultOut(action=action, id=category_id)


@router.patch("/categories/group", response_model=GroupRenameOut)
async def rename_group(
    body: GroupRename,
    user: CurrentUser,
    session: SessionDep,
) -> GroupRenameOut:
    """Переименование категории (группы) — меняет её у всех подкатегорий пользователя."""
    if body.article not in ("income", "expense", "debt"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad article")
    try:
        renamed = await categories_svc.rename_group(
            session, user.id, body.article, body.old_name, body.new_name
        )
    except ValueError as exc:
        detail = "group exists" if str(exc) == "group exists" else str(exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail) from exc
    if renamed == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")
    return GroupRenameOut(group=body.new_name.strip(), renamed=renamed)


@router.patch("/categories/{category_id}", response_model=SubcategoryOut)
async def rename_category(
    category_id: int,
    body: CategoryRename,
    user: CurrentUser,
    session: SessionDep,
) -> SubcategoryOut:
    """Переименование подкатегории (название). id не меняется — история сохраняется."""
    category = await session.get(Category, category_id)
    if category is None or category.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
    try:
        updated = await categories_svc.rename_subcategory(session, category, body.name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return SubcategoryOut(id=updated.id, name=updated.name, emoji=updated.emoji)


# ── Операции ─────────────────────────────────────────────────────────────
@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(
    user: CurrentUser,
    session: SessionDep,
    month: Optional[str] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
) -> list[TransactionOut]:
    year = mon = None
    if month:
        year, mon, _ = _parse_month(month)
    rows = await reports.recent_transactions(
        session, user.id, limit=limit, year=year, month=mon
    )
    return [_tx_out(t) for t in rows]


@router.post("/transactions", response_model=TransactionOut, status_code=201)
async def create_transaction(
    body: TransactionCreate,
    user: CurrentUser,
    session: SessionDep,
) -> TransactionOut:
    category = await session.get(Category, body.category_id)
    if category is None or category.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
    if body.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be > 0")

    tx = await tx_svc.create_transaction(
        session,
        user_id=user.id,
        category_id=category.id,
        article=category.article,
        amount=Decimal(str(body.amount)),
        source="manual_app",
        description=body.comment,
        on_date=body.date,
    )
    # подгружаем категорию для ответа
    tx.category = category
    return _tx_out(tx)


@router.delete("/transactions/{tx_id}", status_code=204)
async def delete_transaction(
    tx_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    tx = await session.get(Transaction, tx_id)
    if tx is None or tx.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")
    await session.delete(tx)
    await session.commit()
