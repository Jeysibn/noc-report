export type Role = "NOC" | "DevOps" | "Admin";

/**
 * Real session shape, populated from GET /api/v1/auth/me (Milestone 13
 * frontend wiring). Backend RBAC is permission-string based
 * (app/deps.py's require_permission) — `roles` happens to reuse the same
 * three names as the old mock `Role` switcher (app/seed.py's
 * ROLE_PERMISSIONS), so nav gating keyed on Role keeps working unchanged;
 * anything gating a specific action should check `permissions`, not role.
 */
export interface CurrentUser {
  id: string;
  username: string;
  displayName: string;
  email: string | null;
  roles: Role[];
  permissions: string[];
}

export interface AdminUser {
  id: string;
  username: string;
  name: string;
  email: string;
  role: Role;
  status: "active" | "disabled";
  lastLogin: string;
}

export interface AdminUserCreate {
  username: string;
  displayName: string;
  email?: string;
  password: string;
  roleNames: Role[];
}

export interface AuditEntry {
  id: string;
  timestamp: string;
  actor: string;
  action: string;
  target: string;
}

/** Milestone 17 follow-up (Admin real wiring): a role plus the permission
 * strings it actually grants, per GET /api/v1/admin/roles. */
export interface RoleInfo {
  id: string;
  name: string;
  permissions: string[];
}

// Milestone 17 (DLQ controls)
export interface DlqQueueStatus {
  queue: string;
  jobType: string;
  messageCount: number;
}

export interface DlqActionResult {
  jobType: string;
  requeued: number;
  purged: number;
}

/** Milestone 17 gap follow-up (Shift Configuration real wiring). Times are
 * "HH:MM:SS" strings, matching the backend's ShiftDefinitionOut verbatim —
 * not parsed into a Date since they represent a daily recurring time, not
 * a specific instant. */
export interface ShiftDefinition {
  id: string;
  name: string;
  startTime: string;
  endTime: string;
  timezone: string;
  enabled: boolean;
}

export interface ShiftDefinitionUpdate {
  startTime?: string;
  endTime?: string;
  timezone?: string;
  enabled?: boolean;
}

/** Milestone 17 gap follow-up (Storage tab real wiring). */
export interface StorageBucketStatus {
  bucket: string;
  versioningEnabled: boolean;
  objectCount: number;
  totalBytes: number;
}

/** Milestone 17 gap follow-up (AI Configuration, "real config, live-wired
 * to the bridge" — operator's explicit scope choice). */
export interface SystemConfig {
  defaultModel: string;
  defaultEffort: string;
  jobTimeoutSeconds: number;
  maxConcurrentJobs: number;
  /** Claude CLI --max-budget-usd cap for one invocation. */
  claudeMaxBudgetUsd: number;
  updatedAt: string;
}

export interface SystemConfigUpdate {
  defaultModel?: string;
  defaultEffort?: string;
  jobTimeoutSeconds?: number;
  maxConcurrentJobs?: number;
  claudeMaxBudgetUsd?: number;
}
