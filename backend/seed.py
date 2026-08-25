"""Нейтральная «коробка» категорий по умолчанию (засев при первом старте пользователя).

Это обобщённый стартовый набор для любого пользователя — БЕЗ личных данных владельца
(без конкретных банков, сумм и лимитов). Все плановые лимиты = 0: человек задаёт свои
бюджеты сам (в приложении или на онбординге). Это НЕ хардкод логики, а seed-шаблон:
пользователь может добавлять/переименовывать/архивировать свои категории.

Структура сохранена как в Excel-модели: Статья (income/expense/debt) → Категория (group)
→ Подкатегория (name). ⚠️ Группа «Траты» — служебная: по ней считается дневной лимит
(см. backend/services/limits.py, DISCRETIONARY_GROUP). Её название менять нельзя.

Каждая запись: article, group (Категория), name (Подкатегория), emoji.
Лимитов в шаблоне нет — Budget-строки не создаются (пусто = «лимит не задан»).
"""

from __future__ import annotations

from decimal import Decimal

from backend.models import Budget, Category

# article: income | expense | debt
SEED_CATEGORIES: list[dict] = [
    # ─── Доход ───────────────────────────────────────────────────────────
    {"article": "income", "group": "Доход", "name": "Зарплата", "emoji": "💼"},
    {"article": "income", "group": "Доход", "name": "Подработка", "emoji": "💰"},
    {"article": "income", "group": "Доход", "name": "Прочий доход", "emoji": "➕"},

    # ─── Расход · Обязательные платежи ───────────────────────────────────
    {"article": "expense", "group": "Обязательные платежи", "name": "Аренда / ипотека", "emoji": "🏠"},
    {"article": "expense", "group": "Обязательные платежи", "name": "Коммунальные услуги", "emoji": "💡"},
    {"article": "expense", "group": "Обязательные платежи", "name": "Связь и интернет", "emoji": "🌐"},
    {"article": "expense", "group": "Обязательные платежи", "name": "Подписки", "emoji": "📺"},
    {"article": "expense", "group": "Обязательные платежи", "name": "Кредиты", "emoji": "🏦"},

    # ─── Расход · Траты (по этой группе считается дневной лимит) ──────────
    {"article": "expense", "group": "Траты", "name": "Продукты", "emoji": "🛒"},
    {"article": "expense", "group": "Траты", "name": "Кафе и рестораны", "emoji": "🍽️"},
    {"article": "expense", "group": "Траты", "name": "Транспорт", "emoji": "🚌"},
    {"article": "expense", "group": "Траты", "name": "Такси", "emoji": "🚕"},
    {"article": "expense", "group": "Траты", "name": "Развлечения", "emoji": "🎉"},
    {"article": "expense", "group": "Траты", "name": "Одежда и уход", "emoji": "👕"},
    {"article": "expense", "group": "Траты", "name": "Здоровье", "emoji": "💊"},
    {"article": "expense", "group": "Траты", "name": "Прочее", "emoji": "📦"},

    # ─── Долг ────────────────────────────────────────────────────────────
    {"article": "debt", "group": "Долги", "name": "Взял в долг", "emoji": "📥"},
    {"article": "debt", "group": "Долги", "name": "Дал в долг", "emoji": "📤"},
    {"article": "debt", "group": "Долги", "name": "Возврат долга", "emoji": "✅"},
]


async def seed_categories(session, user_id: int) -> None:
    """Создаёт пользователю его набор категорий из нейтрального шаблона.

    Бюджеты не засеваются (в шаблоне нет ключа ``budget``) — лимиты пользователь
    задаёт сам. Ключ ``budget`` поддержан на случай будущих коробок с дефолтами.
    """
    for order, item in enumerate(SEED_CATEGORIES, start=1):
        category = Category(
            user_id=user_id,
            article=item["article"],
            group=item["group"],
            name=item["name"],
            emoji=item.get("emoji"),
            sort_order=order,
        )
        session.add(category)
        await session.flush()  # получаем category.id

        if item.get("budget"):
            session.add(
                Budget(
                    user_id=user_id,
                    category_id=category.id,
                    amount=Decimal(str(item["budget"])),
                    period_month=None,  # шаблон по умолчанию
                )
            )
