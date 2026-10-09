"""Запись в удалённую базу (Turso): маленькие транзакции, повтор, честная причина сбоя."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_local_db_writes_everything_in_one_chunk(app_env, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "_USING_TURSO", False)
    assert list(db.write_chunks(list(range(357)))) == [list(range(357))]


def test_remote_db_splits_into_small_chunks(app_env, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "_USING_TURSO", True)
    chunks = list(db.write_chunks(list(range(357))))
    assert all(len(c) <= db.REMOTE_WRITE_CHUNK for c in chunks)
    assert sum(len(c) for c in chunks) == 357 and chunks[0][0] == 0 and chunks[-1][-1] == 356


def test_persist_pending_urls_stores_all_in_chunks_and_dedups(app_env, monkeypatch):
    from app import db
    from app.db import session_scope
    from app.models import Article
    from app.services.harvest import _persist_pending_urls

    monkeypatch.setattr(db, "_USING_TURSO", True)
    urls = [f"https://www.lefigaro.fr/a{i}" for i in range(57)] + ["https://www.lefigaro.fr/a3"]  # дубль
    _persist_pending_urls(date(2026, 10, 5), urls)
    _persist_pending_urls(date(2026, 10, 5), urls)  # повторный вызов ничего не дублирует
    with session_scope() as s:
        assert s.query(Article).count() == 57


def test_retry_write_retries_only_on_remote(app_env, monkeypatch):
    from app import db

    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("Hrana: stream error")
        return "ok"

    monkeypatch.setattr(db, "_USING_TURSO", True)
    assert db.retry_write(flaky) == "ok" and calls["n"] == 3

    monkeypatch.setattr(db, "_USING_TURSO", False)
    calls["n"] = 0
    with pytest.raises(ValueError):
        db.retry_write(flaky)
    assert calls["n"] == 1, "локально повторов нет — как раньше"


def test_secondary_rollback_error_does_not_mask_the_real_one(app_env, monkeypatch):
    from app import db

    class Boom(Exception):
        pass

    real = db.SessionLocal

    def failing_rollback_session():
        s = real()
        s.rollback = lambda: (_ for _ in ()).throw(ValueError("cannot rollback - no transaction is active"))
        return s

    monkeypatch.setattr(db, "SessionLocal", failing_rollback_session)
    with pytest.raises(Boom):
        with db.session_scope():
            raise Boom("настоящая причина")
