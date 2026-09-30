"""Повторный сбор тех же дат не создаёт дубликатов и не теряет статьи.

Живой прогон против реального сайта уже подтвердил это (512 статей до и
после повторного сбора двух дат, см. DEVLOG 2026-09-22). Здесь та же
гарантия закрепляется без сети — на моках, чтобы тест был быстрым и
детерминированным и ловил регрессию сразу, а не только на очередном
ручном прогоне.
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fake_article(url: str):
    from app.core.figaro import FetchedArticle

    return FetchedArticle(
        url=url,
        text=f"Texte de test pour {url} avec le mot guerre plusieurs fois.",
        title="Titre",
        category="International",
        truncated=False,
    )


def _register_job_row(job_id: str) -> None:
    """run_harvest пишет логи через job.log(), которое требует существующую
    строку в jobs (FK). В проде её создаёт registry.submit() перед запуском
    потока; здесь run_harvest вызывается напрямую, минуя реестр, поэтому
    строка создаётся вручную — это особенность теста, не продакшн-кода."""
    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id=job_id, type="harvest"))


def test_reharvest_same_period_no_duplicates(app_env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Article
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    urls = [f"https://www.lefigaro.fr/article-{i}" for i in range(5)]
    day = date(2026, 9, 21)

    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(day, "sitemap-url")]), \
         patch("app.services.harvest.get_articles_from_sitemap", return_value=urls), \
         patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake_article(u)):

        _register_job_row("job-1")
        job1 = JobHandle(id="job-1", type="harvest")
        run_harvest(job1, day, day)

        with session_scope() as db:
            count_after_first = len(db.execute(select(Article)).scalars().all())

        _register_job_row("job-2")
        job2 = JobHandle(id="job-2", type="harvest")
        run_harvest(job2, day, day)

        with session_scope() as db:
            rows_after_second = db.execute(select(Article)).scalars().all()
            count_after_second = len(rows_after_second)
            unique_urls = len({row.url for row in rows_after_second})

    assert count_after_first == 5
    assert count_after_second == 5, "повторный сбор не должен плодить дубликаты"
    assert unique_urls == 5


def test_second_harvest_skips_already_known_urls_without_refetching(app_env):
    """Ключевая экономия разделения сбора и анализа: известные статьи не качаются повторно."""
    from datetime import date as date_cls

    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    urls = [f"https://www.lefigaro.fr/article-{i}" for i in range(3)]
    day = date_cls(2026, 9, 21)
    fetch_calls = []

    def counting_fetch(url, session=None):
        fetch_calls.append(url)
        return _fake_article(url)

    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(day, "sitemap-url")]), \
         patch("app.services.harvest.get_articles_from_sitemap", return_value=urls), \
         patch("app.services.harvest.fetch_article", side_effect=counting_fetch):

        _register_job_row("job-1")
        run_harvest(JobHandle(id="job-1", type="harvest"), day, day)
        assert len(fetch_calls) == 3

        fetch_calls.clear()
        _register_job_row("job-2")
        run_harvest(JobHandle(id="job-2", type="harvest"), day, day)
        assert fetch_calls == [], "уже собранные URL не должны запрашиваться повторно"
