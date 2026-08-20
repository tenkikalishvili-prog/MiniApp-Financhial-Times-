"""Конфигурация приложения. Читает переменные из файла .env."""

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


settings = Settings()
