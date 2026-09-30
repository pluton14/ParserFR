import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ExampleItem } from "../api/types";

function highlight(item: ExampleItem) {
  return (
    <span>
      …{item.context_before} <mark>{item.keyword}</mark> {item.context_after}…
    </span>
  );
}

export default function ExamplesPanel({ analysisId, keyword }: { analysisId: number; keyword: string }) {
  const [items, setItems] = useState<ExampleItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const limit = 20;

  useEffect(() => {
    setOffset(0);
  }, [keyword, search]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getExamples(analysisId, keyword, offset, limit, search || undefined)
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
  }, [analysisId, keyword, offset, search]);

  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <input
          type="text"
          placeholder="Поиск по примерам…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 280 }}
        />
        <span className="muted">Всего примеров: {total}</span>
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
