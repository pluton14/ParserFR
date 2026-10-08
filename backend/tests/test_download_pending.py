"""Докачка уже известных статей без обращения к листингу карт сайта."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fake(url):
    from app.core.figaro import FetchedArticle

    return FetchedArticle(url=url, text="le président russe parle", title="T", category="C")


def _register_job_row(job_id="job-1"):
    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id=job_id, type="harvest"))


def test_download_pending_never_calls_sitemap_listing(app_env):
    """Главное свойство: ни list_daily_sitemaps, ни get_articles_from_sitemap не трогаются."""
    from app.db import session_scope
    from app.models import Article, ArticleStatus, HarvestedDay
    from app.services.harvest import run_download_pending
    from app.services.jobs import JobHandle

    day = date(2012, 3, 1)
    with session_scope() as db:
        db.add(Article(url="https://www.lefigaro.fr/a1", published_date=day, status=ArticleStatus.PENDING.value))
        db.add(Article(url="https://www.lefigaro.fr/a2", published_date=day, status=ArticleStatus.PENDING.value))
        db.add(HarvestedDay(day=day, sitemap_url="https://sitemaps.lefigaro.fr/x/2012-03-01.xml", total_urls=2))

    _register_job_row()
    with patch("app.services.harvest.list_daily_sitemaps") as listing, \
         patch("app.services.harvest.get_articles_from_sitemap") as per_day_listing, \
         patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake(u)):
        run_download_pending(JobHandle(id="job-1", type="harvest"))

    listing.assert_not_called()
    per_day_listing.assert_not_called()
    with session_scope() as db:
        statuses = {a.url: a.status for a in db.query(Article).all()}
        assert statuses == {"https://www.lefigaro.fr/a1": "ok", "https://www.lefigaro.fr/a2": "ok"}


def test_download_pending_respects_date_range(app_env):
    from app.db import session_scope
    from app.models import Article, ArticleStatus, HarvestedDay
    from app.services.harvest import run_download_pending
    from app.services.jobs import JobHandle

    in_range, out_of_range = date(2015, 6, 1), date(2019, 1, 1)
    with session_scope() as db:
        db.add(Article(url="https://www.lefigaro.fr/in", published_date=in_range, status=ArticleStatus.PENDING.value))
        db.add(Article(url="https://www.lefigaro.fr/out", published_date=out_of_range, status=ArticleStatus.PENDING.value))
        db.add(HarvestedDay(day=in_range, total_urls=1))
        db.add(HarvestedDay(day=out_of_range, total_urls=1))

    _register_job_row()
    with patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake(u)):
        run_download_pending(JobHandle(id="job-1", type="harvest"),
                             start=date(2015, 1, 1), end=date(2015, 12, 31))

    with session_scope() as db:
        statuses = {a.url: a.status for a in db.query(Article).all()}
        assert statuses["https://www.lefigaro.fr/in"] == "ok"
        assert statuses["https://www.lefigaro.fr/out"] == "pending", "вне диапазона — не трогаем"


def test_download_pending_with_nothing_to_do_finishes_cleanly(app_env):
    from app.services.harvest import run_download_pending
    from app.services.jobs import JobHandle

    _register_job_row()
    run_download_pending(JobHandle(id="job-1", type="harvest"))  # пустая база — не должно падать
