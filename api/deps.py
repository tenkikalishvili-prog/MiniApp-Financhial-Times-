"""Зависимости FastAPI: сессия БД и текущий пользователь."""

from __future__ import annotations

from typing import Annotated, AsyncIterator, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db import async_session
from backend.models import User
from backend.services.users import get_or_create_user

from .auth import InitDataError, TelegramUser, validate_init_data


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    x_telegram_init_data: Annotated[Optional[str], Header()] = None,
) -> User:
    """Определяет пользователя по Telegram initData (заголовок X-Telegram-Init-Data).

    Вне Telegram (dev) — фолбэк на settings.api_dev_user_id, если он задан.
    Пользователь создаётся при первом обращении (с засевом категорий).
    """
    tg: Optional[TelegramUser] = None

    if x_telegram_init_data:
        try:
            tg = validate_init_data(x_telegram_init_data, settings.bot_token)
        except InitDataError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"invalid init data: {exc}",
            ) from exc

    if tg is None:
        if settings.api_dev_user_id is not None:
            tg = TelegramUser(telegram_id=settings.api_dev_user_id, name="Dev")
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="no Telegram init data",
            )

    return await get_or_create_user(session, telegram_id=tg.telegram_id, name=tg.name)


CurrentUser = Annotated[User, Depends(get_current_user)]
