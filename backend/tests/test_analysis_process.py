"""Анализ в отдельном процессе: результат записан, ход работы дошёл до JobHandle."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _setup_analysis():
    from app.core.tokenizer import tokenize_french_text
    from app.db import session_scope
    from app.models import Analysis, Article, ArticleStatus, Job, JobStatus

    text = "le président russe parle"
    article = Article(
        url="u1", published_date=date(2020, 1, 1), title="Titre",
        category="Politique", status=ArticleStatus.OK.value,
    )
    article.set_content(text, tokenize_french_text(text))

    with session_scope() as db:
        db.add(article)
        db.add(Job(id="job-1", type="analysis"))
        db.add(Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                         keywords=["russe"], status=JobStatus.RUNNING.value))
    with session_scope() as db:
        return db.query(Analysis).one().id


def test_analysis_in_subprocess_saves_result_and_relays_progress(app_env):
    from app.db import session_scope
    from app.models import Analysis
    from app.services.analysis import unpack
    from app.services.analysis_process import run_analysis_in_process
    from app.services.jobs import JobHandle

    analysis_id = _setup_analysis()
    handle = JobHandle(id="job-1", type="analysis")

    run_analysis_in_process(
        handle,
        analysis_id=analysis_id,
        start=date(2020, 1, 1),
        end=date(2020, 1, 1),
        keywords=["russe"],
    )

    assert handle.result_id == analysis_id
    assert handle.stage == "done"
    assert any("Готово" in entry["text"] for entry in handle.logs)
    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        payload = unpack(row.statistics_gz)
        assert payload["statistics"]["russe"]["articles_with_word"] == 1


def test_analysis_in_subprocess_reports_child_error(app_env):
    import pytest

    from app.services.analysis_process import run_analysis_in_process
    from app.services.jobs import JobHandle

    analysis_id = _setup_analysis()

    with pytest.raises(RuntimeError, match="нет статей"):
        run_analysis_in_process(
            JobHandle(id="job-1", type="analysis"),
            analysis_id=analysis_id,
            start=date(1999, 1, 1),
            end=date(1999, 1, 2),
            keywords=["russe"],
        )
