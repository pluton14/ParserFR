"""Резюме сбора после паузы/перезапуска: уже собранные дни не листаются заново.

Закрывает находку 2026-09-22: при полном архиве (7699 суточных карт) повторный
запуск без этой проверки заново обращался бы к сети за каждым уже собранным
днём, прежде чем понять, что скачивать нечего. При многочасовом сборе на
домашнем компьютере, который может выключиться, это критично для скорости
восстановления.
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


def test_resume_skips_listing_for_already_done_days(app_env):
    """День, уже помеченный done, не должен вызывать get_articles_from_sitemap заново."""
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    day1, day2 = date(2026, 9, 20), date(2026, 9, 21)
    sitemaps = [(day1, "sitemap-1"), (day2, "sitemap-2")]
    urls_by_day = {"sitemap-1": ["https://www.lefigaro.fr/a1"], "sitemap-2": ["https://www.lefigaro.fr/a2"]}
    listing_calls = []

    def fake_listing(xml_url, session=None):
        listing_calls.append(xml_url)
        return urls_by_day[xml_url]

    with patch("app.services.harvest.list_daily_sitemaps", return_value=sitemaps), \
         patch("app.services.harvest.get_articles_from_sitemap", side_effect=fake_listing), \
         patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake_article(u)):

        _register_job_row("job-1")
        run_harvest(JobHandle(id="job-1", type="harvest"), day1, day2)
        assert sorted(listing_calls) == ["sitemap-1", "sitemap-2"]

        # День 1 "ломается" вручную, будто сервер выключили между запусками:
        # прогресс уже сохранён в HarvestedDay, но сессия перезапускается.
        listing_calls.clear()
        _register_job_row("job-2")
        run_harvest(JobHandle(id="job-2", type="harvest"), day1, day2)

    assert listing_calls == [], "уже собранные дни не должны листаться заново при резюме"


def test_refresh_flag_bypasses_resume_skip(app_env):
    """С refresh=True пользователь явно просит перекачать — резюме не должно мешать."""
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    day = date(2026, 9, 21)
    sitemaps = [(day, "sitemap-1")]
    listing_calls = []

    def fake_listing(xml_url, session=None):
        listing_calls.append(xml_url)
        return ["https://www.lefigaro.fr/a1"]

    with patch("app.services.harvest.list_daily_sitemaps", return_value=sitemaps), \
         patch("app.services.harvest.get_articles_from_sitemap", side_effect=fake_listing), \
         patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake_article(u)):

        _register_job_row("job-1")
        run_harvest(JobHandle(id="job-1", type="harvest"), day, day)

        listing_calls.clear()
        _register_job_row("job-2")
        run_harvest(JobHandle(id="job-2", type="harvest"), day, day, refresh=True)

    assert listing_calls == ["sitemap-1"], "refresh=True должен листать день заново"
