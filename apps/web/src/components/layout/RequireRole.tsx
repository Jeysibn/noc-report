import type { ReactNode } from "react";
import { useCurrentRole } from "@/lib/session";
import type { Role } from "@/types/admin";
import { Card } from "@/components/ui/Card";

/**
 * Guards a page against direct navigation by a role that shouldn't see it —
 * the Sidebar hides the nav item, but the route itself must also refuse
 * the page, since URLs are not access control. `role` now comes from the
 * real session (GET /api/v1/auth/me, see lib/session.ts) — Milestone 13
 * frontend wiring replaced the mock switcher this used to read from. This
 * is the UI-side half of Flow D (RBAC) in the plan's Playwright test list;
 * real server-side enforcement is app/deps.py's require_permission, this
 * component only prevents a confusing UI state, it grants nothing.
 */
export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const role = useCurrentRole();
  if (!roles.includes(role)) {
    return (
      <Card>
        <p className="text-sm text-muted">
          This page requires the {roles.join(" or ")} role. You are currently viewing as {role}.
        </p>
      </Card>
    );
  }
  return <>{children}</>;
}
