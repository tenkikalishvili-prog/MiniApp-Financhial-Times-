"""Подключение к базе данных и фабрика сессий.

Используем асинхронный SQLAlchemy. Сейчас за кулисами SQLite (файл на диске),
позже эту же строку подключения заменим на Postgres — код не изменится.
"""

import re

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings


class Base(DeclarativeBase):
    """Базовый класс для всех моделей (таблиц)."""


def _normalize_db_url(url: str) -> str:
    """Приводит строку подключения к async-драйверу.

    Railway/Heroku отдают Postgres-URL как ``postgres://`` или ``postgresql://``,
    а асинхронному SQLAlchemy нужен ``postgresql+asyncpg://``. Локальный SQLite
    (``sqlite+aiosqlite://``) остаётся как есть.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Драйвер asyncpg не понимает libpq-параметр sslmode — убираем его,
    # SSL согласуется автоматически (для приватной сети Railway не нужен).
    if "+asyncpg" in url:
        url = re.sub(r"[?&]sslmode=[^&]+", "", url)

    return url


engine = create_async_engine(_normalize_db_url(settings.database_url), echo=False)

# Фабрика сессий — через неё каждый обработчик получает соединение с БД
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет (первый запуск)."""
    from backend import models  # noqa: F401  (регистрируем модели в метаданных)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
