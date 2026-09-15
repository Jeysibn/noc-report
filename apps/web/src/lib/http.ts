/**
 * Milestone 13 (frontend wiring): the one place that knows how to reach the
 * real backend. Base URL is a Vite env convention (VITE_API_BASE_URL) that
 * didn't exist before this milestone — see `.env`. Handles JWT attachment
 * and a single silent refresh-and-retry on 401, per app/deps.py's
 * OAuth2PasswordBearer contract (`Authorization: Bearer <access_token>`).
 * Access tokens are memory-only; the API owns the refresh credential in an
 * HttpOnly cookie that JavaScript cannot read.
 */

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(value: string): void {
  accessToken = value;
}

export function clearTokens(): void {
  accessToken = null;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Set false for endpoints that must not carry (or wait on) a bearer token, e.g. /auth/login. */
  auth?: boolean;
  query?: Record<string, string | number | boolean | undefined>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(path, BASE_URL);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function rawFetch(path: string, opts: RequestOptions, token: string | null): Promise<Response> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (opts.auth !== false && token) headers.Authorization = `Bearer ${token}`;
  return fetch(buildUrl(path, opts.query), {
    method: opts.method ?? "GET",
    headers,
    body,
    credentials: "include",
  });
}

async function rawUpload(path: string, file: File, token: string | null): Promise<Response> {
  const body = new FormData();
  body.append("file", file);
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(buildUrl(path), {
    method: "POST",
    headers,
    body,
    credentials: "include",
  });
}

let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const resp = await fetch(buildUrl("/api/v1/auth/refresh"), {
    method: "POST",
    credentials: "include",
  });
  if (!resp.ok) {
    clearTokens();
    return null;
  }
  const data = (await resp.json()) as { access_token: string };
  setAccessToken(data.access_token);
  return data.access_token;
}

/** Core JSON request helper used by every Api*Service. Throws ApiError on any non-2xx. */
export async function httpRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  let resp = await rawFetch(path, opts, opts.auth === false ? null : getAccessToken());

  if (resp.status === 401 && opts.auth !== false) {
    refreshInFlight ??= refreshAccessToken().finally(() => {
      refreshInFlight = null;
    });
    const refreshed = await refreshInFlight;
    if (refreshed) resp = await rawFetch(path, opts, refreshed);
  }

  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = await resp.json();
    } catch {
      /* body wasn't JSON — fall through with resp.statusText */
    }
    const message =
      (typeof detail === "object" && detail && "detail" in detail && String((detail as { detail: unknown }).detail)) ||
      resp.statusText ||
      `Request failed with ${resp.status}`;
    throw new ApiError(resp.status, message, detail);
  }

  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** Same auth/retry contract as httpRequest, but for an endpoint that returns
 * a binary body (e.g. a proxied file download) instead of JSON. */
export async function httpRequestBlob(path: string, opts: RequestOptions = {}): Promise<Blob> {
  let resp = await rawFetch(path, opts, opts.auth === false ? null : getAccessToken());

  if (resp.status === 401 && opts.auth !== false) {
    refreshInFlight ??= refreshAccessToken().finally(() => {
      refreshInFlight = null;
    });
    const refreshed = await refreshInFlight;
    if (refreshed) resp = await rawFetch(path, opts, refreshed);
  }

  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = await resp.json();
    } catch {
      /* body wasn't JSON — fall through with resp.statusText */
    }
    const message =
      (typeof detail === "object" && detail && "detail" in detail && String((detail as { detail: unknown }).detail)) ||
      resp.statusText ||
      `Request failed with ${resp.status}`;
    throw new ApiError(resp.status, message, detail);
  }

  return resp.blob();
}

/** Multipart request helper for the pre-incident screenshot OCR endpoint. */
export async function uploadRequest<T>(path: string, file: File): Promise<T> {
  let resp = await rawUpload(path, file, getAccessToken());
  if (resp.status === 401) {
    refreshInFlight ??= refreshAccessToken().finally(() => {
      refreshInFlight = null;
    });
    const refreshed = await refreshInFlight;
    if (refreshed) resp = await rawUpload(path, file, refreshed);
  }
  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = await resp.json();
    } catch {
      /* body wasn't JSON */
    }
    const message =
      (typeof detail === "object" && detail && "detail" in detail && String((detail as { detail: unknown }).detail)) ||
      resp.statusText ||
      `Request failed with ${resp.status}`;
    throw new ApiError(resp.status, message, detail);
  }
  return (await resp.json()) as T;
}

/** For the one non-JSON request in the app: PUT-ing a file straight to a presigned storage URL. */
export async function putFile(uploadUrl: string, file: File): Promise<void> {
  const resp = await fetch(uploadUrl, {
    method: "PUT",
    headers: file.type ? { "Content-Type": file.type } : undefined,
    body: file,
  });
  if (!resp.ok) throw new ApiError(resp.status, "Upload to storage failed");
}
