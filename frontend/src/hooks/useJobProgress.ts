import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Job } from "../api/types";

export interface JobLogEntry {
  level: string;
  text: string;
  at: string;
}

/**
 * Подписка на прогресс фоновой задачи (сбор корпуса / анализ) через SSE.
 * Возвращает актуальный снимок задачи и хвост лога — то, что в десктопной
 * версии показывал прогресс-бар и консольный вывод.
 */
export function useJobProgress(jobId: string | null) {
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<JobLogEntry[]>([]);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    setJob(null);
    setLogs([]);
    if (!jobId) return;

    const source = new EventSource(api.jobStreamUrl(jobId));
    sourceRef.current = source;

    source.addEventListener("state", (e) => setJob(JSON.parse((e as MessageEvent).data)));
    source.addEventListener("progress", (e) => setJob(JSON.parse((e as MessageEvent).data)));
    source.addEventListener("done", (e) => setJob(JSON.parse((e as MessageEvent).data)));
    source.addEventListener("log", (e) =>
      setLogs((prev) => [...prev.slice(-199), JSON.parse((e as MessageEvent).data)]),
    );
    source.addEventListener("close", () => source.close());

    // SSE не всегда доступен за некоторыми прокси/расширениями браузера —
    // при ошибке соединения переходим на поллинг раз в 2 секунды.
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    source.onerror = () => {
      source.close();
      if (pollTimer) return;
      pollTimer = setInterval(async () => {
        try {
          const snapshot = await api.getJob(jobId);
          setJob(snapshot);
          if (snapshot.status === "done" || snapshot.status === "failed" || snapshot.status === "cancelled") {
            if (pollTimer) clearInterval(pollTimer);
          }
        } catch {
          // сеть могла временно моргнуть — пробуем на следующем тике
        }
      }, 2000);
    };

    return () => {
      source.close();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [jobId]);

  const cancel = useCallback(() => {
    if (jobId) api.cancelJob(jobId).catch(() => {});
  }, [jobId]);

  const isActive = job ? job.status === "pending" || job.status === "running" : false;

  return { job, logs, isActive, cancel };
}
