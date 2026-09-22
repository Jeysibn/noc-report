import type { AdminService } from "@/services/admin.service";
import type {
  AdminUser,
  AdminUserCreate,
  AuditEntry,
  DlqActionResult,
  DlqQueueStatus,
  Role,
  RoleInfo,
  ShiftDefinition,
  ShiftDefinitionUpdate,
  StorageBucketStatus,
  SystemConfig,
  SystemConfigUpdate,
} from "@/types/admin";
import { httpRequest } from "@/lib/http";

// Mirrors app/core/queue.py's JOB_TYPES — the backend's DLQ status
// response only carries the queue name (e.g. "noc.jobs.log-triage.dlq"),
// not the job_type the requeue/purge endpoints need in their URL, so it's
// derived here from the same slug convention (`job_type.replace("_","-")`).
const JOB_TYPES = ["log_triage", "daily_report"];

function jobTypeForQueue(queue: string): string {
  const match = JOB_TYPES.find(
    (jt) => queue === `noc.jobs.${jt.replace(/_/g, "-")}.dlq`,
  );
  return match ?? queue;
}

interface RawDlqQueueStatus {
  queue: string;
  message_count: number;
}

interface RawDlqActionResult {
  job_type: string;
  requeued: number;
  purged: number;
}

function toActionResult(raw: RawDlqActionResult): DlqActionResult {
  return { jobType: raw.job_type, requeued: raw.requeued, purged: raw.purged };
}

interface RawUserOut {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  enabled: boolean;
  last_login_at: string | null;
  roles: string[];
  permissions: string[];
}

interface RawRoleOut {
  id: string;
  name: string;
  permissions: string[];
}

interface RawAuditLogOut {
  id: string;
  actor_user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

interface RawShiftDefinitionOut {
  id: string;
  name: string;
  start_time: string;
  end_time: string;
  timezone: string;
  enabled: boolean;
}

function toShiftDefinition(raw: RawShiftDefinitionOut): ShiftDefinition {
  return {
    id: raw.id,
    name: raw.name,
    startTime: raw.start_time,
    endTime: raw.end_time,
    timezone: raw.timezone,
    enabled: raw.enabled,
  };
}

interface RawStorageBucketStatusOut {
  bucket: string;
  versioning_enabled: boolean;
  object_count: number;
  total_bytes: number;
}

interface RawSystemConfigOut {
  id: string;
  job_timeout_seconds: number;
  max_concurrent_jobs: number;
  updated_at: string;
}

function toSystemConfig(raw: RawSystemConfigOut): SystemConfig {
  return {
    jobTimeoutSeconds: raw.job_timeout_seconds,
    maxConcurrentJobs: raw.max_concurrent_jobs,
    updatedAt: raw.updated_at,
  };
}

function toAdminUser(raw: RawUserOut): AdminUser {
  return {
    id: raw.id,
    username: raw.username,
    name: raw.display_name,
    email: raw.email ?? "",
    // UserOut carries multiple roles (RBAC is permission-based), but the
    // Admin Users table predates that and shows one — same precedent as
    // CurrentUser's role/permission split in types/admin.ts.
    role: (raw.roles[0] as Role | undefined) ?? "NOC",
    status: raw.enabled ? "active" : "disabled",
    lastLogin: raw.last_login_at ?? "",
  };
}

export class ApiAdminService implements AdminService {
  async listDlqStatus(): Promise<DlqQueueStatus[]> {
    const raw = await httpRequest<RawDlqQueueStatus[]>("/api/v1/admin/dlq");
    return raw.map((row) => ({
      queue: row.queue,
      jobType: jobTypeForQueue(row.queue),
      messageCount: row.message_count,
    }));
  }

  async requeueDlq(jobType: string): Promise<DlqActionResult> {
    const raw = await httpRequest<RawDlqActionResult>(
      `/api/v1/admin/dlq/${jobType}/requeue`,
      {
        method: "POST",
      },
    );
    return toActionResult(raw);
  }

  async purgeDlq(jobType: string): Promise<DlqActionResult> {
    const raw = await httpRequest<RawDlqActionResult>(
      `/api/v1/admin/dlq/${jobType}/purge`,
      {
        method: "POST",
      },
    );
    return toActionResult(raw);
  }

  async listUsers(): Promise<AdminUser[]> {
    const raw = await httpRequest<RawUserOut[]>("/api/v1/admin/users");
    return raw.map(toAdminUser);
  }

  async createUser(input: AdminUserCreate): Promise<AdminUser> {
    const raw = await httpRequest<RawUserOut>("/api/v1/admin/users", {
      method: "POST",
      body: {
        username: input.username,
        display_name: input.displayName,
        email: input.email || null,
        password: input.password,
        role_names: input.roleNames,
      },
    });
    return toAdminUser(raw);
  }

  async setUserEnabled(userId: string, enabled: boolean): Promise<AdminUser> {
    const raw = await httpRequest<RawUserOut>(`/api/v1/admin/users/${userId}`, {
      method: "PATCH",
      body: { enabled },
    });
    return toAdminUser(raw);
  }

  async listRoles(): Promise<RoleInfo[]> {
    const raw = await httpRequest<RawRoleOut[]>("/api/v1/admin/roles");
    return raw.map((r) => ({
      id: r.id,
      name: r.name,
      permissions: r.permissions,
    }));
  }

  async updateRole(roleId: string, permissions: string[]): Promise<RoleInfo> {
    const raw = await httpRequest<RawRoleOut>(`/api/v1/admin/roles/${roleId}`, {
      method: "PATCH",
      body: { permissions },
    });
    return { id: raw.id, name: raw.name, permissions: raw.permissions };
  }

  async listAuditLog(): Promise<AuditEntry[]> {
    // The audit log only carries actor_user_id, not a display name — join
    // against the users list client-side rather than fabricate a name.
    // A failed login (actor_user_id null) is instead keyed by the
    // attempted username, which auth.py records as resource_id.
    const [entries, users] = await Promise.all([
      httpRequest<RawAuditLogOut[]>("/api/v1/admin/audit"),
      httpRequest<RawUserOut[]>("/api/v1/admin/users"),
    ]);
    const nameById = new Map(users.map((u) => [u.id, u.display_name]));
    return entries.map((e) => ({
      id: e.id,
      timestamp: e.created_at,
      actor: e.actor_user_id
        ? (nameById.get(e.actor_user_id) ?? e.actor_user_id)
        : e.resource_id,
      action: e.action,
      target: `${e.resource_type}:${e.resource_id}`,
    }));
  }

  async listShiftDefinitions(): Promise<ShiftDefinition[]> {
    const raw = await httpRequest<RawShiftDefinitionOut[]>(
      "/api/v1/admin/shift-definitions",
    );
    return raw.map(toShiftDefinition);
  }

  async updateShiftDefinition(
    id: string,
    update: ShiftDefinitionUpdate,
  ): Promise<ShiftDefinition> {
    const raw = await httpRequest<RawShiftDefinitionOut>(
      `/api/v1/admin/shift-definitions/${id}`,
      {
        method: "PATCH",
        body: {
          start_time: update.startTime,
          end_time: update.endTime,
          timezone: update.timezone,
          enabled: update.enabled,
        },
      },
    );
    return toShiftDefinition(raw);
  }

  async listStorageStatus(): Promise<StorageBucketStatus[]> {
    const raw = await httpRequest<RawStorageBucketStatusOut[]>(
      "/api/v1/admin/storage",
    );
    return raw.map((b) => ({
      bucket: b.bucket,
      versioningEnabled: b.versioning_enabled,
      objectCount: b.object_count,
      totalBytes: b.total_bytes,
    }));
  }

  async getSystemConfig(): Promise<SystemConfig> {
    const raw = await httpRequest<RawSystemConfigOut>(
      "/api/v1/admin/system-config",
    );
    return toSystemConfig(raw);
  }

  async updateSystemConfig(update: SystemConfigUpdate): Promise<SystemConfig> {
    const raw = await httpRequest<RawSystemConfigOut>(
      "/api/v1/admin/system-config",
      {
        method: "PATCH",
        body: {
          job_timeout_seconds: update.jobTimeoutSeconds,
          max_concurrent_jobs: update.maxConcurrentJobs,
        },
      },
    );
    return toSystemConfig(raw);
  }
}
