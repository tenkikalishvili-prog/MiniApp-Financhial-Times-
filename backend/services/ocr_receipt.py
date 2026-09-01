"""S7: распознавание чека/банковского уведомления через Claude vision.

Фото → Claude извлекает сумму, продавца/назначение и (если видно) дату. Результат
уходит в общий поток умного ввода: подбор подкатегории (``resolve_category``) →
карточка-подтверждение бота (та же, что у текстового ввода S4/S5).

Использует общий клиент Anthropic из ``ai_categorize`` (тот же ключ, что и S5).
Без ключа/пакета/при ошибке — возвращает ``None`` (бот вежливо сообщит).
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from backend.config import settings
from backend.services.ai_categorize import get_client

logger = logging.getLogger(__name__)

# Vision дольше текстовой категоризации — даём запросу больше времени.
_TIMEOUT_S = 30.0
_MAX_TOKENS = 300
_MAX_AMOUNT = Decimal("100000000")

_SYSTEM = (
    "Ты извлекаешь данные операции из фото чека, квитанции или банковского "
    "уведомления/скриншота. Верни СТРОГО один JSON-объект без пояснений, в формате:\n"
    '{"amount": <итоговая сумма к оплате, число, точка как разделитель, без валюты>, '
    '"merchant": "<кто/за что: магазин, услуга или назначение платежа, кратко>", '
    '"date": "<YYYY-MM-DD или null, если даты не видно>"}\n'
    "amount — ИТОГ операции (к оплате/списано), не сдача и не отдельные позиции. "
    "Если это не чек/платёж или сумму не разобрать — верни {\"amount\": null}."
)


@dataclass
class ReceiptData:
    """Разобранные с изображения данные операции."""

    amount: Optional[Decimal]
    description: str
    on_date: Optional[date]


def _parse_amount(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0 or amount > _MAX_AMOUNT:
        return None
    return amount


def _parse_date(value) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


async def extract_receipt(image_bytes: bytes, media_type: str) -> Optional[ReceiptData]:
    """Фото → ReceiptData. ``None`` — AI выключен, не чек или ошибка.

    ``ReceiptData.amount is None`` → распознать сумму не удалось (не чек/размыто).
    """
    client = get_client()
    if client is None or not image_bytes:
        return None

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    try:
        resp = await client.with_options(timeout=_TIMEOUT_S).messages.create(
            model=settings.ai_model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": "Извлеки данные операции из этого изображения."},
                    ],
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 — любой сбой API/сети → бот сообщит
        logger.warning("OCR чека недоступен (%s)", exc)
        return None

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except (json.JSONDecodeError, ValueError):
        return None

    amount = _parse_amount(data.get("amount"))
    merchant = str(data.get("merchant") or "").strip()[:255]
    return ReceiptData(amount=amount, description=merchant, on_date=_parse_date(data.get("date")))
