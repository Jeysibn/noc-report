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
  RuntimeStatus,
} from "@/types/admin";

export interface AdminService {
  listDlqStatus(): Promise<DlqQueueStatus[]>;
  requeueDlq(jobType: string): Promise<DlqActionResult>;
  purgeDlq(jobType: string): Promise<DlqActionResult>;

  /** Milestone 17 follow-up: real Admin-page wiring beyond the Queue tab —
   * Users, Roles, and Audit, the three tabs that have a real backend
   * endpoint to wire to. */
  listUsers(): Promise<AdminUser[]>;
  createUser(input: AdminUserCreate): Promise<AdminUser>;
  setUserEnabled(userId: string, enabled: boolean): Promise<AdminUser>;
  listRoles(): Promise<RoleInfo[]>;
  updateRole(roleId: string, permissions: string[]): Promise<RoleInfo>;
  listAuditLog(): Promise<AuditEntry[]>;

  /** Milestone 17 gap follow-up: Shift Configuration and Storage now have
   * real backend endpoints too. */
  listShiftDefinitions(): Promise<ShiftDefinition[]>;
  updateShiftDefinition(
    id: string,
    update: ShiftDefinitionUpdate,
  ): Promise<ShiftDefinition>;
  listStorageStatus(): Promise<StorageBucketStatus[]>;

  /** Generic worker configuration retained outside provider selection. */
  getSystemConfig(): Promise<SystemConfig>;
  updateSystemConfig(update: SystemConfigUpdate): Promise<SystemConfig>;
  getRuntimeStatus(): Promise<RuntimeStatus>;
}
