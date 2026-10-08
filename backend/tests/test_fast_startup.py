"""На удалённой базе (Turso) сервер открывает порт сразу, а инициализация идёт в фоне."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_remote_db_startup_does_not_block_the_port(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    release = threading.Event()
    started = threading.Event()

    def slow_tasks():
        started.set()
        release.wait(5)

    monkeypatch.setattr(main, "USING_TURSO", True)
    monkeypatch.setattr(main, "_startup_tasks", slow_tasks)
    began = time.monotonic()
    try:
        with TestClient(main.app) as client:
            assert time.monotonic() - began < 2, "lifespan не должен ждать медленную инициализацию"
            assert started.wait(2), "фоновая инициализация должна запуститься"
            assert client.get("/api/corpus/pacing").status_code == 200  # сервер уже отвечает
    finally:
        release.set()


def test_local_db_startup_stays_synchronous(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    calls = []
    monkeypatch.setattr(main, "USING_TURSO", False)
    monkeypatch.setattr(main, "_startup_tasks", lambda: calls.append("done"))
    with TestClient(main.app):
        assert calls == ["done"], "локально инициализация выполняется до приёма запросов"
