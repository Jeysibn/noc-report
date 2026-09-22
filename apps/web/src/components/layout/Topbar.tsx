import { Search, Bell } from "@/components/ui/icons";
import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { useCurrentUser, logout } from "@/lib/session";
import { useOperationalHealth } from "@/lib/operationalHealth";

/**
 * Top bar: search (Ctrl K), notification bell, theme toggle, user menu.
 * Runtime health is shown compact here;
 * full status detail lives on the Admin pages (Milestone 7). Milestone 13
 * frontend wiring replaced the mock role switcher with the real signed-in
 * user (from GET /api/v1/auth/me) and a real sign-out.
 */
export function Topbar() {
  const user = useCurrentUser();
  const health = useOperationalHealth();
  const runtimeStatus = health.data?.dependencies.ai_runtime?.status ?? "unknown";
  const runtimePill = {
    status: runtimeStatus === "healthy" ? "good" : runtimeStatus === "degraded" ? "warning" : runtimeStatus === "unavailable" ? "critical" : "neutral",
    label: runtimeStatus === "healthy" ? "AI runtime ready" : runtimeStatus === "disabled" ? "AI analysis unavailable" : runtimeStatus === "degraded" ? "AI runtime degraded" : runtimeStatus === "unavailable" ? "AI runtime unavailable" : "AI runtime unknown",
  } as const;
  const initials = user
    ? user.displayName
        .split(" ")
        .map((part) => part[0])
        .slice(0, 2)
        .join("")
        .toUpperCase()
    : "?";

  return (
    <header className="flex h-16 flex-shrink-0 items-center justify-between border-b border-border bg-surface px-6">
      <div className="flex max-w-md flex-1 items-center gap-2 rounded-lg border border-border bg-ground/60 px-3 py-2">
        <Search className="h-4 w-4 text-muted" />
        <input
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
          placeholder="Search incidents, reports, knowledge base..."
        />
        <kbd className="rounded border border-border bg-surface px-1.5 py-0.5 text-xs text-muted">
          Ctrl K
        </kbd>
      </div>

      <div className="flex items-center gap-4">
        <StatusPill status={runtimePill.status} label={runtimePill.label} />
        <button
          type="button"
          aria-label="Notifications"
          className="rounded-lg p-2 text-muted hover:bg-ground"
        >
          <Bell className="h-5 w-5" />
        </button>
        {user && (
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-navy text-sm font-semibold text-white">
              {initials}
            </div>
            <div className="hidden text-xs leading-tight sm:block">
              <p className="font-medium text-ink">{user.displayName}</p>
              <p className="text-muted">{user.roles.join(", ")}</p>
            </div>
            <Button variant="ghost" size="sm" onClick={() => logout()}>
              Sign out
            </Button>
          </div>
        )}
      </div>
    </header>
  );
}
