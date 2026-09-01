"""Умный ввод (S4): свободный текст «кофе 350» → готовая операция.

Детерминированный разбор БЕЗ внешнего AI: парсер суммы + эвристический подбор
подкатегории по ключевым словам и названиям пользовательских категорий. Дёшево,
мгновенно, работает офлайн. На этом же модуле позже вырастут:
  • S5 — автокатегоризация через Claude API (заменит/дополнит ``match_category``);
  • приложение — то же поле умного ввода на экране «Добавить» (переиспользует ``interpret``).

Иерархия учёта: Статья (income/expense/debt) → Категория (group) → Подкатегория (name).
Матчер работает по подкатегориям пользователя (у каждого свой набор — засев из коробки),
поэтому подстраивается и под переименованные/добавленные вручную категории.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Category
from backend.services.ai_categorize import ai_match_category

# ─────────────────────────────────────────────────────────────────────────
# Разбор суммы
# ─────────────────────────────────────────────────────────────────────────

# Число: подряд идущие цифры с пробелами-разделителями тысяч (обычными и NBSP)
# и необязательной дробной частью через точку/запятую (до 2 знаков).
_AMOUNT_RE = re.compile(r"\d[\d  ]*(?:[.,]\d{1,2})?")
# Слова-валюты вокруг суммы, которые вычищаем из описания. Символ ₽ — всегда;
# буквенные «руб»/«р» — только как отдельные слова (чтобы не резать «рыба» и т.п.).
_CURRENCY_RE = re.compile(
    r"₽|(?<![а-яёa-z])(?:руб(?:лей|ля)?\.?|rub|р\.?)(?![а-яёa-z])",
    re.IGNORECASE,
)
# Верхняя граница разумной суммы — защита от телефонов/номеров карт.
_MAX_AMOUNT = Decimal("100000000")


def _clean_description(text: str) -> str:
    """Чистит остаток текста: убирает валюту, лишние пробелы и знаки по краям."""
    text = _CURRENCY_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n.,;:-—·")


def parse_amount(text: str) -> tuple[Optional[Decimal], str]:
    """«кофе 350» → (Decimal('350'), 'кофе'). Возвращает (сумма | None, описание).

    Если чисел несколько (например «2 кофе 350»), берём самый крупный номинал —
    обычно это и есть сумма, а не количество. Валюта («р», «₽», «руб») отбрасывается.
    """
    candidates: list[tuple[Decimal, int, int]] = []
    for match in _AMOUNT_RE.finditer(text):
        token = match.group()
        norm = token.replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            value = Decimal(norm)
        except InvalidOperation:
            continue
        if value <= 0 or value > _MAX_AMOUNT:
            continue
        candidates.append((value, match.start(), match.end()))

    if not candidates:
        return None, _clean_description(text)

    value, start, end = max(candidates, key=lambda c: (c[0], c[1]))
    description = text[:start] + " " + text[end:]
    return value, _clean_description(description)


# ─────────────────────────────────────────────────────────────────────────
# Подбор категории
# ─────────────────────────────────────────────────────────────────────────

# Ключевые слова → каноническое имя подкатегории из коробки (backend/seed.py).
# Если пользователь оставил дефолтные имена — подбор точный; если переименовал,
# срабатывает прямое совпадение слова с названием подкатегории (см. _score).
KEYWORD_MAP: dict[str, list[str]] = {
    # ── Траты (повседневные) ─────────────────────────────────────────────
    "Продукты": [
        "продукты", "продукт", "магазин", "супермаркет", "еда", "пятерочка",
        "пятёрочка", "магнит", "ашан", "лента", "перекресток", "перекрёсток",
        "вкусвилл", "дикси", "молоко", "хлеб", "овощи", "мясо", "бакалея",
    ],
    "Кафе и рестораны": [
        "кофе", "латте", "капучино", "кофейня", "кафе", "ресторан", "рестораны",
        "обед", "ужин", "завтрак", "бар", "пиво", "шаурма", "бургер", "пицца",
        "суши", "роллы", "столовая", "перекус", "фастфуд", "макдак", "кола",
    ],
    "Транспорт": [
        "транспорт", "метро", "автобус", "троллейбус", "трамвай", "проезд",
        "электричка", "тройка", "маршрутка", "проездной", "бензин", "заправка",
    ],
    "Такси": ["такси", "убер", "uber", "яндекстакси", "ситимобил", "болт", "bolt"],
    "Развлечения": [
        "развлечения", "кино", "концерт", "театр", "боулинг", "клуб", "игра",
        "игры", "стендап", "выставка", "музей", "квест", "каток",
    ],
    "Одежда и уход": [
        "одежда", "обувь", "футболка", "джинсы", "куртка", "кроссовки",
        "парикмахер", "стрижка", "барбер", "маникюр", "косметика", "уход",
        "салон", "спа",
    ],
    "Здоровье": [
        "аптека", "лекарства", "лекарство", "таблетки", "врач", "доктор",
        "здоровье", "анализы", "стоматолог", "зубной", "клиника", "витамины",
    ],
    "Прочее": ["прочее", "разное"],
    # ── Обязательные платежи ─────────────────────────────────────────────
    "Аренда / ипотека": ["аренда", "квартира", "ипотека", "жилье", "жильё", "съем", "съём"],
    "Коммунальные услуги": [
        "жкх", "коммуналка", "коммунальные", "свет", "электричество", "вода",
        "газ", "отопление", "квитанция",
    ],
    "Связь и интернет": [
        "связь", "интернет", "мобильный", "симка", "роутер", "мтс", "билайн",
        "мегафон", "теле2", "tele2", "йота", "yota",
    ],
    "Подписки": [
        "подписка", "подписки", "нетфликс", "netflix", "spotify", "спотифай",
        "ivi", "окко", "youtube", "ютуб", "плюс", "premium",
    ],
    "Кредиты": ["кредит", "кредиты", "займ", "рассрочка", "платеж", "платёж"],
    # ── Доход ────────────────────────────────────────────────────────────
    "Зарплата": ["зарплата", "зп", "аванс", "оклад", "получка", "премия"],
    "Подработка": ["подработка", "фриланс", "халтура", "шабашка", "калым"],
    "Прочий доход": ["кэшбэк", "кешбэк", "кешбек", "дивиденды", "процент", "подарок"],
}

# Обратный индекс: слово → каноническое имя подкатегории.
_REVERSE_KEYWORDS: dict[str, str] = {
    word: canonical for canonical, words in KEYWORD_MAP.items() for word in words
}

# Слова-признаки статьи «доход» (иначе по умолчанию — расход).
_INCOME_WORDS = {
    "зарплата", "зп", "аванс", "оклад", "получка", "премия", "подработка",
    "фриланс", "халтура", "шабашка", "калым", "кэшбэк", "кешбэк", "кешбек",
    "дивиденды", "стипендия", "пенсия", "доход",
}

_MATCH_THRESHOLD = 3


def _tokens(text: str, min_len: int = 2) -> list[str]:
    """Слова из текста (буквы рус/лат), в нижнем регистре, короче ``min_len`` — отброшены."""
    return [t for t in re.findall(r"[a-zA-Zа-яёА-ЯЁ]+", text.lower()) if len(t) >= min_len]


def guess_article(hint: str) -> str:
    """Определяет статью по описанию. Есть слово-доход → 'income', иначе 'expense'."""
    tokens = set(_tokens(hint))
    if tokens & _INCOME_WORDS:
        return "income"
    return "expense"


def _score(category: Category, tokens: list[str]) -> int:
    """Насколько подкатегория подходит под слова описания (больше — лучше)."""
    name_lower = category.name.lower()
    name_tokens = _tokens(category.name, min_len=3)
    score = 0
    for token in tokens:
        # Прямое совпадение слова с названием подкатегории (ловит и переименованные).
        if len(token) >= 3 and (
            token in name_lower or any(token in nt or nt in token for nt in name_tokens)
        ):
            score += 3
        # Совпадение по словарю ключевых слов → каноническое имя из коробки.
        canonical = _REVERSE_KEYWORDS.get(token)
        if canonical and canonical.lower() == name_lower:
            score += 4
    return score


async def _active_subcategories(
    session: AsyncSession, user_id: int, article: str
) -> list[Category]:
    result = await session.execute(
        select(Category)
        .where(
            Category.user_id == user_id,
            Category.article == article,
            Category.is_archived == False,  # noqa: E712
        )
        .order_by(Category.sort_order)
    )
    return list(result.scalars())


async def _all_active_categories(
    session: AsyncSession, user_id: int
) -> list[Category]:
    """Все активные подкатегории пользователя по всем статьям (для AI-подбора S5)."""
    result = await session.execute(
        select(Category)
        .where(
            Category.user_id == user_id,
            Category.is_archived == False,  # noqa: E712
        )
        .order_by(Category.article, Category.sort_order)
    )
    return list(result.scalars())


async def match_category(
    session: AsyncSession, user_id: int, hint: str, article: str
) -> Optional[Category]:
    """Подбирает подкатегорию пользователя под описание. None — если не уверены.

    Порог: минимум одно уверенное совпадение. При равенстве очков предпочитаем
    служебную группу «Траты» (самые частые операции), затем меньший sort_order.
    """
    tokens = _tokens(hint)
    if not tokens:
        return None

    cats = await _active_subcategories(session, user_id, article)
    best: Optional[Category] = None
    best_score = 0
    for cat in cats:
        score = _score(cat, tokens)
        if score > best_score:
            best, best_score = cat, score
        elif score == best_score and score > 0 and best is not None:
            if _rank(cat) < _rank(best):
                best = cat
    return best if best_score >= _MATCH_THRESHOLD else None


def _rank(category: Category) -> tuple[int, int]:
    """Ключ предпочтения при равенстве очков: «Траты» вперёд, затем sort_order."""
    return (0 if category.group == "Траты" else 1, category.sort_order)


# ─────────────────────────────────────────────────────────────────────────
# Единая точка входа
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ParsedInput:
    """Результат разбора свободного текста."""

    amount: Optional[Decimal]  # None → сумму распознать не удалось
    description: str           # очищенное описание (без суммы и валюты)
    raw: str                   # исходный текст
    article: str               # предполагаемая статья: expense | income | debt
    category: Optional[Category]  # подобранная подкатегория (или None)


async def resolve_category(
    session: AsyncSession, user_id: int, description: str, article: str
) -> Optional[Category]:
    """Подбор подкатегории по описанию: Claude (S5) с фолбэком на эвристику S4.

    Единая точка для всех каналов умного ввода: свободный текст, OCR чеков (S7),
    расшифровка голоса (S6). ``article`` — предполагаемая статья для фолбэк-эвристики
    (AI выбирает по всем статьям сам).
    """
    if not description:
        return None
    # S5: сначала Claude по всем активным подкатегориям пользователя.
    all_cats = await _all_active_categories(session, user_id)
    cid = await ai_match_category(description, all_cats)
    if cid is not None:
        cat = next((c for c in all_cats if c.id == cid), None)
        if cat is not None:
            return cat
    # Фолбэк S4: AI выключен / вернул 0 / ошибка → эвристика по предполагаемой статье.
    return await match_category(session, user_id, description, article)


async def interpret(session: AsyncSession, user_id: int, text: str) -> ParsedInput:
    """«кофе 350» → ParsedInput(сумма, описание, статья, подкатегория)."""
    amount, description = parse_amount(text or "")
    article = guess_article(description)
    category: Optional[Category] = None

    if description:
        category = await resolve_category(session, user_id, description, article)
    if category is not None:
        article = category.article  # статья берётся из подобранной подкатегории

    return ParsedInput(
        amount=amount,
        description=description,
        raw=(text or "").strip(),
        article=article,
        category=category,
    )
