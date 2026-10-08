"""Добор подписей к фото для статей, собранных до появления экстрактора подписей.

Сырой HTML при сборе не сохранялся, поэтому подписи у ~1 млн уже скачанных
статей можно получить только повторной загрузкой страницы. Это отдельная
задача, а не `run_harvest(refresh=True)`: полный пересбор перезаписывает статус
и текст результатом новой загрузки — сетевой сбой превратил бы хорошую статью
в failed и вывел её из анализа. Здесь обновляются ТОЛЬКО подписи.

captions_extracted: 0 — не извлекали, 1 — извлечено (подписей может не быть),
2 — страница сейчас недоступна (404/500), подписи неизвестны.
"""

from __future__ import annotations

import concurrent.futures
import time
from datetime import date

from sqlalchemy import func, select, update

from ..config import settings
from ..db import session_scope
from ..models import Article, ArticleStatus, compress
from .harvest import ErrorBudget, SharedSession, StoppedForRepeatedBlocks, _fetch_with_retries, gate
from .jobs import JobHandle

# Сколько статей брать из базы за раз.
CHUNK = 2000
FLUSH_EVERY = 100

_CANDIDATES = (ArticleStatus.OK.value, ArticleStatus.TRUNCATED.value)


def _pending_filter(start: date | None, end: date | None):
    conditions = [Article.captions_extracted == 0, Article.status.in_(_CANDIDATES)]
    if start:
        conditions.append(Article.published_date >= start)
    if end:
        conditions.append(Article.published_date <= end)
    return conditions


def _save(updates: list[dict]) -> None:
    if not updates:
        return
    with session_scope() as db:
        db.execute(update(Article), updates)


def run_backfill_captions(job: JobHandle, start: date | None = None, end: date | None = None) -> None:
    with session_scope() as db:
        total = db.execute(
            select(func.count()).select_from(Article).where(*_pending_filter(start, end))
        ).scalar_one()

    job.log(f"Добор подписей: статей к обработке — {total}")
    job.set_progress(stage="articles", current=0, total=total, message="Добираем подписи…")
    if total == 0:
        job.set_progress(stage="done", message="Добирать нечего")
        return

    gate.reset()
    budget = ErrorBudget(job)
    shared = SharedSession()
    window = max(settings.harvest_workers * 6, 60)
    processed = 0
    last_id = 0
    updates: list[dict] = []
    started = last_log = time.monotonic()
    log_base = 0

    def apply(future: concurrent.futures.Future, article_id: int) -> None:
        nonlocal processed
        try:
            _, fetched, status = future.result()
        except concurrent.futures.CancelledError:
            return
        except StoppedForRepeatedBlocks:
            return  # повторный бан: задача сама остановлена, см. RateGate.on_block
        if fetched is not None:
            captions = fetched.captions
            updates.append({
                "id": article_id,
                "captions_gz": compress(captions) if captions else None,
                "captions_extracted": 1,
            })
        elif status in (ArticleStatus.PREMIUM.value, ArticleStatus.EMPTY.value):
            # Страницы больше нет (404) или она платная (500): подписи неизвестны.
            updates.append({"id": article_id, "captions_gz": None, "captions_extracted": 2})
        # FAILED: оставляем 0 — следующий запуск попробует снова.
        processed += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.harvest_workers) as executor:
        inflight: dict[concurrent.futures.Future, int] = {}

        def drain(block_until: int) -> None:
            nonlocal last_log, log_base
            while len(inflight) > block_until:
                done, _ = concurrent.futures.wait(
                    inflight, timeout=1, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    apply(future, inflight.pop(future))
                if len(updates) >= FLUSH_EVERY:
                    _save(updates)
                    updates.clear()
                if job.is_cancelled():
                    for future in list(inflight):
                        future.cancel()
            now = time.monotonic()
            job.set_progress(current=processed, message=f"Обработано {processed}/{total}")
            if now - last_log >= 120:
                job.log(f"Скорость: {(processed - log_base) / (now - last_log):.1f} ст/с, обработано {processed}")
                last_log, log_base = now, processed

        while not job.is_cancelled():
            with session_scope() as db:
                rows = db.execute(
                    select(Article.id, Article.url)
                    .where(Article.id > last_id, *_pending_filter(start, end))
                    .order_by(Article.id)
                    .limit(CHUNK)
                ).all()
            if not rows:
                break
            for article_id, url in rows:
                last_id = article_id
                future = executor.submit(_fetch_with_retries, url, shared.get(), budget, job)
                inflight[future] = article_id
                if len(inflight) >= window:
                    drain(window // 2)
            drain(0)

        drain(0)

    _save(updates)
    if job.is_cancelled():
        job.log(f"Добор остановлен. Обработано: {processed}.", level="warning")
        return
    job.set_progress(stage="done", current=processed, total=total, message="Добор подписей завершён")
    job.log(f"Готово. Обработано статей: {processed} за {(time.monotonic() - started) / 60:.0f} мин.")
