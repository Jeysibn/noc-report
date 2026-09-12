# Design System — component inventory

Command Deck tokens and primitives implemented in Milestone 1. Source of
truth for palette/type/layout: the vault's
`03 Projects/NOC Report Builder/03 Frontend/UI Phase Plan.md`.

## Tokens (`src/index.css`, `tailwind.config.ts`)

CSS custom properties on `:root` (light) and `:root[data-theme="dark"]`
(dark) — components read Tailwind color names (`bg-navy`, `text-accent`,
`bg-good-tint`, etc.), never raw hex, so a component never branches on theme.

## Primitives (`src/components/ui/`)

- `Button` — variants `primary`/`secondary`/`ghost`/`danger`, sizes `sm`/`md`
- `StatusPill` — colored pill + dot, statuses `good`/`warning`/`critical`/`info`/`neutral`
- `Card`, `CardHeader`, `CardTitle` — 14px radius, soft shadow
- `StatCard` — icon badge + value + delta or progress bar
- `Table`/`Thead`/`Tbody`/`Tr`/`Th`/`Td` — card-panel table, horizontal-scroll safe
- `Form` (`Label`, `Input`, `Textarea`, `Select`, `FormField`)
- `Modal` — centered dialog (Radix), for confirmations/short forms
- `Drawer` — right-side sliding panel (Radix), for incident quick-view
- `FileUpload` — drag/drop + click-to-browse zone, for screenshot/evidence attachment
- `icons.tsx` — hand-rolled minimal icon set (no icon library dependency yet)

## Layout (`src/components/layout/`)

- `Sidebar` — navy, grouped Operations/Reference/Admin nav, user chip
- `Topbar` — search w/ Ctrl K affordance, bridge status pill, notification bell, user avatar
- `AppShell` — composes Sidebar + Topbar + scrollable content region

## Exit criteria (master plan Milestone 1)

"Reusable UI primitives documented, no page-specific duplication" — met:
`App.tsx`'s placeholder dashboard composes only the primitives above (no
one-off styling), and this document is the durable record of what exists so
later pages reuse rather than reinvent them.
