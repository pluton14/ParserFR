import { useState } from "react";
import type { ContextWord } from "../api/types";

/**
 * Горизонтальная гистограмма топа соседних слов — визуальная замена
 * плоской таблице ContextTable. Проценты уже посчитаны на бэкенде от
 * общего числа вхождений ключевого слова.
 */
export default function ContextBarChart({ words }: { words: ContextWord[] }) {
  const [hovered, setHovered] = useState<number | null>(null);

  if (words.length === 0) return <p className="muted">Нет данных.</p>;

  const top = words.slice(0, 10);
  const maxCount = Math.max(1, ...top.map((w) => w.count));

  const rowHeight = 26;
  const width = 340;
  const labelWidth = 90;
  const barAreaWidth = width - labelWidth - 50;
  const height = top.length * rowHeight + 6;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {top.map((w, i) => {
        const y = i * rowHeight + 3;
        const barWidth = (w.count / maxCount) * barAreaWidth;
        const isHovered = i === hovered;
        return (
          <g
            key={w.word + i}
            onPointerEnter={() => setHovered(i)}
            onPointerLeave={() => setHovered(null)}
          >
            <rect x={0} y={y - 2} width={width} height={rowHeight - 3} fill="transparent" />
            <text
              x={labelWidth - 6}
              y={y + rowHeight / 2 - 3}
              fontSize={11}
              fill="var(--text)"
              textAnchor="end"
              dominantBaseline="middle"
            >
              {w.word}
            </text>
            <rect
              x={labelWidth}
              y={y + 2}
              width={Math.max(2, barWidth)}
              height={rowHeight - 10}
              rx={3}
              fill="var(--accent)"
              opacity={isHovered ? 0.9 : 0.6}
            />
            <text
              x={labelWidth + barWidth + 6}
              y={y + rowHeight / 2 - 3}
              fontSize={10}
              fill="var(--text-muted)"
              dominantBaseline="middle"
            >
              {w.count}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
