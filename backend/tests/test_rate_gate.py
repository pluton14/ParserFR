"""Ограничитель темпа: фиксированная скорость, без авто-снижения.

По решению пользователя (2026-10-02): темп либо держится на заданном значении,
либо сбор останавливается сам — ползти на пониженной скорости не нужно.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _http_error(code: int) -> requests.exceptions.HTTPError:
    response = MagicMock()
    response.status_code = code
    return requests.exceptions.HTTPError(f"{code}", response=response)


def _fake_article(url):
    from app.core.figaro import FetchedArticle

    return FetchedArticle(url=url, text="un texte", title="T", category="C")


def _register_job_row(job_id="job-1"):
    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id=job_id, type="harvest"))


def _fast_gate(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "harvest_rate", 0)

    monkeypatch.setattr(settings, "listing_request_delay", 0)
    monkeypatch.setattr(settings, "block_backoff_seconds", 0.05)
    monkeypatch.setattr(settings, "block_backoff_max_seconds", 0.2)
    monkeypatch.setattr(settings, "retry_delay", 0)


def test_block_pauses_and_retries_without_marking_the_article_failed(app_env, monkeypatch):
    from app.services.harvest import ErrorBudget, _fetch_with_retries, gate
    from app.services.jobs import JobHandle

    _fast_gate(monkeypatch)
    _register_job_row()
    gate.reset()
    calls = []

    def fetch(url, session=None):
        calls.append(url)
        if len(calls) <= 3:
            raise _http_error(403)  # трижды подряд — больше max_retries, но это блок, не сбой статьи
        return _fake_article(url)

    job = JobHandle(id="job-1", type="harvest")
    with patch("app.services.harvest.fetch_article", side_effect=fetch):
        url, article, status = _fetch_with_retries("https://www.lefigaro.fr/a", None, ErrorBudget(job), job)

    assert status == "ok" and article is not None
    assert len(calls) == 4


def test_rate_never_changes_regardless_of_blocks_or_successes(app_env, monkeypatch):
    """Фиксированный потолок: on_block и on_success не меняют темп."""
    from app.config import settings
    from app.services.harvest import RateGate
    from app.services.jobs import JobHandle

    _fast_gate(monkeypatch)
    _register_job_row()
    monkeypatch.setattr(settings, "harvest_rate", 2.0)
    gate = RateGate()
    job = JobHandle(id="job-1", type="harvest")

    assert gate.rate == 2.0
    gate.on_success()
    assert gate.rate == 2.0
    gate.on_block(job)
    assert gate.rate == 2.0, "темп не должен снижаться после блокировки"
    for _ in range(500):
        gate.on_success()
    assert gate.rate == 2.0, "темп не должен расти — он всегда фиксирован"


def test_stops_itself_after_repeated_blocks_without_enough_successes_between(app_env, monkeypatch):
    from app.config import settings
    from app.services.harvest import RateGate, StoppedForRepeatedBlocks
    from app.services.jobs import JobHandle

    _fast_gate(monkeypatch)
    _register_job_row()
    gate = RateGate()
    job = JobHandle(id="job-1", type="harvest")

    for _ in range(RateGate.STOP_AFTER_BLOCKS - 1):
        gate.on_block(job)
        gate._blocked_until = 0  # снимаем паузу вручную, чтобы следующий блок не слился с этим же
    with pytest.raises(StoppedForRepeatedBlocks):
        gate.on_block(job)
    assert job.is_cancelled()


def test_a_single_lucky_success_does_not_reset_the_block_counter(app_env, monkeypatch):
    """Находка 2026-10-05: единичный случайный успех между банами раньше обнулял
    счётчик, и сбор никогда не доходил до STOP_AFTER_BLOCKS — часами полз на
    «1 бан, 1 случайный успех, 1 бан…» вместо того, чтобы остановиться."""
    from app.services.harvest import RateGate, StoppedForRepeatedBlocks
    from app.services.jobs import JobHandle

    _fast_gate(monkeypatch)
    _register_job_row()
    gate = RateGate()
    job = JobHandle(id="job-1", type="harvest")

    for _ in range(RateGate.STOP_AFTER_BLOCKS - 1):
        gate.on_block(job)
        gate._blocked_until = 0
        gate.on_success()  # один проскочивший запрос — этого недостаточно для «восстановления»
    with pytest.raises(StoppedForRepeatedBlocks):
        gate.on_block(job)
    assert job.is_cancelled()


def test_sustained_success_streak_resets_the_block_counter(app_env, monkeypatch):
    """А вот устойчивая серия успехов (источник реально снял блокировку) — снимает счётчик."""
    from app.services.harvest import RateGate
    from app.services.jobs import JobHandle

    _fast_gate(monkeypatch)
    _register_job_row()
    gate = RateGate()
    job = JobHandle(id="job-1", type="harvest")

    for _ in range(RateGate.STOP_AFTER_BLOCKS - 1):
        gate.on_block(job)
        gate._blocked_until = 0
    for _ in range(RateGate.SUCCESSES_TO_RECOVER):
        gate.on_success()
    for _ in range(RateGate.STOP_AFTER_BLOCKS - 1):
        gate.on_block(job)  # не должно хватить для остановки — счётчик реально сброшен
        gate._blocked_until = 0
    assert not job.is_cancelled()


def test_stopped_job_does_not_make_requests(app_env, monkeypatch):
    from app.services.harvest import ErrorBudget, _fetch_with_retries
    from app.services.jobs import JobHandle

    job = JobHandle(id="job-1", type="harvest")
    job.cancel_event.set()
    with patch("app.services.harvest.fetch_article") as fetch:
        _, article, status = _fetch_with_retries("https://www.lefigaro.fr/a", None, ErrorBudget(job), job)
    fetch.assert_not_called()
    assert article is None and status == "failed"
