"""Конфигурация приложения. Читает переменные из файла .env."""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Токен Telegram-бота (обязателен)
    bot_token: str

    # Строка подключения к БД. По умолчанию — локальный SQLite-файл.
    database_url: str = "sqlite+aiosqlite:///fintimes.db"

    # Часовой пояс пользователя (для дат и будущих напоминаний)
    timezone: str = "Europe/Moscow"

    # ── HTTP-API (Mini App) ──────────────────────────────────────────────
    # Разрешённые Origin для CORS, через запятую. Локальный фронт Vite — 5173.
    # На проде добавить домен Vercel.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Dev-фолбэк: если запрос пришёл без валидного Telegram initData
    # (например, фронт открыт в обычном браузере), считаем пользователем
    # этот telegram_id. В проде оставить пустым (None) — тогда только initData.
    api_dev_user_id: Optional[int] = None

    # Владелец продукта. Его данные (личный бюджет из старого seed) НЕ трогаем
    # при сбросе пользователей до нейтральной «коробки» (devtools.py reset-box).
    owner_telegram_id: Optional[int] = 344273869

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
