import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: "var(--color-navy)",
        "navy-hover": "var(--color-navy-hover)",
        accent: "var(--color-accent)",
        "accent-hover": "var(--color-accent-hover)",
        ground: "var(--color-ground)",
        surface: "var(--color-surface)",
        border: "var(--color-border)",
        ink: "var(--color-ink)",
        muted: "var(--color-muted)",
        good: "var(--color-good)",
        "good-tint": "var(--color-good-tint)",
        warning: "var(--color-warning)",
        "warning-tint": "var(--color-warning-tint)",
        critical: "var(--color-critical)",
        "critical-tint": "var(--color-critical-tint)",
        info: "var(--color-info)",
        "info-tint": "var(--color-info-tint)",
        neutral: "var(--color-neutral)",
        "neutral-tint": "var(--color-neutral-tint)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        card: "14px",
      },
      boxShadow: {
        card: "0 1px 2px rgba(15, 23, 42, 0.06), 0 4px 12px rgba(15, 23, 42, 0.06)",
      },
    },
  },
  plugins: [],
} satisfies Config;
