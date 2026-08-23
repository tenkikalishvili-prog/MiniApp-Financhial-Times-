"""Проверка Telegram WebApp initData.

Telegram передаёт из Mini App подписанную строку initData. Мы проверяем её
HMAC-подпись секретом, производным от токена бота (спецификация Telegram),
и достаём id/имя пользователя. Так фронт не может подделать, за кого он.

Док: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


@dataclass
class TelegramUser:
    telegram_id: int
    name: str


class InitDataError(Exception):
    """initData отсутствует, просрочена или подпись не сходится."""


def validate_init_data(
    init_data: str, bot_token: str, max_age_seconds: int = 24 * 3600
) -> TelegramUser:
    """Проверяет подпись initData и возвращает пользователя. Иначе InitDataError."""
    if not init_data:
        raise InitDataError("empty init data")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("no hash")

    # Строка проверки: пары key=value, отсортированные по ключу, через \n
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calc_hash, received_hash):
        raise InitDataError("bad signature")

    # Защита от переигрывания старых данных
    auth_date = int(pairs.get("auth_date", "0"))
    if max_age_seconds and auth_date and time.time() - auth_date > max_age_seconds:
        raise InitDataError("init data expired")

    user_raw = pairs.get("user")
    if not user_raw:
        raise InitDataError("no user")

    user = json.loads(user_raw)
    name = " ".join(
        p for p in (user.get("first_name"), user.get("last_name")) if p
    ).strip()
    return TelegramUser(telegram_id=int(user["id"]), name=name or user.get("username", ""))
