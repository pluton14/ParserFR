"""Добор подписей: пишет только подписи, не трогает ни текст, ни статус статьи."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _article(url, status="ok", text="le président russe parle", extracted=0, day=date(2012, 3, 1)):
    from app.core.tokenizer import tokenize_french_text
    from app.models import Article

    article = Article(url=url, published_date=day, title="T", category="C", status=status)
    article.set_content(text, tokenize_french_text(text))
    article.captions_extracted = extracted
    return article


def test_backfill_updates_only_captions_and_keeps_text_and_status(app_env):
    from app.core.figaro import FetchedArticle, GonePage
    from app.db import session_scope
    from app.models import Article, Job
    from app.services.backfill import run_backfill_captions
    from app.services.jobs import JobHandle

    with session_scope() as db:
        db.add(_article("https://www.lefigaro.fr/a-ok"))
        db.add(_article("https://www.lefigaro.fr/a-gone"))
        db.add(_article("https://www.lefigaro.fr/a-done", extracted=1))     # уже извлечено
        db.add(_article("https://www.lefigaro.fr/a-pending", status="pending"))  # не статья с текстом
        db.add(Job(id="job-1", type="harvest"))

    fetched = []

    def fake_fetch(url, session=None):
        fetched.append(url)
        if url.endswith("a-gone"):
            raise GonePage(url)
        # Новая загрузка отдаёт ДРУГОЙ текст — backfill обязан его проигнорировать.
        return FetchedArticle(url=url, text="совсем другой текст", title="X", category="C",
                              captions="Une légende.")

    with patch("app.services.harvest.fetch_article", side_effect=fake_fetch):
        run_backfill_captions(JobHandle(id="job-1", type="harvest"))

    assert sorted(fetched) == ["https://www.lefigaro.fr/a-gone", "https://www.lefigaro.fr/a-ok"]
    with session_scope() as db:
        ok = db.query(Article).filter_by(url="https://www.lefigaro.fr/a-ok").one()
        gone = db.query(Article).filter_by(url="https://www.lefigaro.fr/a-gone").one()
        assert ok.captions == "Une légende." and ok.captions_extracted == 1
        assert ok.status == "ok" and "président" in " ".join(ok.tokens), "текст и статус не тронуты"
        assert gone.captions_extracted == 2 and gone.status == "ok", "404: подписи неизвестны, статья остаётся ok"


def test_backfill_leaves_failed_fetches_for_the_next_run(app_env):
    import requests

    from app.db import session_scope
    from app.models import Article, Job
    from app.services.backfill import run_backfill_captions
    from app.services.jobs import JobHandle

    with session_scope() as db:
        db.add(_article("https://www.lefigaro.fr/a1"))
        db.add(Job(id="job-1", type="harvest"))

    def always_fails(url, session=None):
        raise requests.exceptions.ConnectionError("boom")

    from app.config import settings

    with patch("app.services.harvest.fetch_article", side_effect=always_fails), \
         patch.object(settings, "retry_delay", 0):
        run_backfill_captions(JobHandle(id="job-1", type="harvest"))

    with session_scope() as db:
        assert db.query(Article).one().captions_extracted == 0, "сбой сети не должен считаться «извлечено»"
