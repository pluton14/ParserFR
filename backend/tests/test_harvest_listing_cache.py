"""Список статей суточной карты кэшируется в базе сразу после чтения.

Закрывает находку пользователя 2026-09-22: раньше список URL внутри карты
жил только в памяти одного прогона и терялся при любом перезапуске — даже
для дня, который ещё не докачан (не done), карта перечитывалась по сети
заново на каждом рестарте. Теперь карта листается по сети только один раз
за всё время жизни архива.
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


def test_listing_not_repeated_for_partially_downloaded_day(app_env):
    """День, остановленный на середине (не done), не должен листаться заново."""
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    day = date(2026, 9, 21)
    urls = [f"https://www.lefigaro.fr/a{i}" for i in range(6)]
    listing_calls = []

    def counting_listing(xml_url, session=None):
        listing_calls.append(xml_url)
        return urls

    fetch_count = {"n": 0}

    def cancel_after_two(job):
        def _fetch(url, session=None):
            fetch_count["n"] += 1
            if fetch_count["n"] >= 2:
                job.cancel_event.set()
            return _fake_article(url)
        return _fetch

    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(day, "sitemap-1")]), \
         patch("app.services.harvest.get_articles_from_sitemap", side_effect=counting_listing):

        _register_job_row("job-1")
        job1 = JobHandle(id="job-1", type="harvest")
        with patch("app.services.harvest.fetch_article", side_effect=cancel_after_two(job1)):
            run_harvest(job1, day, day)

        assert len(listing_calls) == 1, "первый прогон обязан прочитать карту по сети"

        # Второй прогон: день ещё не done (был отменён на середине), но
        # карта уже лежит в базе — сетевого листинга быть не должно.
        listing_calls.clear()
        _register_job_row("job-2")
        with patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake_article(u)):
            run_harvest(JobHandle(id="job-2", type="harvest"), day, day)

        assert listing_calls == [], (
            "карта уже известна из предыдущего прогона — повторный сетевой листинг не нужен"
        )

    from app.db import session_scope
    from app.models import Article, ArticleStatus, DayStatus, HarvestedDay
    from sqlalchemy import select

    with session_scope() as db:
        row = db.get(HarvestedDay, day)
        assert row.status == DayStatus.DONE.value
        rows = db.execute(select(Article)).scalars().all()
        assert len(rows) == 6
        assert all(r.status != ArticleStatus.PENDING.value for r in rows), (
            "после полного резюме не должно остаться pending-заглушек"
        )


def test_pending_placeholder_does_not_count_as_day_complete(app_env):
    """Сразу после листинга (до скачивания) день не должен считаться done."""
    from app.db import session_scope
    from app.models import ArticleStatus, DayStatus, HarvestedDay
    from app.services.harvest import _persist_pending_urls
    from sqlalchemy import select

    day = date(2026, 9, 21)
    urls = ["https://www.lefigaro.fr/a1", "https://www.lefigaro.fr/a2"]

    _persist_pending_urls(day, urls)

    with session_scope() as db:
        from app.models import Article

        rows = db.execute(select(Article).where(Article.published_date == day)).scalars().all()
        assert len(rows) == 2
        assert all(r.status == ArticleStatus.PENDING.value for r in rows)
        # HarvestedDay ещё не создан на этом этапе — сам факт наличия
        # pending-строк не должен нигде интерпретироваться как "готово".
        assert db.get(HarvestedDay, day) is None


def test_day_with_fewer_rows_than_total_urls_is_relisted(app_env):
    """День с частичным набором строк (меньше total_urls) не должен считаться кэшированным.

    Сценарий: старая версия кода (до появления pending-кэша) успела скачать
    часть статей дня перед отменой, полного списка URL в базе нет — только
    те, что реально скачались. Проверка "есть хоть одна строка" ошибочно
    приняла бы такой день за уже листингованный и потеряла бы остальные URL
    навсегда. Проверка должна сверяться с total_urls, а не с фактом
    существования строки.
    """
    from app.db import session_scope
    from app.models import Article, HarvestedDay
    from app.services.harvest import _days_with_known_urls

    day = date(2026, 9, 21)

    with session_scope() as db:
        # Имитация старого поведения: total_urls=6 зафиксирован при листинге,
        # но реально скачано (есть строк) только 2 — как будто отмена
        # случилась на середине скачивания, до появления pending-кэша.
        db.add(HarvestedDay(day=day, total_urls=6, status="partial"))
        db.add(Article(url="https://www.lefigaro.fr/a1", published_date=day, status="ok"))
        db.add(Article(url="https://www.lefigaro.fr/a2", published_date=day, status="ok"))

    cached = _days_with_known_urls([day])
    assert day not in cached, (
        "день с 2 строками из 6 total_urls не должен считаться полностью листингованным"
    )


def test_day_with_all_rows_matching_total_is_cached(app_env):
    """День, где строк ровно столько же, сколько total_urls, — кэшируется."""
    from app.db import session_scope
    from app.models import Article, HarvestedDay
    from app.services.harvest import _days_with_known_urls

    day = date(2026, 9, 21)

    with session_scope() as db:
        db.add(HarvestedDay(day=day, total_urls=2, status="pending"))
        db.add(Article(url="https://www.lefigaro.fr/a1", published_date=day, status="pending"))
        db.add(Article(url="https://www.lefigaro.fr/a2", published_date=day, status="pending"))

    cached = _days_with_known_urls([day])
    assert day in cached
