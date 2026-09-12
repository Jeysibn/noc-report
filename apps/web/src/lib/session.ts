import { useSyncExternalStore } from "react";
import type { CurrentUser, Role } from "@/types/admin";
import {
  clearTokens,
  getAccessToken,
  httpRequest,
  setTokens,
} from "@/lib/http";

/**
 * Real session state (Milestone 13 frontend wiring — replaces the mock
 * in-memory role switcher this file used to hold). Backed by the real
 * `/api/v1/auth/*` endpoints; access/refresh tokens live in localStorage
 * (see lib/http.ts), the decoded user/roles/permissions live here as a
 * small observable store so components can react to login/logout.
 */

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

let currentUser: CurrentUser | null = null;
let initialized = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function toCurrentUser(raw: RawUserOut): CurrentUser {
  return {
    id: raw.id,
    username: raw.username,
    displayName: raw.display_name,
    email: raw.email,
    roles: raw.roles as Role[],
    permissions: raw.permissions,
  };
}

export function getCurrentUser(): CurrentUser | null {
  return currentUser;
}

export function isSessionInitialized(): boolean {
  return initialized;
}

export function useSessionInitialized(): boolean {
  return useSyncExternalStore((onStoreChange) => {
    listeners.add(onStoreChange);
    return () => listeners.delete(onStoreChange);
  }, isSessionInitialized);
}

export function useCurrentUser(): CurrentUser | null {
  return useSyncExternalStore((onStoreChange) => {
    listeners.add(onStoreChange);
    return () => listeners.delete(onStoreChange);
  }, getCurrentUser);
}

/** Nav/role gating (Sidebar, RequireRole) keys off this — falls back to the
 * lowest-privilege role while logged out so a stray render never over-shows. */
export function useCurrentRole(): Role {
  const user = useCurrentUser();
  return user?.roles[0] ?? "NOC";
}

export function hasPermission(permission: string): boolean {
  return currentUser?.permissions.includes(permission) ?? false;
}

/** Populates the session from a stored access token, e.g. on app load after a refresh. */
export async function restoreSession(): Promise<void> {
  if (!getAccessToken()) {
    currentUser = null;
    initialized = true;
    emit();
    return;
  }
  try {
    const raw = await httpRequest<RawUserOut>("/api/v1/auth/me");
    currentUser = toCurrentUser(raw);
  } catch {
    clearTokens();
    currentUser = null;
  }
  initialized = true;
  emit();
}

export async function login(username: string, password: string): Promise<void> {
  const tokens = await httpRequest<{
    access_token: string;
    refresh_token: string;
  }>("/api/v1/auth/login", {
    method: "POST",
    auth: false,
    body: { username, password },
  });
  setTokens(tokens.access_token, tokens.refresh_token);
  const raw = await httpRequest<RawUserOut>("/api/v1/auth/me");
  currentUser = toCurrentUser(raw);
  emit();
}

/** Test-only escape hatch — there is no mock role switcher anymore; tests
 * that need a specific signed-in user/role/permission set call this
 * directly instead of going through the real /auth/login flow. */
export function __setCurrentUserForTests(user: CurrentUser | null): void {
  currentUser = user;
  initialized = true;
  emit();
}

export function logout(): void {
  clearTokens();
  currentUser = null;
  emit();
  // Best-effort — stateless JWTs mean the server has nothing to invalidate
  // (see app/api/v1/routers/auth.py's logout), so we don't await this.
  void httpRequest("/api/v1/auth/logout", { method: "POST" }).catch(
    () => undefined,
  );
}
