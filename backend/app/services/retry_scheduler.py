"""Отложенный повтор сбора — "попробовать снова через N часов", которым
управляет сам пользователь из интерфейса.

Независим от ночного автосбора (scheduler.py, APScheduler + cron, управляется
settings.scheduler_enabled) — работает всегда, даже когда автосбор выключен.
Переживает перезапуск сервера: цель хранится в таблице scheduled_retry, при
старте приложение перечитывает её и восстанавливает отложенный запуск.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from ..db import session_scope
from ..models import JobType, ScheduledRetry
from .harvest import run_download_pending
from .jobs import registry

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_JOB_ID = "user_scheduled_retry"
_ROW_ID = 1

# Пока единственный режим — докачка уже известного: она безопаснее обычного
# сбора (не трогает листинг карт сайта) и как раз то, что имеет смысл
# повторять автоматически после паузы на остывание источника.
MODE_DOWNLOAD_PENDING = "download_pending"


def _aware(dt: datetime) -> datetime:
    """Находка 2026-10-05: BackgroundScheduler(timezone="UTC") + наивный datetime
    приводили к тому, что job считался "просроченным на 3 часа" (ровно разница
    с местным поясом MSK) — APScheduler трактовал наивное время неоднозначно.
    DateTrigger получает только осознанное (aware) время; наружу и в базу по-
    прежнему отдаём наивный UTC, как принято в остальном проекте."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _run(mode: str) -> None:
    with session_scope() as db:
        row = db.get(ScheduledRetry, _ROW_ID)
        if row is not None:
            db.delete(row)

    if registry.active_of_type(JobType.HARVEST.value):
        logger.info("Отложенный повтор пропущен: сбор уже идёт")
        return

    if mode == MODE_DOWNLOAD_PENDING:
        registry.submit(
            JobType.HARVEST.value,
            lambda job: run_download_pending(job),
            params={"mode": mode, "trigger": "scheduled_retry"},
        )
        logger.info("Отложенный повтор запущен (%s)", mode)


def start_retry_scheduler() -> None:
    """Поднимает планировщик и восстанавливает отложенную задачу, если сервер
    был перезапущен, пока она ждала своего часа."""
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()

    with session_scope() as db:
        row = db.get(ScheduledRetry, _ROW_ID)
        fire_at, mode = (row.fire_at, row.mode) if row else (None, None)

    if fire_at is not None:
        # Время уже прошло, пока сервер был выключен, — не теряем задачу,
        # запускаем в ближайшие секунды вместо того, чтобы молча её забыть.
        run_at = max(fire_at, datetime.utcnow() + timedelta(seconds=5))
        _scheduler.add_job(_run, DateTrigger(run_date=_aware(run_at)), args=[mode],
                           id=_JOB_ID, replace_existing=True)
        logger.info("Восстановлен отложенный повтор на %s UTC", run_at)


def stop_retry_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def schedule_retry(delay_hours: float, mode: str = MODE_DOWNLOAD_PENDING) -> datetime:
    if _scheduler is None:
        raise RuntimeError("Планировщик отложенных повторов не запущен")
    fire_at = datetime.utcnow() + timedelta(hours=delay_hours)
    with session_scope() as db:
        existing = db.get(ScheduledRetry, _ROW_ID)
        if existing is not None:
            existing.mode, existing.fire_at = mode, fire_at
        else:
            db.add(ScheduledRetry(id=_ROW_ID, mode=mode, fire_at=fire_at))
    _scheduler.add_job(_run, DateTrigger(run_date=_aware(fire_at)), args=[mode],
                       id=_JOB_ID, replace_existing=True)
    return fire_at


def cancel_retry() -> bool:
    had_job = _scheduler is not None and _scheduler.get_job(_JOB_ID) is not None
    if had_job:
        _scheduler.remove_job(_JOB_ID)
    with session_scope() as db:
        row = db.get(ScheduledRetry, _ROW_ID)
        had_row = row is not None
        if row is not None:
            db.delete(row)
    return had_job or had_row


def get_scheduled_retry() -> dict | None:
    with session_scope() as db:
        row = db.get(ScheduledRetry, _ROW_ID)
        if row is None:
            return None
        return {"mode": row.mode, "fire_at": row.fire_at.isoformat()}
