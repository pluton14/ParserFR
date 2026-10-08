"""Схемы запросов и ответов."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .core.zones import format_text_scope, parse_text_scope

def clean_keywords(value: list[str]) -> list[str]:
    """Убирает пустые строки и повторы, сохраняя порядок и исходный регистр."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in value:
        keyword = item.strip()
        if keyword and keyword.lower() not in seen:
            seen.add(keyword.lower())
            cleaned.append(keyword)
    return cleaned



# --- Корпус ---

class BackfillRequest(BaseModel):
    # Без дат — добираем всё, где подписи ещё не извлекались.
    start_date: date | None = None
    end_date: date | None = None


class HarvestRequest(BaseModel):
    start_date: date
    end_date: date
    # Перекачать даже то, что уже сохранено. По умолчанию выключено:
    # именно пропуск известных статей делает повторный сбор дешёвым.
    refresh: bool = False

    @field_validator("end_date")
    @classmethod
    def check_period(cls, value: date, info):
        start = info.data.get("start_date")
        if start and value < start:
            raise ValueError("Конечная дата раньше начальной")
        return value


class DayCoverage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    total_urls: int
    ok_count: int
    truncated_count: int
    premium_count: int
    empty_count: int
    failed_count: int
    status: str
    harvested_at: datetime | None


class CorpusSummary(BaseModel):
    days_covered: int
    articles_total: int
    articles_with_text: int      # целые + обрезанные, то есть пригодные к анализу
    articles_truncated: int      # из них платные, отданные затравкой
    articles_premium: int
    articles_failed: int
    first_day: date | None
    last_day: date | None
    database_bytes: int
    last_harvest_at: datetime | None


# --- Словари ---

class DictionaryBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return clean_keywords(value)


class DictionaryCreate(DictionaryBase):
    pass


class DictionaryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    keywords: list[str] | None = None


class DictionaryOut(DictionaryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# --- Анализ ---

class AnalysisRequest(BaseModel):
    start_date: date
    end_date: date
    name: str | None = None
    dictionary_id: int | None = None
    # Слова можно передать напрямую — для разовой проверки без сохранения словаря.
    keywords: list[str] | None = None
    # None/пусто — все категории. Иначе — статья учитывается, только если
    # её category входит в список (множественный выбор).
    categories: list[str] | None = None
    # Зоны статьи: "body" (по умолчанию, как раньше) | "title" | "title_body"
    # либо список через запятую из title, captions, body (см. core/zones.py).
    text_scope: str = "body"

    @field_validator("end_date")
    @classmethod
    def check_period(cls, value: date, info):
        start = info.data.get("start_date")
        if start and value < start:
            raise ValueError("Конечная дата раньше начальной")
        return value

    @field_validator("text_scope")
    @classmethod
    def check_text_scope(cls, value: str) -> str:
        return format_text_scope(parse_text_scope(value))


class AnalysisSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    start_date: date
    end_date: date
    keywords: list[str]
    total_processed_articles: int
    status: str
    error: str | None
    dictionary_id: int | None
    categories: list[str] | None
    text_scope: str
    created_at: datetime
    finished_at: datetime | None


class CategoryStat(BaseModel):
    category: str
    articles_with_word: int  # статей категории, где слово встретилось
    category_articles: int  # всего просмотренных статей этой категории
    percentage: float  # доля статей категории со словом
    occurrences: int


class KeywordStats(BaseModel):
    keyword: str
    articles_with_word: int
    total_processed_articles: int
    percentage: float
    total_occurrences: int
    left_context_words: list[ContextWord]
    right_context_words: list[ContextWord]
    # Топ категорий по числу статей со словом. Пусто у анализов, посчитанных до
    # появления этой разбивки (их нужно пересчитать).
    categories: list[CategoryStat] = Field(default_factory=list)


class ContextWord(BaseModel):
    word: str
    count: int
    percentage: float


class TimeseriesPoint(BaseModel):
    date: str
    occurrences: int
    articles: int


class AnalysisDetail(AnalysisSummary):
    articles_in_corpus: int = 0
    # Сумма слов проанализированного текста; None у анализов, посчитанных раньше.
    total_words: int | None = None
    stats: list[KeywordStats] = Field(default_factory=list)
    timeseries: dict[str, list[TimeseriesPoint]] = Field(default_factory=dict)


class ExampleOut(BaseModel):
    url: str
    context_before: str
    keyword: str
    context_after: str
    published_date: str | None = None
    title: str | None = None


class ExamplesPage(BaseModel):
    keyword: str
    total: int
    offset: int
    limit: int
    items: list[ExampleOut]


# --- Задачи ---

class JobOut(BaseModel):
    id: str
    type: str
    status: str
    current: int
    total: int
    stage: str | None
    message: str | None
    error: str | None
    result_id: int | None
    params: dict
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


KeywordStats.model_rebuild()
AnalysisDetail.model_rebuild()
