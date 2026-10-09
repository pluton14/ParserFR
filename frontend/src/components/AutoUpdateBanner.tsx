import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Job } from "../api/types";

// Автообновление корпуса (ночное в 00:30 и догон при запуске сервера) — задачи
// сбора с trigger "schedule" / "startup_catchup". Плашка показывает прогресс,
// пока обновление идёт, и сама исчезает по завершении. Если обновление не
// удалось (или завершилось, но не все дни окна получены), остаётся
// уведомление «данные не актуализировались», пока его не закроют или пока
// следующее обновление не пройдёт успешно.
const AUTO_TRIGGERS = ["schedule", "startup_catchup"];
const DISMISS_KEY = "autoupdate-dismissed-job";
const COLLAPSE_KEY = "autoupdate-collapsed";

const isAuto = (j: Job) => j.type === "harvest" && AUTO_TRIGGERS.includes(String(j.params?.trigger ?? ""));
const isLive = (j: Job) => j.status === "pending" || j.status === "running";

function readDismissed(): string | null {
  try {
    return localStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

export default function AutoUpdateBanner() {
  const [running, setRunning] = useState<Job | null>(null);
  const [problem, setProblem] = useState<{ jobId: string; text: string } | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(readDismissed());
  const [collapsed, setCollapsed] = useState(readCollapsed());
  // Результат проверки завершённого задания считаем один раз на job.id.
  const checkedJobId = useRef<string | null>(null);

  useEffect(() => {
    let stopped = false;

    const evaluateFinished = async (job: Job) => {
      if (checkedJobId.current === job.id) return;
      checkedJobId.current = job.id;
      const start = String(job.params?.start_date ?? "");
      const end = String(job.params?.end_date ?? "");
      const period = start && end ? `${start} — ${end}` : "последние дни";
      if (job.status === "failed") {
        setProblem({
          jobId: job.id,
          text: `Данные не актуализировались: обновление за ${period} завершилось ошибкой.${job.error ? ` ${job.error}` : ""}`,
        });
        return;
      }
      if (job.status === "cancelled") {
        setProblem({
          jobId: job.id,
          text: `Данные не актуализировались: обновление за ${period} было остановлено${job.error ? ` (${job.error})` : ""}.`,
        });
        return;
      }
      // Задача «выполнена», но источник мог не отдать часть дней — проверяем
      // по самому покрытию, а не по статусу задачи.
      if (start && end) {
        try {
          const days = await api.corpusCoverage(start, end);
          const done = new Set(days.filter((d) => d.status === "done").map((d) => d.day));
          const missing: string[] = [];
          for (let t = Date.parse(start); t <= Date.parse(end); t += 86400000) {
            const iso = new Date(t).toISOString().slice(0, 10);
            if (!done.has(iso)) missing.push(iso);
          }
          if (missing.length > 0) {
            setProblem({
              jobId: job.id,
              text: `Данные актуализированы не полностью: не получены дни ${missing.join(", ")}.`,
            });
            return;
          }
        } catch {
          return; // проверку не удалось выполнить — не пугаем пользователя зря
        }
      }
      setProblem(null);
    };

    const tick = async () => {
      try {
        // Находка 2026-10-09: пока задача идёт, её прогресс (current/total/
        // message) обновляется только в памяти процесса — в строку Job в базе
        // он пишется лишь при старте и при завершении (registry.sync() по ходу
        // работы нигде не вызывается). /api/jobs (listJobs) читает именно базу,
        // поэтому всю дорогу отдавал бы начальный снимок («подготовка», 0/0).
        // /api/jobs/active, наоборот, берёт состояние из памяти — им и сверяем,
        // жива ли автозадача прямо сейчас.
        const live = (await api.getActiveJobs()).filter(isAuto).find(isLive) ?? null;
        if (stopped) return;
        setRunning(live);
        if (!live) {
          // Задача уже не в памяти — либо закончилась (тогда в базе лежит
          // честный финальный снимок, _persist писал его в конце), либо
          // процесс перезапустился посреди неё. В обоих случаях дальше смотрим
          // в базу через /api/jobs.
          const jobs = (await api.listJobs(15)).filter(isAuto);
          if (stopped) return;
          const latest = jobs
            .slice()
            .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
          if (latest) await evaluateFinished(latest);
        }
      } catch {
        // сеть моргнула — следующий опрос повторит
      }
    };

    tick();
    const timer = setInterval(tick, 5000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
    } catch {
      /* состояние не сохранится между перезагрузками — не страшно */
    }
  };

  const dismiss = (jobId: string) => {
    setDismissed(jobId);
    try {
      localStorage.setItem(DISMISS_KEY, jobId);
    } catch {
      /* без localStorage закрытие действует до перезагрузки */
    }
  };

  if (running) {
    const percent = running.total > 0 ? Math.min(100, Math.round((running.current / running.total) * 100)) : 0;
    if (collapsed) {
      return (
        <button className="toast toast-pill" onClick={toggleCollapsed} title="Развернуть">
          <span className="toast-spinner" />
          Обновление{running.total > 0 ? ` ${percent}%` : ""}
        </button>
      );
    }
    return (
      <div className="toast" role="status">
        <div className="toast-head">
          <span className="toast-spinner" />
          <strong>Обновляем данные корпуса</strong>
          <button className="toast-icon" onClick={toggleCollapsed} aria-label="Свернуть" title="Свернуть">
            –
          </button>
        </div>
        <div className="progress-bar" style={{ marginTop: 10 }}>
          <div style={{ width: `${running.total > 0 ? percent : 8}%` }} />
        </div>
        <div className="toast-sub">
          {running.total > 0
            ? `${running.current.toLocaleString("ru-RU")} из ${running.total.toLocaleString("ru-RU")} · ${percent}%`
            : running.message || "Подготовка…"}
        </div>
      </div>
    );
  }

  if (problem && dismissed !== problem.jobId) {
    if (collapsed) {
      return (
        <button className="toast toast-pill toast-pill-warn" onClick={toggleCollapsed} title="Развернуть">
          <span className="toast-warn-icon">!</span>
          Данные не актуализированы
        </button>
      );
    }
    return (
      <div className="toast toast-warn" role="alert">
        <div className="toast-head">
          <span className="toast-warn-icon">!</span>
          <strong>Данные не актуализированы</strong>
          <button className="toast-icon" onClick={toggleCollapsed} aria-label="Свернуть" title="Свернуть">
            –
          </button>
        </div>
        <div className="toast-sub">{problem.text.replace(/^Данные не актуализировались:\s*/, "")}</div>
        <div style={{ marginTop: 10, textAlign: "right" }}>
          <button className="secondary" onClick={() => dismiss(problem.jobId)}>
            Понятно
          </button>
        </div>
      </div>
    );
  }

  return null;
}
