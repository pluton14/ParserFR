"""Анализ: прогон словаря по уже собранному корпусу.

Здесь нет сети. Всё, что нужно, — токены статей, которые сбор уже положил
в базу. Поэтому смена словаря стоит секунды, а не часы: именно ради этого
сбор и анализ разведены.

Сами правила подсчёта (соседи слева/справа, окно примера, сопоставление
словосочетаний) взяты из parser.py без изменений.
"""

from __future__ import annotations

import json
import time
import zlib
from dataclasses import asdict
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, defer

from ..config import settings
from ..core.statistics import WordStatistics, build_keyword_plans, scan_article
from ..core.tokenizer import tokenize_french_text
from ..db import session_scope
from ..models import Analysis, Article, ArticleStatus, HarvestedDay, JobStatus
from .jobs import JobHandle

# Что попадает в анализ. Обрезанные платные статьи включены сознательно:
# затравка — это тоже настоящий текст Le Figaro, и терять её на корпусе,
# где платных заметная доля, значило бы обеднить выборку сильнее, чем
# исказить. Доля обрезанных показывается пользователю отдельно.
ANALYSABLE = (ArticleStatus.OK.value, ArticleStatus.TRUNCATED.value)

# Сколько статей тянуть из базы за раз, чтобы не держать корпус в памяти целиком.
CHUNK_SIZE = 1000


def count_available_articles(
    db: Session, start: date, end: date, categories: list[str] | None = None
) -> tuple[int, int]:
    """(статей с текстом, всего статей в корпусе) за период и категории."""
    with_text_stmt = select(func.count()).select_from(Article).where(
        Article.published_date.between(start, end),
        Article.status.in_(ANALYSABLE),
    )
    total_stmt = select(func.count()).select_from(Article).where(
        Article.published_date.between(start, end)
    )
    if categories:
        with_text_stmt = with_text_stmt.where(Article.category.in_(categories))
        total_stmt = total_stmt.where(Article.category.in_(categories))

    with_text = db.execute(with_text_stmt).scalar_one()
    total = db.execute(total_stmt).scalar_one()

    return with_text, total


def estimate_articles_with_text(db: Session, start: date, end: date) -> int:
    """Оценка числа статей с текстом по таблице покрытия дней (одна строка на день).

    Точный COUNT по статьям на удалённой базе (Turso) за несколько лет идёт
    минуты и откладывает старт анализа, а число нужно только полоске прогресса.
    Итоговые числа в результате считаются точно — по реально просмотренным статьям.
    """
    return db.execute(
        select(func.coalesce(func.sum(HarvestedDay.ok_count + HarvestedDay.truncated_count), 0))
        .where(HarvestedDay.day.between(start, end))
    ).scalar_one()


def count_total_articles(db: Session, start: date, end: date) -> int:
    """Все статьи корпуса за период, независимо от статуса."""
    return db.execute(
        select(func.count()).select_from(Article).where(Article.published_date.between(start, end))
    ).scalar_one()


def mark_analysis_unfinished(analysis_id: int, status: str, error: str | None = None) -> None:
    """Переводит анализ из «running» в failed/cancelled, если он всё ещё висит выполняющимся."""
    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        if row is not None and row.status == JobStatus.RUNNING.value:
            row.status = status
            row.error = error
            row.finished_at = datetime.utcnow()


def _article_words(article: Article, text_scope: str) -> list[str]:
    """Слова статьи для сканирования — по выбранной зоне текста.

    Находка 2026-09-28: заголовок токенизируется на лету, а не заранее —
    он короткий (в отличие от текста статьи), лишняя нагрузка ничтожна, зато
    работает ретроактивно для ВСЕХ уже собранных статей: title хранился
    отдельным полем с самого начала, пересбор корпуса не требуется.
    """
    if text_scope == "title":
        return tokenize_french_text(article.title or "")
    if text_scope == "title_body":
        return tokenize_french_text(article.title or "") + article.tokens
    return article.tokens


def _build_statistics_payload(
    stats: dict[str, WordStatistics],
    keywords: list[str],
    start: date,
    end: date,
    total_processed_articles: int,
) -> dict:
    """Собирает JSON в том же формате, что писал parser.save_statistics_to_file.

    Формат сохранён намеренно: старые файлы из папки statistics/ должны
    открываться новым интерфейсом, а новые выгрузки — оставаться читаемыми
    всем, что уже написано вокруг этого формата.
    """
    payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "search_period": {
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
        },
        "total_processed_articles": total_processed_articles,
        "keywords": keywords,
        "statistics": {},
    }

    top = settings.top_context_words
    for keyword in keywords:
        entry = stats.get(keyword)
        if entry is None:
            payload["statistics"][keyword] = {
                "articles_with_word": 0,
                "total_processed_articles": total_processed_articles,
                "percentage": 0,
                "left_context_words": {},
                "right_context_words": {},
            }
            continue

        percentage = (
            (entry.articles_with_word / total_processed_articles * 100)
            if total_processed_articles > 0
            else 0
        )
        payload["statistics"][keyword] = {
            "articles_with_word": entry.articles_with_word,
            "total_processed_articles": total_processed_articles,
            "percentage": round(percentage, 2),
            "total_occurrences": entry.total_occurrences,
            "left_context_words": dict(entry.left_context_words.most_common(top)),
            "right_context_words": dict(entry.right_context_words.most_common(top)),
        }

    return payload


def _build_timeseries(stats: dict[str, WordStatistics], keywords: list[str]) -> dict:
    """Динамика по дням — то, чего в десктопной версии не было вовсе.

    Считать её бесплатно: даты статей уже известны в момент сканирования.
    """
    series = {}
    for keyword in keywords:
        entry = stats.get(keyword)
        if entry is None:
            series[keyword] = []
            continue
        days = sorted(set(entry.occurrences_by_date) | set(entry.articles_by_date))
        series[keyword] = [
            {
                "date": day,
                "occurrences": entry.occurrences_by_date.get(day, 0),
                "articles": entry.articles_by_date.get(day, 0),
            }
            for day in days
        ]
    return series


def _pack(payload) -> bytes:
    return zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), level=6)


def unpack(blob: bytes | None):
    if not blob:
        return None
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def run_analysis(
    job: JobHandle,
    analysis_id: int,
    start: date,
    end: date,
    keywords: list[str],
    categories: list[str] | None = None,
    text_scope: str = "body",
) -> None:
    """Считает статистику по корпусу и записывает результат в analyses."""
    plans = build_keyword_plans(keywords)
    if not plans:
        raise ValueError("Список ключевых слов пуст")

    ordered_keywords = [plan.original for plan in plans]
    stats: dict[str, WordStatistics] = {plan.original: WordStatistics() for plan in plans}

    scope_label = {"body": "текст", "title": "заголовки", "title_body": "заголовки + текст"}[text_scope]
    categories_label = f", категории: {', '.join(categories)}" if categories else ""
    job.log(f"Анализ за {start} — {end}, ключевых слов: {len(plans)}, зона: {scope_label}{categories_label}")
    job.set_progress(stage="counting", message="Считаем объём корпуса…")

    # Без фильтра категорий объём для полоски прогресса берём из таблицы покрытия
    # дней — мгновенно. С фильтром такой таблицы нет, считаем точно, как раньше.
    total_in_corpus: int | None = None
    with session_scope() as db:
        if categories:
            expected, total_in_corpus = count_available_articles(db, start, end, categories)
        else:
            expected = estimate_articles_with_text(db, start, end)
            if expected == 0:
                expected, total_in_corpus = count_available_articles(db, start, end, categories)

    if expected == 0:
        raise ValueError(
            "За этот период в корпусе нет статей с текстом. "
            "Сначала соберите корпус на вкладке «Корпус»."
        )

    approx = "" if total_in_corpus is not None else "≈"
    job.log(f"Статей с текстом: {approx}{expected}")
    job.set_progress(stage="scanning", current=0, total=expected,
                     message="Сканируем статьи…")

    example_limit = settings.example_limit_per_keyword
    seen = 0  # все просмотренные статьи — именно по ним считаются проценты
    processed = 0
    last_id = 0
    cursor_date = start

    chunk_number = 0
    fetch_total = scan_total = 0.0

    while True:
        if job.is_cancelled():
            return

        fetch_started = time.perf_counter()
        with session_scope() as db:
            # Пагинация по (дата, id), а не по одному id: при order by id SQLite
            # на каждой пачке заново сортирует ВСЕ статьи периода (временное
            # B-дерево), и время растёт квадратично — на удалённой базе это
            # часы и миллионы прочитанных строк. По дате идём прямо по индексу.
            stmt = (
                select(Article)
                .where(
                    Article.published_date >= cursor_date,
                    Article.published_date <= end,
                    Article.status.in_(ANALYSABLE),
                    or_(Article.published_date > cursor_date, Article.id > last_id),
                )
                .order_by(Article.published_date, Article.id)
                .limit(CHUNK_SIZE)
                # Полный текст (text_gz) анализу не нужен — только токены и
                # заголовок. По сети с Turso он удваивал объём каждой пачки.
                .options(defer(Article.text_gz))
            )
            if categories:
                stmt = stmt.where(Article.category.in_(categories))
            rows = db.execute(stmt).scalars().all()
            fetch_total += time.perf_counter() - fetch_started

            if not rows:
                break

            scan_started = time.perf_counter()
            for article in rows:
                cursor_date = article.published_date
                last_id = article.id
                seen += 1
                words = _article_words(article, text_scope)
                if not words:
                    continue

                counts = scan_article(
                    words,
                    plans,
                    stats,
                    url=article.url,
                    published_date=article.published_date.isoformat(),
                    title=article.title,
                    example_limit=example_limit,
                )
                for keyword, count in counts.items():
                    if count > 0:
                        stats[keyword].articles_with_word += 1

                processed += 1

            scan_total += time.perf_counter() - scan_started

        # Раздельный замер чтения и подсчёта — чтобы при медленном прогоне было
        # видно, куда уходит время (база или процессор), а не гадать.
        chunk_number += 1
        if chunk_number % 20 == 0:
            job.log(
                f"Пачек: {chunk_number}, просмотрено {seen}; за последние 20 пачек: "
                f"чтение {fetch_total:.1f} с, подсчёт {scan_total:.1f} с"
            )
            fetch_total = scan_total = 0.0

        job.set_progress(
            current=seen,
            total=max(expected, seen),
            message=f"Обработано {seen}/{approx}{max(expected, seen)}",
        )

    with_text = seen

    if total_in_corpus is None:
        job.set_progress(stage="counting", message="Считаем статьи за период…")
        with session_scope() as db:
            total_in_corpus = count_total_articles(db, start, end)

    statistics_payload = _build_statistics_payload(
        stats, ordered_keywords, start, end, with_text
    )
    statistics_payload["articles_in_corpus"] = total_in_corpus

    examples_payload = {
        keyword: [asdict(example) for example in stats[keyword].examples]
        for keyword in ordered_keywords
    }
    timeseries_payload = _build_timeseries(stats, ordered_keywords)

    job.set_progress(stage="saving", message="Сохраняем результат…")

    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        if row is None:
            raise ValueError(f"Анализ {analysis_id} не найден")
        row.total_processed_articles = with_text
        row.statistics_gz = _pack(statistics_payload)
        row.examples_gz = _pack(examples_payload)
        row.timeseries_gz = _pack(timeseries_payload)
        row.status = JobStatus.DONE.value
        row.finished_at = datetime.utcnow()

    job.result_id = analysis_id
    total_found = sum(stats[k].articles_with_word for k in ordered_keywords)
    job.log(f"Готово. Статей с попаданиями: {total_found}.")
    job.log(f"Просмотрено статей: {with_text} (всего в корпусе за период: {total_in_corpus})")
    job.set_progress(stage="done", current=with_text, total=with_text,
                     message="Анализ завершён")
