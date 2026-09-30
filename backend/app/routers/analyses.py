"""Анализ: запуск прогонов, чтение результатов, примеры и выгрузки."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.exports import render_examples_html
from ..db import get_db
from ..models import Analysis, Article, Dictionary, JobStatus, JobType
from ..schemas import (
    AnalysisDetail,
    AnalysisRequest,
    AnalysisSummary,
    ContextWord,
    ExamplesPage,
    JobOut,
    KeywordStats,
    clean_keywords,
)
from ..services.analysis import ANALYSABLE, run_analysis, unpack
from ..services.jobs import registry

router = APIRouter(prefix="/api/analyses", tags=["analyses"])


def _get_or_404(db: Session, analysis_id: int) -> Analysis:
    row = db.get(Analysis, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Анализ не найден")
    return row


def _context_words(raw: dict[str, int], total_occurrences: int) -> list[ContextWord]:
    """Проценты считаются от общего числа вхождений — как в десктопной версии."""
    return [
        ContextWord(
            word=word,
            count=count,
            percentage=round(count / total_occurrences * 100, 2) if total_occurrences else 0.0,
        )
        for word, count in raw.items()
    ]


@router.get("", response_model=list[AnalysisSummary])
def list_analyses(
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
) -> list[Analysis]:
    return list(
        db.execute(select(Analysis).order_by(Analysis.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )


@router.post("", response_model=JobOut, status_code=202)
def start_analysis(payload: AnalysisRequest, db: Session = Depends(get_db)) -> JobOut:
    """Ставит анализ в очередь. Сеть не трогается — считаем по корпусу."""
    keywords: list[str] = []
    dictionary: Dictionary | None = None

    if payload.dictionary_id is not None:
        dictionary = db.get(Dictionary, payload.dictionary_id)
        if dictionary is None:
            raise HTTPException(status_code=404, detail="Словарь не найден")
        keywords = list(dictionary.keywords)

    if payload.keywords:
        keywords = clean_keywords(payload.keywords)

    if not keywords:
        raise HTTPException(status_code=400, detail="Не заданы ключевые слова")

    # Только «есть ли хоть одна статья»: полный COUNT за год на удалённой базе
    # держит запрос минутами, а кнопка на странице всё это время «молчит».
    # Точный подсчёт всё равно делает сам анализ уже в фоне.
    exists_stmt = (
        select(Article.id)
        .where(
            Article.published_date.between(payload.start_date, payload.end_date),
            Article.status.in_(ANALYSABLE),
        )
        .limit(1)
    )
    if payload.categories:
        exists_stmt = exists_stmt.where(Article.category.in_(payload.categories))
    if db.execute(exists_stmt).first() is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "За выбранный период (и выбранных категорий, если заданы) в корпусе "
                "нет статей. Соберите корпус за эти даты на вкладке «Корпус»."
            ),
        )

    row = Analysis(
        name=payload.name or (dictionary.name if dictionary else None),
        start_date=payload.start_date,
        end_date=payload.end_date,
        keywords=keywords,
        dictionary_id=payload.dictionary_id,
        categories=payload.categories,
        text_scope=payload.text_scope,
        status=JobStatus.RUNNING.value,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    analysis_id = row.id

    def target(handle):
        try:
            run_analysis(
                handle,
                analysis_id,
                payload.start_date,
                payload.end_date,
                keywords,
                categories=payload.categories,
                text_scope=payload.text_scope,
            )
        except Exception as exc:
            # Помечаем сам анализ упавшим, иначе он навсегда останется "running"
            # в списке истории, хотя задача уже завершилась.
            from ..db import session_scope

            with session_scope() as session:
                failed = session.get(Analysis, analysis_id)
                if failed is not None:
                    failed.status = JobStatus.FAILED.value
                    failed.error = str(exc)
                    failed.finished_at = datetime.utcnow()
            raise

    job = registry.submit(
        JobType.ANALYSIS.value,
        target,
        params={
            "analysis_id": analysis_id,
            "start_date": payload.start_date.isoformat(),
            "end_date": payload.end_date.isoformat(),
            "keywords_count": len(keywords),
        },
    )
    return JobOut(**job.snapshot())


@router.get("/{analysis_id}", response_model=AnalysisDetail)
def get_analysis(analysis_id: int, db: Session = Depends(get_db)) -> AnalysisDetail:
    row = _get_or_404(db, analysis_id)
    payload = unpack(row.statistics_gz) or {}
    timeseries = unpack(row.timeseries_gz) or {}

    stats: list[KeywordStats] = []
    for keyword in row.keywords:
        entry = payload.get("statistics", {}).get(keyword)
        if entry is None:
            continue
        occurrences = entry.get("total_occurrences", 0)
        stats.append(
            KeywordStats(
                keyword=keyword,
                articles_with_word=entry.get("articles_with_word", 0),
                total_processed_articles=entry.get("total_processed_articles", 0),
                percentage=entry.get("percentage", 0.0),
                total_occurrences=occurrences,
                left_context_words=_context_words(
                    entry.get("left_context_words", {}), occurrences
                ),
                right_context_words=_context_words(
                    entry.get("right_context_words", {}), occurrences
                ),
            )
        )

    return AnalysisDetail(
        id=row.id,
        name=row.name,
        start_date=row.start_date,
        end_date=row.end_date,
        keywords=row.keywords,
        total_processed_articles=row.total_processed_articles,
        status=row.status,
        error=row.error,
        dictionary_id=row.dictionary_id,
        categories=row.categories,
        text_scope=row.text_scope,
        created_at=row.created_at,
        finished_at=row.finished_at,
        articles_in_corpus=payload.get("articles_in_corpus", 0),
        stats=stats,
        timeseries=timeseries,
    )


@router.get("/{analysis_id}/examples", response_model=ExamplesPage)
def get_examples(
    analysis_id: int,
    keyword: str = Query(...),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, le=500),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ExamplesPage:
    """Примеры отдаются страницами: у частотных слов их тысячи."""
    row = _get_or_404(db, analysis_id)
    all_examples = (unpack(row.examples_gz) or {}).get(keyword, [])

    if search:
        needle = search.lower()
        all_examples = [
            item
            for item in all_examples
            if needle in item.get("context_before", "").lower()
            or needle in item.get("context_after", "").lower()
            or needle in (item.get("title") or "").lower()
        ]

    return ExamplesPage(
        keyword=keyword,
        total=len(all_examples),
        offset=offset,
        limit=limit,
        items=all_examples[offset:offset + limit],
    )


@router.delete("/{analysis_id}", status_code=204, response_class=Response)
def delete_analysis(analysis_id: int, db: Session = Depends(get_db)) -> Response:
    row = _get_or_404(db, analysis_id)
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.get("/{analysis_id}/export.json")
def export_json(analysis_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    """Выгрузка в формате, совместимом со старыми файлами statistics/*.json."""
    row = _get_or_404(db, analysis_id)
    payload = unpack(row.statistics_gz)
    if payload is None:
        raise HTTPException(status_code=409, detail="Результат ещё не готов")

    filename = f"statistics_{row.created_at.strftime('%Y%m%d_%H%M%S')}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{analysis_id}/export.html", response_class=HTMLResponse)
def export_html(analysis_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    """HTML-отчёт с примерами — тот же вид, что давал десктоп."""
    row = _get_or_404(db, analysis_id)
    examples = unpack(row.examples_gz)
    if examples is None:
        raise HTTPException(status_code=409, detail="Результат ещё не готов")

    html = render_examples_html(
        keywords=row.keywords,
        examples_by_keyword=examples,
        start_date=row.start_date.strftime("%Y-%m-%d"),
        end_date=row.end_date.strftime("%Y-%m-%d"),
        generated_at=row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    )
    filename = f"examples_{row.created_at.strftime('%Y%m%d_%H%M%S')}.html"
    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
