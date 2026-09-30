"""Подсчёт статистики: соседи, примеры, словосочетания.

Покрывает то, что сломалось бы тише всего — неверный подсчёт соседей или
пропуск словосочетания не кидает исключение, он просто даёт неверные цифры,
которые лингвист унесёт в научную работу не заметив.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.statistics import WordStatistics, build_keyword_plans, scan_article  # noqa: E402
from app.core.tokenizer import tokenize_french_text  # noqa: E402


def test_single_word_neighbors():
    words = tokenize_french_text("le président russe a déclaré hier")
    plans = build_keyword_plans(["russe"])
    stats = {p.original: WordStatistics() for p in plans}
    counts = scan_article(words, plans, stats, url="u1")
    assert counts["russe"] == 1
    assert stats["russe"].left_context_words["président"] == 1
    assert stats["russe"].right_context_words["a"] == 1


def test_phrase_neighbors_use_words_before_and_after_whole_phrase():
    words = tokenize_french_text("hier les troupes russes ont avancé")
    plans = build_keyword_plans(["troupes russes"])
    stats = {p.original: WordStatistics() for p in plans}
    counts = scan_article(words, plans, stats, url="u1")
    assert counts["troupes russes"] == 1
    # Сосед слева — слово перед началом словосочетания, а не перед вторым словом.
    assert stats["troupes russes"].left_context_words["les"] == 1
    # Сосед справа — слово после конца словосочетания.
    assert stats["troupes russes"].right_context_words["ont"] == 1


def test_multiple_occurrences_in_one_article_all_counted():
    words = tokenize_french_text("guerre après guerre, encore la guerre")
    plans = build_keyword_plans(["guerre"])
    stats = {p.original: WordStatistics() for p in plans}
    counts = scan_article(words, plans, stats, url="u1")
    assert counts["guerre"] == 3
    assert stats["guerre"].total_occurrences == 3


def test_no_match_gives_zero_and_no_examples():
    words = tokenize_french_text("le chat noir dort")
    plans = build_keyword_plans(["russie"])
    stats = {p.original: WordStatistics() for p in plans}
    counts = scan_article(words, plans, stats, url="u1")
    assert counts["russie"] == 0
    assert stats["russie"].examples == []


def test_overlapping_keywords_each_counted_independently():
    """'armée russe' и 'russe' пересекаются — оба должны найтись честно."""
    words = tokenize_french_text("l'armée russe avance")
    plans = build_keyword_plans(["armée russe", "russe"])
    stats = {p.original: WordStatistics() for p in plans}
    counts = scan_article(words, plans, stats, url="u1")
    assert counts["armée russe"] == 1
    assert counts["russe"] == 1


def test_example_limit_stops_collecting_but_counters_keep_going():
    """Ограничение на число примеров не должно занижать сами счётчики."""
    words = tokenize_french_text(" ".join(["guerre"] * 10))
    plans = build_keyword_plans(["guerre"])
    stats = {p.original: WordStatistics() for p in plans}
    scan_article(words, plans, stats, url="u1", example_limit=3)
    assert stats["guerre"].total_occurrences == 10
    assert len(stats["guerre"].examples) == 3


def test_empty_keyword_list_ignored():
    assert build_keyword_plans(["", "  ", "russie"]) == build_keyword_plans(["russie"])
