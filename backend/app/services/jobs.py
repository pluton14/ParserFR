"""Реестр фоновых задач.

В десктопной версии прогресс ехал из рабочего потока в tkinter через
queue.Queue. Здесь та же схема, только подписчиков может быть несколько
(вкладки браузера), и они получают события по SSE. Состояние задачи
дублируется в базу, чтобы перезапуск сервера не терял историю.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..db import session_scope
from ..models import Analysis, Job, JobLog, JobStatus

logger = logging.getLogger("parserfr.jobs")

# Сколько последних строк лога держим в памяти для отдачи новым подписчикам.
LOG_TAIL = 300


@dataclass
class JobHandle:
    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = JobStatus.PENDING.value
    current: int = 0
    total: int = 0
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    result_id: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    logs: deque[dict] = field(default_factory=lambda: deque(maxlen=LOG_TAIL))
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _subscribers: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- состояние для подписчиков ---

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "current": self.current,
            "total": self.total,
            "stage": self.stage,
            "message": self.message,
            "error": self.error,
            "result_id": self.result_id,
            "params": self.params,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        # Новый подписчик сразу получает текущее состояние и хвост лога,
        # иначе вкладка, открытая в середине сбора, покажет пустой экран.
        q.put({"event": "state", "data": self.snapshot()})
        for entry in list(self.logs):
            q.put({"event": "log", "data": entry})
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _publish(self, event: str, data: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait({"event": event, "data": data})
            except queue.Full:
                # Медленный клиент не должен тормозить сбор корпуса.
                pass

    # --- обновление хода работы ---

    def set_progress(
        self,
        current: int | None = None,
        total: int | None = None,
        stage: str | None = None,
        message: str | None = None,
    ) -> None:
        if current is not None:
            self.current = current
        if total is not None:
            self.total = total
        if stage is not None:
            self.stage = stage
        if message is not None:
            self.message = message
        self._publish("progress", self.snapshot())

    def log(self, text: str, level: str = "info") -> None:
        entry = {"level": level, "text": text, "at": datetime.utcnow().isoformat()}
        self.logs.append(entry)
        self._publish("log", entry)
        with session_scope() as db:
            db.add(JobLog(job_id=self.id, level=level, text=text))
        # Дублируем в консоль сервера: при многочасовом сборе без открытой
        # вкладки браузера это единственное окно, где виден живой прогресс
        # и — самое главное для резюме после паузы — с какого места
        # продолжили работу.
        getattr(logger, level if level in ("info", "warning", "error") else "info")(
            "[job %s] %s", self.id[:8], text
        )

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()


class JobRegistry:
    """Хранит живые задачи и следит, чтобы тяжёлые не запускались параллельно."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobHandle] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> JobHandle | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[JobHandle]:
        with self._lock:
            return list(self._jobs.values())

    def active_of_type(self, job_type: str) -> JobHandle | None:
        with self._lock:
            for job in self._jobs.values():
                if job.type == job_type and job.status in (
                    JobStatus.PENDING.value,
                    JobStatus.RUNNING.value,
                ):
                    return job
        return None

    def submit(
        self,
        job_type: str,
        target: Callable[[JobHandle], Any],
        params: dict[str, Any] | None = None,
    ) -> JobHandle:
        job = JobHandle(id=str(uuid.uuid4()), type=job_type, params=params or {})
        with self._lock:
            self._jobs[job.id] = job

        with session_scope() as db:
            db.add(
                Job(
                    id=job.id,
                    type=job.type,
                    status=job.status,
                    params=job.params,
                )
            )

        thread = threading.Thread(target=self._run, args=(job, target), daemon=True)
        thread.start()
        return job

    def _run(self, job: JobHandle, target: Callable[[JobHandle], Any]) -> None:
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.utcnow()
        self._persist(job)
        job._publish("progress", job.snapshot())

        try:
            target(job)
            if job.is_cancelled():
                job.status = JobStatus.CANCELLED.value
                job.message = "Остановлено пользователем"
            else:
                job.status = JobStatus.DONE.value
        except Exception as exc:  # noqa: BLE001 — задача не должна ронять сервер
            job.status = JobStatus.FAILED.value
            job.error = f"{type(exc).__name__}: {exc}"
            job.log(f"Задача прервана ошибкой: {job.error}", level="error")
        finally:
            job.finished_at = datetime.utcnow()
            self._persist(job)
            job._publish("done", job.snapshot())
            # Сигнал подписчикам закрыть поток SSE.
            job._publish("close", {})

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.status not in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
            return False
        job.cancel_event.set()
        job.log("Получен запрос на остановку, завершаем текущую пачку…")
        return True

    def _persist(self, job: JobHandle) -> None:
        with session_scope() as db:
            row = db.get(Job, job.id)
            if row is None:
                return
            row.status = job.status
            row.current = job.current
            row.total = job.total
            row.stage = job.stage
            row.message = job.message
            row.error = job.error
            row.result_id = job.result_id
            row.started_at = job.started_at
            row.finished_at = job.finished_at

    def sync(self, job: JobHandle) -> None:
        """Записать текущее состояние задачи в базу (вызывается по ходу работы)."""
        self._persist(job)


registry = JobRegistry()


def recover_stale_jobs() -> None:
    """Задачи, оставшиеся 'running' после падения процесса, помечаются упавшими.

    Иначе интерфейс будет вечно показывать сбор, которого уже нет.
    """
    with session_scope() as db:
        stale = (
            db.query(Job)
            .filter(Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]))
            .all()
        )
        for row in stale:
            row.status = JobStatus.FAILED.value
            row.error = "Сервер был перезапущен во время выполнения задачи"
            row.finished_at = datetime.utcnow()

        # Записи анализов в истории иначе остаются «running» навсегда: их
        # статус меняет только сам анализ, а он оборвался вместе с сервером.
        for analysis in db.query(Analysis).filter(Analysis.status == JobStatus.RUNNING.value).all():
            analysis.status = JobStatus.FAILED.value
            analysis.error = "Сервер был перезапущен во время выполнения анализа"
            analysis.finished_at = datetime.utcnow()
