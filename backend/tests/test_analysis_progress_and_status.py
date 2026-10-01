"""Объём для прогресса — оценка по таблице покрытия, итоги — точные; статусы анализов не зависают."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _article(url, status="ok", text="le président russe parle"):
    from app.core.tokenizer import tokenize_french_text
    from app.models import Article

    article = Article(url=url, published_date=date(2020, 1, 1), title="T", category="C", status=status)
    if status == "ok":
        article.set_content(text, tokenize_french_text(text))
    return article


def _analysis_row(status="running"):
    from app.models import Analysis

    return Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                    keywords=["russe"], status=status)


def test_results_are_exact_even_when_coverage_estimate_is_wrong(app_env):
    from app.db import session_scope
    from app.models import Analysis, HarvestedDay, Job
    from app.services.analysis import run_analysis, unpack
    from app.services.jobs import JobHandle

    with session_scope() as db:
        db.add(_article("u1"))
        db.add(_article("u2", status="failed"))
        # Таблица покрытия врёт: утверждает 5 статей с текстом, а есть одна.
        db.add(HarvestedDay(day=date(2020, 1, 1), total_urls=9, ok_count=5))
        db.add(Job(id="job-1", type="analysis"))
        db.add(_analysis_row())
    with session_scope() as db:
        analysis_id = db.query(Analysis).one().id

    handle = JobHandle(id="job-1", type="analysis")
    run_analysis(handle, analysis_id, date(2020, 1, 1), date(2020, 1, 1), ["russe"])

    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        payload = unpack(row.statistics_gz)
        assert row.total_processed_articles == 1, "итог — по реально просмотренным, а не по оценке"
        assert payload["articles_in_corpus"] == 2, "всего в корпусе считается точно, включая статьи без текста"
    assert handle.current == 1 and handle.total == 1


def test_mark_analysis_unfinished_only_touches_running(app_env):
    from app.db import session_scope
    from app.models import Analysis, JobStatus
    from app.services.analysis import mark_analysis_unfinished

    with session_scope() as db:
        db.add(_analysis_row("running"))
        db.add(_analysis_row("done"))
    with session_scope() as db:
        running_id, done_id = [a.id for a in db.query(Analysis).order_by(Analysis.id)]

    mark_analysis_unfinished(running_id, JobStatus.CANCELLED.value)
    mark_analysis_unfinished(done_id, JobStatus.CANCELLED.value)

    with session_scope() as db:
        assert db.get(Analysis, running_id).status == JobStatus.CANCELLED.value
        assert db.get(Analysis, done_id).status == "done", "завершённый анализ трогать нельзя"


def test_recover_stale_jobs_fails_dangling_analyses(app_env):
    from app.db import session_scope
    from app.models import Analysis, JobStatus
    from app.services.jobs import recover_stale_jobs

    with session_scope() as db:
        db.add(_analysis_row("running"))
    recover_stale_jobs()

    with session_scope() as db:
        row = db.query(Analysis).one()
        assert row.status == JobStatus.FAILED.value
        assert row.finished_at is not None
