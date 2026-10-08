"""Листинг карт сайта уважает тот же RateGate, что и скачивание статей — находка 2026-10-05.

Раньше 403 на листинге просто логировался и тут же бралась следующая карта,
без единой паузы — плотный перебор без отступления, похожий на поведение
бота. Теперь 403/429 на листинге идёт через gate.on_block, как и у статей.
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _http_error(code: int) -> requests.exceptions.HTTPError:
    response = MagicMock()
    response.status_code = code
    return requests.exceptions.HTTPError(f"{code}", response=response)


def _register_job_row(job_id="job-1"):
    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id=job_id, type="harvest"))


def test_listing_403_pauses_via_gate_then_succeeds(app_env, monkeypatch):
    from app.config import settings
    from app.models import DayStatus, HarvestedDay
    from app.services.harvest import gate, run_harvest
    from app.services.jobs import JobHandle
    from app.db import session_scope

    monkeypatch.setattr(settings, "block_backoff_seconds", 0.01)
    monkeypatch.setattr(settings, "harvest_rate", 0)
    monkeypatch.setattr(settings, "listing_request_delay", 0)
    day = date(2026, 9, 21)
    calls = []

    def flaky(xml_url, session=None):
        calls.append(xml_url)
        if len(calls) <= 2:
            raise _http_error(403)
        return ["https://www.lefigaro.fr/a1"]

    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(day, "sm")]), \
         patch("app.services.harvest.get_articles_from_sitemap", side_effect=flaky), \
         patch("app.services.harvest.fetch_article",
               side_effect=lambda u, session=None: __import__("app.core.figaro", fromlist=["FetchedArticle"])
               .FetchedArticle(url=u, text="x guerre", title="T", category="C")):
        _register_job_row("job-1")
        run_harvest(JobHandle(id="job-1", type="harvest"), day, day)

    # Один и тот же URL запрашивался трижды подряд (2 блока + финальный успех) —
    # в рамках ОДНОГО вызова, т.к. блокировка не расходует общие max_retries.
    assert len(calls) == 3
    with session_scope() as db:
        assert db.get(HarvestedDay, day).status == DayStatus.DONE.value


def test_listing_block_counter_is_shared_with_article_downloads(app_env, monkeypatch):
    """Если источник банит и на листинге, и на статьях, это один и тот же счётчик —
    после STOP_AFTER_BLOCKS суммарных банов сбор останавливает себя сам."""
    from app.config import settings
    from app.models import JobStatus
    from app.services.harvest import RateGate, gate, run_harvest
    from app.services.jobs import JobHandle
    from app.db import session_scope
    from app.models import Job

    monkeypatch.setattr(settings, "block_backoff_seconds", 0.01)
    monkeypatch.setattr(settings, "harvest_rate", 0)
    monkeypatch.setattr(settings, "listing_request_delay", 0)
    days = [date(2026, 9, d) for d in range(10, 10 + RateGate.STOP_AFTER_BLOCKS)]
    sitemaps = [(d, f"sm-{d.day}") for d in days]

    def always_blocked(xml_url, session=None):
        raise _http_error(403)

    with patch("app.services.harvest.list_daily_sitemaps", return_value=sitemaps), \
         patch("app.services.harvest.get_articles_from_sitemap", side_effect=always_blocked):
        with session_scope() as db:
            db.add(Job(id="job-1", type="harvest"))
        job = JobHandle(id="job-1", type="harvest")
        run_harvest(job, days[0], days[-1])

    assert job.is_cancelled(), "после накопления STOP_AFTER_BLOCKS банов листинг должен остановить сбор"


def test_generic_listing_error_still_tries_exactly_once(app_env, monkeypatch):
    """Не-403 сбой (обрыв сети) по-прежнему НЕ ретраится внутри одного прогона —
    это отдельное поведение (резюме на следующем запуске), которое правка не должна была менять."""
    from app.config import settings
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    monkeypatch.setattr(settings, "harvest_rate", 0)

    monkeypatch.setattr(settings, "listing_request_delay", 0)
    day = date(2026, 9, 21)
    calls = {"n": 0}

    def flaky(xml_url, session=None):
        calls["n"] += 1
        raise ConnectionError("boom")

    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(day, "sm")]), \
         patch("app.services.harvest.get_articles_from_sitemap", side_effect=flaky):
        _register_job_row("job-1")
        run_harvest(JobHandle(id="job-1", type="harvest"), day, day)

    assert calls["n"] == 1
