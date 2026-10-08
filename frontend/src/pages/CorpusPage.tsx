import { useEffect, useRef, useState } from "react";
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
  const [lt, setLt] = useState({ rate: "0", workers: "16" });
  // Что реально действует на бэкенде сейчас (а не то, что набрано в полях).
  const [activePacing, setActivePacing] = useState<{ rate: number; workers: number } | null>(null);
  // Актуальный диапазон для таймера обновления (замыкание таймера иначе видит старые даты).
  const coverageRange = useRef({ start: "", end: "" });
  const [ltSaving, setLtSaving] = useState(false);
  const [ltSaved, setLtSaved] = useState(false);
  const [scheduledRetry, setScheduledRetry] = useState<{ mode: string; fire_at: string } | null>(null);
  const [retryDelayInput, setRetryDelayInput] = useState("2");
  const [retrySaving, setRetrySaving] = useState(false);
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

  // Лёгкое обновление только сводки (без расписания и таблицы покрытия) —
  // цифры «в реальном времени», пока идёт сбор.
  const refreshSummary = async () => {
    try {
      setSummary(await api.corpusSummary());
    } catch {
      /* временный сбой — оставляем прошлые цифры, следующий опрос повторит */
    }
  };

  const loadCoverage = async (start: string, end: string, keepPage = false) => {
    try {
      const c = await api.corpusCoverage(start || undefined, end || undefined);
      setCoverage(c);
      if (!keepPage) setCoveragePage(0);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить покрытие по дням");
    }
  };

  // Перезагружаем покрытие при смене диапазона фильтра (в т.ч. когда он
  // выставился впервые значениями по умолчанию из load()).
  useEffect(() => {
    coverageRange.current = { start: coverageStart, end: coverageEnd };
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
    api
      .getPacing()
      .then((t) => {
        setLt({ rate: String(t.rate), workers: String(t.workers) });
        setActivePacing(t);
      })
      .catch(() => {});
    api.getScheduledRetry().then(setScheduledRetry).catch(() => {});
  }, []);

  // Живое обновление цифр и процента покрытия: раз в 15 с, пока вкладка открыта.
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.hidden) return;
      refreshSummary();
      if (coverageRange.current.start && coverageRange.current.end) {
        loadCoverage(coverageRange.current.start, coverageRange.current.end, true);
      }
    }, 15000);
    return () => clearInterval(timer);
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

  // Статьи, чей URL уже известен из прошлых листингов, но ещё не скачан —
  // качает их напрямую, вообще не обращаясь к листингу карт сайта. Полезно,
  // когда именно листинг (sitemaps.lefigaro.fr) сейчас заблокирован, а
  // скачивание самих статей (www.lefigaro.fr) — ещё нет.
  const startDownloadPending = async () => {
    try {
      const j = await api.downloadPending();
      setJobId(j.id);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось запустить докачку");
    }
  };

  const applyPacing = async () => {
    const rate = Number(lt.rate);
    const workers = Number(lt.workers);
    if (!Number.isFinite(rate) || rate < 0) {
      setError("Потолок темпа: число ≥ 0 (0 — без ограничения)");
      return;
    }
    if (!Number.isInteger(workers) || workers < 1 || workers > 32) {
      setError("Потоков: целое число от 1 до 32");
      return;
    }
    setLtSaving(true);
    try {
      setActivePacing(await api.setPacing({ rate, workers }));
      setError(null);
      setLtSaved(true);
      setTimeout(() => setLtSaved(false), 2500);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось сохранить скорость сбора");
    } finally {
      setLtSaving(false);
    }
  };

  // Отложенный повтор переживает перезапуск сервера (хранится в базе), поэтому
  // после срабатывания задача появляется сама — просто подхватываем её так же,
  // как и запущенную вручную.
  const applyRetry = async () => {
    const hours = Number(retryDelayInput);
    if (!Number.isFinite(hours) || hours <= 0) {
      setError("Задержка должна быть числом часов > 0");
      return;
    }
    setRetrySaving(true);
    try {
      const r = await api.scheduleRetry(hours);
      setScheduledRetry({ mode: "download_pending", fire_at: r.fire_at });
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось запланировать повтор");
    } finally {
      setRetrySaving(false);
    }
  };

  const cancelRetry = async () => {
    setRetrySaving(true);
    try {
      await api.cancelScheduledRetry();
      setScheduledRetry(null);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось отменить повтор");
    } finally {
      setRetrySaving(false);
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
        <div className="row" style={{ marginBottom: 12, alignItems: "flex-end" }}>
          <button className="secondary" onClick={startDownloadPending} disabled={job?.status === "running"}>
            Докачать уже найденное
          </button>
          <span className="muted" style={{ maxWidth: 360 }}>
            URL уже известны из прошлых листингов, но статья ещё не скачана — без
            обращения к листингу карт сайта.
          </span>
        </div>
        <div className="row" style={{ marginBottom: 12, alignItems: "flex-end" }}>
          <div>
            <label>Потолок темпа, запросов/с</label>
            <input
              type="number"
              min={0}
              step={0.5}
              style={{ width: 100 }}
              value={lt.rate}
              onChange={(e) => setLt({ ...lt, rate: e.target.value })}
            />
          </div>
          <div>
            <label>Потоков скачивания</label>
            <input
              type="number"
              min={1}
              max={32}
              step={1}
              style={{ width: 80 }}
              value={lt.workers}
              onChange={(e) => setLt({ ...lt, workers: e.target.value })}
            />
          </div>
          <button className="secondary" onClick={applyPacing} disabled={ltSaving}>
            {ltSaving ? "Сохраняем…" : "Применить"}
          </button>
          <span className="muted" style={{ maxWidth: 380 }}>
            {ltSaved ? "Сохранено. " : ""}
            <b>
              Сейчас действует:{" "}
              {activePacing
                ? `${activePacing.rate > 0 ? `потолок ${activePacing.rate} запр/с` : "без потолка"}, ${activePacing.workers} потоков`
                : "…"}
            </b>
            . Потолок применяется сразу, число потоков — со следующего запуска сбора. 0 в поле потолка = без ограничения.
          </span>
        </div>
        <div className="row" style={{ marginBottom: 12, alignItems: "flex-end" }}>
          <div>
            <label>Отложенный повтор (докачка) через, ч</label>
            <input
              type="number"
              min={0.1}
              step={0.5}
              style={{ width: 100 }}
              value={retryDelayInput}
              onChange={(e) => setRetryDelayInput(e.target.value)}
            />
          </div>
          <button className="secondary" onClick={applyRetry} disabled={retrySaving}>
            {retrySaving ? "Сохраняем…" : scheduledRetry ? "Переставить" : "Запланировать"}
          </button>
          {scheduledRetry && (
            <>
              <span className="muted">
                Запуск: {new Date(scheduledRetry.fire_at + "Z").toLocaleString("ru-RU")}. Переживает
                перезапуск сервера.
              </span>
              <button className="secondary" onClick={cancelRetry} disabled={retrySaving}>
                Отменить
              </button>
            </>
          )}
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
        {coverage.length > 0 && (() => {
          // Покрытие в выбранном диапазоне: целых / всех известных адресов
          // дней диапазона, не считая премиум и пустые страницы (404 и не
          // статьи). Считается по строкам дней, поэтому дни, которые ещё не
          // листинговались, в знаменатель не входят.
          const sum = (f: (d: DayCoverage) => number) => coverage.reduce((a, d) => a + f(d), 0);
          const whole = sum((d) => d.ok_count);
          const known = sum((d) => d.total_urls) - sum((d) => d.premium_count) - sum((d) => d.empty_count);
          const pct = known > 0 ? (whole / known) * 100 : 0;
          const fmt = (v: number) => v.toFixed(1).replace(".", ",");
          return (
            <div style={{ margin: "8px 0 12px" }}>
              <span style={{ fontSize: 28, fontWeight: 700 }}>{fmt(pct)}%</span>{" "}
              покрытие в выбранном диапазоне ({coverage.length} дн.)
              <div style={{ height: 8, background: "rgba(128,128,128,.25)", borderRadius: 4, overflow: "hidden", margin: "4px 0" }}>
                <div style={{ width: `${Math.min(100, pct)}%`, height: "100%", background: "#2e7d32", transition: "width .6s" }} />
              </div>
              <p className="muted" style={{ margin: 0 }}>
                {whole.toLocaleString("ru-RU")} целых из {known.toLocaleString("ru-RU")} адресов
                (без премиум и пустых страниц: 404 и не статей). Обновляется раз в 15 с.
              </p>
            </div>
          );
        })()}
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
