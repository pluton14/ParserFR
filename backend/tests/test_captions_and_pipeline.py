"""Подписи к фото, 404 как терминальный исход, конвейер без барьера по дням, миграция колонок."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE = """
<html><head><title>Titre page</title>
<meta property="og:title" content="Titre og"></head>
<body>
<aside><figure class="fig-media"><figcaption class="fig-media__legend">Légende du widget latéral</figcaption></figure></aside>
<main><article>
  <div class="fig-wrapper fig-zone-main">
    <figure class="fig-media fig-media--type-photo">
      <figcaption class="fig-media__legend">Le président russe lors de la cérémonie.
        <span class="fig-media__credits">AFP</span></figcaption>
    </figure>
  </div>
  <div class="fig-content-body">
    <p class="fig-paragraph">Premier <a href="#">paragraphe</a> du texte.</p>
    <p class="fig-paragraph">Second paragraphe.</p>
    <figure class="fig-media"><figcaption class="fig-media__legend">Le président russe lors de la cérémonie.
        <span class="fig-media__credits">AFP</span></figcaption></figure>
    <figure class="fig-media"><figcaption class="fig-media__legend">Une autre légende.</figcaption></figure>
  </div>
</article></main>
</body></html>
"""


def test_parse_extracts_text_title_and_captions_without_credits():
    from app.core.figaro import parse_article_html

    article = parse_article_html("https://www.lefigaro.fr/politique/x", PAGE.encode("utf-8"))

    assert article.text == "Premier paragraphe du texte. Second paragraphe."
    assert article.title == "Titre og"
    assert article.truncated is False
    # Агентство («AFP») не входит в подпись, дубль объединён, боковой виджет вне <article> не берётся.
    assert article.captions.split("\n") == [
        "Le président russe lors de la cérémonie.",
        "Une autre légende.",
    ]


def test_parse_text_matches_beautifulsoup_reference():
    """Корпус должен остаться сравнимым со старыми выгрузками: те же слова, что давал bs4."""
    from bs4 import BeautifulSoup

    from app.core.figaro import parse_article_html

    reference = " ".join(
        p.get_text(" ", strip=True)
        for p in BeautifulSoup(PAGE, "html.parser").select(".fig-paragraph")
    )
    assert parse_article_html("u", PAGE).text == reference


def test_parse_detects_paywall_teaser():
    from app.core.figaro import parse_article_html

    html = PAGE.replace("<article>", '<article><div class="fig-premium-paywall"></div>')
    assert parse_article_html("u", html).truncated is True


def _fake(url, captions="Une légende."):
    from app.core.figaro import FetchedArticle

    return FetchedArticle(url=url, text="le président russe parle", title="T", category="C", captions=captions)


def _register_job_row(job_id):
    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id=job_id, type="harvest"))


def test_harvest_saves_captions_and_marks_gone_pages_empty(app_env):
    from app.core.figaro import GonePage
    from app.db import session_scope
    from app.models import Article, ArticleStatus, DayStatus, HarvestedDay
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    day = date(2012, 3, 1)
    urls = ["https://www.lefigaro.fr/a1", "https://www.lefigaro.fr/gone"]

    def fetch(url, session=None):
        if url.endswith("/gone"):
            raise GonePage(url)
        return _fake(url)

    _register_job_row("job-1")
    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(day, "sm")]), \
         patch("app.services.harvest.get_articles_from_sitemap", return_value=urls), \
         patch("app.services.harvest.fetch_article", side_effect=fetch):
        run_harvest(JobHandle(id="job-1", type="harvest"), day, day)

    with session_scope() as db:
        ok = db.query(Article).filter_by(url=urls[0]).one()
        gone = db.query(Article).filter_by(url=urls[1]).one()
        assert ok.captions == "Une légende." and ok.captions_extracted == 1
        assert gone.status == ArticleStatus.EMPTY.value, "404 — терминальный исход, не failed"
        assert db.get(HarvestedDay, day).status == DayStatus.DONE.value, (
            "день с 404 не должен висеть partial: повторять нечего"
        )


def test_many_small_days_all_complete_without_day_barrier(app_env):
    from app.db import session_scope
    from app.models import DayStatus, HarvestedDay
    from app.services.harvest import run_harvest
    from app.services.jobs import JobHandle

    days = [date(2010, 3, d) for d in range(1, 21)]
    per_day_urls = {d: [f"https://www.lefigaro.fr/{d.day}-{i}" for i in range(3)] for d in days}
    by_sitemap = {f"sm-{d.day}": per_day_urls[d] for d in days}

    _register_job_row("job-1")
    with patch("app.services.harvest.list_daily_sitemaps", return_value=[(d, f"sm-{d.day}") for d in days]), \
         patch("app.services.harvest.get_articles_from_sitemap",
               side_effect=lambda xml, session=None: by_sitemap[xml]), \
         patch("app.services.harvest.fetch_article", side_effect=lambda u, session=None: _fake(u)):
        run_harvest(JobHandle(id="job-1", type="harvest"), days[0], days[-1])

    with session_scope() as db:
        statuses = {row.day: row.status for row in db.query(HarvestedDay).all()}
        assert set(statuses) == set(days)
        assert all(s == DayStatus.DONE.value for s in statuses.values())
        assert all(row.ok_count == 3 for row in db.query(HarvestedDay).all())


def test_init_db_adds_caption_columns_to_existing_database(app_env):
    from sqlalchemy import text

    from app.db import engine, init_db

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE articles DROP COLUMN captions_gz"))
        conn.execute(text("ALTER TABLE articles DROP COLUMN captions_extracted"))

    init_db()

    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(articles)"))}
    assert {"captions_gz", "captions_extracted"} <= columns
