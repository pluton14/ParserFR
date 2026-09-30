"""SSE-поток прогресса не должен блокировать цикл событий сервера.

Раньше stream_job ждал события через queue.Queue.get(timeout=15) прямо
в async-генераторе: пока очередь пуста (прогресс приходит раз в несколько
секунд), замирал весь сервер — не отвечали ни словари, ни health.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_idle_stream_does_not_block_event_loop(app_env):
    from app.routers.jobs import stream_job
    from app.services.jobs import JobHandle, registry

    job = JobHandle(id="job-stream", type="analysis")
    registry._jobs[job.id] = job

    async def scenario() -> int:
        response = await stream_job(job.id, _FakeRequest())
        body = response.body_iterator

        await body.__anext__()  # начальное состояние приходит сразу
        waiting = asyncio.ensure_future(body.__anext__())  # дальше очередь пуста

        ticks = 0
        for _ in range(12):
            await asyncio.sleep(0.05)
            ticks += 1

        waiting.cancel()
        try:
            await waiting
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        return ticks

    # Если бы поток блокировал цикл, счётчик тиков за 0.6 с остался бы нулевым
    # (цикл стоял бы до таймаута очереди), а тест упёрся бы в лимит времени.
    ticks = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert ticks == 12
