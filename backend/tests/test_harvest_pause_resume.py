"""Пауза посреди дня не должна тихо терять недобранные статьи.

Закрывает находку 2026-09-22 (после добавления резюме по дням): если день,
остановленный на середине, помечается done, он больше никогда не листается
повторно — недобор остаётся в корпусе навсегда и незаметно. День обязан
помечаться partial, чтобы следующий запуск его доскачал.
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


def test_cancelling_mid_day_marks_it_partial_not_done(app_env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Article, DayStatus, HarvestedDay
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    day = date(2026, 9, 21)
    urls = [f"https://www.lefigaro.fr/a{i}" for i in range(6)]

    fetched_so_far = []

    def fetch_then_cancel_after_two(job):
        def _fetch(url, session=None):
            fetched_so_far.append(url)
            if len(fetched_so_far) >= 2:
                job.cancel_event.set()
            return _fake_article(url)
        return _fetch

    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(day, "sitemap-1")]), \
         patch("app.services.harvest.get_articles_from_sitemap", return_value=urls):

        _register_job_row("job-1")
        job = JobHandle(id="job-1", type="harvest")
        with patch("app.services.harvest.fetch_article", side_effect=fetch_then_cancel_after_two(job)):
            run_harvest(job, day, day)

        with session_scope() as db:
            row = db.get(HarvestedDay, day)
            assert row is not None
            assert row.status != DayStatus.DONE.value, (
                "день, остановленный на середине, не должен считаться done"
            )
            # Строка в базе появляется для КАЖДОГО url сразу после листинга
            # (pending-заглушка) — поэтому считаем не наличие строк, а
            # сколько из них реально скачаны (не остались pending).
            from app.models import ArticleStatus

            fetched_ok = len(
                db.execute(
                    select(Article).where(Article.status != ArticleStatus.PENDING.value)
                ).scalars().all()
            )
            assert fetched_ok < len(urls), (
                "не все статьи должны быть реально скачаны при отмене на середине"
            )

        # Резюме без cancel: должно доскачать оставшееся, а не пропустить день.
        _register_job_row("job-2")
        with patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake_article(u)):
            run_harvest(JobHandle(id="job-2", type="harvest"), day, day)

        with session_scope() as db:
            row = db.get(HarvestedDay, day)
            assert row.status == DayStatus.DONE.value
            from app.models import ArticleStatus

            fetched_ok = len(
                db.execute(
                    select(Article).where(Article.status != ArticleStatus.PENDING.value)
                ).scalars().all()
            )
            assert fetched_ok == len(urls), "после резюме должны быть скачаны все статьи дня"
