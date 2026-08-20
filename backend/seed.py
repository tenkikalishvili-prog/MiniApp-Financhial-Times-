"""Шаблон категорий по умолчанию (засев при первом старте пользователя).

Перенесён из «Financial Times 2.0.xlsx» → таб «Мой бюджет». Это НЕ хардкод логики,
а seed-шаблон: позже пользователь сможет добавлять/переименовывать/архивировать свои
категории, а мы — подложить «коробочный» набор без миграций.

Каждая запись: article, group (Категория), name (Подкатегория), emoji, budget (₽/мес).
"""

from __future__ import annotations

from decimal import Decimal

from backend.models import Budget, Category

# article: income | expense | debt
SEED_CATEGORIES: list[dict] = [
    # ─── Доход ───────────────────────────────────────────────────────────
    {"article": "income", "group": "Доход", "name": "Lime", "emoji": "💼", "budget": 208000},
    {"article": "income", "group": "Доход", "name": "SkyPro", "emoji": "🎓", "budget": 80000},
    {"article": "income", "group": "Ожидаемый доход", "name": "ЗП Lime (01–12)", "emoji": "📅", "budget": 0},
    {"article": "income", "group": "Ожидаемый доход", "name": "ЗП Lime (12–30/31)", "emoji": "📅", "budget": 0},
    {"article": "income", "group": "Ожидаемый доход", "name": "ЗП SkyPro", "emoji": "📅", "budget": 0},

    # ─── Расход · Подписки ───────────────────────────────────────────────
    {"article": "expense", "group": "Подписки", "name": "TicTic", "emoji": "📺", "budget": 350},
    {"article": "expense", "group": "Подписки", "name": "Fitness Online", "emoji": "🏋️", "budget": 250},
    {"article": "expense", "group": "Подписки", "name": "iCloud", "emoji": "☁️", "budget": 150},
    {"article": "expense", "group": "Подписки", "name": "Apple Music", "emoji": "🎵", "budget": 170},
    {"article": "expense", "group": "Подписки", "name": "Claude Code", "emoji": "🤖", "budget": 2000},
    {"article": "expense", "group": "Подписки", "name": "DDX", "emoji": "🏋️", "budget": 2000},

    # ─── Расход · Кредиты и постоянные ───────────────────────────────────
    {"article": "expense", "group": "Кредиты и постоянные", "name": "Альфа кредитка 1", "emoji": "💳", "budget": 600},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "Альфа кредитка 2", "emoji": "💳", "budget": 600},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "Альфа кредит", "emoji": "🏦", "budget": 5251},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "Сбер кредитка", "emoji": "💳", "budget": 10000},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "Сбер кредит", "emoji": "🏦", "budget": 10000},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "Уралсиб кредит", "emoji": "🏦", "budget": 3500},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "Уралсиб кредитка", "emoji": "💳", "budget": 3000},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "МТС кредит", "emoji": "🏦", "budget": 21300},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "МТС (Коля)", "emoji": "🤝", "budget": 7500},
    {"article": "expense", "group": "Кредиты и постоянные", "name": "Жильё/аренда", "emoji": "🏡", "budget": 65000},

    # ─── Расход · Траты ──────────────────────────────────────────────────
    {"article": "expense", "group": "Траты", "name": "Еда/Продукты", "emoji": "🥦", "budget": 20000},
    {"article": "expense", "group": "Траты", "name": "Развлечения", "emoji": "🎉", "budget": 10000},
    {"article": "expense", "group": "Траты", "name": "Одежда/уход/товары для дома", "emoji": "👕", "budget": 5000},
    {"article": "expense", "group": "Траты", "name": "Транспорт", "emoji": "🚌", "budget": 5000},
    {"article": "expense", "group": "Траты", "name": "Рестораны", "emoji": "🍽️", "budget": 20000},
    {"article": "expense", "group": "Траты", "name": "Такси", "emoji": "🚕", "budget": 5000},
    {"article": "expense", "group": "Траты", "name": "Путешествия", "emoji": "✈️", "budget": 10000},
    {"article": "expense", "group": "Траты", "name": "Обеды на работе", "emoji": "🍱", "budget": 9000},
    {"article": "expense", "group": "Траты", "name": "Табак", "emoji": "🚬", "budget": 8400},
    {"article": "expense", "group": "Траты", "name": "Связь", "emoji": "📱", "budget": 2000},

    # ─── Расход · Прочее ─────────────────────────────────────────────────
    {"article": "expense", "group": "Прочее", "name": "Дни рождения", "emoji": "🎂", "budget": 5000},
    {"article": "expense", "group": "Прочее", "name": "Бери заряд", "emoji": "🔋", "budget": 1000},
    {"article": "expense", "group": "Прочее", "name": "Сплит", "emoji": "🧩", "budget": 3000},
    {"article": "expense", "group": "Прочее", "name": "Переводы в лари", "emoji": "🇬🇪", "budget": 5000},
    {"article": "expense", "group": "Прочее", "name": "Каршеринг", "emoji": "🚗", "budget": 5000},
    {"article": "expense", "group": "Прочее", "name": "Прочее", "emoji": "📦", "budget": 10000},

    # ─── Долг · Долги и просрочки ────────────────────────────────────────
    {"article": "debt", "group": "Долги и просрочки", "name": "Долг до ЗП 12/27", "emoji": "💸", "budget": 0},
    {"article": "debt", "group": "Долги и просрочки", "name": "Просрочка с ЗП 12/27", "emoji": "⏰", "budget": 0},
    {"article": "debt", "group": "Долги и просрочки", "name": "Погашение долга", "emoji": "✅", "budget": 0},
    {"article": "debt", "group": "Долги и просрочки", "name": "Погашение просрочки", "emoji": "✅", "budget": 0},
]


async def seed_categories(session, user_id: int) -> None:
    """Создаёт пользователю его набор категорий и плановых бюджетов из шаблона."""
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
