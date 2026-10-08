import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { AnalysisDetail, KeywordStats } from "../api/types";
import TimeseriesChart from "../components/TimeseriesChart";
import KeywordBarChart from "../components/KeywordBarChart";
import ContextBarChart from "../components/ContextBarChart";
import ExamplesPanel from "../components/ExamplesPanel";

// Фиксированный порядок цветов по индексу слова в общем списке словаря —
// цвет всегда закреплён за конкретным словом, не за его позицией в текущей
// выборке (снятие/добавление другого слова с графика не перекрашивает
// оставшиеся линии).
const SERIES_COLORS = [
  "#2f6fed", "#e0623c", "#0f9b9b", "#8b5cf6",
  "#c2984f", "#4f7942", "#c2478b", "#5b6b7a",
];

export default function AnalysisDetailPage() {
  const { id } = useParams<{ id: string }>();
  const analysisId = Number(id);
  const [analysis, setAnalysis] = useState<AnalysisDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [chartKeywords, setChartKeywords] = useState<string[]>([]);
  const [chartStart, setChartStart] = useState("");
  const [chartEnd, setChartEnd] = useState("");

  useEffect(() => {
    api
      .getAnalysis(analysisId)
      .then((a) => {
        setAnalysis(a);
        const withHits = a.stats.find((s) => s.total_occurrences > 0);
        const initial = withHits?.keyword || a.keywords[0] || null;
        setSelected(initial);
        setChartKeywords(initial ? [initial] : []);
        setChartStart(a.start_date);
        setChartEnd(a.end_date);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Не удалось загрузить анализ"));
  }, [analysisId]);

  if (error) return <p className="error-text">{error}</p>;
  if (!analysis) return <p className="muted">Загрузка…</p>;

  const selectedStats: KeywordStats | undefined = analysis.stats.find((s) => s.keyword === selected);

  const colorFor = (keyword: string) => {
    const idx = analysis.keywords.indexOf(keyword);
    return SERIES_COLORS[(idx >= 0 ? idx : 0) % SERIES_COLORS.length];
  };

  const chartSeries = chartKeywords.map((kw) => ({
    keyword: kw,
    color: colorFor(kw),
    points: (analysis.timeseries[kw] || []).filter(
      (p) => (!chartStart || p.date >= chartStart) && (!chartEnd || p.date <= chartEnd)
    ),
  }));

  const toggleChartKeyword = (keyword: string) => {
    setChartKeywords((prev) =>
      prev.includes(keyword) ? prev.filter((k) => k !== keyword) : [...prev, keyword]
    );
  };

  return (
    <div>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <h2>{analysis.name || `Анализ #${analysis.id}`}</h2>
            <p className="muted">
              {analysis.start_date} — {analysis.end_date} ·{" "}
              <span title={`В корпусе за период: ${analysis.articles_in_corpus.toLocaleString("ru-RU")} статей`}>
                {analysis.total_processed_articles.toLocaleString("ru-RU")} статей проанализировано
              </span>
              {analysis.total_words != null && (
                <> · {analysis.total_words.toLocaleString("ru-RU")} слов в проанализированном тексте</>
              )}
            </p>
          </div>
          <div className="row">
            <a href={api.exportJsonUrl(analysis.id)}>
              <button className="secondary">Скачать JSON</button>
            </a>
            <a href={api.exportHtmlUrl(analysis.id)}>
              <button className="secondary">Скачать HTML с примерами</button>
            </a>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Ключевые слова</h2>
        {analysis.stats.length > 1 && (
          <KeywordBarChart stats={analysis.stats} selected={selected} onSelect={setSelected} />
        )}
        <table>
          <thead>
            <tr>
              <th>На графике</th>
              <th>Слово / фраза</th>
              <th>Статей</th>
              <th>%</th>
              <th>Вхождений</th>
            </tr>
          </thead>
          <tbody>
            {analysis.stats
              .slice()
              .sort((a, b) => b.total_occurrences - a.total_occurrences)
              .map((s) => (
                <tr
                  key={s.keyword}
                  style={{ fontWeight: s.keyword === selected ? 600 : 400 }}
                >
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={chartKeywords.includes(s.keyword)}
                      onChange={() => toggleChartKeyword(s.keyword)}
                      style={{ accentColor: colorFor(s.keyword), cursor: "pointer" }}
                    />
                  </td>
                  <td onClick={() => setSelected(s.keyword)} style={{ cursor: "pointer" }}>{s.keyword}</td>
                  <td onClick={() => setSelected(s.keyword)} style={{ cursor: "pointer" }}>{s.articles_with_word}</td>
                  <td onClick={() => setSelected(s.keyword)} style={{ cursor: "pointer" }}>{s.percentage.toFixed(2)}%</td>
                  <td onClick={() => setSelected(s.keyword)} style={{ cursor: "pointer" }}>{s.total_occurrences}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      {chartKeywords.length > 0 && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <h2 style={{ margin: 0 }}>
              Динамика{chartKeywords.length === 1 ? `: «${chartKeywords[0]}»` : ` (${chartKeywords.length} слов)`}
            </h2>
            <div className="row">
              <div>
                <label style={{ fontSize: 12 }}>С даты</label>
                <input
                  type="date"
                  value={chartStart}
                  min={analysis.start_date}
                  max={analysis.end_date}
                  onChange={(e) => setChartStart(e.target.value)}
                />
              </div>
              <div>
                <label style={{ fontSize: 12 }}>По дату</label>
                <input
                  type="date"
                  value={chartEnd}
                  min={analysis.start_date}
                  max={analysis.end_date}
                  onChange={(e) => setChartEnd(e.target.value)}
                />
              </div>
            </div>
          </div>
          <TimeseriesChart series={chartSeries} />
        </div>
      )}

      {selectedStats && (
        <>
          <div className="card">
            <h2>Контекст: «{selectedStats.keyword}»</h2>
            <div className="two-col">
              <div>
                <h3 style={{ fontSize: 14 }}>Топ слов слева</h3>
                <ContextBarChart words={selectedStats.left_context_words} />
              </div>
              <div>
                <h3 style={{ fontSize: 14 }}>Топ слов справа</h3>
                <ContextBarChart words={selectedStats.right_context_words} />
              </div>
            </div>
          </div>

          <div className="card">
            <h2>Топ категорий: «{selectedStats.keyword}»</h2>
            {selectedStats.categories && selectedStats.categories.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Категория</th>
                    <th>Статей со словом</th>
                    <th>Статей в категории</th>
                    <th>% статей категории</th>
                    <th>Вхождений</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedStats.categories.slice(0, 15).map((c) => (
                    <tr key={c.category}>
                      <td>{c.category}</td>
                      <td>{c.articles_with_word.toLocaleString("ru-RU")}</td>
                      <td>{c.category_articles.toLocaleString("ru-RU")}</td>
                      <td>{c.percentage.toFixed(2)}%</td>
                      <td>{c.occurrences.toLocaleString("ru-RU")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">
                Нет данных по категориям. Они считаются только в анализах, запущенных после обновления;
                запустите анализ заново.
              </p>
            )}
          </div>

          <div className="card">
            <h2>Примеры употребления</h2>
            <ExamplesPanel
              analysisId={analysis.id}
              keyword={selectedStats.keyword}
              leftOptions={selectedStats.left_context_words}
              rightOptions={selectedStats.right_context_words}
            />
          </div>
        </>
      )}
    </div>
  );
}

