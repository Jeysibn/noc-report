export interface BarDatum {
  label: string;
  value: number;
}

/** Minimal bar chart drawn directly in SVG — Recharts is deliberately deferred (rule 20) until real data justifies it. */
export function BarChart({ data, color = "var(--color-accent)" }: { data: BarDatum[]; color?: string }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  const width = 480;
  const height = 180;
  const barGap = 12;
  const barWidth = (width - barGap * (data.length - 1)) / data.length;

  return (
    <svg viewBox={`0 0 ${width} ${height + 24}`} className="w-full" role="img" aria-label="Bar chart">
      {data.map((d, i) => {
        const barHeight = (d.value / max) * height;
        const x = i * (barWidth + barGap);
        const y = height - barHeight;
        return (
          <g key={d.label}>
            <rect x={x} y={y} width={barWidth} height={barHeight} rx={4} fill={color} />
            <text
              x={x + barWidth / 2}
              y={height + 16}
              textAnchor="middle"
              fontSize="10"
              fill="var(--color-muted)"
            >
              {d.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
