"""Ночной автосбор (00:30): каждую ночь добирает предыдущий день и недостающие в окне.

Смысл: корпус должен расти сам. Тогда пользователь почти никогда не ждёт
сбора — нужный период уже в базе, и остаётся только запустить анализ.
Ручной сбор остаётся для старых периодов, которых в базе ещё нет.

Окно автосбора жёстко ограничено: ТРИ ЗАВЕРШЁННЫХ дня до сегодняшнего
(вчера, позавчера, позапозавчера). Сегодняшний день не берётся никогда —
его sitemap ещё не полон. Всё, что старше окна, админ добирает вручную: на
хостинге автосбор не должен генерировать много запросов к источнику.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from ..config import settings
from ..db import session_scope
from ..models import HarvestedDay, Job, JobType
from .harvest import run_harvest
from .jobs import registry

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _today() -> date:
    return datetime.now(ZoneInfo(settings.scheduler_timezone)).date()


def window(today: date | None = None) -> tuple[date, date]:
    """(начало, конец) окна автосбора: последние N завершённых дней, без сегодняшнего."""
    today = today or _today()
    days = max(settings.scheduler_catchup_max_days, 1)
    return today - timedelta(days=days), today - timedelta(days=1)


def plan_catch_up(today: date | None = None) -> dict:
    """Сверяет базу с окном автосбора и говорит, что нужно подгрузить.

    Собранным считается день со статусом done в harvested_days; день в статусе
    partial/failed/pending внутри окна будет добран повторно.
    """
    today = today or _today()
    start, end = window(today)
    with session_scope() as db:
        last_day = db.execute(
            select(func.max(HarvestedDay.day)).where(HarvestedDay.status.in_(["done", "partial"]))
        ).scalar_one_or_none()
        done_in_window = set(
            db.execute(
                select(HarvestedDay.day).where(
                    HarvestedDay.day >= start,
                    HarvestedDay.day <= end,
                    HarvestedDay.status == "done",
                )
            ).scalars()
        )
    wanted = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    missing = [d for d in wanted if d not in done_in_window]
    return {
        "today": today,
        "last_day": last_day,
        "start": start,
        "end": end,
        "missing": missing,
        "window_done": not missing,
        # Между последним собранным днём и окном остались дни, которые автосбор
        # намеренно не трогает.
        "skipped_older": last_day is not None and (start - last_day).days > 1,
        "behind_days": (end - last_day).days if last_day else None,
    }


def _submit_window(trigger: str, plan: dict) -> None:
    start, end = plan["start"], plan["end"]
    registry.submit(
        JobType.HARVEST.value,
        lambda job: run_harvest(job, start, end, refresh=False),
        params={
            "trigger": trigger,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )


def _nightly_harvest() -> None:
    # Если пользователь как раз запустил сбор руками, не мешаем:
    # два параллельных сбора на одном ядре только замедлят друг друга.
    if registry.active_of_type(JobType.HARVEST.value):
        logger.info("Ночной сбор пропущен: сбор уже идёт")
        return
    plan = plan_catch_up()
    if plan["window_done"]:
        logger.info("Ночной сбор: дни %s — %s уже собраны", plan["start"], plan["end"])
        return
    _submit_window("schedule", plan)
    logger.info("Ночной сбор запущен: %s — %s", plan["start"], plan["end"])


def catch_up_on_start() -> None:
    """При запуске: какой сегодня день, какой последний собран, что докачать (макс. окно)."""
    if not settings.scheduler_catchup_on_start:
        return
    if registry.active_of_type(JobType.HARVEST.value):
        return
    cooldown = timedelta(minutes=settings.scheduler_catchup_cooldown_minutes)
    with session_scope() as db:
        last_job = db.execute(
            select(func.max(Job.created_at)).where(Job.type == JobType.HARVEST.value)
        ).scalar_one_or_none()
    if last_job is not None and datetime.utcnow() - last_job < cooldown:
        logger.info("Автосбор при запуске пропущен: сбор уже запускался в последние %s", cooldown)
        return
    plan = plan_catch_up()
    if plan["window_done"]:
        logger.info(
            "Автосбор при запуске: сегодня %s; дни %s — %s уже собраны, догонять нечего",
            plan["today"], plan["start"], plan["end"],
        )
        return
    logger.info(
        "Автосбор при запуске: сегодня %s (сегодняшний день не берём); последний собранный день %s; "
        "докачиваем только окно %s — %s, не собрано в нём: %s%s",
        plan["today"],
        plan["last_day"] or "—",
        plan["start"],
        plan["end"],
        ", ".join(d.isoformat() for d in plan["missing"]),
        "; более старые дни автосбор НЕ трогает, их добирают вручную" if plan["skipped_older"] else "",
    )
    _submit_window("startup_catchup", plan)


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.scheduler_enabled:
        logger.info("Планировщик выключен настройкой")
        return None

    _scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.scheduler_timezone))
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
        "Планировщик запущен: ежедневно в %02d:%02d (%s)",
        settings.scheduler_hour,
        settings.scheduler_minute,
        settings.scheduler_timezone,
    )
    try:
        catch_up_on_start()
    except Exception:  # noqa: BLE001 — догон не должен мешать запуску сервера
        logger.exception("Автосбор при запуске не удался")
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
