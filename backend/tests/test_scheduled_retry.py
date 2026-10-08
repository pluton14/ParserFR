"""Отложенный повтор ("попробовать снова через N часов"), которым управляет пользователь."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_schedule_persists_and_is_readable(app_env):
    from app.services.retry_scheduler import (
        cancel_retry,
        get_scheduled_retry,
        schedule_retry,
        start_retry_scheduler,
        stop_retry_scheduler,
    )

    start_retry_scheduler()
    try:
        before = datetime.utcnow()
        fire_at = schedule_retry(2.0)
        assert timedelta(hours=1, minutes=59) < fire_at - before < timedelta(hours=2, minutes=1)

        status = get_scheduled_retry()
        assert status is not None
        assert status["mode"] == "download_pending"
        assert status["fire_at"] == fire_at.isoformat()

        assert cancel_retry() is True
        assert get_scheduled_retry() is None
        assert cancel_retry() is False, "второй раз отменять нечего"
    finally:
        stop_retry_scheduler()


def test_schedule_survives_restart_and_fires_when_due(app_env):
    """Переживает «перезапуск»: новый старт планировщика на уже существующей базе
    должен подхватить просроченную цель и выполнить её, а не потерять."""
    import time

    from app.db import session_scope
    from app.models import Job, ScheduledRetry
    from app.services.retry_scheduler import start_retry_scheduler, stop_retry_scheduler

    # Имитация «сервер был выключен, пока время не вышло»: кладём просроченную
    # цель напрямую в базу, как будто schedule_retry() была вызвана раньше.
    with session_scope() as db:
        db.add(ScheduledRetry(id=1, mode="download_pending",
                              fire_at=datetime.utcnow() - timedelta(minutes=5)))

    start_retry_scheduler()
    try:
        # Не проверяем "задача ещё активна": в пустой тестовой базе качать
        # нечего, и run_download_pending успевает отработать и завершиться
        # меньше чем за секунду — такая проверка была бы гонкой. Проверяем
        # сам факт, что задача была создана и запущена (любой статус).
        for _ in range(100):
            with session_scope() as db:
                if db.query(Job).filter_by(type="harvest").count() > 0:
                    break
            time.sleep(0.2)
        else:
            raise AssertionError("просроченная цель не запустилась после восстановления")
    finally:
        stop_retry_scheduler()


def test_set_and_cancel_via_api(app_env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/corpus/scheduled-retry", json={"delay_hours": 1.5})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = client.get("/api/corpus/scheduled-retry")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "download_pending"

        resp = client.delete("/api/corpus/scheduled-retry")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True

        resp = client.get("/api/corpus/scheduled-retry")
        assert resp.json() is None

        resp = client.post("/api/corpus/scheduled-retry", json={"delay_hours": -1})
        assert resp.status_code == 400
