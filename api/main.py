"""FastAPI-приложение Mini App.

Запуск локально (из папки app/):
    source .venv/bin/activate
    uvicorn api.main:app --reload --port 8000

Прод (Railway, web-сервис): см. Procfile.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.db import init_db

from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаём таблицы, если их ещё нет (как и бот при старте)
    await init_db()
    yield


app = FastAPI(title="Financial Times API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
