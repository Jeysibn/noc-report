import type { AdminService } from "@/services/admin.service";
import type {
  AdminUser,
  AdminUserCreate,
  AuditEntry,
  DlqActionResult,
  DlqQueueStatus,
  RoleInfo,
  ShiftDefinition,
  ShiftDefinitionUpdate,
  StorageBucketStatus,
  SystemConfig,
  SystemConfigUpdate,
} from "@/types/admin";
import { mockUsers, mockAuditLog } from "@/mock/fixtures/admin";

const JOB_TYPES = ["log_triage", "daily_report"];

const MOCK_ROLES: RoleInfo[] = [
  {
    id: "role-noc",
    name: "NOC",
    permissions: [
      "incident.read",
      "incident.create",
      "incident.update",
      "incident.analysis.execute",
    ],
  },
  {
    id: "role-devops",
    name: "DevOps",
    permissions: [
      "incident.read",
      "incident.create",
      "incident.update",
      "analytics.read",
    ],
  },
  {
    id: "role-admin",
    name: "Admin",
    permissions: [
      "user.manage",
      "role.manage",
      "audit.read",
      "system.configure",
    ],
  },
];

const MOCK_SHIFT_DEFINITIONS: ShiftDefinition[] = [
  {
    id: "shift-day",
    name: "Day",
    startTime: "08:00:00",
    endTime: "16:00:00",
    timezone: "Asia/Manila",
    enabled: true,
  },
  {
    id: "shift-swing",
    name: "Swing",
    startTime: "16:00:00",
    endTime: "00:00:00",
    timezone: "Asia/Manila",
    enabled: true,
  },
  {
    id: "shift-night",
    name: "Night",
    startTime: "00:00:00",
    endTime: "08:00:00",
    timezone: "Asia/Manila",
    enabled: true,
  },
];

const MOCK_STORAGE_STATUS: StorageBucketStatus[] = [
  {
    bucket: "noc-evidence",
    versioningEnabled: true,
    objectCount: 42,
    totalBytes: 15_728_640,
  },
  {
    bucket: "noc-reports",
    versioningEnabled: true,
    objectCount: 18,
    totalBytes: 4_194_304,
  },
  {
    bucket: "noc-job-artifacts",
    versioningEnabled: false,
    objectCount: 6,
    totalBytes: 262_144,
  },
];

const MOCK_SYSTEM_CONFIG: SystemConfig = {
  defaultModel: "claude-sonnet-5",
  defaultEffort: "medium",
  jobTimeoutSeconds: 300,
  maxConcurrentJobs: 1,
  updatedAt: new Date().toISOString(),
};

/** Client-only test double — mirrors the real service's semantics closely
 * enough for component tests: an in-memory copy of the fixture users so
 * enable/disable actually mutates what listUsers next returns. */
export class MockAdminService implements AdminService {
  private counts: Record<string, number> = { log_triage: 2, daily_report: 0 };
  private users: AdminUser[] = mockUsers.map((u) => ({ ...u }));
  private shiftDefinitions: ShiftDefinition[] = MOCK_SHIFT_DEFINITIONS.map(
    (d) => ({ ...d }),
  );
  private systemConfig: SystemConfig = { ...MOCK_SYSTEM_CONFIG };

  async listDlqStatus(): Promise<DlqQueueStatus[]> {
    return JOB_TYPES.map((jobType) => ({
      queue: `noc.jobs.${jobType.replace(/_/g, "-")}.dlq`,
      jobType,
      messageCount: this.counts[jobType] ?? 0,
    }));
  }

  async requeueDlq(jobType: string): Promise<DlqActionResult> {
    if ((this.counts[jobType] ?? 0) === 0) {
      return { jobType, requeued: 0, purged: 0 };
    }
    this.counts[jobType] -= 1;
    return { jobType, requeued: 1, purged: 0 };
  }

  async purgeDlq(jobType: string): Promise<DlqActionResult> {
    const purged = this.counts[jobType] ?? 0;
    this.counts[jobType] = 0;
    return { jobType, requeued: 0, purged };
  }

  async listUsers(): Promise<AdminUser[]> {
    return this.users.map((u) => ({ ...u }));
  }

  async createUser(input: AdminUserCreate): Promise<AdminUser> {
    if (this.users.some((user) => user.username === input.username))
      throw new Error("Username already exists");
    const user: AdminUser = {
      id: `u${this.users.length + 1}`,
      username: input.username,
      name: input.displayName,
      email: input.email ?? "",
      role: input.roleNames[0] ?? "NOC",
      status: "active",
      lastLogin: "",
    };
    this.users.push(user);
    return { ...user };
  }

  async setUserEnabled(userId: string, enabled: boolean): Promise<AdminUser> {
    const user = this.users.find((u) => u.id === userId);
    if (!user) throw new Error("Unknown user");
    user.status = enabled ? "active" : "disabled";
    return { ...user };
  }

  async listRoles(): Promise<RoleInfo[]> {
    return MOCK_ROLES.map((r) => ({ ...r }));
  }

  async updateRole(roleId: string, permissions: string[]): Promise<RoleInfo> {
    const role = MOCK_ROLES.find((item) => item.id === roleId);
    if (!role || role.name === "Admin")
      throw new Error("Role cannot be edited");
    role.permissions = [...permissions];
    return { ...role, permissions: [...role.permissions] };
  }

  async listAuditLog(): Promise<AuditEntry[]> {
    return mockAuditLog.map((e) => ({ ...e }));
  }

  async listShiftDefinitions(): Promise<ShiftDefinition[]> {
    return this.shiftDefinitions.map((d) => ({ ...d }));
  }

  async updateShiftDefinition(
    id: string,
    update: ShiftDefinitionUpdate,
  ): Promise<ShiftDefinition> {
    const def = this.shiftDefinitions.find((d) => d.id === id);
    if (!def) throw new Error("Unknown shift definition");
    Object.assign(def, update);
    return { ...def };
  }

  async listStorageStatus(): Promise<StorageBucketStatus[]> {
    return MOCK_STORAGE_STATUS.map((b) => ({ ...b }));
  }

  async getSystemConfig(): Promise<SystemConfig> {
    return { ...this.systemConfig };
  }

  async updateSystemConfig(update: SystemConfigUpdate): Promise<SystemConfig> {
    this.systemConfig = {
      ...this.systemConfig,
      ...Object.fromEntries(
        Object.entries(update).filter(([, v]) => v !== undefined),
      ),
      updatedAt: new Date().toISOString(),
    };
    return { ...this.systemConfig };
  }
}
