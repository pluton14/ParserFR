"""Ночной автосбор.

Смысл: корпус должен расти сам. Тогда пользователь почти никогда не ждёт
сбора — нужный период уже в базе, и остаётся только запустить анализ.
Ручной сбор остаётся для старых периодов, которых в базе ещё нет.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import settings
from ..models import JobType
from .harvest import harvest_recent
from .jobs import registry

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _nightly_harvest() -> None:
    # Если пользователь как раз запустил сбор руками, не мешаем:
    # два параллельных сбора на одном ядре только замедлят друг друга.
    if registry.active_of_type(JobType.HARVEST.value):
        logger.info("Ночной сбор пропущен: сбор уже идёт")
        return

    lookback = settings.scheduler_lookback_days
    registry.submit(
        JobType.HARVEST.value,
        lambda job: harvest_recent(job, lookback),
        params={"trigger": "schedule", "lookback_days": lookback},
    )
    logger.info("Ночной сбор запущен")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.scheduler_enabled:
        logger.info("Планировщик выключен настройкой")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _nightly_harvest,
        CronTrigger(hour=settings.scheduler_hour, minute=settings.scheduler_minute),
        id="nightly_harvest",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Планировщик запущен: ежедневно в %02d:%02d UTC",
        settings.scheduler_hour,
        settings.scheduler_minute,
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_run_time():
    if _scheduler is None:
        return None
    job = _scheduler.get_job("nightly_harvest")
    return job.next_run_time if job else None
