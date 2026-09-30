"""Точка входа FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db, session_scope
from .models import Dictionary
from .routers import analyses, corpus, dictionaries, jobs
from .services.jobs import recover_stale_jobs
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

# Словарь из исходного dict.txt — чтобы на чистой установке было с чем работать.
SEED_KEYWORDS = [
    "invasion russe",
    "troupes russes",
    "autorités russes",
    "soldats russes",
    "Crimée annexée",
    "union soviétique",
    "république soviétique",
    "ex-république soviétique",
    "forces russes",
    "séparatiste prorusse",
    "armée russe",
    "guerre en Ukraine",
    "Église orthodoxe russe",
    "Église indépendante ukrainienne",
    "ministère russe",
    "président russe",
]


def seed_default_dictionary() -> None:
    with session_scope() as db:
        if db.query(Dictionary).count() > 0:
            return
        db.add(
            Dictionary(
                name="Базовый словарь",
                description="Перенесён из dict.txt десктопной версии",
                keywords=SEED_KEYWORDS,
            )
        )
        logger.info("Создан словарь по умолчанию")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Задачи, оставшиеся «выполняющимися» после падения процесса, живыми
    # уже не станут — честнее сразу показать их упавшими.
    recover_stale_jobs()
    seed_default_dictionary()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


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
