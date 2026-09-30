import { useJobProgress } from "../hooks/useJobProgress";
import { jobStatusLabel } from "../lib/labels";

export default function JobProgress({ jobId }: { jobId: string }) {
  const { job, logs, isActive, cancel } = useJobProgress(jobId);

  if (!job) return <p className="muted">Подключаемся к задаче…</p>;

  const percent = job.total > 0 ? Math.min(100, Math.round((job.current / job.total) * 100)) : 0;

  return (
    <div style={{ marginTop: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className={`badge badge-${job.status}`}>{jobStatusLabel(job.status)}</span>
        <span className="muted">{job.message || job.stage || ""}</span>
        {isActive && (
          <button className="secondary" onClick={cancel}>
            Остановить
          </button>
        )}
      </div>
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
