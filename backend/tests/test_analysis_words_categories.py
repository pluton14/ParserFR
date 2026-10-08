"""Общее число слов и разбивка по категориям — в результате анализа."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _article(url, category, text):
    from app.core.tokenizer import tokenize_french_text
    from app.models import Article, ArticleStatus

    article = Article(url=url, published_date=date(2020, 1, 1), title="", category=category,
                      status=ArticleStatus.OK.value)
    article.set_content(text, tokenize_french_text(text))
    return article


def test_total_words_and_categories_are_saved_and_exposed(app_env):
    from fastapi.testclient import TestClient

    from app.core.tokenizer import tokenize_french_text
    from app.db import session_scope
    from app.main import app
    from app.models import Analysis, Job, JobStatus
    from app.services.analysis import run_analysis
    from app.services.jobs import JobHandle

    texts = {
        "http://a/1": ("Politique", "la guerre en Ukraine continue aujourd hui"),
        "http://a/2": ("Politique", "rien de special ici"),
        "http://a/3": ("Culture", "la guerre en Ukraine inspire un film"),
    }
    expected_words = sum(len(tokenize_french_text(t)) for _, t in texts.values())
    keywords = ["guerre en Ukraine"]
    with session_scope() as db:
        for url, (cat, text) in texts.items():
            db.add(_article(url, cat, text))
        db.add(Job(id="job-1", type="analysis"))
        db.add(Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                        keywords=keywords, status=JobStatus.RUNNING.value, text_scope="body"))
    with session_scope() as db:
        analysis_id = db.query(Analysis).one().id
    run_analysis(JobHandle(id="job-1", type="analysis"), analysis_id,
                 date(2020, 1, 1), date(2020, 1, 1), keywords, text_scope="body")

    detail = TestClient(app).get(f"/api/analyses/{analysis_id}").json()
    assert detail["total_words"] == expected_words
    cats = {c["category"]: c for c in detail["stats"][0]["categories"]}
    assert cats["Politique"]["articles_with_word"] == 1 and cats["Politique"]["category_articles"] == 2
    assert cats["Politique"]["percentage"] == 50.0
    assert cats["Culture"]["articles_with_word"] == 1 and cats["Culture"]["percentage"] == 100.0


def test_old_analyses_without_new_fields_still_open(app_env):
    from fastapi.testclient import TestClient

    from app.db import session_scope
    from app.main import app
    from app.models import Analysis, JobStatus
    from app.services.analysis import _pack

    with session_scope() as db:
        db.add(Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1), keywords=["x"],
                        status=JobStatus.DONE.value, text_scope="body",
                        statistics_gz=_pack({"total_processed_articles": 5, "articles_in_corpus": 6,
                                             "statistics": {"x": {"articles_with_word": 1, "percentage": 20.0,
                                                                  "total_occurrences": 1}}}),
                        timeseries_gz=_pack({"x": []})))
    with session_scope() as db:
        aid = db.query(Analysis).one().id
    detail = TestClient(app).get(f"/api/analyses/{aid}").json()
    assert detail["total_words"] is None
    assert detail["stats"][0]["categories"] == []
