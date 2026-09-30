import { useState } from "react";
import type { KeywordStats } from "../api/types";

/**
 * Горизонтальная гистограмма: сколько статей содержат каждое ключевое
 * слово (в % от обработанного корпуса) — быстрее читается, чем таблица,
 * когда слов в словаре много. Клик по столбцу выбирает слово — то же
 * действие, что клик по строке таблицы выше.
 */
export default function KeywordBarChart({
  stats,
  selected,
  onSelect,
}: {
  stats: KeywordStats[];
  selected: string | null;
  onSelect: (keyword: string) => void;
}) {
  const [hovered, setHovered] = useState<string | null>(null);

  if (stats.length === 0) return <p className="muted">Нет данных.</p>;

  const sorted = stats.slice().sort((a, b) => b.percentage - a.percentage);
  const maxPct = Math.max(1, ...sorted.map((s) => s.percentage));

  const rowHeight = 28;
  const width = 720;
  const labelWidth = 160;
  const barAreaWidth = width - labelWidth - 70;
  const height = sorted.length * rowHeight + 8;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {sorted.map((s, i) => {
        const y = i * rowHeight + 4;
        const barWidth = (s.percentage / maxPct) * barAreaWidth;
        const isSelected = s.keyword === selected;
        const isHovered = s.keyword === hovered;
        return (
          <g
            key={s.keyword}
            style={{ cursor: "pointer" }}
            onClick={() => onSelect(s.keyword)}
            onPointerEnter={() => setHovered(s.keyword)}
            onPointerLeave={() => setHovered(null)}
          >
            {/* Увеличенная зона наведения — вся строка, не только столбец */}
            <rect x={0} y={y - 2} width={width} height={rowHeight - 4} fill="transparent" />
            <text
              x={labelWidth - 8}
              y={y + rowHeight / 2 - 2}
              fontSize={12}
              fill="var(--text)"
              fontWeight={isSelected ? 600 : 400}
              textAnchor="end"
              dominantBaseline="middle"
            >
              {s.keyword}
            </text>
            <rect
              x={labelWidth}
              y={y + 2}
              width={Math.max(2, barWidth)}
              height={rowHeight - 12}
              rx={3}
              fill="var(--accent)"
              opacity={isSelected ? 1 : isHovered ? 0.85 : 0.65}
              stroke={isSelected ? "var(--accent-hover)" : "none"}
              strokeWidth={isSelected ? 2 : 0}
            />
            <text
              x={labelWidth + barWidth + 8}
              y={y + rowHeight / 2 - 2}
              fontSize={11}
              fill="var(--text-muted)"
              dominantBaseline="middle"
            >
              {s.percentage.toFixed(1)}% · {s.total_occurrences}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
