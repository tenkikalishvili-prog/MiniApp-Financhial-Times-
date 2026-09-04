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
        await conn.run_sync(_ensure_ledger_columns)
        await conn.run_sync(_backfill_ledger)


def _ensure_ledger_columns(conn) -> None:
    """Идемпотентно готовит таблицы под единый реестр «Операции» (цели + долги).

    Добавляет ссылки/поля в ``transactions`` и ``started_on`` в ``debts``; на Postgres
    снимает NOT NULL с ``transactions.category_id`` (у операций по целям/долгам категории
    нет). Работает и для SQLite (локально), и для Postgres (прод).
    """
    from sqlalchemy import inspect, text

    is_pg = conn.dialect.name == "postgresql"
    guard = "IF NOT EXISTS " if is_pg else ""

    tx_cols = {col["name"] for col in inspect(conn).get_columns("transactions")}
    tx_add = {
        "goal_id": "INTEGER",
        "debt_id": "INTEGER",
        "debt_role": "VARCHAR(10)",
        "flow": "VARCHAR(3)",
    }
    for name, sql_type in tx_add.items():
        if name in tx_cols:
            continue
        conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {guard}{name} {sql_type}"))

    debt_cols = {col["name"] for col in inspect(conn).get_columns("debts")}
    if "started_on" not in debt_cols:
        conn.execute(text(f"ALTER TABLE debts ADD COLUMN {guard}started_on DATE"))

    # Postgres: у операций по целям/долгам категории нет → снимаем NOT NULL. Идемпотентно
    # (на уже nullable-столбце — no-op). SQLite: create_all на свежей БД уже создаёт столбец
    # nullable (по модели); старую локальную dev-БД не перестраиваем (прод — Postgres).
    if is_pg:
        conn.execute(text("ALTER TABLE transactions ALTER COLUMN category_id DROP NOT NULL"))


def _backfill_ledger(conn) -> None:
    """Разовый перенос старых долгов и их возвратов (S9) в единый реестр «Операции».

    Для каждого долга без операции-тела создаёт ``principal`` (дата = ``started_on`` или
    дата создания долга; ``flow`` = приток для «я должен», отток для «мне должны») и
    переносит каждый его ``DebtPayment`` в операцию ``payment`` (противоположный ``flow``).
    Идемпотентно ПО ДОЛГУ: если у долга уже есть ``principal`` — он (и его платежи)
    пропускается. На Postgres берём advisory-lock, чтобы параллельный старт бота и API
    не задвоил перенос. Старые ``debt_payments`` не удаляем — остаются резервной копией.
    """
    from datetime import date as _date
    from datetime import datetime as _datetime

    from sqlalchemy import text

    def _as_date(value):
        """created_at/started_on → date (str на SQLite, datetime/date на Postgres)."""
        if value is None:
            return None
        if isinstance(value, _datetime):
            return value.date()
        if isinstance(value, _date):
            return value
        return _date.fromisoformat(str(value)[:10])

    is_pg = conn.dialect.name == "postgresql"
    if is_pg:
        conn.execute(text("SELECT pg_advisory_xact_lock(915623)"))

    debts = conn.execute(
        text("SELECT id, user_id, direction, amount, created_at, started_on FROM debts")
    ).fetchall()

    for d in debts:
        debt_id = d[0]
        has_principal = conn.execute(
            text(
                "SELECT COUNT(*) FROM transactions "
                "WHERE debt_id = :d AND debt_role = 'principal'"
            ),
            {"d": debt_id},
        ).scalar()
        if has_principal:
            continue  # уже перенесён

        user_id, direction, amount, created_at, started_on = d[1], d[2], d[3], d[4], d[5]
        # Дата движения тела: приоритет started_on → дата создания долга → сегодня.
        move_date = _as_date(started_on) or _as_date(created_at) or _date.today()

        principal_flow = "in" if direction == "owe" else "out"
        payment_flow = "out" if direction == "owe" else "in"

        conn.execute(
            text(
                "INSERT INTO transactions "
                "(user_id, date, article, amount, source, debt_id, debt_role, flow) "
                "VALUES (:u, :dt, 'debt', :amt, 'migrate', :debt, 'principal', :flow)"
            ),
            {"u": user_id, "dt": move_date, "amt": amount, "debt": debt_id, "flow": principal_flow},
        )
        if started_on is None:
            conn.execute(
                text("UPDATE debts SET started_on = :dt WHERE id = :d"),
                {"dt": move_date, "d": debt_id},
            )

        payments = conn.execute(
            text("SELECT user_id, amount, on_date FROM debt_payments WHERE debt_id = :d"),
            {"d": debt_id},
        ).fetchall()
        for p in payments:
            conn.execute(
                text(
                    "INSERT INTO transactions "
                    "(user_id, date, article, amount, source, debt_id, debt_role, flow) "
                    "VALUES (:u, :dt, 'debt', :amt, 'migrate', :debt, 'payment', :flow)"
                ),
                {"u": p[0], "dt": _as_date(p[2]) or move_date, "amt": p[1], "debt": debt_id, "flow": payment_flow},
            )


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
        "reminders_enabled": "BOOLEAN DEFAULT TRUE",
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
