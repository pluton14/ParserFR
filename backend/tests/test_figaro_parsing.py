"""Извлечение дат, категорий и детектор обрезанных статей.

Детектор обрезки закрывает находку 2026-09-22: с 2026 Le Figaro отдаёт
платные статьи кодом 200 с затравкой, а не 500. Без этих тестов регрессия
в детекторе тихо испортит корпус — слово в скрытой части текста снова
станет "отсутствующим".
"""

import sys
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.figaro import (  # noqa: E402
    PAYWALL_PHRASES,
    _is_truncated,
    extract_category_from_url,
    extract_date_from_url,
)


def test_extract_date_from_sitemap_url():
    url = "https://sitemaps.lefigaro.fr/lefigaro.fr/articles/2026-09-21.xml"
    assert extract_date_from_url(url) == date(2026, 9, 21)


def test_extract_date_from_malformed_url_returns_none():
    assert extract_date_from_url("https://sitemaps.lefigaro.fr/not-a-date.xml") is None


def test_extract_category_from_article_url():
    url = "https://www.lefigaro.fr/international/deux-oligarques-russes-20260921"
    assert extract_category_from_url(url) == "International"


def test_truncated_detected_by_real_paywall_class():
    html = '<html><body><div class="fig-premium-paywall">abonnez-vous</div></body></html>'
    soup = BeautifulSoup(html, "html.parser")
    assert _is_truncated(soup, html) is True


def test_truncated_detected_by_phrase_without_class():
    html = "<html><body><p>Il vous reste 90% de cet article à lire.</p></body></html>"
    soup = BeautifulSoup(html, "html.parser")
    assert _is_truncated(soup, html) is True


def test_navigation_widget_alone_is_not_truncation():
    """Регрессионный тест на находку 2026-09-22: fig-premium-mark стоит на
    КАЖДОЙ странице (виджет навигации) и сам по себе не значит обрезку."""
    html = '<html><body><span class="fig-premium-mark">Premium</span><p>texte complet ici</p></body></html>'
    soup = BeautifulSoup(html, "html.parser")
    assert _is_truncated(soup, html) is False


def test_full_article_not_flagged():
    html = "<html><body><p>Un article complet sans aucune restriction de lecture.</p></body></html>"
    soup = BeautifulSoup(html, "html.parser")
    assert _is_truncated(soup, html) is False


def test_paywall_phrases_are_lowercase_for_case_insensitive_match():
    assert all(p == p.lower() for p in PAYWALL_PHRASES)
