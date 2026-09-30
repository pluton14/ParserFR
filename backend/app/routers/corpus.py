"""Корпус: состояние покрытия по дням и запуск сбора."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Article, ArticleStatus, HarvestedDay, JobType
from ..schemas import CorpusSummary, DayCoverage, HarvestRequest, JobOut
from ..services.harvest import run_harvest
from ..services.jobs import registry
from ..services.scheduler import next_run_time

router = APIRouter(prefix="/api/corpus", tags=["corpus"])


def _database_bytes() -> int:
    url = settings.resolved_database_url
    if not url.startswith("sqlite:///"):
        return 0
    path = Path(url.replace("sqlite:///", ""))
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            total += candidate.stat().st_size
    return total


@router.get("/summary", response_model=CorpusSummary)
def corpus_summary(db: Session = Depends(get_db)) -> CorpusSummary:
    by_status = dict(
        db.execute(select(Article.status, func.count()).group_by(Article.status)).all()
    )
    bounds = db.execute(
        select(func.min(Article.published_date), func.max(Article.published_date))
    ).one()
    days_covered = db.execute(
        select(func.count(func.distinct(Article.published_date)))
    ).scalar_one()
    last_harvest = db.execute(select(func.max(HarvestedDay.harvested_at))).scalar_one()

    return CorpusSummary(
        days_covered=days_covered or 0,
        articles_total=sum(by_status.values()),
        articles_with_text=(
            by_status.get(ArticleStatus.OK.value, 0)
            + by_status.get(ArticleStatus.TRUNCATED.value, 0)
        ),
        articles_truncated=by_status.get(ArticleStatus.TRUNCATED.value, 0),
        articles_premium=by_status.get(ArticleStatus.PREMIUM.value, 0),
        articles_failed=by_status.get(ArticleStatus.FAILED.value, 0),
        first_day=bounds[0],
        last_day=bounds[1],
        database_bytes=_database_bytes(),
        last_harvest_at=last_harvest,
    )


@router.get("/coverage", response_model=list[DayCoverage])
def corpus_coverage(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[HarvestedDay]:
    """Покрытие по дням — чтобы было видно, за какие даты анализ уже возможен."""
    stmt = select(HarvestedDay).order_by(HarvestedDay.day.desc())
    if start_date:
        stmt = stmt.where(HarvestedDay.day >= start_date)
    if end_date:
        stmt = stmt.where(HarvestedDay.day <= end_date)
    return list(db.execute(stmt).scalars().all())


@router.get("/categories")
def corpus_categories(db: Session = Depends(get_db)) -> list[dict]:
    """Категории статей с текстом — для мультивыбора фильтра в анализе."""
    rows = db.execute(
        select(Article.category, func.count())
        .where(Article.status.in_([ArticleStatus.OK.value, ArticleStatus.TRUNCATED.value]))
        .group_by(Article.category)
        .order_by(func.count().desc())
    ).all()
    return [{"category": category, "count": count} for category, count in rows if category]


@router.get("/schedule")
def schedule_info() -> dict:
    run_at = next_run_time()
    return {
        "enabled": settings.scheduler_enabled,
        "hour": settings.scheduler_hour,
        "minute": settings.scheduler_minute,
        "lookback_days": settings.scheduler_lookback_days,
        "next_run_at": run_at.isoformat() if run_at else None,
    }


@router.post("/harvest", response_model=JobOut, status_code=202)
def start_harvest(payload: HarvestRequest) -> JobOut:
    """Запускает фоновый сбор корпуса за период."""
    if settings.readonly_demo:
        raise HTTPException(
            status_code=403,
            detail="Это демо-копия с зафиксированным снимком корпуса — сбор здесь отключён.",
        )

    active = registry.active_of_type(JobType.HARVEST.value)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Сбор уже идёт. Дождитесь окончания или остановите текущий.",
                "job_id": active.id,
            },
        )

    if payload.end_date > date.today():
        raise HTTPException(status_code=400, detail="Нельзя собирать статьи из будущего")

    job = registry.submit(
        JobType.HARVEST.value,
        lambda handle: run_harvest(
            handle, payload.start_date, payload.end_date, refresh=payload.refresh
        ),
        params={
            "start_date": payload.start_date.isoformat(),
            "end_date": payload.end_date.isoformat(),
            "refresh": payload.refresh,
            "trigger": "manual",
        },
    )
    return JobOut(**job.snapshot())
