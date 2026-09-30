"""Фильтры анализа: категории (мультивыбор) и зона текста (заголовок/тело).

Заголовок для старых статей уже есть в базе (хранился отдельно с самого
начала), поэтому фильтр "только заголовки" должен работать ретроактивно,
без пересбора корпуса — именно это здесь и проверяется.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_article(url, category, title, text, day=date(2020, 1, 1)):
    from app.core.tokenizer import tokenize_french_text
    from app.models import Article, ArticleStatus

    article = Article(
        url=url,
        published_date=day,
        title=title,
        category=category,
        status=ArticleStatus.OK.value,
    )
    article.set_content(text, tokenize_french_text(text))
    return article


def _register_job_row(job_id: str) -> None:
    from app.db import session_scope
    from app.models import Job

    with session_scope() as db:
        db.add(Job(id=job_id, type="analysis"))


def test_category_filter_only_counts_matching_articles(app_env):
    from app.db import session_scope
    from app.models import Analysis, JobStatus
    from app.services.analysis import run_analysis, unpack
    from app.services.jobs import JobHandle

    with session_scope() as db:
        db.add(_make_article("u1", "Politique", "Titre", "le président russe parle"))
        db.add(_make_article("u2", "Sport", "Titre", "le président russe parle aussi"))
        db.add(Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                         keywords=["russe"], status=JobStatus.RUNNING.value))

    with session_scope() as db:
        analysis_id = db.query(Analysis).one().id

    _register_job_row("job-1")
    run_analysis(
        JobHandle(id="job-1", type="analysis"),
        analysis_id,
        date(2020, 1, 1),
        date(2020, 1, 1),
        ["russe"],
        categories=["Politique"],
    )

    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        assert row.total_processed_articles == 1, "должна учитываться только статья категории Politique"
        payload = unpack(row.statistics_gz)
        assert payload["statistics"]["russe"]["articles_with_word"] == 1


def test_title_scope_finds_word_only_in_title(app_env):
    from app.db import session_scope
    from app.models import Analysis, JobStatus
    from app.services.analysis import run_analysis, unpack
    from app.services.jobs import JobHandle

    with session_scope() as db:
        db.add(_make_article(
            "u1", "International", "Guerre en Ukraine: le point",
            "le texte ne mentionne pas le mot recherché",
        ))
        db.add(Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                         keywords=["guerre"], status=JobStatus.RUNNING.value))

    with session_scope() as db:
        analysis_id = db.query(Analysis).one().id

    _register_job_row("job-1")
    run_analysis(
        JobHandle(id="job-1", type="analysis"),
        analysis_id,
        date(2020, 1, 1),
        date(2020, 1, 1),
        ["guerre"],
        text_scope="title",
    )

    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        payload = unpack(row.statistics_gz)
        assert payload["statistics"]["guerre"]["articles_with_word"] == 1, (
            "слово 'guerre' есть только в заголовке — при text_scope=title должно найтись"
        )


def test_body_scope_ignores_title_only_matches(app_env):
    from app.db import session_scope
    from app.models import Analysis, JobStatus
    from app.services.analysis import run_analysis, unpack
    from app.services.jobs import JobHandle

    with session_scope() as db:
        db.add(_make_article(
            "u1", "International", "Guerre en Ukraine: le point",
            "le texte ne mentionne pas le mot recherché",
        ))
        db.add(Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                         keywords=["guerre"], status=JobStatus.RUNNING.value))

    with session_scope() as db:
        analysis_id = db.query(Analysis).one().id

    _register_job_row("job-1")
    run_analysis(
        JobHandle(id="job-1", type="analysis"),
        analysis_id,
        date(2020, 1, 1),
        date(2020, 1, 1),
        ["guerre"],
        text_scope="body",
    )

    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        payload = unpack(row.statistics_gz)
        assert payload["statistics"]["guerre"]["articles_with_word"] == 0, (
            "text_scope=body не должен видеть слово, встречающееся только в заголовке"
        )
