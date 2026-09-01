"""S5: автокатегоризация свободного ввода через Claude API.

Надстройка над эвристикой S4 (``backend/services/smart_input.py``). Claude видит
ВСЕ активные подкатегории пользователя (по всем статьям) и выбирает лучшую под
описание — это умнее словаря ``KEYWORD_MAP``: ловит неочевидные формулировки,
переименованные и добавленные вручную категории, сам определяет статью
(расход/доход/долг) по смыслу. Задача — простая классификация, поэтому берём
дешёвую и быструю модель (Haiku).

Фолбэк (никогда не роняем бот): нет ключа ``ANTHROPIC_API_KEY`` / пакет
``anthropic`` не установлен / ошибка API / таймаут → возвращаем ``None``, и
вызывающий код (``smart_input.interpret``) откатывается на детерминированную
эвристику S4. Пока ключ не добавлен в окружение, поведение продукта = S4.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from backend.config import settings
from backend.models import Category

logger = logging.getLogger(__name__)

# Бот должен отвечать быстро: при таймауте молча падаем на эвристику.
_TIMEOUT_S = 8.0
# Ответ — одно число (id подкатегории), больше и не нужно.
_MAX_TOKENS = 16

_ARTICLE_RU = {"expense": "Расход", "income": "Доход", "debt": "Долг"}

_SYSTEM = (
    "Ты — помощник для учёта личных финансов. Пользователь пишет короткое описание "
    "операции (например «кофе», «такси до дома», «зарплата», «аптека»). Тебе дан "
    "список его подкатегорий с числовыми id. Выбери ОДНУ подкатегорию, которая точнее "
    "всего подходит под описание. Отвечай СТРОГО одним числом — id выбранной "
    "подкатегории из списка. Если ни одна подкатегория явно не подходит — ответь 0. "
    "Никакого другого текста, только число."
)

# Ленивый singleton клиента: создаётся при первом успешном обращении.
_client = None  # type: ignore[var-annotated]
_client_ready = False


def _get_client():
    """Ленивая инициализация ``AsyncAnthropic``. ``None`` → работаем без AI (фолбэк).

    Импорт пакета и создание клиента — внутри функции, чтобы модуль (и весь бот)
    поднимался даже без установленного ``anthropic`` и без ключа.
    """
    global _client, _client_ready
    if _client_ready:
        return _client
    _client_ready = True  # больше не пытаемся при каждом сообщении
    if not settings.anthropic_api_key:
        logger.info("ANTHROPIC_API_KEY не задан — автокатегоризация выключена (фолбэк на эвристику)")
        return None
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("Пакет anthropic не установлен — автокатегоризация выключена (фолбэк)")
        return None
    _client = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=_TIMEOUT_S)
    return _client


def _build_catalog(categories: list[Category]) -> str:
    """Нумерованный список подкатегорий для промпта: «12. Расход · Траты · Кафе»."""
    return "\n".join(
        f"{c.id}. {_ARTICLE_RU.get(c.article, c.article)} · {c.group} · {c.name}"
        for c in categories
    )


def _parse_id(text: str, valid_ids: set[int]) -> Optional[int]:
    """Достаёт id из ответа модели. None — если 0/мусор/id не из списка."""
    match = re.search(r"\d+", text or "")
    if not match:
        return None
    cid = int(match.group())
    return cid if cid in valid_ids else None


async def ai_match_category(
    description: str, categories: list[Category]
) -> Optional[int]:
    """Подбирает id подкатегории через Claude. ``None`` → фолбэк на эвристику S4.

    Возвращает id из ``categories`` либо ``None`` (модель недоступна, ответила 0,
    вернула мусор или id не из списка).
    """
    client = _get_client()
    if client is None or not description or not categories:
        return None

    catalog = _build_catalog(categories)
    valid_ids = {c.id for c in categories}
    user_msg = (
        f"Описание операции: «{description}»\n\n"
        f"Подкатегории пользователя:\n{catalog}\n\n"
        "id подходящей подкатегории (или 0):"
    )
    try:
        resp = await client.messages.create(
            model=settings.ai_model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:  # noqa: BLE001 — любой сбой API/сети → тихий фолбэк
        logger.warning("Автокатегоризация недоступна (%s) — фолбэк на эвристику", exc)
        return None

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    return _parse_id(text, valid_ids)
