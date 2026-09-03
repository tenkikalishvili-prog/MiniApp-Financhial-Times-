"""Эндпоинты Mini App. Тонкий слой поверх backend/services."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, select

from backend.models import Bill, BillMark, Budget, Category, Debt, DebtPayment, Transaction
from backend.models import User
from backend.services import bills as bills_svc
from backend.services import categories as categories_svc
from backend.services import debts as debts_svc
from backend.services import onboarding as onboarding_svc
from backend.services import reports
from backend.services import settings as settings_svc
from backend.services import transactions as tx_svc
from backend.services.limits import DISCRETIONARY_GROUP, get_daily_limit
from backend.services.smart_input import interpret

from .deps import CurrentUser, SessionDep
from .schemas import (
    AnalyticsOut,
    BillCreate,
    BillOut,
    BillPaidUpdate,
    BillUpdate,
    BudgetGroupViewOut,
    BudgetLineOut,
    BudgetSet,
    BudgetSubOut,
    CategoryGroupOut,
    CategoryRename,
    CreatedSubcategoryOut,
    DebtCreate,
    DebtOut,
    DebtPaymentCreate,
    DebtPaymentOut,
    DebtUpdate,
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
    SmartParseIn,
    SmartParseOut,
    SubcategoryCreate,
    SubcategoryOut,
    TopSpendOut,
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
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


def _debt_out(d: Debt) -> DebtOut:
    amount = float(d.amount)
    paid = float(d.paid)
    return DebtOut(
        id=d.id,
        direction=d.direction,
        counterparty=d.counterparty,
        amount=amount,
        paid=paid,
        remaining=round(amount - paid, 2),
        due_date=d.due_date,
        note=d.note,
        is_closed=d.is_closed,
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
        reminders_enabled=s.reminders_enabled,
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
        reminders_enabled=body.reminders_enabled,
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
    article: str = Query(default="expense"),
) -> list[BudgetGroupViewOut]:
    """Полный обзор категорий каруселью: все категории статьи → все подкатегории.

    Для расходов (``article=expense``) — с лимитами (бюджет). Для доходов
    (``article=income``) лимитов нет: ``limit`` всегда 0, ``spent`` — сумма полученного
    за месяц по категории.
    """
    if article not in ("income", "expense", "debt"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad article")
    year, mon, _ = _parse_month(month)
    groups = await reports.budget_overview(session, user.id, year, mon, article=article)
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


# ── Умный ввод (экран «Добавить») ────────────────────────────────────────
@router.post("/smart-parse", response_model=SmartParseOut)
async def smart_parse(
    body: SmartParseIn,
    user: CurrentUser,
    session: SessionDep,
) -> SmartParseOut:
    """«кофе 350» → сумма + подобранная подкатегория для предзаполнения формы.

    Переиспользует ``smart_input.interpret`` (та же логика, что в боте: Claude S5
    при наличии ключа, иначе детерминированная эвристика S4). Ничего не пишет в БД —
    только разбирает текст.
    """
    parsed = await interpret(session, user.id, body.text or "")
    cat = parsed.category
    return SmartParseOut(
        amount=float(parsed.amount) if parsed.amount is not None else None,
        description=parsed.description,
        article=parsed.article,
        matched=cat is not None,
        category_id=cat.id if cat else None,
        group=cat.group if cat else None,
        subcategory_name=cat.name if cat else None,
        emoji=cat.emoji if cat else None,
    )


# ── Операции ─────────────────────────────────────────────────────────────
@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(
    user: CurrentUser,
    session: SessionDep,
    month: Optional[str] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    article: Optional[str] = Query(default=None, description="income | expense | debt"),
    group: Optional[str] = Query(default=None, description="имя категории (группы)"),
    q: Optional[str] = Query(default=None, description="поиск по описанию и подкатегории"),
) -> list[TransactionOut]:
    """Список операций (Главная + экран «История»). Все фильтры опциональны."""
    year = mon = None
    if month:
        year, mon, _ = _parse_month(month)
    if article is not None and article not in ("income", "expense", "debt"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad article")
    rows = await reports.recent_transactions(
        session,
        user.id,
        limit=limit,
        offset=offset,
        year=year,
        month=mon,
        article=article,
        group=group,
        query=q,
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


@router.patch("/transactions/{tx_id}", response_model=TransactionOut)
async def update_transaction(
    tx_id: int,
    body: TransactionUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> TransactionOut:
    """Редактирование операции: сумма / категория / дата / заметка (любое подмножество).

    При смене категории статья (``article``) синхронизируется с новой категорией —
    так операцию можно переносить между расходом/доходом/долгом.
    """
    tx = await session.get(Transaction, tx_id)
    if tx is None or tx.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")

    category = await session.get(Category, tx.category_id)
    if body.category_id is not None and body.category_id != tx.category_id:
        category = await session.get(Category, body.category_id)
        if category is None or category.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
        tx.category_id = category.id
        tx.article = category.article

    if body.amount is not None:
        tx.amount = Decimal(str(body.amount))
    if body.on_date is not None:
        tx.date = body.on_date
    if body.comment is not None:
        tx.description = body.comment.strip() or None

    await session.commit()
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


# ── Долги (направление C, S8) ────────────────────────────────────────────
@router.get("/debts", response_model=list[DebtOut])
async def list_debts(
    user: CurrentUser,
    session: SessionDep,
    include_closed: bool = Query(False, alias="includeClosed"),
) -> list[DebtOut]:
    """Реестр долгов пользователя (по умолчанию — только открытые)."""
    debts = await debts_svc.list_debts(session, user.id, include_closed=include_closed)
    return [_debt_out(d) for d in debts]


@router.post("/debts", response_model=DebtOut, status_code=201)
async def create_debt(
    body: DebtCreate,
    user: CurrentUser,
    session: SessionDep,
) -> DebtOut:
    if body.direction not in debts_svc.DIRECTIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "direction must be owe|lent")
    name = body.counterparty.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "counterparty is required")
    if body.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be > 0")

    debt = await debts_svc.create_debt(
        session,
        user_id=user.id,
        direction=body.direction,
        counterparty=name,
        amount=Decimal(str(body.amount)),
        due_date=body.due_date,
        note=(body.note or "").strip() or None,
    )
    return _debt_out(debt)


@router.patch("/debts/{debt_id}", response_model=DebtOut)
async def update_debt(
    debt_id: int,
    body: DebtUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> DebtOut:
    """Правка карточки долга: направление / контрагент / сумма / срок / заметка / закрытие."""
    debt = await session.get(Debt, debt_id)
    if debt is None or debt.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "debt not found")

    if body.direction is not None:
        if body.direction not in debts_svc.DIRECTIONS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "direction must be owe|lent")
        debt.direction = body.direction
    if body.counterparty is not None:
        name = body.counterparty.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "counterparty is required")
        debt.counterparty = name
    if body.amount is not None:
        debt.amount = Decimal(str(body.amount))
    if body.due_date is not None:
        debt.due_date = body.due_date
    if body.note is not None:
        debt.note = body.note.strip() or None
    if body.is_closed is not None:
        debt.is_closed = body.is_closed

    await session.commit()
    return _debt_out(debt)


@router.delete("/debts/{debt_id}", status_code=204)
async def delete_debt(
    debt_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    debt = await session.get(Debt, debt_id)
    if debt is None or debt.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "debt not found")
    # Сначала убираем платежи долга (нет каскада FK) — иначе останутся сироты.
    await session.execute(delete(DebtPayment).where(DebtPayment.debt_id == debt.id))
    await session.delete(debt)
    await session.commit()


# ── Возвраты долга частями (S9) ──────────────────────────────────────────
def _payment_out(p: DebtPayment) -> DebtPaymentOut:
    return DebtPaymentOut(id=p.id, amount=float(p.amount), on_date=p.on_date)


async def _get_owned_debt(session, user, debt_id: int) -> Debt:
    debt = await session.get(Debt, debt_id)
    if debt is None or debt.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "debt not found")
    return debt


@router.get("/debts/{debt_id}/payments", response_model=list[DebtPaymentOut])
async def list_debt_payments(
    debt_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> list[DebtPaymentOut]:
    """История возвратов по долгу (свежие сверху)."""
    await _get_owned_debt(session, user, debt_id)
    payments = await debts_svc.list_payments(session, debt_id)
    return [_payment_out(p) for p in payments]


@router.post("/debts/{debt_id}/payments", response_model=DebtOut, status_code=201)
async def add_debt_payment(
    debt_id: int,
    body: DebtPaymentCreate,
    user: CurrentUser,
    session: SessionDep,
) -> DebtOut:
    """Записывает частичный возврат. Возвращает обновлённую карточку долга.

    Сумма возврата не должна превышать остаток (округление до копеек).
    """
    debt = await _get_owned_debt(session, user, debt_id)
    if body.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be > 0")
    remaining = round(float(debt.amount) - float(debt.paid), 2)
    if round(body.amount, 2) > remaining:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount exceeds remaining")

    await debts_svc.add_payment(
        session,
        debt,
        amount=Decimal(str(body.amount)),
        on_date=body.on_date or date.today(),
    )
    return _debt_out(debt)


@router.delete("/debts/{debt_id}/payments/{payment_id}", response_model=DebtOut)
async def delete_debt_payment(
    debt_id: int,
    payment_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> DebtOut:
    """Удаляет возврат и пересчитывает остаток/статус. Возвращает карточку долга."""
    debt = await _get_owned_debt(session, user, debt_id)
    payment = await session.get(DebtPayment, payment_id)
    if payment is None or payment.debt_id != debt.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment not found")
    await debts_svc.delete_payment(session, payment, debt)
    return _debt_out(debt)


# ── Обязательные платежи (направление C, S10) ────────────────────────────
def _bill_out(bill: Bill, category: Category | None, paid: bool) -> BillOut:
    return BillOut(
        id=bill.id,
        title=bill.title,
        amount=float(bill.amount),
        due_day=bill.due_day,
        category_id=bill.category_id,
        category_name=category.name if category else "—",
        group=category.group if category else "—",
        emoji=category.emoji if category else None,
        note=bill.note,
        is_active=bill.is_active,
        paid=paid,
    )


async def _get_expense_category(session, user, category_id: int) -> Category:
    cat = await session.get(Category, category_id)
    if cat is None or cat.user_id != user.id or cat.article != "expense":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "category must be an expense subcategory")
    return cat


async def _get_owned_bill(session, user, bill_id: int) -> Bill:
    bill = await session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "bill not found")
    return bill


@router.get("/bills", response_model=list[BillOut])
async def list_bills(
    user: CurrentUser,
    session: SessionDep,
    month: Optional[str] = Query(default=None),
) -> list[BillOut]:
    """Обязательные платежи с отметкой оплаты за выбранный месяц (по умолчанию — текущий)."""
    _, _, period = _parse_month(month)
    bills = await bills_svc.list_bills(session, user.id, active_only=True)
    marks = await bills_svc.marks_for_period(session, user.id, period)
    out: list[BillOut] = []
    for b in bills:
        cat = await session.get(Category, b.category_id)
        out.append(_bill_out(b, cat, b.id in marks))
    return out


@router.post("/bills", response_model=BillOut, status_code=201)
async def create_bill(
    body: BillCreate,
    user: CurrentUser,
    session: SessionDep,
) -> BillOut:
    title = body.title.strip()
    if not title:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "title is required")
    if body.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be > 0")
    cat = await _get_expense_category(session, user, body.category_id)
    bill = await bills_svc.create_bill(
        session,
        user_id=user.id,
        title=title,
        amount=Decimal(str(body.amount)),
        due_day=body.due_day,
        category_id=body.category_id,
        note=(body.note or "").strip() or None,
    )
    return _bill_out(bill, cat, paid=False)


@router.patch("/bills/{bill_id}", response_model=BillOut)
async def update_bill(
    bill_id: int,
    body: BillUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> BillOut:
    """Правка платежа: название / сумма / число-срок / категория / заметка / активность."""
    bill = await _get_owned_bill(session, user, bill_id)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "title is required")
        bill.title = title
    if body.amount is not None:
        bill.amount = Decimal(str(body.amount))
    if body.due_day is not None:
        bill.due_day = body.due_day
    if body.category_id is not None:
        await _get_expense_category(session, user, body.category_id)
        bill.category_id = body.category_id
    if body.note is not None:
        bill.note = body.note.strip() or None
    if body.is_active is not None:
        bill.is_active = body.is_active
    await session.commit()

    _, _, period = _parse_month(None)
    marks = await bills_svc.marks_for_period(session, user.id, period)
    cat = await session.get(Category, bill.category_id)
    return _bill_out(bill, cat, bill.id in marks)


@router.delete("/bills/{bill_id}", status_code=204)
async def delete_bill(
    bill_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    """Удаляет платёж и его отметки. Ранее записанные операции остаются в истории."""
    bill = await _get_owned_bill(session, user, bill_id)
    await session.execute(delete(BillMark).where(BillMark.bill_id == bill.id))
    await session.delete(bill)
    await session.commit()


@router.patch("/bills/{bill_id}/paid", response_model=BillOut)
async def set_bill_paid(
    bill_id: int,
    body: BillPaidUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> BillOut:
    """Ставит/снимает отметку оплаты за месяц. Отметка создаёт/удаляет расходную операцию."""
    bill = await _get_owned_bill(session, user, bill_id)
    _, _, period = _parse_month(body.month)
    await bills_svc.set_paid(session, user.id, bill, period, body.paid)
    cat = await session.get(Category, bill.category_id)
    return _bill_out(bill, cat, body.paid)
