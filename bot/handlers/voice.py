"""S6: голосовое сообщение → операция (транскрипция через Whisper).

Пользователь надиктовывает «кофе 350» → Whisper распознаёт текст → тот же поток,
что и у текстового умного ввода (``interpret`` → подбор категории → карточка
✅ Записать / ✏️ Другая категория / ✖). Сохранение — ``source='bot_voice'``.

Требует ключ OpenAI. Без ключа/при ошибке — вежливое сообщение.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from backend.db import async_session
from backend.services.smart_input import interpret
from backend.services.transcribe import transcribe, voice_enabled
from backend.services.users import get_or_create_user
from bot.handlers.smart_add import SmartAdd, _confirm_text
from bot.keyboards import smart_confirm_kb

router = Router()


@router.message(F.voice)
async def on_voice(message: Message, state: FSMContext) -> None:
    status = await message.answer("🎙 Слушаю…")

    if not voice_enabled():
        await status.edit_text(
            "🎙 Голосовой ввод включится, когда будет добавлен ключ OpenAI.\n"
            "Пока напиши текстом («кофе 350») или пришли фото чека."
        )
        return

    try:
        file = await message.bot.get_file(message.voice.file_id)
        buffer = await message.bot.download_file(file.file_path)
        audio_bytes = buffer.read()
    except Exception:  # noqa: BLE001 — сбой загрузки → просим повторить
        await status.edit_text("Не смог скачать голосовое 🤔 Попробуй ещё раз.")
        return

    text = await transcribe(audio_bytes)
    if not text:
        await status.edit_text("Не расслышал 🤔 Скажи короче: «кофе 350».")
        return

    heard = f"🎙 Услышал: «{text}»\n\n"

    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name or "",
        )
        uid = user.id
        parsed = await interpret(session, uid, text)
        cat_id = parsed.category.id if parsed.category else None
        card = _confirm_text(parsed)

    if parsed.amount is None:
        await status.edit_text(heard + "Сумму не понял. Скажи, например: «кофе 350».")
        return

    await state.set_state(SmartAdd.confirming)
    await state.update_data(
        user_id=uid,
        amount=str(parsed.amount),
        description=(parsed.description[:255] or None),
        article=parsed.article,
        category_id=cat_id,
        source="bot_voice",
    )
    await status.edit_text(heard + card, reply_markup=smart_confirm_kb(matched=parsed.category is not None))
