import type { SVGProps } from "react";

/**
 * Minimal inline icon set for Milestone 1 — deliberately hand-rolled instead
 * of adding an icon library dependency this early; revisit if the icon
 * surface grows past the app-shell/nav/stat-card needs.
 */
type IconProps = SVGProps<SVGSVGElement>;

const base = (props: IconProps) => ({
  width: 20,
  height: 20,
  viewBox: "0 0 20 20",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  ...props,
});

export function X(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M5 5l10 10M15 5L5 15" />
    </svg>
  );
}

export function Home(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M3 9.5L10 3l7 6.5" />
      <path d="M5 8.5V17h10V8.5" />
    </svg>
  );
}

export function AlertTriangle(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M10 3l8.5 14.5H1.5L10 3z" />
      <path d="M10 8v4M10 14.5h.01" />
    </svg>
  );
}

export function FileText(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M5 2.5h7l3 3V17.5H5z" />
      <path d="M8 9h5M8 12h5M8 15h3" />
    </svg>
  );
}

export function BarChart(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 17V10M10 17V4M16 17v-6" />
    </svg>
  );
}

export function BookOpen(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M10 5.5C8.5 4 6 3.5 3.5 4v11.5c2.5-.5 5 0 6.5 1.5 1.5-1.5 4-2 6.5-1.5V4C14 3.5 11.5 4 10 5.5z" />
    </svg>
  );
}

export function Settings(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="10" cy="10" r="2.5" />
      <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.6 4.6l1.4 1.4M14 14l1.4 1.4M4.6 15.4L6 14M14 6l1.4-1.4" />
    </svg>
  );
}

export function Search(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="8.5" cy="8.5" r="5.5" />
      <path d="M17 17l-4-4" />
    </svg>
  );
}

export function Bell(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M5 8a5 5 0 0110 0c0 4 1.5 5 1.5 5h-13S5 12 5 8z" />
      <path d="M8.5 16a1.5 1.5 0 003 0" />
    </svg>
  );
}

export function Upload(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M10 13V3M6.5 6.5L10 3l3.5 3.5" />
      <path d="M4 14v2.5h12V14" />
    </svg>
  );
}
