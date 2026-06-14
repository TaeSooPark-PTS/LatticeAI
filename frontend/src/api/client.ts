import createClient from "openapi-fetch";
import type { paths } from "./openapi";
import { useAppStore } from "@/store/appStore";

export type ApiResult<T = unknown> = {
  ok: boolean;
  status: number;
  data: T;
  source: "live" | "unavailable";
  error?: string;
};

type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";
type Query = Record<string, string | number | boolean | null | undefined>;
export type AdminAuditFilters = {
  q?: string;
  actor?: string;
  action?: string;
  severity?: string;
  limit?: number;
};

const TIMEOUT_MS = 10_000;
const clients = new Map<string, ReturnType<typeof createClient<paths>>>();
let desktopBase: Promise<string | null> | null = null;

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

function sameOriginBase() {
  return "";
}

async function tauriBackendOrigin(): Promise<string | null> {
  if (!window.__TAURI_INTERNALS__) return null;
  if (!desktopBase) {
    desktopBase = import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke<string>("backend_origin"))
      .then((origin) => origin || null)
      .catch(() => null);
  }
  return desktopBase;
}

async function tauriInvoke<T>(command: string): Promise<T | null> {
  if (!window.__TAURI_INTERNALS__) return null;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<T>(command);
  } catch {
    return null;
  }
}

async function apiBase() {
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
    clients.set(baseUrl, createClient<paths>({ baseUrl, credentials: "same-origin" }));
  }
  return clients.get(baseUrl)!;
}

function emptyFor<T>(shape: T): T {
  if (Array.isArray(shape)) return [] as T;
  if (shape && typeof shape === "object") return { ...(shape as Record<string, unknown>) } as T;
  return shape;
}

function workspaceHeaders(): Record<string, string> {
  const workspaceId = useAppStore.getState().workspaceId;
  return workspaceId ? { "X-Workspace-Id": workspaceId } : {};
}

function friendlyError(error: unknown, fallback: string) {
  if (!error) return fallback;
  const record = typeof error === "object" && error !== null ? error as Record<string, unknown> : null;
  const detail = record?.detail;
  if (typeof detail === "string") return detail;
  const detailRecord = typeof detail === "object" && detail !== null ? detail as Record<string, unknown> : null;
  if (detailRecord) {
    const message = detailRecord.user_message || detailRecord.reason || detailRecord.action || detailRecord.status;
    if (message) return String(message);
  }
  const message = record?.message || record?.error;
  if (message) return String(message);
  return fallback;
}

async function apiJson<T>(
  method: HttpMethod,
  path: string,
  opts: { body?: unknown; query?: Query; headers?: Record<string, string>; shape: T },
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
    const result = await (call as unknown as (p: never, r: never) => Promise<{ data?: unknown; error?: unknown; response: Response }>)(path as never, request);
    const { data, error, response } = result;
    if (response.ok && data !== undefined) {
      return { ok: true, status: response.status, data: data as T, source: "live" };
    }
    return {
      ok: false,
      status: response.status,
      data: emptyFor(opts.shape),
      source: "unavailable",
      error: friendlyError(error, response.statusText),
    };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      data: emptyFor(opts.shape),
      source: "unavailable",
      error: err instanceof Error ? err.message : String(err),
    };
  } finally {
    window.clearTimeout(timer);
  }
}

function get<T>(path: string, shape: T, query?: Query) {
  return apiJson<T>("GET", path, { query, shape });
}

function post<T>(path: string, body: unknown, shape: T) {
  return apiJson<T>("POST", path, { body, shape });
}

function patch<T>(path: string, body: unknown, shape: T) {
  return apiJson<T>("PATCH", path, { body, shape });
}

function del<T>(path: string, shape: T) {
  return apiJson<T>("DELETE", path, { shape });
}

async function uploadDocument(file: File): Promise<ApiResult<Record<string, unknown> | null>> {
  const base = await apiBase();
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch(`${base}/upload/document`, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", ...workspaceHeaders() } satisfies HeadersInit,
      body: form,
    });
    const data = await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, data, source: res.ok ? "live" : "unavailable" };
  } catch (err) {
    return { ok: false, status: 0, data: null, source: "unavailable", error: String(err) };
  }
}

export type ChatEventHandlers = {
  onChunk?: (delta: string, fullText: string) => void;
  onTrace?: (trace: unknown) => void;
  signal?: AbortSignal;
};

export type ModelPrepareHandlers = {
  onProgress?: (data: Record<string, unknown>) => void;
  onDone?: (data: Record<string, unknown>) => void;
  onError?: (data: Record<string, unknown>) => void;
  signal?: AbortSignal;
};

async function streamModelPrepare(
  body: { model: string; engine?: string; allow_download?: boolean },
  handlers: ModelPrepareHandlers = {},
) {
  const base = await apiBase();
  const res = await fetch(`${base}/engines/prepare-model/stream`, {
    method: "POST",
    credentials: "same-origin",
    signal: handlers.signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...workspaceHeaders(),
    } satisfies HeadersInit,
    body: JSON.stringify({ engine: null, allow_download: false, ...body }),
  });
  if (!res.ok || !res.body || !(res.headers.get("content-type") || "").includes("text/event-stream")) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail && typeof payload.detail === "object" ? payload.detail : payload;
    const message = friendlyError(payload, res.statusText);
    handlers.onError?.({ status: "error", user_message: message, ...(detail || {}) });
    return { source: "live" as const, ok: false, status: res.status, data: detail || {}, error: message };
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";
  let finalData: Record<string, unknown> = {};
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const lines = part.split("\n");
      eventName = lines.find((item) => item.startsWith("event:"))?.slice(6).trim() || "message";
      const dataLine = lines.find((item) => item.startsWith("data:"));
      if (!dataLine) continue;
      const raw = dataLine.slice(5).trim();
      const data = raw ? JSON.parse(raw) as Record<string, unknown> : {};
      if (eventName === "progress") handlers.onProgress?.(data);
      if (eventName === "error") {
        const detail = typeof data.detail === "object" && data.detail !== null ? data.detail as Record<string, unknown> : data;
        handlers.onError?.(detail);
        return { source: "live" as const, ok: false, status: Number(data.status_code || 500), data: detail, error: friendlyError({ detail }, "Model setup failed") };
      }
      if (eventName === "done") {
        finalData = data;
        handlers.onDone?.(data);
      }
    }
  }
  return { source: "live" as const, ok: true, status: 200, data: finalData };
}

async function streamChat(body: Record<string, unknown>, handlers: ChatEventHandlers = {}) {
  const base = await apiBase();
  const res = await fetch(`${base}/chat`, {
    method: "POST",
    credentials: "same-origin",
    signal: handlers.signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...workspaceHeaders(),
    } satisfies HeadersInit,
    body: JSON.stringify({ stream: true, max_tokens: 2048, temperature: 0.2, ...body }),
  });
  if (!res.ok || !res.body || !(res.headers.get("content-type") || "").includes("text/event-stream")) {
    const payload = await res.json().catch(() => null);
    return { source: "live", text: "", trace: null, error: payload?.error || payload?.detail || res.statusText };
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let text = "";
  let trace: unknown = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((item) => item.startsWith("data:"));
      if (!line) continue;
      const raw = line.slice(5).trim();
      if (raw === "[DONE]") return { source: "live", text, trace };
      const data = JSON.parse(raw);
      const delta = data.chunk || data.text || "";
      if (delta) {
        text += delta;
        handlers.onChunk?.(delta, text);
      }
      if (data.trace) {
        trace = data.trace;
        handlers.onTrace?.(trace);
      }
    }
  }
  return { source: "live", text, trace };
}

export const latticeApi = {
  raw: get,
  desktopBackendStatus: async (): Promise<ApiResult<Record<string, unknown>>> => {
    const status = await tauriInvoke<Record<string, unknown>>("backend_status");
    if (status) return { ok: true, status: 200, data: status, source: "live" };
    return { ok: false, status: 0, data: {}, source: "unavailable", error: "Desktop backend status is available only inside the Tauri shell." };
  },
  health: () => get("/health", {}),
  workspaceOs: () => get("/workspace/os", { counts: {}, models: {}, workspace_registry: { workspaces: [] } }),
  indexStatus: () => get("/api/index/status", {}),
  rebuildIndex: () => post("/api/index/rebuild", { full: false, include_nodes: true, include_chunks: true }, {}),
  graph: () => get("/knowledge-graph/graph", { nodes: [], edges: [] }),
  graphStats: () => get("/knowledge-graph/stats", { nodes: {}, edges: {}, total_nodes: 0, total_edges: 0 }),
  graphPortability: () => get("/api/knowledge-graph/portability", {}),
  brainStorage: () => get("/api/brain/storage", {}),
  backupHealth: () => get("/api/knowledge-graph/backup-health", {}),
  dockerPostgres: (body: { consent: boolean; dry_run?: boolean; port?: number }) => post("/api/brain/storage/postgres/docker", body, {}),
  migratePostgres: (body: { dsn: string; schema_name?: string; dry_run?: boolean }) => post("/api/brain/storage/migrate-postgres", body, {}),
  graphProvenance: (limit = 50) => get("/api/knowledge-graph/provenance", { items: [] }, { limit }),
  graphCoverage: () => get("/knowledge-graph/provenance/coverage", {}),
  graphExport: () => post("/api/knowledge-graph/export", {}, {}),
  graphBackup: () => post("/api/knowledge-graph/backup", {}, {}),
  graphImport: (artifact: unknown, dry_run = true) => post("/api/knowledge-graph/import", { artifact, mode: "merge", dry_run }, {}),
  brainArchive: (body: { path?: string | null; passphrase: string }) => post("/api/knowledge-graph/archive", body, {}),
  brainArchiveInspect: (body: { path: string; passphrase?: string | null }) => post("/api/knowledge-graph/archive/inspect", body, {}),
  brainArchiveVerify: (body: { path: string; passphrase: string }) => post("/api/knowledge-graph/archive/verify", body, {}),
  brainArchiveRestore: (body: { path: string; passphrase: string; dry_run?: boolean; confirm?: boolean }) => post("/api/knowledge-graph/archive/restore", body, {}),
  brainArchiveImport: (body: { path: string; passphrase: string; dry_run?: boolean; confirm?: boolean }) => post("/api/knowledge-graph/archive/import", body, {}),
  hybridSearch: async (query: string, weights?: unknown) => {
    const res = await post<Record<string, unknown>>("/api/search/hybrid", { query, ...(weights ? { weights } : {}) }, { matches: [] });
    const data = res.data as Record<string, unknown>;
    if (res.ok && !Array.isArray(data.matches) && Array.isArray(data.results)) {
      return { ...res, data: { ...data, matches: data.results } };
    }
    return res;
  },
  browserReadUrl: (url: string) => post("/api/browser/read-url", { url }, {}),
  memoryManager: () => get("/api/memory/manager", { sources: [], tiers: [], usage: {} }),
  memoryRecall: (query: string, limit = 20) => post("/api/memory/recall", { query, limit }, { matches: [] }),
  memoryCompact: () => post("/api/memory/compact", {}, {}),
  memoryRebuild: () => post("/api/memory/rebuild", { target: "vector" }, {}),
  chatHistory: () => get("/history/conversations", []),
  conversation: (id: string) => get(`/history/conversations/${encodeURIComponent(id)}`, { messages: [] }),
  deleteConversation: (id: string) => del(`/history/conversations/${encodeURIComponent(id)}`, {}),
  streamChat,
  uploadDocument,
  documents: (limit = 200) => get("/knowledge-graph/documents", { documents: [] }, { limit }),
  localSources: () => get("/knowledge-graph/local/sources", { sources: [], watch: { available: false, active: {} } }),
  localAgent: () => get("/api/local-agent/status", { agent: { online: false }, sources: [] }),
  connectFolder: (path: string) => post("/knowledge-graph/local/index", { path, approved: true, watch_enabled: true, consent: { approved: true, source: "desktop-spa" } }, {}),
  localWatchStop: (source_id: string) => post("/knowledge-graph/local/watch/stop", { source_id }, {}),
  models: () => get("/models", { catalog: [], loaded: [], recommended: [] }),
  setupScan: () => get("/setup/scan", { environment: {}, recommendations: {}, zero_config: {} }),
  modelRecommendations: (engine = "local_mlx") => get("/models/recommendations", { profile: {}, recommendations: { models: [], families: [], counts: {} } }, { engine }),
  installEngine: (engine: string) => post("/engines/install", { engine }, {}),
  prepareModel: (model: string, engine?: string, allow_download = false) => post("/engines/prepare-model", { model, engine: engine || null, allow_download }, {}),
  streamModelPrepare,
  loadModel: (model_id: string, engine?: string, allow_download = false) => post("/models/load", { model_id, engine: engine || null, allow_download }, {}),
  unloadModel: (model_id: string) => del(`/models/unload/${encodeURIComponent(model_id)}`, {}),
  embeddingsStatus: () => get("/api/embeddings/status", {}),
  agentRuntime: () => get("/agents/api/runtime/status", { runtime: {}, agents: [], runs: [] }),
  runAgent: (goal: string, roles: string[]) => post("/agents/api/run", { goal, roles }, {}),
  agentRun: (id: string) => get(`/agents/api/runs/${encodeURIComponent(id)}`, {}),
  stopAgentRun: (id: string) => post(`/agents/api/runs/${encodeURIComponent(id)}/stop`, {}, {}),
  agentRegistry: () => get("/agents/api/registry", { agents: [] }),
  agentCapabilities: () => get("/agents/api/registry/capabilities", { capabilities: {} }),
  registerAgent: (body: unknown) => post("/agents/api/registry", body, {}),
  updateAgent: (id: string, body: unknown) => patch(`/agents/api/registry/${encodeURIComponent(id)}`, body, {}),
  removeAgent: (id: string) => del(`/agents/api/registry/${encodeURIComponent(id)}`, {}),
  workflowDefinitions: () => get("/workflows/api/definitions", { workflows: [] }),
  workflowRuns: () => get("/workflows/api/runs", { runs: [] }),
  workflowTriggers: () => get("/workflows/api/triggers", { armed: [] }),
  createWorkflow: (body: { name: string; nodes: Array<Record<string, unknown>>; metadata?: Record<string, unknown> }) => post("/workflows/api/definitions", body, {}),
  importWorkflow: (data: Record<string, unknown>) => post("/workflows/api/import", { data }, {}),
  exportWorkflow: (id: string) => get(`/workflows/api/export/${encodeURIComponent(id)}`, {}),
  runWorkflow: (id: string) => post(`/workflows/api/definitions/${encodeURIComponent(id)}/run`, {}, {}),
  updateWorkflow: (id: string, body: unknown) => patch(`/workflows/api/definitions/${encodeURIComponent(id)}`, body, {}),
  stopWorkflowRun: (id: string) => post(`/workflows/api/runs/${encodeURIComponent(id)}/stop`, {}, {}),
  resumeWorkflowRun: (id: string, approved: boolean) => post(`/workflows/api/runs/${encodeURIComponent(id)}/resume`, { approved }, {}),
  hooks: () => get("/api/hooks", { hooks: [] }),
  hookRuns: () => get("/api/hooks/runs", { runs: [] }, { limit: 50 }),
  hookRun: (body: unknown) => post("/api/hooks/run", body, {}),
  permissionsPending: () => get("/permissions/pending", { pending: {}, count: 0 }),
  approvePermission: (token: string) => post(`/permissions/approve/${encodeURIComponent(token)}`, {}, {}),
  denyPermission: (token: string) => post(`/permissions/deny/${encodeURIComponent(token)}`, {}, {}),
  skills: () => get("/workspace/skills", { skills: [] }),
  skillToggle: (skill: string, enabled: boolean) => post(enabled ? "/workspace/skills/disable" : "/workspace/skills/enable", { skill }, {}),
  skillsMarketplace: () => get("/skills/marketplace", { skills: [] }),
  skillInstall: (skill: string, plugin?: string) => post("/workspace/skills/install", { skill, plugin: plugin || "" }, {}),
  mcpTools: () => get("/mcp/tools", { tools: [], installed_mcps: [] }),
  mcpRecommend: (query: string) => post("/mcp/recommend", { query, limit: 6 }, {}),
  templates: () => get("/marketplace/templates", { templates: [], kinds: [] }),
  installTemplate: (data: unknown) => post("/marketplace/templates/install", { data }, {}),
  pluginsRegistry: () => get("/plugins/registry", { plugins: [] }),
  pluginsDirectory: () => get("/plugins/directory", { plugins: [] }),
  profile: () => get("/account/profile", {}),
  login: (email: string, password: string) => post("/login", { email, password }, {}),
  register: (body: unknown) => post("/register", body, {}),
  logout: () => post("/logout", {}, {}),
  updateProfile: (body: unknown) => patch("/account/profile", body, {}),
  changePassword: (current_password: string, new_password: string) => post("/account/change-password", { current_password, new_password }, {}),
  ssoConfig: () => get("/auth/sso/config", { enabled: false, providers: [] }),
  workspaceRegistry: () => get("/workspace/registry", { workspaces: [] }),
  createOrg: (name: string) => post("/workspace/orgs", { name }, {}),
  activateWorkspace: (workspace_id: string) => post("/workspace/activate", { workspace_id }, {}),
  archiveWorkspace: (workspace_id: string) => post(`/workspace/orgs/${encodeURIComponent(workspace_id)}/archive`, {}, {}),
  addWorkspaceMember: (workspace_id: string, user_id: string, role: string) => post(`/workspace/orgs/${encodeURIComponent(workspace_id)}/members`, { user_id, role }, {}),
  removeWorkspaceMember: (workspace_id: string, user_id: string) => del(`/workspace/orgs/${encodeURIComponent(workspace_id)}/members/${encodeURIComponent(user_id)}`, {}),
  invitations: () => get("/invitations", { invitations: [] }),
  createInvitation: (body: unknown) => post("/invitations", body, {}),
  acceptInvitation: (token: string) => post(`/invitations/${encodeURIComponent(token)}/accept`, {}, {}),
  snapshots: () => get("/workspace/snapshots", { snapshots: [] }),
  createSnapshot: (name: string) => post("/workspace/snapshots", { name }, {}),
  compareSnapshots: (before_id: string, after_id: string) => post("/workspace/snapshots/compare", { before_id, after_id }, {}),
  restoreSnapshot: (id: string) => post(`/workspace/snapshots/${encodeURIComponent(id)}/restore`, {}, {}),
  exportSnapshot: (id: string) => post(`/workspace/snapshots/${encodeURIComponent(id)}/export`, {}, {}),
  timeMachine: () => get("/workspace/time-machine", { events: [] }, { limit: 100 }),
  realtimeFeed: () => get("/realtime/feed", { events: [] }, { limit: 80 }),
  presence: () => get("/realtime/presence", { presence: [] }),
  networkIdentity: () => get("/network/identity", {}),
  networkPeers: () => get("/network/peers", { peers: [] }),
  pairPeer: (body: unknown) => post("/network/peers", body, {}),
  unpairPeer: (id: string) => del(`/network/peers/${encodeURIComponent(id)}`, {}),
  pushPeer: (id: string, workspace_id?: string | null) => post(`/network/push/${encodeURIComponent(id)}`, { workspace_id }, {}),
  sysinfo: () => get("/local/sysinfo", {}),
  computerMemory: () => get("/workspace/computer-memory", {}),
  setComputerMemory: (enabled: boolean) => post("/workspace/computer-memory", { enabled, consent: { approved: enabled } }, {}),
  adminSummary: () => get("/admin/summary", {}),
  adminStats: () => get("/admin/stats", {}),
  adminUsers: () => get("/admin/users", []),
  adminAudit: (filters?: AdminAuditFilters) => get("/admin/audit", { recent_events: [], filters: {} }, filters),
  adminRoles: () => get("/admin/roles", { roles: [] }),
  adminPolicies: () => get("/admin/policies", { policies: [] }),
  adminLogRetention: () => get("/admin/log-retention", {}),
  adminProductHardening: () => get("/admin/product-hardening", {}),
  adminSecurity: () => get("/admin/security/overview", {}),
  adminSecurityEvents: (limit = 50) => get("/admin/security/events", { events: [] }, { limit }),
  vpcStatus: () => get("/vpc/status", {}),
  toolPermissions: () => get("/tools/permissions", { permissions: [] }),
};
