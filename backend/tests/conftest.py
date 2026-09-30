"""Общие фикстуры: каждый тест получает свою пустую SQLite-базу."""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def app_env(monkeypatch, tmp_path):
    """Настраивает свежую БД в tmp_path и перезагружает модули приложения.

    Нужно перезагружать модули, а не просто менять settings.data_dir: engine
    в app.db создаётся один раз на уровне модуля при импорте.
    """
    monkeypatch.setenv("PARSERFR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARSERFR_SCHEDULER_ENABLED", "false")

    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]
    if "app" in sys.modules:
        del sys.modules["app"]

    from app.db import init_db  # noqa: E402

    init_db()
    yield tmp_path
