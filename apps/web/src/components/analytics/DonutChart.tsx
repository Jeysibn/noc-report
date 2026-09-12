export interface DonutSlice {
  label: string;
  value: number;
  color: string;
}

/** Minimal donut chart drawn directly in SVG — pairs with BarChart per the plan's "bar + donut pairing" pattern. */
export function DonutChart({ data }: { data: DonutSlice[] }) {
  const total = data.reduce((sum, d) => sum + d.value, 0) || 1;
  const radius = 60;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="flex items-center gap-6">
      <svg viewBox="0 0 160 160" className="h-40 w-40 flex-shrink-0" role="img" aria-label="Donut chart">
        <g transform="translate(80,80) rotate(-90)">
          {data.map((d) => {
            const fraction = d.value / total;
            const dash = fraction * circumference;
            const el = (
              <circle
                key={d.label}
                r={radius}
                cx={0}
                cy={0}
                fill="transparent"
                stroke={d.color}
                strokeWidth={24}
                strokeDasharray={`${dash} ${circumference - dash}`}
                strokeDashoffset={-offset}
              />
            );
            offset += dash;
            return el;
          })}
        </g>
      </svg>
      <ul className="flex flex-col gap-2 text-sm">
        {data.map((d) => (
          <li key={d.label} className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: d.color }} />
            <span className="text-muted">{d.label}</span>
            <span className="font-data font-medium">{d.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
