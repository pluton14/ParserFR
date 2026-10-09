"""Зоны анализа: заголовок / подписи к фото / основной текст."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_parse_text_scope_accepts_legacy_and_new_values():
    from app.core.zones import format_text_scope, parse_text_scope

    assert parse_text_scope("body") == ("body",)
    assert parse_text_scope("title_body") == ("title", "body")
    assert parse_text_scope("body, captions ,title") == ("title", "captions", "body")
    # Каноническая запись: прежние значения сохраняются как были.
    assert format_text_scope(("title", "body")) == "title_body"
    assert format_text_scope(("title", "captions", "body")) == "title,captions,body"
    for bad in ("", "quotes", "title,quotes"):
        with pytest.raises(ValueError):
            parse_text_scope(bad)


def test_request_schema_normalizes_text_scope():
    from app.schemas import AnalysisRequest

    request = AnalysisRequest(start_date=date(2020, 1, 1), end_date=date(2020, 1, 2),
                              dictionary_id=1, text_scope="captions, title")
    assert request.text_scope == "title,captions"


def _article(url, title, text, captions=None, extracted=1):
    from app.core.tokenizer import tokenize_french_text
    from app.models import Article, ArticleStatus

    article = Article(url=url, published_date=date(2020, 1, 1), title=title, category="C",
                      status=ArticleStatus.OK.value)
    article.set_content(text, tokenize_french_text(text))
    if extracted:
        article.set_captions(captions or "")
    return article


def _run(scope, keywords):
    from app.db import session_scope
    from app.models import Analysis, Job, JobStatus
    from app.services.analysis import run_analysis, unpack
    from app.services.jobs import JobHandle

    with session_scope() as db:
        db.add(Job(id="job-1", type="analysis"))
        db.add(Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                         keywords=keywords, status=JobStatus.RUNNING.value, text_scope=scope))
    with session_scope() as db:
        analysis_id = db.query(Analysis).one().id
    run_analysis(JobHandle(id="job-1", type="analysis"), analysis_id,
                 date(2020, 1, 1), date(2020, 1, 1), keywords, text_scope=scope)
    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        return row.total_processed_articles, unpack(row.statistics_gz)["statistics"]


def test_mixed_zones_keep_article_without_extracted_captions(app_env):
    """Находка 2026-10-09: title+captions+body не должен выкидывать статью
    целиком из-за одних лишь отсутствующих подписей — заголовок и текст у неё
    есть, и слово должно найтись именно в них."""
    from app.db import session_scope

    with session_scope() as db:
        # extracted=0 — подписи у статьи ещё не извлекали (как у всех,
        # собранных до backfill); заголовок и текст при этом полноценные.
        db.add(_article("u1", "Titre avec guerre en Ukraine", "le texte sans le mot",
                        extracted=0))

    total, stats = _run("title,captions,body", ["guerre en Ukraine"])
    assert total == 1, "статья без извлечённых подписей не должна пропадать из выборки"
    assert stats["guerre en Ukraine"]["articles_with_word"] == 1, "слово должно найтись в заголовке"


def test_captions_only_zone_still_excludes_unextracted_from_denominator(app_env):
    """А для ЕДИНСТВЕННОЙ зоны «подписи» прежнее поведение сохраняется:
    статьи без извлечённых подписей не засоряют знаменатель."""
    from app.db import session_scope

    with session_scope() as db:
        db.add(_article("u1", "Titre", "texte", captions="Un char russe.", extracted=1))
        db.add(_article("u2", "Titre", "texte", extracted=0))  # подписи не извлекались

    total, stats = _run("captions", ["char russe"])
    assert total == 1, "статья без извлечённых подписей не должна попасть в знаменатель зоны «подписи»"


def test_captions_zone_finds_words_only_in_captions(app_env):
    from app.db import session_scope

    with session_scope() as db:
        db.add(_article("u1", "Titre", "le texte sans le mot", captions="Un char russe sur la route."))
        db.add(_article("u2", "Titre", "le texte sans le mot", captions="Une rue calme."))

    total, stats = _run("captions", ["char russe"])
    assert total == 2
    assert stats["char russe"]["articles_with_word"] == 1

    _, body_stats = _run_again_body()
    assert body_stats["char russe"]["articles_with_word"] == 0, "в основном тексте этих слов нет"


def _run_again_body():
    from app.db import session_scope
    from app.models import Analysis, Job, JobStatus
    from app.services.analysis import run_analysis, unpack
    from app.services.jobs import JobHandle

    with session_scope() as db:
        db.add(Job(id="job-2", type="analysis"))
        db.add(Analysis(start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                         keywords=["char russe"], status=JobStatus.RUNNING.value, text_scope="body"))
    with session_scope() as db:
        analysis_id = db.query(Analysis).order_by(Analysis.id.desc()).first().id
    run_analysis(JobHandle(id="job-2", type="analysis"), analysis_id,
                 date(2020, 1, 1), date(2020, 1, 1), ["char russe"], text_scope="body")
    with session_scope() as db:
        row = db.get(Analysis, analysis_id)
        return row.total_processed_articles, unpack(row.statistics_gz)["statistics"]


def test_captions_zone_ignores_articles_whose_captions_were_not_extracted(app_env):
    from app.db import session_scope

    with session_scope() as db:
        db.add(_article("u1", "Titre", "texte", captions="Un char russe.", extracted=1))
        # Собрана до появления экстрактора: «нет подписей» и «не извлекали» — разные вещи.
        db.add(_article("u2", "Titre", "texte", extracted=0))

    total, stats = _run("captions", ["char russe"])
    assert total == 1, "знаменатель — только статьи, у которых подписи реально извлекались"
    assert stats["char russe"]["articles_with_word"] == 1


def test_zones_are_scanned_separately_not_glued_together(app_env):
    from app.db import session_scope

    # Заголовок кончается на «guerre», текст начинается с «en Ukraine»: склейка дала бы ложное «guerre en Ukraine».
    with session_scope() as db:
        db.add(_article("u1", "Vers la guerre", "en Ukraine on parle"))

    _, stats = _run("title_body", ["guerre en Ukraine"])
    assert stats["guerre en Ukraine"]["articles_with_word"] == 0
