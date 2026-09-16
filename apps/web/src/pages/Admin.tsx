import { useCallback, useEffect, useState } from "react";
import { Card, CardTitle } from "@/components/ui/Card";
import { RequireRole } from "@/components/layout/RequireRole";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Table, Thead, Tbody, Tr, Th, Td } from "@/components/ui/Table";
import { StatusPill } from "@/components/ui/StatusPill";
import { FormField, Input, Select } from "@/components/ui/Form";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { formatTime } from "@/lib/incidentStatus";
import { hasPermission } from "@/lib/session";
import { adminService } from "@/services";
import { ApiError } from "@/lib/http";
import type {
  AdminUser,
  AuditEntry,
  DlqQueueStatus,
  RoleInfo,
  ShiftDefinition,
  StorageBucketStatus,
  SystemConfig,
} from "@/types/admin";

const roleDescriptions: Record<string, string> = {
  NOC: "Create/view incidents, request log analysis, view own shift reports.",
  DevOps: "All NOC permissions + analytics access.",
  Admin:
    "All DevOps permissions + user/role management, system configuration, audit log.",
};

/**
 * Admin: Users, Roles, Shift Configuration, AI/Claude Configuration,
 * Storage, Queue, Audit — grouped as tabs on one page per the plan's Admin
 * scope table. Users/Roles/Shift Configuration/Storage/Queue/Audit are all
 * wired to real backend endpoints (Milestones 8/11/17), including AI
 * Configuration — a real, single-row system_config table, live-wired to
 * the bridge (it re-reads the row on every job dispatch).
 */
export function Admin() {
  const [dlqStatus, setDlqStatus] = useState<DlqQueueStatus[] | null>(null);
  const [dlqError, setDlqError] = useState<string | null>(null);
  const [dlqActionPending, setDlqActionPending] = useState<string | null>(null);
  const canManageDlq = hasPermission("system.configure");
  const canManageUsers = hasPermission("user.manage");
  const canManageRoles = hasPermission("role.manage");

  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [userActionPending, setUserActionPending] = useState<string | null>(
    null,
  );
  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [createUserSaving, setCreateUserSaving] = useState(false);
  const [createUserError, setCreateUserError] = useState<string | null>(null);
  const [newUser, setNewUser] = useState({
    username: "",
    displayName: "",
    email: "",
    password: "",
    role: "NOC" as "NOC" | "DevOps" | "Admin",
  });
  const [roles, setRoles] = useState<RoleInfo[] | null>(null);
  const [editingRole, setEditingRole] = useState<RoleInfo | null>(null);
  const [rolePermissions, setRolePermissions] = useState<string[]>([]);
  const [roleSaving, setRoleSaving] = useState(false);
  const [roleError, setRoleError] = useState<string | null>(null);
  const [auditLog, setAuditLog] = useState<AuditEntry[] | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);
  const canManageShifts = hasPermission("shift.manage");

  const [shiftDefinitions, setShiftDefinitions] = useState<
    ShiftDefinition[] | null
  >(null);
  const [shiftError, setShiftError] = useState<string | null>(null);
  const [shiftActionPending, setShiftActionPending] = useState<string | null>(
    null,
  );
  const [storageStatus, setStorageStatus] = useState<
    StorageBucketStatus[] | null
  >(null);
  const [storageError, setStorageError] = useState<string | null>(null);

  const canManageSystemConfig = hasPermission("system.configure");
  const [systemConfig, setSystemConfig] = useState<SystemConfig | null>(null);
  const [systemConfigDraft, setSystemConfigDraft] = useState({
    defaultModel: "claude-sonnet-5",
    defaultEffort: "medium",
    jobTimeoutSeconds: 300,
    maxConcurrentJobs: 1,
    claudeMaxBudgetUsd: 0.5,
  });
  const [systemConfigError, setSystemConfigError] = useState<string | null>(
    null,
  );
  const [systemConfigSaving, setSystemConfigSaving] = useState(false);

  const loadUsers = useCallback(() => {
    adminService
      .listUsers()
      .then((rows) => {
        setUsers(rows);
        setUsersError(null);
      })
      .catch(() => setUsersError("Could not load users."));
  }, []);

  const loadShiftDefinitions = useCallback(() => {
    adminService
      .listShiftDefinitions()
      .then((rows) => {
        setShiftDefinitions(rows);
        setShiftError(null);
      })
      .catch(() => setShiftError("Could not load shift definitions."));
  }, []);

  useEffect(() => {
    loadUsers();
    adminService
      .listRoles()
      .then(setRoles)
      .catch(() => setRoles([]));
    adminService
      .listAuditLog()
      .then((rows) => {
        setAuditLog(rows);
        setAuditError(null);
      })
      .catch(() => setAuditError("Could not load audit log."));
    loadShiftDefinitions();
    adminService
      .listStorageStatus()
      .then((rows) => {
        setStorageStatus(rows);
        setStorageError(null);
      })
      .catch(() => setStorageError("Could not load storage status."));
    adminService
      .getSystemConfig()
      .then((config) => {
        setSystemConfig(config);
        setSystemConfigDraft({
          defaultModel: config.defaultModel,
          defaultEffort: config.defaultEffort,
          jobTimeoutSeconds: config.jobTimeoutSeconds,
          maxConcurrentJobs: config.maxConcurrentJobs,
          claudeMaxBudgetUsd: config.claudeMaxBudgetUsd,
        });
        setSystemConfigError(null);
      })
      .catch(() => setSystemConfigError("Could not load AI configuration."));
  }, [loadUsers, loadShiftDefinitions]);

  async function handleSaveSystemConfig() {
    setSystemConfigSaving(true);
    try {
      const updated = await adminService.updateSystemConfig(systemConfigDraft);
      setSystemConfig(updated);
      setSystemConfigError(null);
    } catch {
      setSystemConfigError("Could not save AI configuration.");
    } finally {
      setSystemConfigSaving(false);
    }
  }

  async function handleToggleShift(def: ShiftDefinition) {
    setShiftActionPending(def.id);
    try {
      await adminService.updateShiftDefinition(def.id, {
        enabled: !def.enabled,
      });
      loadShiftDefinitions();
    } catch {
      setShiftError("Could not update shift definition.");
    } finally {
      setShiftActionPending(null);
    }
  }

  async function handleToggleUser(user: AdminUser) {
    setUserActionPending(user.id);
    try {
      await adminService.setUserEnabled(user.id, user.status !== "active");
      loadUsers();
    } catch {
      setUsersError("Could not update user.");
    } finally {
      setUserActionPending(null);
    }
  }

  async function handleSaveRole() {
    if (!editingRole) return;
    setRoleSaving(true);
    setRoleError(null);
    try {
      const updated = await adminService.updateRole(
        editingRole.id,
        rolePermissions,
      );
      setRoles(
        (current) =>
          current?.map((role) => (role.id === updated.id ? updated : role)) ??
          null,
      );
      setEditingRole(null);
    } catch (error) {
      setRoleError(
        error instanceof ApiError ? error.message : "Could not update role.",
      );
    } finally {
      setRoleSaving(false);
    }
  }

  async function handleCreateUser(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateUserSaving(true);
    setCreateUserError(null);
    try {
      await adminService.createUser({
        username: newUser.username.trim(),
        displayName: newUser.displayName.trim(),
        email: newUser.email.trim() || undefined,
        password: newUser.password,
        roleNames: [newUser.role],
      });
      setNewUser({
        username: "",
        displayName: "",
        email: "",
        password: "",
        role: "NOC",
      });
      setCreateUserOpen(false);
      loadUsers();
    } catch (error) {
      setCreateUserError(
        error instanceof ApiError ? error.message : "Could not create user.",
      );
    } finally {
      setCreateUserSaving(false);
    }
  }

  const loadDlqStatus = useCallback(() => {
    adminService
      .listDlqStatus()
      .then((rows) => {
        setDlqStatus(rows);
        setDlqError(null);
      })
      .catch(() => {
        setDlqError("Could not load queue status.");
      });
  }, []);

  useEffect(() => {
    loadDlqStatus();
  }, [loadDlqStatus]);

  async function handleRequeue(jobType: string) {
    setDlqActionPending(`${jobType}:requeue`);
    try {
      await adminService.requeueDlq(jobType);
      loadDlqStatus();
    } catch {
      setDlqError("Requeue failed.");
    } finally {
      setDlqActionPending(null);
    }
  }

  async function handlePurge(jobType: string) {
    setDlqActionPending(`${jobType}:purge`);
    try {
      await adminService.purgeDlq(jobType);
      loadDlqStatus();
    } catch {
      setDlqError("Purge failed.");
    } finally {
      setDlqActionPending(null);
    }
  }

  return (
    <RequireRole roles={["Admin"]}>
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-xl font-semibold">Admin</h1>
          <p className="text-sm text-muted">
            Users, roles, configuration, and system status.
          </p>
        </div>

        <Tabs defaultValue="users">
          <TabsList>
            <TabsTrigger value="users">Users</TabsTrigger>
            <TabsTrigger value="roles">Roles</TabsTrigger>
            <TabsTrigger value="shifts">Shift Configuration</TabsTrigger>
            <TabsTrigger value="ai">AI Configuration</TabsTrigger>
            <TabsTrigger value="storage">Storage</TabsTrigger>
            <TabsTrigger value="queue">Queue</TabsTrigger>
            <TabsTrigger value="audit">Audit</TabsTrigger>
          </TabsList>

          <TabsContent value="users">
            <Card>
              <div className="mb-4 flex items-center justify-between">
                <CardTitle>Users</CardTitle>
                {canManageUsers && (
                  <Button onClick={() => setCreateUserOpen(true)}>
                    Add user
                  </Button>
                )}
              </div>
              {usersError && (
                <p className="text-sm text-danger" role="alert">
                  {usersError}
                </p>
              )}
              {users === null && !usersError && (
                <p className="text-sm text-muted">Loading users…</p>
              )}
              {users !== null && (
                <Table>
                  <Thead>
                    <Tr>
                      <Th>Name</Th>
                      <Th>Email</Th>
                      <Th>Role</Th>
                      <Th>Status</Th>
                      <Th>Last login</Th>
                      {canManageUsers && <Th />}
                    </Tr>
                  </Thead>
                  <Tbody>
                    {users.map((u) => (
                      <Tr key={u.id}>
                        <Td className="font-medium">{u.name}</Td>
                        <Td className="text-muted">{u.email}</Td>
                        <Td>{u.role}</Td>
                        <Td>
                          <StatusPill
                            status={u.status === "active" ? "good" : "neutral"}
                            label={
                              u.status === "active" ? "Active" : "Disabled"
                            }
                          />
                        </Td>
                        <Td className="font-data">
                          {u.lastLogin ? formatTime(u.lastLogin) : "Never"}
                        </Td>
                        {canManageUsers && (
                          <Td>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={userActionPending !== null}
                              onClick={() => handleToggleUser(u)}
                            >
                              {userActionPending === u.id
                                ? "Saving…"
                                : u.status === "active"
                                  ? "Disable"
                                  : "Enable"}
                            </Button>
                          </Td>
                        )}
                      </Tr>
                    ))}
                  </Tbody>
                </Table>
              )}
            </Card>
            <Modal
              open={createUserOpen}
              onOpenChange={setCreateUserOpen}
              title="Add user"
            >
              <form className="flex flex-col gap-4" onSubmit={handleCreateUser}>
                <FormField label="Username" htmlFor="new-user-username">
                  <Input
                    id="new-user-username"
                    required
                    autoComplete="off"
                    value={newUser.username}
                    onChange={(event) =>
                      setNewUser((user) => ({
                        ...user,
                        username: event.target.value,
                      }))
                    }
                  />
                </FormField>
                <FormField label="Display name" htmlFor="new-user-display-name">
                  <Input
                    id="new-user-display-name"
                    required
                    value={newUser.displayName}
                    onChange={(event) =>
                      setNewUser((user) => ({
                        ...user,
                        displayName: event.target.value,
                      }))
                    }
                  />
                </FormField>
                <FormField label="Email" htmlFor="new-user-email">
                  <Input
                    id="new-user-email"
                    type="email"
                    value={newUser.email}
                    onChange={(event) =>
                      setNewUser((user) => ({
                        ...user,
                        email: event.target.value,
                      }))
                    }
                  />
                </FormField>
                <FormField
                  label="Temporary password"
                  htmlFor="new-user-password"
                >
                  <Input
                    id="new-user-password"
                    type="password"
                    required
                    minLength={8}
                    autoComplete="new-password"
                    value={newUser.password}
                    onChange={(event) =>
                      setNewUser((user) => ({
                        ...user,
                        password: event.target.value,
                      }))
                    }
                  />
                </FormField>
                <FormField label="Role" htmlFor="new-user-role">
                  <Select
                    id="new-user-role"
                    value={newUser.role}
                    onChange={(event) =>
                      setNewUser((user) => ({
                        ...user,
                        role: event.target.value as typeof user.role,
                      }))
                    }
                  >
                    <option value="NOC">NOC</option>
                    <option value="DevOps">DevOps</option>
                    <option value="Admin">Admin</option>
                  </Select>
                </FormField>
                {createUserError && (
                  <p className="text-sm text-danger" role="alert">
                    {createUserError}
                  </p>
                )}
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => setCreateUserOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={createUserSaving}>
                    {createUserSaving ? "Creating…" : "Create user"}
                  </Button>
                </div>
              </form>
            </Modal>
          </TabsContent>

          <TabsContent value="roles">
            <Card>
              <CardTitle>Roles</CardTitle>
              {roles === null && (
                <p className="mt-4 text-sm text-muted">Loading roles…</p>
              )}
              {roles !== null && (
                <ul className="mt-4 flex flex-col gap-4">
                  {roles.map((r) => (
                    <li
                      key={r.name}
                      className="border-t border-border pt-4 first:border-t-0 first:pt-0"
                    >
                      <div className="flex items-center justify-between gap-4">
                        <p className="font-medium">{r.name}</p>
                        {canManageRoles && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={r.name === "Admin"}
                            title={
                              r.name === "Admin"
                                ? "The Administrator role is protected"
                                : undefined
                            }
                            onClick={() => {
                              setEditingRole(r);
                              setRolePermissions([...r.permissions]);
                              setRoleError(null);
                            }}
                          >
                            {r.name === "Admin"
                              ? "Protected"
                              : "Edit permissions"}
                          </Button>
                        )}
                      </div>
                      <p className="text-sm text-muted">
                        {roleDescriptions[r.name] ?? "Custom role."}
                      </p>
                      {r.permissions.length > 0 && (
                        <p className="mt-1 font-data text-xs text-muted">
                          {r.permissions.join(", ")}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Modal
              open={editingRole !== null}
              onOpenChange={(open) => !open && setEditingRole(null)}
              title={`Edit ${editingRole?.name ?? "role"} permissions`}
            >
              <div className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto">
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {(
                    roles?.find((role) => role.name === "Admin")?.permissions ??
                    []
                  ).map((permission) => (
                    <label
                      key={permission}
                      className="flex items-center gap-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={rolePermissions.includes(permission)}
                        onChange={(event) =>
                          setRolePermissions((current) =>
                            event.target.checked
                              ? [...current, permission]
                              : current.filter((item) => item !== permission),
                          )
                        }
                      />
                      <span className="font-data text-xs">{permission}</span>
                    </label>
                  ))}
                </div>
                {roleError && (
                  <p className="text-sm text-danger" role="alert">
                    {roleError}
                  </p>
                )}
                <div className="flex justify-end gap-2 border-t border-border pt-4">
                  <Button
                    variant="secondary"
                    onClick={() => setEditingRole(null)}
                  >
                    Cancel
                  </Button>
                  <Button onClick={handleSaveRole} disabled={roleSaving}>
                    {roleSaving ? "Saving…" : "Save permissions"}
                  </Button>
                </div>
              </div>
            </Modal>
          </TabsContent>

          <TabsContent value="shifts">
            <Card>
              <CardTitle>Shift Configuration</CardTitle>
              {shiftError && (
                <p className="mt-4 text-sm text-danger" role="alert">
                  {shiftError}
                </p>
              )}
              {shiftDefinitions === null && !shiftError && (
                <p className="mt-4 text-sm text-muted">
                  Loading shift definitions…
                </p>
              )}
              {shiftDefinitions !== null && (
                <Table>
                  <Thead>
                    <Tr>
                      <Th>Name</Th>
                      <Th>Start</Th>
                      <Th>End</Th>
                      <Th>Timezone</Th>
                      <Th>Status</Th>
                      {canManageShifts && <Th />}
                    </Tr>
                  </Thead>
                  <Tbody>
                    {shiftDefinitions.map((d) => (
                      <Tr key={d.id}>
                        <Td className="font-medium">{d.name}</Td>
                        <Td className="font-data">{d.startTime.slice(0, 5)}</Td>
                        <Td className="font-data">{d.endTime.slice(0, 5)}</Td>
                        <Td className="text-muted">{d.timezone}</Td>
                        <Td>
                          <StatusPill
                            status={d.enabled ? "good" : "neutral"}
                            label={d.enabled ? "Enabled" : "Disabled"}
                          />
                        </Td>
                        {canManageShifts && (
                          <Td>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={shiftActionPending !== null}
                              onClick={() => handleToggleShift(d)}
                            >
                              {shiftActionPending === d.id
                                ? "Saving…"
                                : d.enabled
                                  ? "Disable"
                                  : "Enable"}
                            </Button>
                          </Td>
                        )}
                      </Tr>
                    ))}
                  </Tbody>
                </Table>
              )}
            </Card>
          </TabsContent>

          <TabsContent value="ai">
            <Card className="flex flex-col gap-4">
              <CardTitle>AI / Claude Configuration</CardTitle>
              {systemConfigError && (
                <p className="text-sm text-danger" role="alert">
                  {systemConfigError}
                </p>
              )}
              {systemConfig === null && !systemConfigError && (
                <p className="text-sm text-muted">Loading AI configuration…</p>
              )}
              {systemConfig !== null && (
                <>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="mb-1.5 block text-sm font-medium">
                        Default model
                      </label>
                      <Select
                        value={systemConfigDraft.defaultModel}
                        disabled={!canManageSystemConfig}
                        onChange={(e) =>
                          setSystemConfigDraft((d) => ({
                            ...d,
                            defaultModel: e.target.value,
                          }))
                        }
                      >
                        <option value="claude-sonnet-5">Claude Sonnet 5</option>
                        <option value="claude-opus-5">Claude Opus 5</option>
                      </Select>
                    </div>
                    <div>
                      <label className="mb-1.5 block text-sm font-medium">
                        Default effort
                      </label>
                      <Select
                        value={systemConfigDraft.defaultEffort}
                        disabled={!canManageSystemConfig}
                        onChange={(e) =>
                          setSystemConfigDraft((d) => ({
                            ...d,
                            defaultEffort: e.target.value,
                          }))
                        }
                      >
                        <option value="low">Low</option>
                        <option value="medium">Medium</option>
                        <option value="high">High</option>
                      </Select>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="mb-1.5 block text-sm font-medium">
                        Job timeout (seconds)
                      </label>
                      <input
                        type="number"
                        min={1}
                        className="w-full rounded border border-border bg-transparent px-2 py-1.5 text-sm"
                        value={systemConfigDraft.jobTimeoutSeconds}
                        disabled={!canManageSystemConfig}
                        onChange={(e) =>
                          setSystemConfigDraft((d) => ({
                            ...d,
                            jobTimeoutSeconds: Number(e.target.value),
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="mb-1.5 block text-sm font-medium">
                        Bridge worker concurrency
                      </label>
                      <input
                        type="number"
                        min={1}
                        max={1}
                        readOnly
                        className="w-full rounded border border-border bg-transparent px-2 py-1.5 text-sm"
                        value={1}
                        disabled
                      />
                      <p className="mt-1 text-xs text-muted">
                        Serial host bridge; RabbitMQ prefetch is not worker parallelism.
                      </p>
                    </div>
                    <div>
                      <label className="mb-1.5 block text-sm font-medium">
                        Claude budget per invocation (USD)
                      </label>
                      <input
                        type="number"
                        min={0.01}
                        max={10}
                        step={0.01}
                        className="w-full rounded border border-border bg-transparent px-2 py-1.5 text-sm"
                        value={systemConfigDraft.claudeMaxBudgetUsd}
                        disabled={!canManageSystemConfig}
                        onChange={(e) =>
                          setSystemConfigDraft((d) => ({
                            ...d,
                            claudeMaxBudgetUsd: Number(e.target.value),
                          }))
                        }
                      />
                      <p className="mt-1 text-xs text-muted">
                        Maximum Claude spend for one CLI invocation. Allowed range: $0.01–$10.00.
                      </p>
                    </div>
                  </div>
                  {canManageSystemConfig && (
                    <div className="flex items-center gap-3 border-t border-border pt-4">
                      <Button
                        onClick={handleSaveSystemConfig}
                        disabled={systemConfigSaving}
                      >
                        {systemConfigSaving ? "Saving…" : "Save changes"}
                      </Button>
                      <span className="text-sm text-muted">
                        Applies to the very next job the bridge dispatches — no
                        restart required.
                      </span>
                    </div>
                  )}
                  <div className="flex items-center gap-2 border-t border-border pt-4 text-sm text-muted">
                    Last updated {formatTime(systemConfig.updatedAt)}
                  </div>
                </>
              )}
            </Card>
          </TabsContent>

          <TabsContent value="storage">
            <Card className="flex flex-col gap-4">
              <CardTitle>Storage (MinIO)</CardTitle>
              {storageError && (
                <p className="text-sm text-danger" role="alert">
                  {storageError}
                </p>
              )}
              {storageStatus === null && !storageError && (
                <p className="text-sm text-muted">Loading storage status…</p>
              )}
              {storageStatus !== null && (
                <Table>
                  <Thead>
                    <Tr>
                      <Th>Bucket</Th>
                      <Th>Versioning</Th>
                      <Th>Objects</Th>
                      <Th>Size</Th>
                    </Tr>
                  </Thead>
                  <Tbody>
                    {storageStatus.map((b) => (
                      <Tr key={b.bucket}>
                        <Td className="font-medium">{b.bucket}</Td>
                        <Td>
                          <StatusPill
                            status={b.versioningEnabled ? "good" : "neutral"}
                            label={b.versioningEnabled ? "Enabled" : "Disabled"}
                          />
                        </Td>
                        <Td className="font-data">
                          {b.objectCount.toLocaleString()}
                        </Td>
                        <Td className="font-data">
                          {(b.totalBytes / (1024 * 1024)).toFixed(1)} MB
                        </Td>
                      </Tr>
                    ))}
                  </Tbody>
                </Table>
              )}
            </Card>
          </TabsContent>

          <TabsContent value="queue">
            <Card className="flex flex-col gap-4">
              <CardTitle>Queue (RabbitMQ) — Dead Letter Queues</CardTitle>
              {dlqError && (
                <p className="text-sm text-danger" role="alert">
                  {dlqError}
                </p>
              )}
              {dlqStatus === null && !dlqError && (
                <p className="text-sm text-muted">Loading queue status…</p>
              )}
              {dlqStatus !== null && (
                <Table>
                  <Thead>
                    <Tr>
                      <Th>Job type</Th>
                      <Th>DLQ count</Th>
                      {canManageDlq && <Th />}
                    </Tr>
                  </Thead>
                  <Tbody>
                    {dlqStatus.map((row) => (
                      <Tr key={row.jobType}>
                        <Td className="font-medium">{row.jobType}</Td>
                        <Td className="font-data">{row.messageCount}</Td>
                        {canManageDlq && (
                          <Td>
                            <div className="flex gap-2">
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={
                                  row.messageCount === 0 ||
                                  dlqActionPending !== null
                                }
                                onClick={() => handleRequeue(row.jobType)}
                              >
                                {dlqActionPending === `${row.jobType}:requeue`
                                  ? "Requeuing…"
                                  : "Requeue one"}
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={
                                  row.messageCount === 0 ||
                                  dlqActionPending !== null
                                }
                                onClick={() => handlePurge(row.jobType)}
                              >
                                {dlqActionPending === `${row.jobType}:purge`
                                  ? "Purging…"
                                  : "Purge"}
                              </Button>
                            </div>
                          </Td>
                        )}
                      </Tr>
                    ))}
                  </Tbody>
                </Table>
              )}
            </Card>
          </TabsContent>

          <TabsContent value="audit">
            <Card>
              <CardTitle>Audit</CardTitle>
              {auditError && (
                <p className="text-sm text-danger" role="alert">
                  {auditError}
                </p>
              )}
              {auditLog === null && !auditError && (
                <p className="text-sm text-muted">Loading audit log…</p>
              )}
              {auditLog !== null && (
                <Table>
                  <Thead>
                    <Tr>
                      <Th>Timestamp</Th>
                      <Th>Actor</Th>
                      <Th>Action</Th>
                      <Th>Target</Th>
                    </Tr>
                  </Thead>
                  <Tbody>
                    {auditLog.map((entry) => (
                      <Tr key={entry.id}>
                        <Td className="font-data">
                          {formatTime(entry.timestamp)}
                        </Td>
                        <Td>{entry.actor}</Td>
                        <Td>{entry.action}</Td>
                        <Td className="text-muted">{entry.target}</Td>
                      </Tr>
                    ))}
                  </Tbody>
                </Table>
              )}
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </RequireRole>
  );
}
