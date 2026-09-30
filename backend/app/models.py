"""Модель данных.

Ключевая идея переноса: скачивание статей (дорогое, сетевое) отделено от
подсчёта статистики (дешёвый, локальный). Поэтому статья хранится один раз
в `articles`, а каждый прогон словаря по корпусу — это запись в `analyses`,
не требующая повторных запросов к источнику.
"""

from __future__ import annotations

import enum
import zlib
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def compress(value: str | None) -> bytes | None:
    if value is None:
        return None
    return zlib.compress(value.encode("utf-8"), level=6)


def decompress(value: bytes | None) -> str:
    if not value:
        return ""
    return zlib.decompress(value).decode("utf-8")


class ArticleStatus(str, enum.Enum):
    PENDING = "pending"    # URL известен из суточной карты, текст ещё не скачан
    OK = "ok"              # получен полный текст
    TRUNCATED = "truncated"  # платная статья: отдана только затравка (см. figaro.is_truncated)
    PREMIUM = "premium"    # платная статья, источник ответил 500 (поведение до 2026)
    EMPTY = "empty"        # страница открылась, но текста статьи нет (видео, опрос, дайджест)
    FAILED = "failed"      # исчерпаны попытки


class DayStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"    # часть статей не далась даже после повторов
    FAILED = "failed"


class JobType(str, enum.Enum):
    HARVEST = "harvest"
    ANALYSIS = "analysis"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Article(Base):
    """Одна статья Le Figaro. Скачивается ровно один раз."""

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    published_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ArticleStatus.OK.value)

    # Полный текст статьи — сжатый, чтобы корпус за год умещался
    # в сотни мегабайт вместо гигабайтов.
    text_gz: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Токены, уже разобранные tokenize_french_text. Хранятся отдельно,
    # чтобы анализ не перетокенизировал весь корпус при каждом прогоне.
    tokens_gz: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_count: Mapped[int] = mapped_column(Integer, default=0)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_articles_date_status", "published_date", "status"),
    )

    @property
    def text(self) -> str:
        return decompress(self.text_gz)

    @property
    def tokens(self) -> list[str]:
        raw = decompress(self.tokens_gz)
        return raw.split("\n") if raw else []

    def set_content(self, text: str, tokens: list[str]) -> None:
        self.text_gz = compress(text)
        self.tokens_gz = compress("\n".join(tokens))
        self.token_count = len(tokens)


class HarvestedDay(Base):
    """Покрытие корпуса по дням: какие даты уже собраны и насколько полно."""

    __tablename__ = "harvested_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    sitemap_url: Mapped[str | None] = mapped_column(String(1024))
    total_urls: Mapped[int] = mapped_column(Integer, default=0)
    ok_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated_count: Mapped[int] = mapped_column(Integer, default=0)
    premium_count: Mapped[int] = mapped_column(Integer, default=0)
    empty_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=DayStatus.PENDING.value)
    harvested_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Dictionary(Base):
    """Список ключевых слов — веб-замена файлу dict.txt."""

    __tablename__ = "dictionaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    analyses: Mapped[list[Analysis]] = relationship(back_populates="dictionary")


class Analysis(Base):
    """Один прогон словаря по собранному корпусу."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    total_processed_articles: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text)

    # Фильтр по категориям (null/пусто — все категории) и по зоне текста
    # статьи: "body" — только основной текст (поведение по умолчанию, как
    # раньше), "title" — только заголовок, "title_body" — оба вместе.
    # Заголовок токенизируется на лету при сканировании (см. run_analysis) —
    # отдельного поля токенов заголовка в Article нет и не нужно, это не
    # требует пересбора уже скачанных статей.
    categories: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    text_scope: Mapped[str] = mapped_column(String(16), default="body")

    dictionary_id: Mapped[int | None] = mapped_column(
        ForeignKey("dictionaries.id", ondelete="SET NULL")
    )
    dictionary: Mapped[Dictionary | None] = relationship(back_populates="analyses")

    # Статистика в том же формате, что писал parser.save_statistics_to_file,
    # чтобы выгрузка оставалась совместимой со старыми JSON-файлами.
    statistics_gz: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Примеры и динамика по дням вынесены отдельно: они объёмные и нужны
    # не всегда, а список анализов должен открываться быстро.
    examples_gz: Mapped[bytes | None] = mapped_column(LargeBinary)
    timeseries_gz: Mapped[bytes | None] = mapped_column(LargeBinary)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Job(Base):
    """Фоновая задача (сбор или анализ) — переживает перезапуск сервера."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING.value)
    params: Mapped[dict] = mapped_column(JSON, default=dict)

    current: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    result_id: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class JobLog(Base):
    """Строки лога задачи — веб-аналог текстового вывода в консоль."""

    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(16), default="info")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
