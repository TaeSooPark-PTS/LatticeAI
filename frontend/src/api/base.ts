import createClient from "openapi-fetch";
import type { paths } from "./openapi";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";

export type ApiResult<T = unknown> = {
  ok: boolean;
  status: number;
  data: T;
  source: "live" | "unavailable";
  error?: string;
};

type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";
export type Query = Record<string, string | number | boolean | null | undefined>;
export type OpenApiClient = ReturnType<typeof createClient<paths>>;

const TIMEOUT_MS = 10_000;
const clients = new Map<string, OpenApiClient>();
let desktopBase: Promise<string | null> | null = null;

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
    __TAURI__?: {
      core?: {
        invoke?: <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
      };
    };
    latticeDesktop?: {
      selectFolder?: () => Promise<string | null>;
    };
  }
}

function sameOriginBase() {
  return "";
}

function hasTauriBridge() {
  return Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__?.core?.invoke);
}

async function tauriBackendOrigin(): Promise<string | null> {
  if (!hasTauriBridge()) return null;
  if (!desktopBase) {
    desktopBase = import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke<string>("backend_origin"))
      .then((origin) => origin || null)
      .catch(() => null);
  }
  return desktopBase;
}

export async function tauriInvoke<T>(
  command: string,
  args?: Record<string, unknown>,
): Promise<T | null> {
  const globalInvoke = window.__TAURI__?.core?.invoke;
  if (globalInvoke) {
    try {
      return await globalInvoke<T>(command, args);
    } catch {
      return null;
    }
  }
  if (!window.__TAURI_INTERNALS__) return null;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<T>(command, args);
  } catch {
    return null;
  }
}

export async function selectFolder(): Promise<string | null> {
  const tauriPath = await tauriInvoke<string | null>("select_folder");
  if (tauriPath) return tauriPath;
  try {
    const electronPath = await window.latticeDesktop?.selectFolder?.();
    return electronPath || null;
  } catch {
    return null;
  }
}

export async function apiBase() {
  const stateBase = useAppStore.getState().apiBase;
  if (stateBase) return stateBase;
  const desktop = await tauriBackendOrigin();
  if (desktop) {
    useAppStore.getState().setApiBase(desktop);
    return desktop;
  }
  return sameOriginBase();
}

function clientFor(baseUrl: string) {
  if (!clients.has(baseUrl)) {
    clients.set(baseUrl, createClient<paths>({ baseUrl, credentials: "include" }));
  }
  return clients.get(baseUrl)!;
}

export function emptyFor<T>(shape: T): T {
  if (Array.isArray(shape)) return [] as T;
  if (shape && typeof shape === "object") {
    return { ...(shape as Record<string, unknown>) } as T;
  }
  return shape;
}

export function workspaceHeaders(): Record<string, string> {
  const workspaceId = useAppStore.getState().workspaceId;
  return workspaceId ? { "X-Workspace-Id": workspaceId } : {};
}

// The API layer runs outside React, so it reads the persisted language from
// the store (which is itself seeded from localStorage "lattice.language").
function uiLanguage(): Language {
  try {
    return useAppStore.getState().language;
  } catch {
    return "ko";
  }
}

// Friendly, localized copy for the two failure shapes non-developers actually
// hit: the local service not answering at all, and answering with an error.
function unreachableMessage() {
  return t(uiLanguage(), "api.error.unreachable");
}

function requestFailedMessage(status: number) {
  return t(uiLanguage(), "api.error.request", { status });
}

export function friendlyError(error: unknown, fallback: string) {
  if (!error) return fallback;
  const record =
    typeof error === "object" && error !== null
      ? error as Record<string, unknown>
      : null;
  const detail = record?.detail;
  if (typeof detail === "string") return detail;
  const detailRecord =
    typeof detail === "object" && detail !== null
      ? detail as Record<string, unknown>
      : null;
  if (detailRecord) {
    const message =
      detailRecord.user_message ||
      detailRecord.reason ||
      detailRecord.action ||
      detailRecord.status;
    if (message) return String(message);
  }
  const message = record?.message || record?.error;
  if (message) return String(message);
  return fallback;
}

export function friendlyCaughtError(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message : String(error);
  if (/not valid JSON|Unexpected token|JSON/i.test(message)) return fallback;
  if (/aborted|abort|timed?\s?out/i.test(message)) {
    return t(uiLanguage(), "api.error.timeout");
  }
  if (/failed to fetch|load failed|networkerror|network request failed/i.test(message)) {
    return t(uiLanguage(), "api.error.unreachable");
  }
  return message || fallback;
}

async function apiJson<T>(
  method: HttpMethod,
  path: string,
  opts: {
    body?: unknown;
    query?: Query;
    headers?: Record<string, string>;
    shape: T;
  },
): Promise<ApiResult<T>> {
  const base = await apiBase();
  const client = clientFor(base);
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const request = {
      body: opts.body,
      params: { query: opts.query || {} },
      headers: { ...workspaceHeaders(), ...(opts.headers || {}) },
      signal: ctrl.signal,
    } as never;
    const call =
      method === "GET" ? client.GET :
      method === "POST" ? client.POST :
      method === "PATCH" ? client.PATCH :
      client.DELETE;
    const result = await (
      call as unknown as (
        p: never,
        r: never,
      ) => Promise<{ data?: unknown; error?: unknown; response: Response }>
    )(path as never, request);
    const { data, error, response } = result;
    if (response.ok && data !== undefined) {
      return { ok: true, status: response.status, data: data as T, source: "live" };
    }
    return {
      ok: false,
      status: response.status,
      data: emptyFor(opts.shape),
      source: "unavailable",
      error: friendlyError(error, requestFailedMessage(response.status)),
    };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      data: emptyFor(opts.shape),
      source: "unavailable",
      error: friendlyCaughtError(err, unreachableMessage()),
    };
  } finally {
    window.clearTimeout(timer);
  }
}

export async function openApiJson<T>(
  shape: T,
  execute: (
    client: OpenApiClient,
    signal: AbortSignal,
  ) => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<ApiResult<T>> {
  const base = await apiBase();
  const client = clientFor(base);
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const { data, error, response } = await execute(client, ctrl.signal);
    if (response.ok && data !== undefined) {
      return { ok: true, status: response.status, data, source: "live" };
    }
    return {
      ok: false,
      status: response.status,
      data: emptyFor(shape),
      source: "unavailable",
      error: friendlyError(error, requestFailedMessage(response.status)),
    };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      data: emptyFor(shape),
      source: "unavailable",
      error: friendlyCaughtError(err, unreachableMessage()),
    };
  } finally {
    window.clearTimeout(timer);
  }
}

export function get<T>(path: string, shape: T, query?: Query) {
  return apiJson<T>("GET", path, { query, shape });
}

export function post<T>(path: string, body: unknown, shape: T) {
  return apiJson<T>("POST", path, { body, shape });
}

export function patch<T>(path: string, body: unknown, shape: T) {
  return apiJson<T>("PATCH", path, { body, shape });
}

export function del<T>(path: string, shape: T) {
  return apiJson<T>("DELETE", path, { shape });
}
