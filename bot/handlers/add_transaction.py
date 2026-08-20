"""Мастер ввода операции по кнопкам: Статья → Категория → Подкатегория → Сумма."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from backend.db import async_session
from backend.services.categories import (
    get_category,
    get_groups,
    get_subcategories,
)
from backend.services.transactions import (
    create_transaction,
    get_budget_amount,
    get_month_spent,
)
from backend.services.users import get_or_create_user
from bot.keyboards import (
    ARTICLES,
    article_kb,
    groups_kb,
    main_menu_kb,
    subcategories_kb,
)

router = Router()

ARTICLE_TITLES = dict(ARTICLES)  # code -> "💸 Расход"


class AddTx(StatesGroup):
    choosing_article = State()
    choosing_group = State()
    choosing_subcategory = State()
    entering_amount = State()


def _fmt(amount: Decimal) -> str:
    """Формат суммы с разделителями тысяч: 12 500."""
    return f"{amount:,.0f}".replace(",", " ")


# ─── Вход в мастер ──────────────────────────────────────────────────────
@router.message(F.text == "➕ Добавить операцию")
async def start_add(message: Message, state: FSMContext) -> None:
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name or "",
        )
    await state.clear()
    await state.update_data(user_id=user.id)
    await state.set_state(AddTx.choosing_article)
    await message.answer("Шаг 1 из 4. Выберите статью:", reply_markup=article_kb())


# ─── Отмена ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "add:cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено. Возвращайтесь, когда будете готовы 🙂")
    await callback.answer()


# ─── Шаг 1 → 2: выбрана статья, показываем категории ────────────────────
@router.callback_query(AddTx.choosing_article, F.data.startswith("add:art:"))
async def choose_article(callback: CallbackQuery, state: FSMContext) -> None:
    article = callback.data.split(":")[2]
    data = await state.get_data()

    async with async_session() as session:
        groups = await get_groups(session, data["user_id"], article)

    if not groups:
        await callback.answer("Для этой статьи нет категорий", show_alert=True)
        return

    await state.update_data(article=article, groups=groups)
    await state.set_state(AddTx.choosing_group)
    await callback.message.edit_text(
        f"{ARTICLE_TITLES[article]}\nШаг 2 из 4. Выберите категорию:",
        reply_markup=groups_kb(groups),
    )
    await callback.answer()


# ─── Назад: к выбору статьи ─────────────────────────────────────────────
@router.callback_query(F.data == "add:back:article")
async def back_to_article(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddTx.choosing_article)
    await callback.message.edit_text("Шаг 1 из 4. Выберите статью:", reply_markup=article_kb())
    await callback.answer()


# ─── Шаг 2 → 3: выбрана категория, показываем подкатегории ──────────────
@router.callback_query(AddTx.choosing_group, F.data.startswith("add:grp:"))
async def choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.split(":")[2])
    data = await state.get_data()
    groups: list[str] = data["groups"]

    if index >= len(groups):
        await callback.answer("Категория не найдена, начните заново", show_alert=True)
        return

    group = groups[index]
    async with async_session() as session:
        subcategories = await get_subcategories(
            session, data["user_id"], data["article"], group
        )

    await state.update_data(group=group)
    await state.set_state(AddTx.choosing_subcategory)
    await callback.message.edit_text(
        f"{ARTICLE_TITLES[data['article']]} · {group}\n"
        "Шаг 3 из 4. Выберите подкатегорию:",
        reply_markup=subcategories_kb(subcategories),
    )
    await callback.answer()


# ─── Назад: к выбору категории ──────────────────────────────────────────
@router.callback_query(F.data == "add:back:group")
async def back_to_group(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(AddTx.choosing_group)
    await callback.message.edit_text(
        f"{ARTICLE_TITLES[data['article']]}\nШаг 2 из 4. Выберите категорию:",
        reply_markup=groups_kb(data["groups"]),
    )
    await callback.answer()


# ─── Шаг 3 → 4: выбрана подкатегория, просим сумму ──────────────────────
@router.callback_query(AddTx.choosing_subcategory, F.data.startswith("add:sub:"))
async def choose_subcategory(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        category = await get_category(session, category_id)

    if category is None:
        await callback.answer("Подкатегория не найдена", show_alert=True)
        return

    emoji = f"{category.emoji} " if category.emoji else ""
    await state.update_data(category_id=category.id, category_name=f"{emoji}{category.name}")
    await state.set_state(AddTx.entering_amount)
    await callback.message.edit_text(
        f"{ARTICLE_TITLES[category.article]} · {category.group} · {emoji}{category.name}\n"
        "Шаг 4 из 4. Введите сумму в ₽ (например: 350):"
    )
    await callback.answer()


# ─── Шаг 4: получили сумму — сохраняем операцию ─────────────────────────
@router.message(AddTx.entering_amount)
async def enter_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(",", ".").replace(" ", "").strip()
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer("Не понял сумму. Введите число, например: 350")
        return
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    today = date.today()

    async with async_session() as session:
        await create_transaction(
            session,
            user_id=data["user_id"],
            category_id=data["category_id"],
            article=data["article"],
            amount=amount,
            source="bot_buttons",
        )
        # Для расходов покажем остаток планового бюджета за текущий месяц
        budget_line = ""
        if data["article"] == "expense":
            budget = await get_budget_amount(session, data["user_id"], data["category_id"])
            if budget:
                spent = await get_month_spent(
                    session, data["user_id"], data["category_id"], today.year, today.month
                )
                remaining = budget - spent
                budget_line = (
                    f"\n💼 Бюджет «{data['category_name']}»: осталось "
                    f"<b>{_fmt(remaining)} ₽</b> из {_fmt(budget)} ₽ на этот месяц"
                )

    await state.clear()
    await message.answer(
        f"✅ Записал!\n"
        f"{ARTICLE_TITLES[data['article']]} · {data['category_name']}\n"
        f"Сумма: <b>{_fmt(amount)} ₽</b>\n"
        f"Дата: {today.strftime('%d.%m.%Y')}"
        f"{budget_line}",
        reply_markup=main_menu_kb(),
    )
