"""Дубль ссылки внутри одной суточной карты не должен ронять сбор.

Закрывает находку на реальном сборе 2026-09-22: sitemap Le Figaro за
2009-08-26 дважды перечислил одну и ту же спортивную статью. Без дедупа
вторая попытка сохранить её как pending падала на UNIQUE constraint
(articles.url) и роняла всю задачу с IntegrityError.
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


def test_duplicate_url_within_one_sitemap_does_not_crash(app_env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Article, DayStatus, HarvestedDay
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    day = date(2009, 8, 26)
    # Та же ссылка встречается в карте дважды, как в реальном инциденте.
    urls_with_dup = [
        "https://sport24.lefigaro.fr/football/ligue-des-champions/actualites/les-12-travaux-d-arsenal-291212",
        "https://www.lefigaro.fr/a2",
        "https://sport24.lefigaro.fr/football/ligue-des-champions/actualites/les-12-travaux-d-arsenal-291212",
    ]

    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(day, "sitemap-1")]), \
         patch("app.services.harvest.get_articles_from_sitemap", return_value=urls_with_dup), \
         patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake_article(u)):

        _register_job_row("job-1")
        job = JobHandle(id="job-1", type="harvest")
        run_harvest(job, day, day)  # не должно бросить IntegrityError

    assert job.status != "failed", f"задача не должна падать на дубле: {job.error}"

    with session_scope() as db:
        rows = db.execute(select(Article).where(Article.published_date == day)).scalars().all()
        urls_saved = [r.url for r in rows]
        assert len(urls_saved) == 2, "дубль должен схлопнуться в одну статью"
        assert len(set(urls_saved)) == 2

        row = db.get(HarvestedDay, day)
        assert row.status == DayStatus.DONE.value
        assert row.total_urls == 2, "total_urls должен учитывать уникальные ссылки, не сырой список"


def test_persist_pending_urls_dedupes_within_call(app_env):
    """_persist_pending_urls сама по себе тоже не должна падать на дубле в списке."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Article
    from app.services.harvest import _persist_pending_urls

    day = date(2026, 9, 21)
    url = "https://www.lefigaro.fr/a1"

    _persist_pending_urls(day, [url, url, url])  # не должно бросить IntegrityError

    with session_scope() as db:
        rows = db.execute(select(Article).where(Article.url == url)).scalars().all()
        assert len(rows) == 1
