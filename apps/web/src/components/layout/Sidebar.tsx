import { NavLink } from "react-router-dom";
import { Home, AlertTriangle, FileText, BarChart, BookOpen, Settings } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { useCurrentRole } from "@/lib/session";
import type { Role } from "@/types/admin";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Home;
  /** Roles that can see this item. Omit to show it to every role. */
  roles?: Role[];
}

const operationsItems: NavItem[] = [
  { to: "/", label: "Dashboard", icon: Home },
  { to: "/incidents", label: "Incidents", icon: AlertTriangle },
  { to: "/reports", label: "Shift Reports", icon: FileText },
];

const referenceItems: NavItem[] = [
  { to: "/knowledge", label: "Knowledge Base", icon: BookOpen },
  // Analytics is DevOps/Admin per the master plan's Admin scope table.
  { to: "/analytics", label: "Analytics", icon: BarChart, roles: ["DevOps", "Admin"] },
];

const adminItems: NavItem[] = [{ to: "/admin", label: "Admin", icon: Settings, roles: ["Admin"] }];

function NavGroup({ label, items, role }: { label: string; items: NavItem[]; role: Role }) {
  const visible = items.filter((item) => !item.roles || item.roles.includes(role));
  if (visible.length === 0) return null;
  return (
    <div className="mb-6">
      <p className="mb-2 px-3 text-xs font-semibold uppercase tracking-wide text-white/40">
        {label}
      </p>
      <nav className="flex flex-col gap-1">
        {visible.map(({ to, label: itemLabel, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-white/70 transition-colors",
                "hover:bg-white/10 hover:text-white",
                isActive && "bg-accent text-white",
              )
            }
          >
            <Icon className="h-4 w-4" />
            {itemLabel}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

/** Command Deck sidebar: navy, icon+label items, rounded active state, grouped nav, user chip pinned at bottom. */
export function Sidebar() {
  const role = useCurrentRole();
  return (
    <aside className="flex h-screen w-64 flex-shrink-0 flex-col bg-navy px-3 py-4">
      <div className="mb-6 px-3 text-lg font-semibold text-white">NOC Report Builder</div>
      <div className="flex-1 overflow-y-auto">
        <NavGroup label="Operations" items={operationsItems} role={role} />
        <NavGroup label="Reference" items={referenceItems} role={role} />
        <NavGroup label="Admin" items={adminItems} role={role} />
      </div>
      <div className="flex items-center gap-3 rounded-lg bg-white/5 px-3 py-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-sm font-semibold text-white">
          JC
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-white">Jomel Concon</p>
          <p className="truncate text-xs text-white/50">{role}</p>
        </div>
      </div>
    </aside>
  );
}
