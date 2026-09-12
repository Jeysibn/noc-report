import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

/** Top-level app shell — sidebar + topbar + scrollable content region. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen bg-ground">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
