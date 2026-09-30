"""Фоновые задачи: состояние, поток прогресса, остановка.

Прогресс отдаётся через Server-Sent Events. Для одностороннего потока
«сервер → браузер» этого достаточно, а в отличие от WebSocket SSE ходит
через обычный HTTP и не требует отдельной настройки проксирования.
"""

from __future__ import annotations

import json
import queue
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Job, JobLog, JobStatus
from ..schemas import JobOut
from ..services.jobs import registry

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Пустой комментарий раз в N секунд: держит соединение живым, когда
# сбор долго молчит, и не даёт прокси закрыть его по таймауту.
HEARTBEAT_SECONDS = 15


def _row_to_out(row: Job) -> JobOut:
    return JobOut(
        id=row.id,
        type=row.type,
        status=row.status,
        current=row.current,
        total=row.total,
        stage=row.stage,
        message=row.message,
        error=row.error,
        result_id=row.result_id,
        params=row.params or {},
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@router.get("", response_model=list[JobOut])
def list_jobs(limit: int = 20, db: Session = Depends(get_db)) -> list[JobOut]:
    rows = (
        db.execute(select(Job).order_by(Job.created_at.desc()).limit(limit)).scalars().all()
    )
    return [_row_to_out(row) for row in rows]


@router.get("/active", response_model=list[JobOut])
def list_active_jobs() -> list[JobOut]:
    """Живые задачи текущего процесса — то, за чем стоит следить."""
    return [
        JobOut(**job.snapshot())
        for job in registry.list()
        if job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value)
    ]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobOut:
    live = registry.get(job_id)
    if live is not None:
        return JobOut(**live.snapshot())
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return _row_to_out(row)


@router.get("/{job_id}/logs")
def get_job_logs(job_id: str, limit: int = 300, db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.execute(
            select(JobLog)
            .where(JobLog.job_id == job_id)
            .order_by(JobLog.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        {"level": row.level, "text": row.text, "at": row.created_at.isoformat()}
        for row in reversed(rows)
    ]


@router.post("/{job_id}/cancel", status_code=202)
def cancel_job(job_id: str) -> dict:
    if not registry.cancel(job_id):
        raise HTTPException(status_code=409, detail="Задачу нельзя остановить")
    return {"ok": True}


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> StreamingResponse:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена или уже завершена")

    subscription = job.subscribe()

    async def event_source():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = subscription.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue

                if item["event"] == "close":
                    break

                payload = json.dumps(item["data"], ensure_ascii=False, default=str)
                yield f"event: {item['event']}\ndata: {payload}\n\n"
        finally:
            job.unsubscribe(subscription)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx иначе буферизует ответ и прогресс приходит рывками в конце.
            "X-Accel-Buffering": "no",
        },
    )
