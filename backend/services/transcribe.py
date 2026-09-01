"""S6: транскрипция голосовых сообщений через OpenAI Whisper.

Голосовое (OGG/Opus от Telegram) → текст → дальше общий поток умного ввода
(``interpret`` → карточка-подтверждение бота). Провайдер — OpenAI (ОТДЕЛЬНЫЙ от
Anthropic: свой ключ ``OPENAI_API_KEY`` и биллинг).

Без ключа/пакета/при ошибке — ``None`` (бот вежливо сообщит). Клиент создаётся
лениво (импорт внутри функции), чтобы бот поднимался и без установленного ``openai``.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0

_client = None  # type: ignore[var-annotated]
_client_ready = False


def _get_client():
    """Ленивый ``AsyncOpenAI``. ``None`` → голос выключен."""
    global _client, _client_ready
    if _client_ready:
        return _client
    _client_ready = True
    if not settings.openai_api_key:
        logger.info("OPENAI_API_KEY не задан — голосовой ввод выключен")
        return None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.warning("Пакет openai не установлен — голосовой ввод выключен")
        return None
    _client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=_TIMEOUT_S)
    return _client


def voice_enabled() -> bool:
    """Есть ли рабочий клиент OpenAI (ключ задан, пакет установлен)."""
    return _get_client() is not None


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg") -> Optional[str]:
    """Голос → распознанный текст. ``None`` — выключено, тишина или ошибка."""
    client = _get_client()
    if client is None or not audio_bytes:
        return None
    try:
        result = await client.audio.transcriptions.create(
            model=settings.whisper_model,
            file=(filename, audio_bytes, "audio/ogg"),
            language="ru",
        )
    except Exception as exc:  # noqa: BLE001 — любой сбой API/сети → бот сообщит
        logger.warning("Транскрипция недоступна (%s)", exc)
        return None
    text = (getattr(result, "text", "") or "").strip()
    return text or None
