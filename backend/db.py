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
    """Создаёт таблицы, если их ещё нет, и добивает недостающие столбцы (лёгкая миграция)."""
    from backend import models  # noqa: F401  (регистрируем модели в метаданных)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_user_columns)


def _ensure_user_columns(conn) -> None:
    """Идемпотентно добавляет новые столбцы в ``users`` (create_all их не добавляет).

    Работает и для SQLite (локально), и для Postgres (прод). При первом добавлении
    ``onboarded_at`` разово помечает всех существующих пользователей пройденными —
    чтобы владелец и уже заведённые люди НЕ попадали на мастер онбординга; он нужен
    только новичкам, созданным после этого деплоя.
    """
    from sqlalchemy import inspect, text

    is_pg = conn.dialect.name == "postgresql"
    dt_type = "TIMESTAMP" if is_pg else "DATETIME"

    existing = {col["name"] for col in inspect(conn).get_columns("users")}
    # Для новых столбцов с DEFAULT: ADD COLUMN проставит значение всем существующим
    # строкам, поэтому уже заведённые пользователи сохраняют привычные 9:00 / 23:00.
    to_add = {
        "onboarded_at": dt_type,
        "monthly_income": "NUMERIC(12, 2)",
        "discretionary_budget": "NUMERIC(12, 2)",
        "morning_enabled": "BOOLEAN DEFAULT TRUE",
        "morning_hour": "INTEGER DEFAULT 9",
        "evening_enabled": "BOOLEAN DEFAULT TRUE",
        "evening_hour": "INTEGER DEFAULT 23",
    }

    onboarded_was_missing = "onboarded_at" not in existing
    for name, sql_type in to_add.items():
        if name in existing:
            continue
        # На Postgres бот и API стартуют на одной БД одновременно — используем
        # IF NOT EXISTS, чтобы параллельный ALTER не падал с «column already exists».
        # SQLite (локально, один процесс) IF NOT EXISTS не поддерживает — там хватает
        # проверки по inspect выше.
        guard = "IF NOT EXISTS " if is_pg else ""
        conn.execute(text(f"ALTER TABLE users ADD COLUMN {guard}{name} {sql_type}"))

    # Разовый бэкфилл: существующие пользователи считаются онбординг-пройденными.
    # Идемпотентно (WHERE ... IS NULL) — безопасно даже при гонке бота и API.
    if onboarded_was_missing:
        conn.execute(
            text("UPDATE users SET onboarded_at = CURRENT_TIMESTAMP WHERE onboarded_at IS NULL")
        )
