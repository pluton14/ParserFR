import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ContextWord, ExampleItem } from "../api/types";
import WordPicker from "./WordPicker";

function highlight(item: ExampleItem) {
  return (
    <span>
      …{item.context_before} <mark>{item.keyword}</mark> {item.context_after}…
    </span>
  );
}

// Слово как токен для сравнения: без регистра и без знаков вокруг.
const norm = (w: string) => w.toLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
const lastWord = (text: string) => norm(text.trim().split(/\s+/).pop() || "");
const firstWord = (text: string) => norm(text.trim().split(/\s+/)[0] || "");

export default function ExamplesPanel({
  analysisId,
  keyword,
  leftOptions,
  rightOptions,
}: {
  analysisId: number;
  keyword: string;
  // Слова, найденные рядом с ключевым в ходе анализа, — варианты для подсказки.
  leftOptions: ContextWord[];
  rightOptions: ContextWord[];
}) {
  const [items, setItems] = useState<ExampleItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  // Фильтр по ближайшему слову слева/справа от найденного ключевого слова.
  // Можно выбрать несколько слов: внутри одного поля они работают как «или».
  const [leftWords, setLeftWords] = useState<string[]>([]);
  const [rightWords, setRightWords] = useState<string[]>([]);
  const wordFilter = leftWords.length > 0 || rightWords.length > 0;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const limit = 20;

  useEffect(() => {
    setOffset(0);
  }, [keyword, leftWords, rightWords]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const load = async () => {
      if (!wordFilter) {
        const page = await api.getExamples(analysisId, keyword, offset, limit);
        return { items: page.items, total: page.total };
      }
      // Сервер фильтрует только по подстроке, поэтому при фильтре по слову слева/справа
      // забираем все примеры пачками по 500 и отбираем точное соседнее слово здесь.
      const l = new Set(leftWords.map(norm));
      const r = new Set(rightWords.map(norm));
      const matched: ExampleItem[] = [];
      for (let from = 0; ; from += 500) {
        const page = await api.getExamples(analysisId, keyword, from, 500);
        for (const it of page.items) {
          if (l.size && !l.has(lastWord(it.context_before))) continue;
          if (r.size && !r.has(firstWord(it.context_after))) continue;
          matched.push(it);
        }
        if (cancelled || from + 500 >= page.total) break;
      }
      return { items: matched.slice(offset, offset + limit), total: matched.length };
    };
    load()
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setTotal(page.total);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : "Не удалось загрузить примеры");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [analysisId, keyword, offset, leftWords, rightWords]);

  return (
    <div>
      <div className="examples-filters">
        <WordPicker label="Слово слева" values={leftWords} onChange={setLeftWords} options={leftOptions} />
        <WordPicker label="Слово справа" values={rightWords} onChange={setRightWords} options={rightOptions} />
        {wordFilter && (
          <button className="secondary" onClick={() => { setLeftWords([]); setRightWords([]); }}>
            Сбросить
          </button>
        )}
        <span className="count">Всего примеров: {total}</span>
      </div>
      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p className="muted">Загрузка…</p>
      ) : items.length === 0 ? (
        <p className="muted">Ничего не найдено.</p>
      ) : (
        items.map((item, i) => (
          <div className="example-item" key={i}>
            <div>{highlight(item)}</div>
            <div className="row" style={{ marginTop: 4, justifyContent: "space-between" }}>
              <a className="example-link" href={item.url} target="_blank" rel="noreferrer">
                {item.title || item.url}
              </a>
              {item.published_date && <span className="muted">{item.published_date}</span>}
            </div>
          </div>
        ))
      )}
      {total > limit && (
        <div className="row" style={{ marginTop: 10, justifyContent: "center" }}>
          <button className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
            ← Назад
          </button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + limit, total)} из {total}
          </span>
          <button className="secondary" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>
            Вперёд →
          </button>
        </div>
      )}
    </div>
  );
}
