"""POST /api/corpus/run-auto-update-now и GET /api/corpus/auto-update-window — ручная проверка автообновления."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _day(day, status):
    from app.models import HarvestedDay

    return HarvestedDay(day=day, sitemap_url="x", total_urls=1, status=status)


def test_window_endpoint_reports_current_plan(app_env):
    from fastapi.testclient import TestClient

    from app.db import session_scope
    from app.main import app
    from app.services import scheduler

    with session_scope() as db:
        db.add(_day(date(2026, 10, 3), "done"))
    with patch.object(scheduler, "_today", return_value=date(2026, 10, 6)):
        resp = TestClient(app).get("/api/corpus/auto-update-window")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False  # по умолчанию выключено
    assert body["start"] == "2026-10-03" and body["end"] == "2026-10-05"
    assert body["missing"] == ["2026-10-04", "2026-10-05"]


def test_window_endpoint_reports_enabled_flag(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "admin_tools_enabled", True)
    assert TestClient(app).get("/api/corpus/auto-update-window").json()["enabled"] is True


def test_endpoint_disabled_by_default(app_env):
    from fastapi.testclient import TestClient

    from app.main import app

    resp = TestClient(app).post("/api/corpus/run-auto-update-now")
    assert resp.status_code == 403


def test_endpoint_starts_job_and_conflicts_when_already_running(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.db import session_scope
    from app.main import app
    from app.models import Job
    from app.services import scheduler

    monkeypatch.setattr(settings, "admin_tools_enabled", True)
    with session_scope() as db:
        db.add(_day(date(2026, 9, 1), "done"))
    client = TestClient(app)
    with patch.object(scheduler, "_today", return_value=date(2026, 10, 6)), patch.object(
        scheduler.registry, "active_of_type", return_value=None
    ), patch.object(scheduler.registry, "submit") as submit:
        resp = client.post("/api/corpus/run-auto-update-now")
    assert resp.status_code == 202
    body = resp.json()
    assert body["started"] is True
    assert (body["window_start"], body["window_end"]) == ("2026-10-03", "2026-10-05")
    submit.assert_called_once()
    assert submit.call_args.kwargs["params"]["trigger"] == "schedule"

    class FakeActive:
        id = "job-busy"

    with patch.object(scheduler.registry, "active_of_type", return_value=FakeActive()):
        resp = client.post("/api/corpus/run-auto-update-now")
    assert resp.status_code == 409


def test_endpoint_blocked_in_readonly_demo(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "admin_tools_enabled", True)
    monkeypatch.setattr(settings, "readonly_demo", True)
    resp = TestClient(app).post("/api/corpus/run-auto-update-now")
    assert resp.status_code == 403


def test_single_day_endpoint_disabled_by_default(app_env):
    from fastapi.testclient import TestClient

    from app.main import app

    resp = TestClient(app).post("/api/corpus/test-single-day-harvest")
    assert resp.status_code == 403


def test_single_day_endpoint_submits_exactly_one_day(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.routers import corpus as corpus_router

    monkeypatch.setattr(settings, "admin_tools_enabled", True)
    client = TestClient(app)

    with patch.object(corpus_router.registry, "active_of_type", return_value=None), patch.object(
        corpus_router.registry, "submit"
    ) as submit:
        submit.return_value.id = "job-x"
        resp = client.post("/api/corpus/test-single-day-harvest?day=2020-05-01")
    assert resp.status_code == 202
    body = resp.json()
    assert body["day"] == "2020-05-01"
    params = submit.call_args.kwargs["params"]
    assert params["trigger"] == "schedule"
    assert params["start_date"] == params["end_date"] == "2020-05-01"


def test_single_day_endpoint_defaults_to_two_weeks_ago(app_env, monkeypatch):
    from datetime import date, timedelta

    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.routers import corpus as corpus_router

    monkeypatch.setattr(settings, "admin_tools_enabled", True)
    client = TestClient(app)
    expected = (date.today() - timedelta(days=14)).isoformat()

    with patch.object(corpus_router.registry, "active_of_type", return_value=None), patch.object(
        corpus_router.registry, "submit"
    ) as submit:
        submit.return_value.id = "job-y"
        resp = client.post("/api/corpus/test-single-day-harvest")
    assert resp.status_code == 202
    assert resp.json()["day"] == expected


def test_single_day_endpoint_conflicts_when_harvest_active(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.routers import corpus as corpus_router

    monkeypatch.setattr(settings, "admin_tools_enabled", True)
    client = TestClient(app)

    class FakeActive:
        id = "job-busy"

    with patch.object(corpus_router.registry, "active_of_type", return_value=FakeActive()):
        resp = client.post("/api/corpus/test-single-day-harvest")
    assert resp.status_code == 409
