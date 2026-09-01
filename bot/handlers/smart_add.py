"""Умный ввод (S4): свободный текст «кофе 350» → операция.

Любое текстовое сообщение, которое НЕ команда и не кнопка меню, попадает сюда.
Роутер подключается ПОСЛЕДНИМ (см. bot/main.py), поэтому кнопку «➕ Добавить
операцию» и шаги мастера по кнопкам (их состояния/колбэки) перехватывают более
ранние роутеры, а до умного разбора доходит только «человеческий» текст.

Поток: разобрали сумму + подобрали подкатегорию → карточка-подтверждение
(✅ Записать / ✏️ Другая категория / ✖). Разбор — в backend/services/smart_input.py
(переиспользуемый модуль; на нём же вырастут S5-автокатегоризация и поле умного
ввода в приложении).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from backend.db import async_session
from backend.services.categories import get_category, get_groups, get_subcategories
from backend.services.smart_input import ParsedInput, interpret
from backend.services.transactions import (
    create_transaction,
    get_budget_amount,
    get_month_spent,
)
from backend.services.users import get_or_create_user
from bot.keyboards import (
    ARTICLES,
    main_menu_kb,
    smart_article_kb,
    smart_confirm_kb,
    smart_groups_kb,
    smart_subcategories_kb,
)

router = Router()

ARTICLE_TITLES = dict(ARTICLES)  # code -> "💸 Расход"


class SmartAdd(StatesGroup):
    confirming = State()  # показана карточка-подтверждение
    picking_group = State()  # выбирает категорию вручную (после «Другая категория»)
    picking_sub = State()


def _fmt(amount: Decimal) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def _cat_label(emoji: str | None, name: str) -> str:
    return f"{emoji} {name}" if emoji else name


def _confirm_text(parsed: ParsedInput) -> str:
    """Текст карточки-подтверждения по результату разбора."""
    amount = _fmt(parsed.amount) if parsed.amount is not None else "?"
    desc_line = f"\n📝 <i>{parsed.description}</i>" if parsed.description else ""
    if parsed.category is not None:
        label = _cat_label(parsed.category.emoji, parsed.category.name)
        return (
            "🧠 Понял так:\n"
            f"{ARTICLE_TITLES[parsed.category.article]} · {parsed.category.group} · {label}\n"
            f"Сумма: <b>{amount} ₽</b>"
            f"{desc_line}\n\n"
            "Записать?"
        )
    return (
        f"Сумму понял: <b>{amount} ₽</b>, а категорию — нет 🤔"
        f"{desc_line}\n\n"
        "Выбери категорию 👇"
    )


# ─── Вход: свободный текст (не команда, не кнопка меню) ──────────────────
@router.message(F.text, ~F.text.startswith("/"))
async def smart_text(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name or "",
        )
        parsed = await interpret(session, user.id, text)

    if parsed.amount is None:
        await message.answer(
            "Не нашёл сумму 🤔\n"
            "Напиши коротко: <b>кофе 350</b>, <b>такси 420</b>, <b>продукты 1500</b>.\n"
            "Или жми «➕ Добавить операцию» для ввода по шагам.",
            reply_markup=main_menu_kb(),
        )
        return

    await state.set_state(SmartAdd.confirming)
    await state.update_data(
        user_id=user.id,
        amount=str(parsed.amount),
        description=parsed.description[:255] or None,
        article=parsed.category.article if parsed.category else parsed.article,
        category_id=parsed.category.id if parsed.category else None,
    )
    await message.answer(
        _confirm_text(parsed),
        reply_markup=smart_confirm_kb(matched=parsed.category is not None),
    )


# ─── Сохранение операции (после ✅ или выбора подкатегории вручную) ──────
async def _finish_save(callback: CallbackQuery, state: FSMContext, category_id: int) -> None:
    data = await state.get_data()
    amount = Decimal(data["amount"])
    description = data.get("description")
    # Источник записи: 'bot_text' (умный текст) или 'bot_photo' (чек, S7).
    source = data.get("source", "bot_text")
    # Дата: с чека может прийти своя (ISO); иначе — сегодня.
    on_date = date.fromisoformat(data["on_date"]) if data.get("on_date") else date.today()
    today = on_date

    async with async_session() as session:
        category = await get_category(session, category_id)
        if category is None or category.user_id != data["user_id"]:
            await callback.answer("Подкатегория не найдена", show_alert=True)
            return
        await create_transaction(
            session,
            user_id=data["user_id"],
            category_id=category.id,
            article=category.article,
            amount=amount,
            source=source,
            description=description,
            on_date=on_date,
        )
        budget_line = ""
        if category.article == "expense":
            budget = await get_budget_amount(session, data["user_id"], category.id)
            if budget:
                spent = await get_month_spent(
                    session, data["user_id"], category.id, today.year, today.month
                )
                remaining = budget - spent
                budget_line = (
                    f"\n💼 Бюджет «{category.name}»: осталось "
                    f"<b>{_fmt(remaining)} ₽</b> из {_fmt(budget)} ₽ на этот месяц"
                )
        label = _cat_label(category.emoji, category.name)
        article_title = ARTICLE_TITLES[category.article]

    await state.clear()
    await callback.message.edit_text(
        f"✅ Записал!\n"
        f"{article_title} · {label}\n"
        f"Сумма: <b>{_fmt(amount)} ₽</b>\n"
        f"Дата: {today.strftime('%d.%m.%Y')}"
        f"{budget_line}"
    )
    await callback.answer()


@router.callback_query(SmartAdd.confirming, F.data == "smart:save")
async def smart_save(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    category_id = data.get("category_id")
    if category_id is None:  # на всякий случай: нечего сохранять без категории
        await callback.answer("Сначала выбери категорию", show_alert=True)
        return
    await _finish_save(callback, state, category_id)


# ─── Ручной выбор категории: статья → категория → подкатегория ──────────
@router.callback_query(F.data == "smart:pick")
async def smart_pick(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SmartAdd.picking_group)
    await callback.message.edit_text(
        "Выбери статью:", reply_markup=smart_article_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("smart:art:"))
async def smart_choose_article(callback: CallbackQuery, state: FSMContext) -> None:
    article = callback.data.split(":")[2]
    data = await state.get_data()
    async with async_session() as session:
        groups = await get_groups(session, data["user_id"], article)
    if not groups:
        await callback.answer("Для этой статьи нет категорий", show_alert=True)
        return
    await state.update_data(article=article, groups=groups)
    await callback.message.edit_text(
        f"{ARTICLE_TITLES[article]}\nВыбери категорию:",
        reply_markup=smart_groups_kb(groups),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("smart:grp:"))
async def smart_choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.split(":")[2])
    data = await state.get_data()
    groups: list[str] = data.get("groups", [])
    if index >= len(groups):
        await callback.answer("Категория не найдена, начни заново", show_alert=True)
        return
    group = groups[index]
    async with async_session() as session:
        subs = await get_subcategories(session, data["user_id"], data["article"], group)
    await state.update_data(group=group)
    await state.set_state(SmartAdd.picking_sub)
    await callback.message.edit_text(
        f"{ARTICLE_TITLES[data['article']]} · {group}\nВыбери подкатегорию:",
        reply_markup=smart_subcategories_kb(subs),
    )
    await callback.answer()


@router.callback_query(F.data == "smart:back:group")
async def smart_back_to_group(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(SmartAdd.picking_group)
    await callback.message.edit_text(
        f"{ARTICLE_TITLES[data['article']]}\nВыбери категорию:",
        reply_markup=smart_groups_kb(data.get("groups", [])),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("smart:sub:"))
async def smart_choose_sub(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[2])
    await _finish_save(callback, state, category_id)


# ─── Отмена ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "smart:cancel")
async def smart_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено 🙂 Напиши операцию заново, когда будешь готов.")
    await callback.answer()
