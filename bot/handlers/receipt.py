"""S7: фото чека → операция (OCR через Claude vision).

Пользователь присылает фото чека / банковского уведомления → Claude извлекает
сумму, продавца и (если видно) дату → подбираем подкатегорию (та же логика S4/S5)
→ показываем ту же карточку-подтверждение, что и у текстового умного ввода
(✅ Записать / ✏️ Другая категория / ✖). Сохранение идёт через общий поток
``smart_add`` с ``source='bot_photo'``.

Требует ключ Anthropic (тот же, что S5). Без ключа/при ошибке — вежливое сообщение.
"""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from backend.db import async_session
from backend.services.ai_categorize import ai_enabled
from backend.services.ocr_receipt import extract_receipt
from backend.services.smart_input import ParsedInput, guess_article, resolve_category
from backend.services.users import get_or_create_user
from bot.handlers.smart_add import SmartAdd, _confirm_text
from bot.keyboards import smart_confirm_kb

router = Router()


@router.message(F.photo)
async def on_photo(message: Message, state: FSMContext) -> None:
    status = await message.answer("🧾 Читаю чек…")

    if not ai_enabled():
        await status.edit_text(
            "📸 Распознавание чеков включится, когда будет добавлен ключ AI.\n"
            "Пока добавь операцию текстом («продукты 1500») или кнопкой "
            "«➕ Добавить операцию»."
        )
        return

    # Берём самое крупное изображение из набора превью Telegram.
    photo = message.photo[-1]
    try:
        file = await message.bot.get_file(photo.file_id)
        buffer = await message.bot.download_file(file.file_path)
        image_bytes = buffer.read()
    except Exception:  # noqa: BLE001 — сбой загрузки → просим повторить
        await status.edit_text("Не смог скачать изображение 🤔 Попробуй ещё раз.")
        return

    data = await extract_receipt(image_bytes, "image/jpeg")
    if data is None or data.amount is None:
        await status.edit_text(
            "Не смог разобрать чек 🤔\n"
            "Пришли фото чётче или введи операцию текстом: «продукты 1500»."
        )
        return

    # Подпись к фото (если есть) помогает точнее подобрать категорию.
    caption = (message.caption or "").strip()
    hint = " ".join(x for x in [caption, data.description] if x)
    article = guess_article(hint)

    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name or "",
        )
        uid = user.id
        category = await resolve_category(session, uid, hint, article)
        if category is not None:
            article = category.article
        parsed = ParsedInput(
            amount=data.amount,
            description=data.description,
            raw="",
            article=article,
            category=category,
        )
        cat_id = category.id if category else None
        text = _confirm_text(parsed)

    await state.set_state(SmartAdd.confirming)
    await state.update_data(
        user_id=uid,
        amount=str(data.amount),
        description=(data.description[:255] or None),
        article=article,
        category_id=cat_id,
        source="bot_photo",
        on_date=(data.on_date.isoformat() if data.on_date else None),
    )

    if data.on_date and data.on_date != date.today():
        text += f"\n📅 Дата с чека: {data.on_date.strftime('%d.%m.%Y')}"

    await status.edit_text(text, reply_markup=smart_confirm_kb(matched=category is not None))
