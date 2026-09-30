"""Работа с источником: sitemap Le Figaro и извлечение текста статей.

Сетевое поведение перенесено из parser.py: те же
повторы, та же трактовка кода 500 как платной статьи, тот же селектор
`.fig-paragraph`. Отличие в том, что результат здесь возвращается
вызывающему, а не подсчитывается на месте, — это и позволяет хранить
статью в базе и анализировать её потом сколько угодно раз.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ..config import settings

SITEMAP_NS = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# Находка 2026-09-28: одного User-Agent недостаточно — запрос без Accept/
# Accept-Language выглядит как написанный скриптом (у настоящего браузера
# эти заголовки идут всегда). BROWSER_HEADERS переиспользуется везде, где
# мы обращаемся к источнику напрямую, а не через сессию с уже
# выставленными заголовками.
def browser_headers() -> dict[str, str]:
    return {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }


class PremiumArticle(Exception):
    """Источник ответил 500 — статья платная, пропускаем без повторов."""


@dataclass
class FetchedArticle:
    url: str
    text: str
    title: str | None
    category: str | None
    truncated: bool = False


# Маркеры платной статьи, отданной в виде затравки.
# Замер 2026-09-22: классы `fig-premium-mark` и `fig-ranking-profile--premium`
# есть на КАЖДОЙ странице (виджеты навигации) и индикаторами не являются.
# Настоящие признаки — эти, и они всегда идут вместе с фразами ниже.
PAYWALL_SELECTORS = ".fig-premium-paywall, .fig-premium-mark-article"
PAYWALL_PHRASES = (
    "réservé aux abonnés",
    "il vous reste",
    "cet article est réservé",
)


def _is_truncated(soup: BeautifulSoup, html: str) -> bool:
    """Отдал ли источник только затравку платной статьи.

    До 2026 такие статьи отвечали кодом 500 и просто пропускались. Теперь они
    приходят с кодом 200 и двумя-тремя абзацами, то есть внешне неотличимы от
    целых. Без этой проверки корпус тихо наполняется огрызками, а слово,
    стоящее в скрытой части, считается отсутствующим.
    """
    if soup.select_one(PAYWALL_SELECTORS):
        return True
    lowered = html.lower()
    return any(phrase in lowered for phrase in PAYWALL_PHRASES)


def extract_date_from_url(url: str) -> date | None:
    """Извлекает дату из формата: .../articles/YYYY-MM-DD.xml"""
    try:
        date_str = url.split("/articles/")[-1].replace(".xml", "")
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None


def extract_category_from_url(url: str) -> str | None:
    """Извлекает категорию из URL.

    Пример: https://www.lefigaro.fr/politique/article-2024-01-01 -> "Politique"
    """
    try:
        parts = url.split("/")
        if len(parts) >= 4:
            return parts[3].replace("-", " ").title()
    except Exception:
        pass
    return None


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(browser_headers())
    return session


def list_daily_sitemaps(start: date, end: date) -> list[tuple[date, str]]:
    """Возвращает (дата, URL суточного sitemap) для дат внутри периода."""
    response = requests.get(
        settings.sitemap_index_url,
        headers=browser_headers(),
        timeout=settings.request_timeout,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)

    found: list[tuple[date, str]] = []
    for sitemap in root.findall("ns:sitemap", SITEMAP_NS):
        loc = sitemap.find("ns:loc", SITEMAP_NS)
        if loc is None or not loc.text:
            continue
        xml_url = loc.text.strip()
        day = extract_date_from_url(xml_url)
        if day and start <= day <= end:
            found.append((day, xml_url))

    found.sort(key=lambda item: item[0])
    return found


def get_articles_from_sitemap(xml_url: str, session: requests.Session | None = None) -> list[str]:
    """Список URL статей внутри суточного sitemap."""
    client = session or requests
    response = client.get(xml_url, headers=browser_headers(), timeout=settings.request_timeout)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    urls: list[str] = []
    for url_node in root.findall("ns:url", SITEMAP_NS):
        loc = url_node.find("ns:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def _extract_title(soup: BeautifulSoup) -> str | None:
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        return og_title["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    heading = soup.find("h1")
    if heading:
        return heading.get_text(" ", strip=True)
    return None


def fetch_article(url: str, session: requests.Session | None = None) -> FetchedArticle:
    """Скачивает статью и возвращает её текст.

    Бросает PremiumArticle при 500 и requests-исключения в остальных
    ошибочных случаях — повторы организует вызывающий код.
    """
    client = session or _session()
    response = client.get(
        url,
        headers=browser_headers(),
        timeout=settings.request_timeout,
    )

    if response.status_code == 500:
        # Premium article, skip it
        raise PremiumArticle(url)

    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    text = " ".join(
        p.get_text(" ", strip=True) for p in soup.select(settings.article_selector)
    )

    return FetchedArticle(
        url=url,
        text=text,
        title=_extract_title(soup),
        category=extract_category_from_url(url),
        truncated=_is_truncated(soup, response.text),
    )
