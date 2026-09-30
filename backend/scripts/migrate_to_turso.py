"""Разовый перенос локальной базы в Turso по сети — обходной путь, раз
`turso db import` недоступен на Windows (CLI не публикует сборку под неё),
а Docker Desktop не поднялся (не хватило ресурсов на его WSL2-VM).

Переносит только то, что реально нужно для демо-снимка: словари, статьи,
покрытие по дням, готовые анализы. Служебные таблицы job/job_logs —
рабочий журнал самого сбора, для чтения корпуса не нужны, не переносим,
чтобы не тащить тысячи строк отладочного шума сегодняшней сессии.

Находка: один клиент, пачки по 300 строк — это ~5-7 строк/с (каждая
пачка = отдельный HTTP-запрос в eu-west, задержка сети доминирует над
размером данных). На 2.76М строк это заняло бы больше 100 часов.
Лечится не размером пачки, а параллельными запросами — несколько
потоков, каждый со своим клиентом, разбирают общую очередь пачек.

Запуск: .venv/Scripts/python.exe scripts/migrate_to_turso.py
Нужны переменные окружения TURSO_DATABASE_URL и TURSO_AUTH_TOKEN
(например, в backend/.env.turso — скрипт читает его сам).
"""

from __future__ import annotations

import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import libsql_client  # noqa: E402

LOCAL_DB = BACKEND_DIR / "data" / "parserfr.db"
ENV_FILE = BACKEND_DIR / ".env.turso"

TABLES = ["dictionaries", "articles", "harvested_days", "analyses"]
BATCH_SIZE = 500
WORKERS = 10


def _load_env_file() -> tuple[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    import os

    url = os.environ.get("TURSO_DATABASE_URL") or values.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN") or values.get("TURSO_AUTH_TOKEN", "")
    if not url or not token:
        raise SystemExit(
            f"Заполните TURSO_DATABASE_URL и TURSO_AUTH_TOKEN в {ENV_FILE} и запустите снова."
        )
    url = url.removeprefix("https://").removeprefix("libsql://")
    # HTTP, не wss: libsql://(->WebSocket/Hrana) падал с WSServerHandshakeError
    # 400 на этом инстансе Turso. Обычный HTTPS подключается без проблем.
    return f"https://{url}", token


def _table_ddl(local: sqlite3.Connection, table: str) -> list[str]:
    rows = local.execute(
        "SELECT sql FROM sqlite_master WHERE type IN ('table','index') "
        "AND tbl_name = ? AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'",
        (table,),
    ).fetchall()
    return [row[0] for row in rows]


def _worker(url: str, token: str, insert_sql: str, work_q: "queue.Queue", stats: dict, lock: threading.Lock) -> None:
    client = libsql_client.create_client_sync(url, auth_token=token)
    try:
        while True:
            batch = work_q.get()
            if batch is None:
                work_q.task_done()
                break
            statements = [libsql_client.Statement(insert_sql, list(row)) for row in batch]
            try:
                client.batch(statements)
            except Exception as exc:  # noqa: BLE001
                print(f"  ОШИБКА в пачке: {exc}")
                raise
            with lock:
                stats["done"] += len(batch)
            work_q.task_done()
    finally:
        client.close()


def main() -> None:
    url, token = _load_env_file()
    print(f"Подключаюсь к Turso: {url}")
    setup_client = libsql_client.create_client_sync(url, auth_token=token)

    local = sqlite3.connect(str(LOCAL_DB))
    local.row_factory = sqlite3.Row

    print("Переношу схему…")
    for table in TABLES:
        for ddl in _table_ddl(local, table):
            try:
                setup_client.execute(ddl)
            except Exception as exc:  # noqa: BLE001
                # Повторный запуск после обрыва — таблица/индекс, скорее
                # всего, уже созданы в прошлый раз. Ошибка от HTTP-клиента
                # libsql на "already exists" приходит как голый KeyError
                # без текста, поэтому не фильтруем по сообщению — просто
                # предупреждаем и идём дальше. Если проблема настоящая,
                # она громко всплывёт на попытке вставки данных ниже.
                print(f"  (пропускаю DDL, похоже уже применён: {type(exc).__name__})")
    setup_client.close()
    print("Схема готова.")

    for table in TABLES:
        total = local.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if total == 0:
            print(f"{table}: пусто, пропускаю")
            continue

        # Резюме: если в целевой таблице уже что-то есть (прошлый обрыв
        # соединения на середине), не дублируем — считаем по id больше
        # максимального уже перенесённого.
        check_client = libsql_client.create_client_sync(url, auth_token=token)
        try:
            existing = check_client.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").rows[0][0]
        except Exception:
            existing = 0
        check_client.close()

        cols = [row[1] for row in local.execute(f"PRAGMA table_info({table})")]
        placeholders = ",".join("?" for _ in cols)
        insert_sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"

        remaining = total if not existing else local.execute(
            f"SELECT COUNT(*) FROM {table} WHERE id > ?", (existing,)
        ).fetchone()[0]
        print(f"{table}: всего {total}, уже перенесено ~{existing and existing or 0}, осталось {remaining}")
        if remaining == 0:
            continue

        work_q: "queue.Queue" = queue.Queue(maxsize=WORKERS * 3)
        stats = {"done": 0}
        lock = threading.Lock()
        threads = [
            threading.Thread(target=_worker, args=(url, token, insert_sql, work_q, stats, lock), daemon=True)
            for _ in range(WORKERS)
        ]
        for t in threads:
            t.start()

        t0 = time.time()
        where = f"WHERE id > {existing}" if existing else ""
        cursor = local.execute(f"SELECT {','.join(cols)} FROM {table} {where}")
        last_report = 0
        while True:
            rows = cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break
            work_q.put(rows)
            if stats["done"] - last_report >= BATCH_SIZE * 20:
                last_report = stats["done"]
                rate = stats["done"] / max(time.time() - t0, 0.001)
                eta_min = (remaining - stats["done"]) / max(rate, 0.001) / 60
                print(f"  {stats['done']}/{remaining} ({rate:.0f} строк/с, ETA {eta_min:.0f} мин)")

        for _ in threads:
            work_q.put(None)
        work_q.join()
        for t in threads:
            t.join()

        rate = stats["done"] / max(time.time() - t0, 0.001)
        print(f"{table}: готово, {stats['done']} строк за {time.time()-t0:.0f}с ({rate:.0f} строк/с)")

    local.close()
    print("Готово.")


if __name__ == "__main__":
    main()
