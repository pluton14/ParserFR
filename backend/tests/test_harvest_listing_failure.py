"""Сбой чтения суточной карты не должен помечать день как собранный.

Закрывает находку 2026-09-22 (замечена по логу реального сбора: одна карта
не прочиталась из-за RemoteDisconnected). Без этого фикса временный сетевой
сбой при листинге (таймаут, обрыв соединения) давал urls=[] неотличимо от
дня, где реально нет статей, — день помечался done и благодаря резюме по
дням больше никогда не перечитывался. Статьи за такой день терялись
навсегда без единой ошибки, которую было бы видно.
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fake_article(url: str):
    from app.core.figaro import FetchedArticle

    return FetchedArticle(
        url=url, text=f"texte {url} guerre", title="T", category="International", truncated=False,
    )


def _register_job_row(job_id: str) -> None:
    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id=job_id, type="harvest"))


def test_listing_failure_does_not_mark_day_done(app_env):
    from app.db import session_scope
    from app.models import DayStatus, HarvestedDay
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    good_day, bad_day = date(2026, 9, 20), date(2026, 9, 21)
    sitemaps = [(good_day, "sitemap-good"), (bad_day, "sitemap-bad")]

    def listing(xml_url, session=None):
        if xml_url == "sitemap-bad":
            raise ConnectionError("Remote end closed connection without response")
        return ["https://www.lefigaro.fr/a1"]

    with patch("app.services.harvest.list_daily_sitemaps", return_value=sitemaps), \
         patch("app.services.harvest.get_articles_from_sitemap", side_effect=listing), \
         patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake_article(u)):

        _register_job_row("job-1")
        run_harvest(JobHandle(id="job-1", type="harvest"), good_day, bad_day)

    with session_scope() as db:
        good_row = db.get(HarvestedDay, good_day)
        bad_row = db.get(HarvestedDay, bad_day)

    assert good_row.status == DayStatus.DONE.value
    assert bad_row.status != DayStatus.DONE.value, (
        "день со сбоем чтения карты не должен считаться собранным"
    )
    assert bad_row.status == DayStatus.FAILED.value


def test_listing_failure_is_retried_on_next_run(app_env):
    """День со сбитой картой должен листаться заново, а не пропускаться при резюме."""
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    day = date(2026, 9, 21)
    sitemaps = [(day, "sitemap-1")]
    attempts = {"count": 0}

    def flaky_listing(xml_url, session=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionError("transient network error")
        return ["https://www.lefigaro.fr/a1"]

    with patch("app.services.harvest.list_daily_sitemaps", return_value=sitemaps), \
         patch("app.services.harvest.get_articles_from_sitemap", side_effect=flaky_listing), \
         patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake_article(u)):

        _register_job_row("job-1")
        run_harvest(JobHandle(id="job-1", type="harvest"), day, day)
        assert attempts["count"] == 1

        _register_job_row("job-2")
        run_harvest(JobHandle(id="job-2", type="harvest"), day, day)
        assert attempts["count"] == 2, "день со сбоем листинга должен быть перечитан на резюме"

    from app.db import session_scope
    from app.models import Article, DayStatus, HarvestedDay
    from sqlalchemy import select

    with session_scope() as db:
        row = db.get(HarvestedDay, day)
        assert row.status == DayStatus.DONE.value
        saved = db.execute(select(Article)).scalars().all()
        assert len(saved) == 1
