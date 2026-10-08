"""Ручной пропуск паузы: не ждём оставшееся время, но счётчик банов и темп не трогаем."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_skip_pause_lets_blocked_thread_through_immediately(app_env, monkeypatch):
    from app.config import settings
    from app.services.harvest import RateGate
    from app.services.jobs import JobHandle

    monkeypatch.setattr(settings, "harvest_rate", 0)

    monkeypatch.setattr(settings, "listing_request_delay", 0)
    monkeypatch.setattr(settings, "block_backoff_seconds", 300)  # долгая пауза, как в бою
    gate = RateGate()
    job = JobHandle(id="job-1", type="harvest")

    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id="job-1", type="harvest"))

    gate.on_block(job)  # ставит паузу на 300 секунд
    assert gate.wait_turn.__self__ is gate

    gate.skip_pause()
    # wait_turn не должен реально ждать 300 секунд — пауза снята.
    import time

    started = time.monotonic()
    assert gate.wait_turn(job) is True
    assert time.monotonic() - started < 1.0


def test_skip_pause_does_not_reset_block_counter_or_rate(app_env, monkeypatch):
    """Пропуск паузы — это не «забыть про бан»: счётчик для автостопа остаётся."""
    from app.config import settings
    from app.services.harvest import RateGate
    from app.services.jobs import JobHandle

    monkeypatch.setattr(settings, "block_backoff_seconds", 300)
    gate = RateGate()
    job = JobHandle(id="job-1", type="harvest")

    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id="job-1", type="harvest"))

    gate.on_block(job)
    rate_before = gate.rate
    gate.skip_pause()
    assert gate.rate == rate_before
    assert gate._consecutive_blocks == 1
