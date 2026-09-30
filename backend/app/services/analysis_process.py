"""Запуск анализа в отдельном процессе.

Анализ — тяжёлая работа и на CPU (распаковка токенов, подсчёт), и на сетевом
драйвере базы. В потоке внутри веб-сервера он делит с ним один интерпретатор
(GIL), поэтому пока идёт анализ, остальные страницы отвечают с задержкой.
Отдельный процесс с пониженным приоритетом эту связь разрывает: веб-сервер
обслуживает запросы как обычно, а анализ добирает то, что осталось от CPU.

Прогресс и лог едут из дочернего процесса в родительский через очередь —
родитель пересылает их в обычный JobHandle, так что остальной код не меняется.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
from typing import Any

from .jobs import JobHandle


class _ProxyJob:
    """Подменяет JobHandle в дочернем процессе: всё шлёт родителю."""

    def __init__(self, messages, cancel_event) -> None:
        self._messages = messages
        self._cancel_event = cancel_event
        self.result_id: int | None = None

    def log(self, text: str, level: str = "info") -> None:
        self._messages.put(("log", text, level))

    def set_progress(self, current=None, total=None, stage=None, message=None) -> None:
        self._messages.put(
            ("progress", {"current": current, "total": total, "stage": stage, "message": message})
        )

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()


def _worker(messages, cancel_event, args: dict[str, Any]) -> None:
    if hasattr(os, "nice"):
        try:
            os.nice(10)  # веб-серверу — приоритет, анализу — остатки CPU
        except OSError:
            pass

    from .analysis import run_analysis

    proxy = _ProxyJob(messages, cancel_event)
    try:
        run_analysis(proxy, **args)
        messages.put(("done", proxy.result_id))
    except BaseException as exc:  # noqa: BLE001 — любую причину надо донести до родителя
        messages.put(("error", f"{type(exc).__name__}: {exc}"))


def run_analysis_in_process(handle: JobHandle, **args: Any) -> None:
    """Запускает run_analysis(**args) в дочернем процессе и ретранслирует ход работы."""
    # spawn, а не fork: форк процесса с живыми потоками и открытыми соединениями
    # к базе оставляет дочернему процессу сломанные блокировки и сокеты.
    ctx = multiprocessing.get_context("spawn")
    messages = ctx.Queue()
    cancel_event = ctx.Event()
    process = ctx.Process(target=_worker, args=(messages, cancel_event, args), daemon=True)
    process.start()

    dead_seen = False
    try:
        while True:
            if handle.is_cancelled():
                cancel_event.set()
            try:
                message = messages.get(timeout=1)
            except queue.Empty:
                if process.is_alive():
                    continue
                if not dead_seen:
                    # Процесс мог положить последнее сообщение прямо перед выходом.
                    dead_seen = True
                    continue
                raise RuntimeError(
                    "Процесс анализа неожиданно завершился (вероятно, не хватило памяти)"
                ) from None

            kind = message[0]
            if kind == "log":
                handle.log(message[1], message[2])
            elif kind == "progress":
                handle.set_progress(**message[1])
            elif kind == "done":
                handle.result_id = message[1]
                return
            elif kind == "error":
                raise RuntimeError(message[1])
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
