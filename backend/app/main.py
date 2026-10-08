"""Точка входа FastAPI."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import _USING_TURSO as USING_TURSO
from .db import init_db, session_scope
from .routers import analyses, corpus, dictionaries, jobs
from .services.jobs import recover_stale_jobs
from .services.retry_scheduler import start_retry_scheduler, stop_retry_scheduler
from .services.scheduler import start_scheduler, stop_scheduler

# Находка 2026-09-29: на однослужебном деплое (Replit) фронтенд и бэкенд —
# один процесс, nginx перед статикой не поднимается. FastAPI сам отдаёт
# собранный dist/ фронтенда. Локально (npm run dev через vite) этой папки
# нет, и весь этот блок просто не активируется — обычная разработка не
# меняется ни на строчку.
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

def _startup_tasks() -> None:
    """Всё, что обращается к базе при запуске, с замерами по шагам.

    Находка 2026-10-08: на Turso (удалённая база) этот блок занимает заметное
    время — Replit не дождался открытия порта и откатил публикацию. Поэтому на
    удалённой базе он выполняется в фоне, а сервер открывает порт сразу.
    """
    def step(name, fn):
        started = time.perf_counter()
        fn()
        logger.info("Запуск: %s — %.1f с", name, time.perf_counter() - started)

    step("инициализация схемы базы", init_db)
    # Задачи, оставшиеся «выполняющимися» после падения процесса, живыми
    # уже не станут — честнее сразу показать их упавшими.
    step("восстановление зависших задач", recover_stale_jobs)
    step("планировщик автосбора", start_scheduler)
    # Независим от scheduler_enabled: это пользовательская кнопка "попробовать
    # снова через N часов", не ночной автосбор.
    step("планировщик отложенного повтора", start_retry_scheduler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if USING_TURSO:
        def background() -> None:
            try:
                _startup_tasks()
            except Exception:  # noqa: BLE001 — сервер уже слушает порт, пусть работает
                logger.exception("Фоновая инициализация не завершилась")

        threading.Thread(target=background, name="startup-tasks", daemon=True).start()
        logger.info("Удалённая база: инициализация идёт в фоне, порт открывается сразу")
    else:
        _startup_tasks()
    try:
        yield
    finally:
        stop_scheduler()
        stop_retry_scheduler()


app = FastAPI(
    title="ParserFR",
    description=(
        "Анализ употребления французских ключевых слов в статьях Le Figaro. "
        "Сбор корпуса отделён от анализа: статьи скачиваются один раз, "
        "а словарь можно менять и пересчитывать сколько угодно."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(corpus.router)
app.include_router(dictionaries.router)
app.include_router(analyses.router)
app.include_router(jobs.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        """Раздаёт index.html на любой путь, не начинающийся с /api —
        клиентский роутер (react-router) сам разберёт, что показать."""
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
