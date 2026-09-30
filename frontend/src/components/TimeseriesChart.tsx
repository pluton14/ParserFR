import { useRef, useState } from "react";
import type { TimeseriesPoint } from "../api/types";

export interface ChartSeries {
  keyword: string;
  color: string;
  points: TimeseriesPoint[];
}

/**
 * SVG-график динамики по дням для одного или нескольких слов сразу.
 * Кроссхэйр следует за курсором и примагничивается к ближайшей дате —
 * тултип показывает значения ВСЕХ выбранных слов на этот день разом
 * (чтобы не гадать, в чей именно пиксель попал курсор), с легендой,
 * если серий больше одной.
 */
export default function TimeseriesChart({ series }: { series: ChartSeries[] }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const nonEmpty = series.filter((s) => s.points.length > 0);

  if (nonEmpty.length === 0) return <p className="muted">Нет данных для графика.</p>;

  // Общая ось X — объединение всех дат по всем сериям, отсортированное.
  const allDates = Array.from(new Set(nonEmpty.flatMap((s) => s.points.map((p) => p.date)))).sort();

  if (allDates.length === 1) {
    return (
      <p className="muted">
        Данных за один день ({allDates[0]}). Для графика динамики выберите более широкий период.
      </p>
    );
  }

  const width = 720;
  const height = 240;
  const padding = { top: 16, right: 16, bottom: 28, left: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  // Пропущенная дата у серии — 0 вхождений в этот день, а не "нет данных":
  // бэкенд отдаёт только дни с хотя бы одним совпадением.
  const seriesMaps = nonEmpty.map((s) => ({
    ...s,
    byDate: new Map(s.points.map((p) => [p.date, p])),
  }));

  const maxOccurrences = Math.max(1, ...nonEmpty.flatMap((s) => s.points.map((p) => p.occurrences)));
  const stepX = plotWidth / (allDates.length - 1);
  const xForIndex = (i: number) => padding.left + i * stepX;
  const yForValue = (v: number) => padding.top + plotHeight - (v / maxOccurrences) * plotHeight;

  const seriesPaths = seriesMaps.map((s) => {
    const coords = allDates.map((date, i) => {
      const p = s.byDate.get(date);
      return { x: xForIndex(i), y: yForValue(p?.occurrences ?? 0) };
    });
    const path = coords.map((c, i) => `${i === 0 ? "M" : "L"} ${c.x.toFixed(1)} ${c.y.toFixed(1)}`).join(" ");
    return { keyword: s.keyword, color: s.color, path, coords };
  });

  const yTicks = 4;
  const yTickValues = Array.from({ length: yTicks + 1 }, (_, i) => Math.round((maxOccurrences / yTicks) * i));

  const xTickCount = Math.min(6, allDates.length);
  const xTickIndexes = Array.from({ length: xTickCount }, (_, i) =>
    Math.round((i * (allDates.length - 1)) / Math.max(1, xTickCount - 1))
  );

  const handleMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const scaleX = width / rect.width;
    const localX = (e.clientX - rect.left) * scaleX;
    const idx = Math.round((localX - padding.left) / stepX);
    setHoverIndex(Math.max(0, Math.min(allDates.length - 1, idx)));
  };

  const hoverDate = hoverIndex !== null ? allDates[hoverIndex] : null;
  const hoverX = hoverIndex !== null ? xForIndex(hoverIndex) : 0;
  const tooltipOnLeft = hoverX > width - 180;

  return (
    <div style={{ position: "relative" }}>
      {nonEmpty.length > 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 8 }}>
          {nonEmpty.map((s) => (
            <span key={s.keyword} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12 }}>
              <span style={{ width: 14, height: 2, background: s.color, display: "inline-block" }} />
              {s.keyword}
            </span>
          ))}
        </div>
      )}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", height: "auto", display: "block" }}
        onPointerMove={handleMove}
        onPointerLeave={() => setHoverIndex(null)}
      >
        {yTickValues.map((v, i) => {
          const y = yForValue(v);
          return (
            <g key={i}>
              <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} stroke="var(--border)" strokeWidth={1} />
              <text x={padding.left - 8} y={y + 3} fontSize={10} fill="var(--text-muted)" textAnchor="end">
                {v}
              </text>
            </g>
          );
        })}

        {seriesPaths.map((sp) => (
          <path key={sp.keyword} d={sp.path} fill="none" stroke={sp.color} strokeWidth={2} />
        ))}

        {xTickIndexes.map((idx, i) => (
          <text
            key={i}
            x={xForIndex(idx)}
            y={height - 6}
            fontSize={10}
            fill="var(--text-muted)"
            textAnchor={i === 0 ? "start" : i === xTickIndexes.length - 1 ? "end" : "middle"}
          >
            {allDates[idx]}
          </text>
        ))}

        {hoverIndex !== null && (
          <>
            <line
              x1={hoverX}
              y1={padding.top}
              x2={hoverX}
              y2={padding.top + plotHeight}
              stroke="var(--text-muted)"
              strokeWidth={1}
              strokeDasharray="3,3"
            />
            {seriesPaths.map((sp) => (
              <circle
                key={sp.keyword}
                cx={hoverX}
                cy={sp.coords[hoverIndex].y}
                r={4}
                fill={sp.color}
                stroke="var(--surface)"
                strokeWidth={2}
              />
            ))}
          </>
        )}
      </svg>

      {hoverDate && (
        <div
          style={{
            position: "absolute",
            top: nonEmpty.length > 1 ? 32 : 8,
            left: tooltipOnLeft ? undefined : `${(hoverX / width) * 100}%`,
            right: tooltipOnLeft ? `${100 - (hoverX / width) * 100}%` : undefined,
            transform: "translateX(8px)",
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 12,
            boxShadow: "0 2px 8px rgba(0,0,0,0.12)",
            pointerEvents: "none",
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ color: "var(--text-muted)", marginBottom: 2 }}>{hoverDate}</div>
          {seriesMaps.map((s) => {
            const p = s.byDate.get(hoverDate);
            return (
              <div key={s.keyword} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 10, height: 2, background: s.color, display: "inline-block" }} />
                <strong>{p?.occurrences ?? 0}</strong>
                <span className="muted">
                  {nonEmpty.length > 1 ? s.keyword : "вхожд."} {p ? `· ${p.articles} ст.` : ""}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
