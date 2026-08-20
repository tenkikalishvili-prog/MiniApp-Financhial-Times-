"""Клавиатуры бота: главное меню и шаги мастера ввода операции."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from backend.models import Category

# Человеческие названия статей + эмодзи
ARTICLES = [
    ("expense", "💸 Расход"),
    ("income", "💰 Доход"),
    ("debt", "⚠️ Долг"),
]


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Постоянная кнопка под полем ввода."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="➕ Добавить операцию")]],
        resize_keyboard=True,
    )


def article_kb() -> InlineKeyboardMarkup:
    """Шаг 1: выбор статьи."""
    builder = InlineKeyboardBuilder()
    for code, title in ARTICLES:
        builder.button(text=title, callback_data=f"add:art:{code}")
    builder.button(text="✖️ Отмена", callback_data="add:cancel")
    builder.adjust(1)
    return builder.as_markup()


def groups_kb(groups: list[str]) -> InlineKeyboardMarkup:
    """Шаг 2: выбор категории (группы). Кодируем индексом из сохранённого списка."""
    builder = InlineKeyboardBuilder()
    for index, group in enumerate(groups):
        builder.button(text=group, callback_data=f"add:grp:{index}")
    builder.button(text="⬅️ Назад", callback_data="add:back:article")
    builder.adjust(1)
    return builder.as_markup()


def subcategories_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    """Шаг 3: выбор подкатегории. В callback — реальный id категории."""
    builder = InlineKeyboardBuilder()
    for category in categories:
        emoji = f"{category.emoji} " if category.emoji else ""
        builder.button(
            text=f"{emoji}{category.name}", callback_data=f"add:sub:{category.id}"
        )
    builder.button(text="⬅️ Назад", callback_data="add:back:group")
    # 1 колонка — длинные названия не обрезаются ни на телефоне, ни на десктопе (адаптивность №11)
    builder.adjust(1)
    return builder.as_markup()
