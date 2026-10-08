"""Скорость сбора управляется из интерфейса, без перезапуска сервера (services/pacing.py)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_defaults_match_sept30_technique(app_env, monkeypatch):
    """По умолчанию — как 30 сентября: 16 потоков, без потолка темпа."""
    from app.config import Settings

    defaults = Settings()
    assert defaults.harvest_workers == 16
    assert defaults.harvest_rate == 0


def test_override_has_priority_over_settings(app_env, monkeypatch):
    from app.config import settings
    from app.services.pacing import Pacing

    monkeypatch.setattr(settings, "harvest_rate", 0.0)
    monkeypatch.setattr(settings, "harvest_workers", 16)
    local = Pacing()
    assert local.snapshot() == {"rate": 0.0, "workers": 16}

    local.update(4.5, 8)
    assert local.snapshot() == {"rate": 4.5, "workers": 8}

    local.reset()
    assert local.snapshot() == {"rate": 0.0, "workers": 16}


def test_zero_rate_does_not_throttle_but_block_pause_still_applies(app_env, monkeypatch):
    """Без потолка запросы идут сразу, но пауза после 403 всё равно соблюдается
    (раньше при выключенном ограничителе wait_turn пропускал и её)."""
    from app.config import settings
    from app.db import session_scope
    from app.models import Job
    from app.services.harvest import RateGate
    from app.services.jobs import JobHandle

    monkeypatch.setattr(settings, "harvest_rate", 0)
    monkeypatch.setattr(settings, "block_backoff_seconds", 0.3)
    with session_scope() as db:
        db.add(Job(id="job-z", type="harvest"))
    gate = RateGate()
    job = JobHandle(id="job-z", type="harvest")

    started = time.monotonic()
    for _ in range(50):
        assert gate.wait_turn(job) is True
    assert time.monotonic() - started < 0.2, "без потолка — без задержек"

    gate.on_block(job)
    started = time.monotonic()
    assert gate.wait_turn(job) is True
    assert time.monotonic() - started >= 0.25, "пауза после блокировки должна соблюдаться"


def test_rate_spaces_requests_evenly(app_env, monkeypatch):
    from app.config import settings
    from app.db import session_scope
    from app.models import Job
    from app.services.harvest import RateGate
    from app.services.jobs import JobHandle

    monkeypatch.setattr(settings, "harvest_rate", 20.0)  # 1 запрос раз в 50 мс
    with session_scope() as db:
        db.add(Job(id="job-r", type="harvest"))
    gate = RateGate()
    job = JobHandle(id="job-r", type="harvest")
    stamps = []
    for _ in range(5):
        assert gate.wait_turn(job) is True
        stamps.append(time.monotonic())
    assert stamps[-1] - stamps[0] >= 4 * 0.05 - 0.01


def test_pacing_endpoints_change_live_values(app_env):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.harvest import gate
    from app.services.pacing import pacing

    client = TestClient(app)
    try:
        resp = client.post("/api/corpus/pacing", json={"rate": 5, "workers": 12})
        assert resp.status_code == 200
        assert pacing.snapshot() == {"rate": 5.0, "workers": 12}
        assert gate.rate == 5.0 and gate.enabled is True

        assert client.get("/api/corpus/pacing").json() == {"rate": 5.0, "workers": 12}

        assert client.post("/api/corpus/pacing", json={"rate": -1, "workers": 4}).status_code == 400
        assert client.post("/api/corpus/pacing", json={"rate": 1, "workers": 0}).status_code == 400
        assert client.post("/api/corpus/pacing", json={"rate": 1, "workers": 99}).status_code == 400
    finally:
        pacing.reset()


def test_requests_use_plain_sept30_headers_without_randomization():
    """Откат 2026-10-06: никаких подменных профилей браузера, sec-ch-ua,
    Referer и cache-buster — те же заголовки, что 30 сентября, всегда одни и те же."""
    from app.core.figaro import browser_headers

    first = browser_headers()
    assert set(first) == {"User-Agent", "Accept", "Accept-Language"}
    assert all(browser_headers() == first for _ in range(20))
