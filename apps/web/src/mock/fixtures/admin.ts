import type { AdminUser, AuditEntry } from "@/types/admin";

export const mockUsers: AdminUser[] = [
  {
    id: "u1",
    username: "jomel",
    name: "Jomel Concon",
    email: "jomel.concon@bbwave.ph",
    role: "Admin",
    status: "active",
    lastLogin: "2026-09-09T08:12:00Z",
  },
  {
    id: "u2",
    username: "ana",
    name: "Ana Reyes",
    email: "ana.reyes@bbwave.ph",
    role: "NOC",
    status: "active",
    lastLogin: "2026-09-09T00:41:00Z",
  },
  {
    id: "u3",
    username: "miguel",
    name: "Miguel Santos",
    email: "miguel.santos@bbwave.ph",
    role: "DevOps",
    status: "active",
    lastLogin: "2026-09-08T15:03:00Z",
  },
  {
    id: "u4",
    username: "liza",
    name: "Liza Torres",
    email: "liza.torres@bbwave.ph",
    role: "NOC",
    status: "disabled",
    lastLogin: "2026-08-30T22:10:00Z",
  },
];

export const mockAuditLog: AuditEntry[] = [
  {
    id: "a1",
    timestamp: "2026-09-09T08:10:00Z",
    actor: "Jomel Concon",
    action: "Generated shift report",
    target: "Version 3, night shift",
  },
  {
    id: "a2",
    timestamp: "2026-09-09T07:55:00Z",
    actor: "Ana Reyes",
    action: "Requested log analysis",
    target: "INC-1042",
  },
  {
    id: "a3",
    timestamp: "2026-09-09T06:20:00Z",
    actor: "Miguel Santos",
    action: "Changed role",
    target: "Liza Torres → NOC",
  },
  {
    id: "a4",
    timestamp: "2026-09-08T21:45:00Z",
    actor: "Jomel Concon",
    action: "Disabled user",
    target: "Liza Torres",
  },
  {
    id: "a5",
    timestamp: "2026-09-08T18:02:00Z",
    actor: "Ana Reyes",
    action: "Created incident",
    target: "INC-1038",
  },
];
