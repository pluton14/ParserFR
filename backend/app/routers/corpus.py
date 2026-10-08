"""Корпус: состояние покрытия по дням и запуск сбора."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Article, ArticleStatus, HarvestedDay, JobType
from ..schemas import BackfillRequest, CorpusSummary, DayCoverage, HarvestRequest, JobOut
from ..services.backfill import run_backfill_captions
from ..services.harvest import gate as harvest_gate
from ..services.pacing import MAX_WORKERS, pacing
from ..services.harvest import run_download_pending, run_harvest
from ..services.jobs import registry
from ..services.retry_scheduler import cancel_retry, get_scheduled_retry, schedule_retry
from ..services.scheduler import next_run_time

router = APIRouter(prefix="/api/corpus", tags=["corpus"])

DEMO_CATEGORIES_FILE = Path(__file__).resolve().parent.parent / "demo_categories.json"


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


@router.get("/range")
def corpus_range(db: Session = Depends(get_db)) -> dict:
    """Первый и последний день, за которые реально есть статьи с текстом.

    Берём из harvested_days (по одной строке на день, ~8 тысяч), а не из
    articles: там на удалённой базе (Turso) любой проход по миллионам строк
    занимает минуты, а хвост таблицы — это URL без текста (ещё не скачаны),
    так что MAX(published_date) показал бы даты, за которые анализировать нечего.
    """
    first, last = db.execute(
        select(func.min(HarvestedDay.day), func.max(HarvestedDay.day)).where(
            HarvestedDay.ok_count + HarvestedDay.truncated_count > 0
        )
    ).one()
    return {"first_day": first, "last_day": last}


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
    # Демо-снимок заморожен, а GROUP BY по всему корпусу на Turso слишком
    # медленный — отдаём заранее посчитанный список.
    if settings.readonly_demo and DEMO_CATEGORIES_FILE.is_file():
        return json.loads(DEMO_CATEGORIES_FILE.read_text(encoding="utf-8"))
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


@router.post("/skip-pause")
def skip_harvest_pause() -> dict:
    """Досрочно снимает текущую паузу после блокировки — ручная кнопка «Повторить сейчас».

    Темп и счётчик банов (для автостопа после нескольких подряд) не трогает —
    только перестаёт ждать оставшееся время паузы.
    """
    harvest_gate.skip_pause()
    return {"ok": True, "rate": harvest_gate.rate}


@router.get("/pacing")
def get_pacing() -> dict:
    return pacing.snapshot()


@router.post("/pacing")
def set_pacing(payload: dict) -> dict:
    """Скорость сбора: потолок запросов в секунду (0 — без ограничения, как в
    технике сбора 30 сентября; действует сразу) и число потоков скачивания
    (действует со следующего запуска сбора)."""
    try:
        rate = float(payload.get("rate"))
        workers = int(payload.get("workers"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="rate и workers должны быть числами")
    if rate < 0:
        raise HTTPException(status_code=400, detail="Потолок темпа: число ≥ 0 (0 — без ограничения)")
    if not 1 <= workers <= MAX_WORKERS:
        raise HTTPException(status_code=400, detail=f"Потоков: от 1 до {MAX_WORKERS}")
    pacing.update(rate, workers)
    return {"ok": True, **pacing.snapshot()}


@router.post("/download-pending", response_model=JobOut, status_code=202)
def start_download_pending(payload: BackfillRequest) -> JobOut:
    """Докачивает уже известные, но не скачанные статьи — без обращения к
    листингу карт сайта (sitemaps.lefigaro.fr). Полезно, когда листинг для
    ещё не читанных дней сейчас заблокирован, а в базе уже накопился большой
    запас найденных ранее, но не скачанных URL."""
    if settings.readonly_demo:
        raise HTTPException(status_code=403, detail="В демо-копии сбор отключён.")

    active = registry.active_of_type(JobType.HARVEST.value)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "Сбор или добор уже идёт.", "job_id": active.id},
        )

    job = registry.submit(
        JobType.HARVEST.value,
        lambda handle: run_download_pending(handle, payload.start_date, payload.end_date),
        params={
            "mode": "download_pending",
            "start_date": payload.start_date.isoformat() if payload.start_date else None,
            "end_date": payload.end_date.isoformat() if payload.end_date else None,
        },
    )
    return JobOut(**job.snapshot())


@router.get("/scheduled-retry")
def get_scheduled_retry_endpoint() -> dict | None:
    return get_scheduled_retry()


@router.post("/scheduled-retry")
def set_scheduled_retry(payload: dict) -> dict:
    """Ставит "докачку уже известного" на отложенный автозапуск через N часов —
    например, пока источник не остынет после блокировки. Переживает
    перезапуск сервера (хранится в базе, восстанавливается при старте)."""
    delay_hours = payload.get("delay_hours")
    if not isinstance(delay_hours, (int, float)) or delay_hours <= 0:
        raise HTTPException(status_code=400, detail="delay_hours должен быть числом > 0")
    fire_at = schedule_retry(float(delay_hours))
    return {"ok": True, "fire_at": fire_at.isoformat()}


@router.delete("/scheduled-retry")
def delete_scheduled_retry() -> dict:
    return {"ok": True, "cancelled": cancel_retry()}


@router.post("/backfill-captions", response_model=JobOut, status_code=202)
def start_backfill_captions(payload: BackfillRequest) -> JobOut:
    """Добирает подписи к фото у статей, собранных до появления экстрактора подписей.

    Идёт тем же типом задачи, что и сбор: оба качают с одного источника, и
    параллельный запуск только удвоил бы нагрузку на него.
    """
    if settings.readonly_demo:
        raise HTTPException(status_code=403, detail="В демо-копии сбор отключён.")

    active = registry.active_of_type(JobType.HARVEST.value)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "Сбор или добор уже идёт.", "job_id": active.id},
        )

    job = registry.submit(
        JobType.HARVEST.value,
        lambda handle: run_backfill_captions(handle, payload.start_date, payload.end_date),
        params={
            "mode": "backfill_captions",
            "start_date": payload.start_date.isoformat() if payload.start_date else None,
            "end_date": payload.end_date.isoformat() if payload.end_date else None,
        },
    )
    return JobOut(**job.snapshot())
