import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { AnalysisSummary, CategoryCount, Dictionary, TextScope } from "../api/types";
import JobProgress from "../components/JobProgress";
import { useJobProgress } from "../hooks/useJobProgress";
import { jobStatusLabel } from "../lib/labels";

const TEXT_SCOPE_LABELS: Record<TextScope, string> = {
  body: "Основной текст",
  title: "Только заголовки",
  title_body: "Заголовки + текст",
};

// И "заголовки", и "текст" сняты одновременно не бывает — хотя бы одна
// зона всегда должна остаться выбранной, иначе анализу нечего сканировать.
// Снятие второго флага при обеих включённых оставляет ПЕРВУЮ (ту, что
// осталась true) — простое и предсказуемое правило.
function pickTextScope(wantTitle: boolean, wantBody: boolean): TextScope {
  if (wantTitle && wantBody) return "title_body";
  if (wantTitle) return "title";
  if (wantBody) return "body";
  return "body";
}

export default function AnalysesPage() {
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [dictionaries, setDictionaries] = useState<Dictionary[]>([]);
  const [categories, setCategories] = useState<CategoryCount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dictionaryId, setDictionaryId] = useState<number | "">("");
  const [corpusRange, setCorpusRange] = useState<{ first: string; last: string } | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [name, setName] = useState("");
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);
  const [textScope, setTextScope] = useState<TextScope>("body");
  const [jobId, setJobId] = useState<string | null>(null);
  const navigate = useNavigate();
  const { job } = useJobProgress(jobId);

  const load = async () => {
    try {
      const [a, d] = await Promise.all([api.listAnalyses(), api.listDictionaries()]);
      setAnalyses(a);
      setDictionaries(d);
      if (d.length > 0 && dictionaryId === "") setDictionaryId(d[0].id);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить список анализов");
    }
    // Категории — отдельным, не блокирующим запросом: это group-by по всей
    // таблице статей, под активным сбором может отвечать не мгновенно, и
    // ждать его нет смысла — словарь и история должны появиться сразу же.
    api.corpusCategories().then(setCategories).catch(() => {});

    // Диапазон дат корпуса — чтобы не дать выбрать период, за который
    // заведомо ничего не собрано (например, 2001 год при первой статье
    // из 2004). По умолчанию подставляем последний доступный календарный
    // год, но только один раз — не переписываем даты, если пользователь
    // их уже поменял вручную.
    api
      .corpusSummary()
      .then((s) => {
        if (!s.first_day || !s.last_day) return;
        setCorpusRange({ first: s.first_day, last: s.last_day });
        const yearStart = `${s.last_day!.slice(0, 4)}-01-01`;
        const defaultStart = yearStart < s.first_day! ? s.first_day! : yearStart;
        setStartDate((prev) => prev || defaultStart);
        setEndDate((prev) => prev || s.last_day!);
      })
      .catch(() => {});
  };

  useEffect(() => {
    load();
    // Тот же случай, что на вкладке «Корпус»: анализ мог быть запущен до
    // открытия этой страницы — подхватываем уже идущую задачу.
    api
      .getActiveJobs()
      .then((jobs) => {
        const activeAnalysis = jobs.find((j) => j.type === "analysis");
        if (activeAnalysis) setJobId(activeAnalysis.id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (job?.status === "done" && job.result_id) {
      navigate(`/analyses/${job.result_id}`);
    } else if (job?.status === "done" || job?.status === "failed") {
      load();
    }
  }, [job?.status]);

  const start = async () => {
    if (!startDate || !endDate) {
      setError("Укажите период");
      return;
    }
    if (corpusRange && (startDate < corpusRange.first || endDate > corpusRange.last)) {
      setError(
        `Корпус собран только за ${corpusRange.first} — ${corpusRange.last}. ` +
          `Выберите период в этих границах.`
      );
      return;
    }
    if (dictionaryId === "") {
      setError("Выберите словарь");
      return;
    }
    try {
      const j = await api.startAnalysis({
        start_date: startDate,
        end_date: endDate,
        dictionary_id: dictionaryId,
        name: name || undefined,
        categories: selectedCategories.length > 0 ? selectedCategories : undefined,
        text_scope: textScope,
      });
      setJobId(j.id);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось запустить анализ");
    }
  };

  const toggleCategory = (category: string) => {
    setSelectedCategories((prev) =>
      prev.includes(category) ? prev.filter((c) => c !== category) : [...prev, category]
    );
  };

  const remove = async (id: number) => {
    if (!confirm("Удалить этот анализ?")) return;
    await api.deleteAnalysis(id);
    await load();
  };

  return (
    <div>
      <div className="card">
        <h2>Новый анализ</h2>
        <p className="muted">
          Считается по уже собранному корпусу — сеть не используется, обычно занимает секунды.
          {corpusRange && ` Доступный период: ${corpusRange.first} — ${corpusRange.last}.`}
        </p>
        <div className="row" style={{ marginBottom: 12 }}>
          <div>
            <label>Словарь</label>
            <select value={dictionaryId} onChange={(e) => setDictionaryId(Number(e.target.value))}>
              {dictionaries.map((d) => (
                <option key={d.id} value={d.id}>{d.name} ({d.keywords.length} слов)</option>
              ))}
            </select>
          </div>
          <div>
            <label>С даты</label>
            <input
              type="date"
              value={startDate}
              min={corpusRange?.first}
              max={corpusRange?.last}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>
          <div>
            <label>По дату</label>
            <input
              type="date"
              value={endDate}
              min={corpusRange?.first}
              max={corpusRange?.last}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <label>Название (необязательно)</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
        </div>

        <div className="row" style={{ marginBottom: 12 }}>
          <div>
            <label>Где искать слова</label>
            <div style={{ display: "flex", gap: 14, alignItems: "center", height: 38 }}>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={textScope === "title_body"}
                  onChange={(e) => setTextScope(e.target.checked ? "title_body" : "body")}
                />
                Все
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={textScope === "title" || textScope === "title_body"}
                  onChange={(e) => {
                    const wantTitle = e.target.checked;
                    const wantBody = textScope === "body" || textScope === "title_body";
                    setTextScope(pickTextScope(wantTitle, wantBody));
                  }}
                />
                Заголовки
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={textScope === "body" || textScope === "title_body"}
                  onChange={(e) => {
                    const wantBody = e.target.checked;
                    const wantTitle = textScope === "title" || textScope === "title_body";
                    setTextScope(pickTextScope(wantTitle, wantBody));
                  }}
                />
                Основной текст
              </label>
            </div>
          </div>
          <div style={{ flex: 1, minWidth: 260 }}>
            <label>
              Категории{" "}
              {selectedCategories.length > 0 && (
                <span className="muted">(выбрано: {selectedCategories.length})</span>
              )}
            </label>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                maxHeight: 140,
                overflowY: "auto",
                padding: 8,
                border: "1px solid var(--border, #444)",
                borderRadius: 6,
              }}
            >
              {categories.length === 0 && <span className="muted">Категорий пока нет</span>}
              {selectedCategories.length > 0 && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => setSelectedCategories([])}
                  title="Сбросить — значит все категории"
                >
                  Все категории ×
                </button>
              )}
              {categories.map((c) => (
                <button
                  type="button"
                  key={c.category}
                  onClick={() => toggleCategory(c.category)}
                  className={`chip${selectedCategories.includes(c.category) ? " chip-active" : ""}`}
                  title={`${c.count.toLocaleString("ru-RU")} статей`}
                >
                  {c.category}
                </button>
              ))}
            </div>
            <p className="muted" style={{ marginTop: 4 }}>
              Ничего не выбрано — учитываются все категории.
            </p>
          </div>
        </div>
        {error && <p className="error-text">{error}</p>}
        {dictionaries.length === 0 ? (
          <p className="muted">Сначала создайте словарь на вкладке «Словари».</p>
        ) : (
          <button onClick={start} disabled={job?.status === "running"}>
            Запустить анализ
          </button>
        )}
        {jobId && <JobProgress jobId={jobId} />}
      </div>

      <div className="card">
        <h2>История анализов</h2>
        {analyses.length === 0 ? (
          <p className="muted">Анализов пока не было.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Название</th>
                <th>Период</th>
                <th>Фильтр</th>
                <th>Слов</th>
                <th>Статей</th>
                <th>Статус</th>
                <th>Создан</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {analyses.map((a) => (
                <tr key={a.id}>
                  <td><Link to={`/analyses/${a.id}`}>{a.name || `Анализ #${a.id}`}</Link></td>
                  <td>{a.start_date} — {a.end_date}</td>
                  <td className="muted">
                    {TEXT_SCOPE_LABELS[a.text_scope]}
                    {a.categories && a.categories.length > 0
                      ? ` · ${a.categories.length === 1 ? a.categories[0] : `${a.categories.length} категорий`}`
                      : ""}
                  </td>
                  <td>{a.keywords.length}</td>
                  <td>{a.total_processed_articles.toLocaleString("ru-RU")}</td>
                  <td><span className={`badge badge-${a.status}`}>{jobStatusLabel(a.status)}</span></td>
                  <td className="muted">{new Date(a.created_at).toLocaleString("ru-RU")}</td>
                  <td>
                    <button className="danger" onClick={() => remove(a.id)}>Удалить</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
