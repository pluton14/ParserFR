import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { CorpusSummary, DayCoverage, ScheduleInfo } from "../api/types";
import { useJobProgress } from "../hooks/useJobProgress";
import JobProgress from "../components/JobProgress";
import { dayStatusLabel } from "../lib/labels";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} КБ`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
  return `${(bytes / 1024 ** 3).toFixed(2)} ГБ`;
}

const COVERAGE_PAGE_SIZE = 30;

export default function CorpusPage() {
  const [summary, setSummary] = useState<CorpusSummary | null>(null);
  const [coverage, setCoverage] = useState<DayCoverage[]>([]);
  const [schedule, setSchedule] = useState<ScheduleInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  // Отдельные даты для фильтра таблицы покрытия — не путать с датами формы
  // запуска сбора выше: это разные поля с разным назначением.
  const [coverageStart, setCoverageStart] = useState("");
  const [coverageEnd, setCoverageEnd] = useState("");
  const [coveragePage, setCoveragePage] = useState(0);
  const { job } = useJobProgress(jobId);

  const load = async () => {
    try {
      const [s, sch] = await Promise.all([api.corpusSummary(), api.corpusSchedule()]);
      setSummary(s);
      setSchedule(sch);
      // По умолчанию — весь доступный диапазон, только один раз (не
      // переписываем, если пользователь уже сам сузил фильтр).
      if (s.first_day && s.last_day) {
        setCoverageStart((prev) => prev || s.first_day!);
        setCoverageEnd((prev) => prev || s.last_day!);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить состояние корпуса");
    }
  };

  const loadCoverage = async (start: string, end: string) => {
    try {
      const c = await api.corpusCoverage(start || undefined, end || undefined);
      setCoverage(c);
      setCoveragePage(0);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить покрытие по дням");
    }
  };

  // Перезагружаем покрытие при смене диапазона фильтра (в т.ч. когда он
  // выставился впервые значениями по умолчанию из load()).
  useEffect(() => {
    if (coverageStart && coverageEnd) loadCoverage(coverageStart, coverageEnd);
  }, [coverageStart, coverageEnd]);

  useEffect(() => {
    load();
    // Сбор мог быть запущен где угодно — с другой вкладки, по расписанию,
    // напрямую через API — до того, как эта страница открылась. Без этой
    // проверки прогресс-бар был бы виден только тому, кто сам нажал кнопку
    // в этой же вкладке и не перезагружал страницу.
    api
      .getActiveJobs()
      .then((jobs) => {
        const activeHarvest = jobs.find((j) => j.type === "harvest");
        if (activeHarvest) setJobId(activeHarvest.id);
      })
      .catch(() => {});
  }, []);

  // Когда задача сбора завершилась — перечитываем сводку, чтобы цифры обновились.
  useEffect(() => {
    if (job && (job.status === "done" || job.status === "failed")) {
      load();
      if (coverageStart && coverageEnd) loadCoverage(coverageStart, coverageEnd);
    }
  }, [job?.status]);

  const startHarvest = async () => {
    if (!startDate || !endDate) {
      setError("Укажите обе даты периода");
      return;
    }
    try {
      const j = await api.startHarvest({ start_date: startDate, end_date: endDate });
      setJobId(j.id);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось запустить сбор");
    }
  };

  const startFullArchive = async () => {
    if (!confirm(
      "Собрать весь архив с 2004 года? Это займёт от суток до двух и будет " +
      "выполняться в фоне, пока сервер работает."
    )) return;
    try {
      const j = await api.startHarvest({ start_date: "2004-05-05", end_date: new Date().toISOString().slice(0, 10) });
      setJobId(j.id);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось запустить сбор");
    }
  };

  return (
    <div>
      <div className="card">
        <h2>Состояние корпуса</h2>
        {error && <p className="error-text">{error}</p>}
        {summary && (
          <div className="grid">
            <div className="stat">
              <div className="value">{summary.days_covered}</div>
              <div className="label">дней собрано</div>
            </div>
            <div className="stat">
              <div className="value">{summary.articles_with_text.toLocaleString("ru-RU")}</div>
              <div className="label">статей пригодно к анализу</div>
            </div>
            <div className="stat">
              <div className="value">
                {summary.articles_with_text > 0
                  ? Math.round((summary.articles_truncated / summary.articles_with_text) * 100)
                  : 0}
                %
              </div>
              <div className="label">из них платные (затравка)</div>
            </div>
            <div className="stat">
              <div className="value">{formatBytes(summary.database_bytes)}</div>
              <div className="label">размер базы</div>
            </div>
          </div>
        )}
        {summary?.first_day && (
          <p className="muted" style={{ marginTop: 12 }}>
            Собрано с {summary.first_day} по {summary.last_day}.
            {schedule?.enabled && schedule.next_run_at && (
              <> Следующий автосбор: {new Date(schedule.next_run_at).toLocaleString("ru-RU")}.</>
            )}
          </p>
        )}
      </div>

      <div className="card">
        <h2>Запустить сбор</h2>
        <p className="muted">
          Статья скачивается один раз и остаётся в базе — повторный сбор за те же даты
          пропускает уже известные статьи и почти ничего не стоит.
        </p>
        <div className="row" style={{ marginBottom: 12 }}>
          <div>
            <label>С даты</label>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </div>
          <div>
            <label>По дату</label>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </div>
          <button onClick={startHarvest} disabled={job?.status === "running"}>
            Собрать период
          </button>
          <button className="secondary" onClick={startFullArchive} disabled={job?.status === "running"}>
            Собрать весь архив (с 2004)
          </button>
        </div>
        {jobId && <JobProgress jobId={jobId} />}
      </div>

      <div className="card">
        <h2>Покрытие по дням</h2>
        <div className="row" style={{ marginBottom: 12 }}>
          <div>
            <label>С даты</label>
            <input
              type="date"
              value={coverageStart}
              min={summary?.first_day || undefined}
              max={summary?.last_day || undefined}
              onChange={(e) => setCoverageStart(e.target.value)}
            />
          </div>
          <div>
            <label>По дату</label>
            <input
              type="date"
              value={coverageEnd}
              min={summary?.first_day || undefined}
              max={summary?.last_day || undefined}
              onChange={(e) => setCoverageEnd(e.target.value)}
            />
          </div>
        </div>
        {coverage.length === 0 ? (
          <p className="muted">Нет собранных дней в этом диапазоне.</p>
        ) : (
          <>
            <table>
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Статей</th>
                  <th>Целых</th>
                  <th>Затравок</th>
                  <th>Не статьи</th>
                  <th>Ошибок</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {coverage
                  .slice(coveragePage * COVERAGE_PAGE_SIZE, (coveragePage + 1) * COVERAGE_PAGE_SIZE)
                  .map((d) => (
                    <tr key={d.day}>
                      <td>{d.day}</td>
                      <td>{d.total_urls}</td>
                      <td>{d.ok_count}</td>
                      <td>{d.truncated_count}</td>
                      <td>{d.empty_count}</td>
                      <td>{d.failed_count}</td>
                      <td><span className={`badge badge-${d.status}`}>{dayStatusLabel(d.status)}</span></td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <div className="row" style={{ marginTop: 12, justifyContent: "space-between" }}>
              <span className="muted">
                Дни {coveragePage * COVERAGE_PAGE_SIZE + 1}
                –{Math.min((coveragePage + 1) * COVERAGE_PAGE_SIZE, coverage.length)} из {coverage.length}
              </span>
              <div className="row">
                <button
                  className="secondary"
                  disabled={coveragePage === 0}
                  onClick={() => setCoveragePage((p) => p - 1)}
                >
                  ← Назад
                </button>
                <button
                  className="secondary"
                  disabled={(coveragePage + 1) * COVERAGE_PAGE_SIZE >= coverage.length}
                  onClick={() => setCoveragePage((p) => p + 1)}
                >
                  Вперёд →
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
