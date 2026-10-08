import { useState } from "react";
import { api, ApiError } from "../api/client";
import { useJobProgress } from "../hooks/useJobProgress";
import { jobStatusLabel } from "../lib/labels";

export default function JobProgress({ jobId }: { jobId: string }) {
  const { job, logs, isActive, cancel } = useJobProgress(jobId);
  const [skipping, setSkipping] = useState(false);
  const [skipError, setSkipError] = useState<string | null>(null);

  if (!job) return <p className="muted">Подключаемся к задаче…</p>;

  const percent = job.total > 0 ? Math.min(100, Math.round((job.current / job.total) * 100)) : 0;

  // Сбор — единственный тип задачи с паузами при блокировке источника; для
  // анализа кнопка была бы бессмысленной.
  const canSkipPause = isActive && job.type === "harvest";

  const skipPause = async () => {
    setSkipping(true);
    try {
      await api.skipHarvestPause();
      setSkipError(null);
    } catch (e) {
      setSkipError(e instanceof ApiError ? e.message : "Не удалось пропустить паузу");
    } finally {
      setSkipping(false);
    }
  };

  return (
    <div style={{ marginTop: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className={`badge badge-${job.status}`}>{jobStatusLabel(job.status)}</span>
        <span className="muted">{job.message || job.stage || ""}</span>
        <div className="row" style={{ gap: 8 }}>
          {canSkipPause && (
            <button className="secondary" onClick={skipPause} disabled={skipping}>
              {skipping ? "Пробуем…" : "Повторить сейчас"}
            </button>
          )}
          {isActive && (
            <button className="secondary" onClick={cancel}>
              Остановить
            </button>
          )}
        </div>
      </div>
      {skipError && <p className="error-text">{skipError}</p>}
      {job.total > 0 && (
        <div className="progress-bar" style={{ marginBottom: 6 }}>
          <div style={{ width: `${percent}%` }} />
        </div>
      )}
      {job.total > 0 && (
        <p className="muted">
          {job.current} / {job.total} ({percent}%)
        </p>
      )}
      {job.error && <p className="error-text">{job.error}</p>}
      {logs.length > 0 && (
        <div className="log-panel">
          {logs.map((l, i) => (
            <div key={i} className={l.level === "error" ? "log-error" : l.level === "warning" ? "log-warning" : ""}>
              {l.text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
