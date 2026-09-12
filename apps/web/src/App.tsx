import { useEffect } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Dashboard } from "@/pages/Dashboard";
import { IncidentList } from "@/pages/IncidentList";
import { IncidentDetail } from "@/pages/IncidentDetail";
import { CreateIncident } from "@/pages/CreateIncident";
import { ShiftReport } from "@/pages/ShiftReport";
import { KnowledgeBase } from "@/pages/KnowledgeBase";
import { Analytics } from "@/pages/Analytics";
import { Admin } from "@/pages/Admin";
import { Login } from "@/pages/Login";
import {
  isSessionInitialized,
  restoreSession,
  useCurrentUser,
  useSessionInitialized,
} from "@/lib/session";

/**
 * Milestone 13 frontend wiring: everything used to sit behind AppShell
 * unconditionally, with no concept of a session. Now the whole app is
 * guarded — an unauthenticated visit to any route redirects to /login and
 * remembers where it was headed.
 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const user = useCurrentUser();
  const sessionInitialized = useSessionInitialized();
  const location = useLocation();
  if (!sessionInitialized) {
    return (
      <div className="flex h-screen items-center justify-center bg-navy text-sm text-white/70">
        Restoring session…
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

export default function App() {
  useEffect(() => {
    if (!isSessionInitialized()) void restoreSession();
  }, []);

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <AppShell>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/incidents" element={<IncidentList />} />
                <Route path="/incidents/new" element={<CreateIncident />} />
                <Route path="/incidents/:id" element={<IncidentDetail />} />
                <Route path="/reports" element={<ShiftReport />} />
                <Route path="/knowledge" element={<KnowledgeBase />} />
                <Route path="/analytics" element={<Analytics />} />
                <Route path="/admin" element={<Admin />} />
              </Routes>
            </AppShell>
          </RequireAuth>
        }
      />
    </Routes>
  );
}
