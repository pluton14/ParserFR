"""Подключение к SQLite — локально файлом, в проде опционально через Turso.

Отдельный сервер БД по умолчанию не поднимается: база — это один файл в
data_dir. Для однопользовательской нагрузки (пачечная запись при сборе,
чтение при анализе) этого достаточно, а стоит она ноль.

Находка 2026-09-29: демо-деплой (Replit) не может унести с собой локальный
файл в 3+ ГБ между перезапусками — вместо этого, если заданы
PARSERFR_TURSO_DATABASE_URL/PARSERFR_TURSO_AUTH_TOKEN (или их
Turso-псевдонимы без префикса), подключаемся к managed libSQL на Turso.
Это тот же движок SQLite, просто удалённый — модели и запросы не меняются
ни на строчку.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from .config import settings

logger = logging.getLogger(__name__)

_USING_TURSO = bool(settings.turso_database_url and settings.turso_auth_token)

if _USING_TURSO:
    # Формат из документации tursodatabase/libsql-sqlalchemy: хост без
    # схемы (turso даёт его как "libsql://xxx.turso.io" — при копировании
    # схему на всякий случай срезаем, чтобы не задваивалась).
    _host = settings.turso_database_url.removeprefix("libsql://").removeprefix("https://")

    # У libsql_experimental нет DB-API-атрибута Binary (у sqlite3 он есть), а
    # SQLAlchemy берёт его для любого LargeBinary-параметра — без этого падает
    # запись результатов анализа (сжатые блобы). Блоб драйвер принимает как bytes.
    import libsql_experimental

    if not hasattr(libsql_experimental, "Binary"):
        libsql_experimental.Binary = bytes

    engine = create_engine(
        f"sqlite+libsql://{_host}?secure=true",
        connect_args={"auth_token": settings.turso_auth_token},
        # Без явного пула SQLAlchemy берёт для удалённого URL (без пути к файлу)
        # SingletonThreadPool на 5 соединений: при большем числе потоков он
        # закрывает соединения, которые соседние запросы ещё используют, и
        # rollback на закрытом соединении роняет драйвер Rust-паникой
        # (Option::unwrap on None, lib.rs:260).
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        # Turso сам закрывает простаивающие потоки — не держим соединения долго.
        pool_recycle=240,
        pool_pre_ping=True,
        future=True,
    )
else:
    engine = create_engine(
        settings.resolved_database_url,
        # Сбор корпуса идёт в фоновом потоке, а SQLite по умолчанию
        # запрещает использовать соединение вне создавшего его потока.
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
        future=True,
    )


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record) -> None:
    # WAL/synchronous — только для локального файла. Turso сам управляет
    # репликацией и журналированием на своей стороне; отправка этих PRAGMA
    # туда — лишний риск без всякой пользы.
    if _USING_TURSO:
        return
    cursor = dbapi_connection.cursor()
    # WAL позволяет читать статистику, пока фоновый сбор пишет статьи.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """Зависимость FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Сессия для фоновых задач, вне цикла запросов."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        # На удалённой базе (Turso) транзакция может быть уже закрыта сервером,
        # и сам rollback падает («no transaction is active»). Это вторичная
        # ошибка — она не должна затирать настоящую причину сбоя.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.warning("rollback не удался (транзакция уже закрыта сервером)", exc_info=True)
        raise
    finally:
        db.close()


# Находка 2026-10-08: на Turso каждая операция идёт по сети, поэтому транзакция
# на сотни вставок живёт минуты, и сервер её закрывает — commit падает. На
# удалённой базе пишем маленькими транзакциями и повторяем при временном сбое;
# локально ничего не меняется (всё одной пачкой, без повторов).
REMOTE_WRITE_CHUNK = 10


def write_chunks(items: list) -> Iterator[list]:
    if not _USING_TURSO or len(items) <= REMOTE_WRITE_CHUNK:
        yield items
        return
    for i in range(0, len(items), REMOTE_WRITE_CHUNK):
        yield items[i:i + REMOTE_WRITE_CHUNK]


def retry_write(fn, attempts: int = 3):
    """Выполняет запись; на удалённой базе повторяет до attempts раз при сбое."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception:
            if not _USING_TURSO or attempt == attempts:
                raise
            logger.warning("Запись в удалённую базу не удалась (попытка %d/%d), повторяем", attempt, attempts, exc_info=True)
            time.sleep(1.0 * attempt)


def init_db() -> None:
    from . import models  # noqa: F401  — регистрирует таблицы в метаданных

    models.Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# create_all не добавляет колонки в уже существующие таблицы. Здесь — только
# аддитивные правки схемы (ADD COLUMN в SQLite выполняется мгновенно и не
# перезаписывает файл, даже на многогигабайтной базе).
_ADDED_COLUMNS = {
    "articles": [
        ("captions_gz", "BLOB"),
        ("captions_extracted", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def _add_missing_columns() -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
