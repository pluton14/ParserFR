"""Управление скоростью сбора из интерфейса — без перезапуска сервера.

Два параметра:
- rate — потолок запросов в секунду ко всему источнику (листинг и статьи).
  0 — без ограничения: темп задаётся только числом потоков, как в технике
  сбора 30 сентября. Действует сразу, даже на уже идущую задачу.
- workers — число потоков скачивания. Пул создаётся при старте задачи,
  поэтому новое значение действует со следующего запуска.

None в переопределении = значение из config.settings.
"""

from __future__ import annotations

import threading

from ..config import settings

MAX_WORKERS = 32


class Pacing:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rate: float | None = None
        self._workers: int | None = None

    @property
    def rate(self) -> float:
        return settings.harvest_rate if self._rate is None else self._rate

    @property
    def workers(self) -> int:
        return settings.harvest_workers if self._workers is None else self._workers

    def update(self, rate: float, workers: int) -> None:
        with self._lock:
            self._rate = rate
            self._workers = workers

    def reset(self) -> None:
        with self._lock:
            self._rate = None
            self._workers = None

    def snapshot(self) -> dict:
        return {"rate": self.rate, "workers": self.workers}


pacing = Pacing()
