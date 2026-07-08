import type { components, operations } from "./openapi";
import {
  apiBase,
  type ApiResult,
  del,
  friendlyCaughtError,
  friendlyError,
  get,
  openApiJson,
  type OpenApiClient,
  patch,
  post,
  selectFolder,
  tauriInvoke,
  workspaceHeaders,
} from "./base";

export type { ApiResult } from "./base";

export type AdminAuditFilters = {
  q?: string;
  actor?: string;
  action?: string;
  severity?: string;
  limit?: number;
};

type OperationJson200<Operation> = Operation extends {
  responses: { 200: { content: { "application/json": infer Result } } };
} ? Result : never;
type ReviewListOperation = operations["list_items_automation_reviews_get"];
type ReviewItemOperation =
  | operations["approve_item_automation_reviews__item_id__approve_post"]
  | operations["dismiss_item_automation_reviews__item_id__dismiss_post"]
  | operations["run_now_item_automation_reviews__item_id__run_now_post"]
  | operations["snooze_item_automation_reviews__item_id__snooze_post"]
  | operations["unsnooze_item_automation_reviews__item_id__unsnooze_post"];

export type ReviewItem = components["schemas"]["ReviewItem"];
export type ReviewItemList = components["schemas"]["ReviewItemList"];
export type ReviewStatusFilter = "pending" | "snoozed" | "approved" | "dismissed" | "all";
export type ReviewSourceFilter = "workflow_run" | "trigger" | "kg_change_digest" | "chat_followup" | "agent_followup" | "all";
export type CreateReviewItemBody = {
  title: string;
  summary?: string;
  source?: Exclude<ReviewSourceFilter, "all">;
  kind?: string;
  payload?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
};

function reviewItemShape(): ReviewItem {
  return {
    id: "",
    status: "pending",
    effective_status: "pending",
    title: "",
    summary: "",
    source: "workflow_run",
    kind: "suggestion",
    payload: {},
    provenance: {},
  };
}

function reviewItemListShape(): ReviewItemList {
  return { items: [] };
}

function reviewList(query?: { status?: Exclude<ReviewStatusFilter, "all">; source?: Exclude<ReviewSourceFilter, "all"> }) {
  return openApiJson<OperationJson200<ReviewListOperation>>(
    reviewItemListShape(),
    (client, signal) => client.GET("/automation/reviews", {
      params: { query: query || {} },
      headers: workspaceHeaders(),
      signal,
    }),
  );
}

function reviewAction(
  id: string,
  action: "approve" | "dismiss" | "run_now" | "unsnooze",
) {
  return openApiJson<OperationJson200<ReviewItemOperation>>(
    reviewItemShape(),
    (client, signal) => {
      const request = {
        params: { path: { item_id: id } },
        headers: workspaceHeaders(),
        signal,
      };
      if (action === "approve") return client.POST("/automation/reviews/{item_id}/approve", request);
      if (action === "dismiss") return client.POST("/automation/reviews/{item_id}/dismiss", request);
      if (action === "run_now") return client.POST("/automation/reviews/{item_id}/run_now", request);
      return client.POST("/automation/reviews/{item_id}/unsnooze", request);
    },
  );
}

function snoozeReview(id: string, until: string) {
  return openApiJson<OperationJson200<operations["snooze_item_automation_reviews__item_id__snooze_post"]>>(
    reviewItemShape(),
    (client, signal) => client.POST("/automation/reviews/{item_id}/snooze", {
      params: { path: { item_id: id } },
      body: { until },
      headers: workspaceHeaders(),
      signal,
    }),
  );
}

async function uploadDocument(file: File): Promise<ApiResult<Record<string, unknown> | null>> {
  const base = await apiBase();
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch(`${base}/upload/document`, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json", ...workspaceHeaders() } satisfies HeadersInit,
      body: form,
    });
    const data = await res.json().catch(() => null);
    return {
      ok: res.ok,
      status: res.status,
      data,
      source: res.ok ? "live" : "unavailable",
      error: res.ok ? undefined : friendlyError(data, res.statusText || "Upload failed"),
    };
  } catch (err) {
    return { ok: false, status: 0, data: null, source: "unavailable", error: String(err) };
  }
}

export type ChatCreatedFile = {
  path: string;
  filename: string;
  bytes: number;
  action?: string;
};

export type ChatAgentPayload = {
  status?: string;
  response?: string;
  created_files?: ChatCreatedFile[];
  routed_to_agent?: boolean;
  action_route?: string;
};

export type ChatEventHandlers = {
  onChunk?: (delta: string, fullText: string) => void;
  onTrace?: (trace: unknown) => void;
  onAgent?: (agent: ChatAgentPayload) => void;
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
    credentials: "include",
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
    credentials: "include",
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
  let agent: ChatAgentPayload | null = null;
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
      if (raw === "[DONE]") return { source: "live", text, trace, agent };
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
      if (data.agent && typeof data.agent === "object") {
        agent = data.agent as ChatAgentPayload;
        handlers.onAgent?.(agent);
      }
    }
  }
  return { source: "live", text, trace, agent };
}

async function saveChatFile(path: string, content: string): Promise<ApiResult<{ path?: string; bytes?: number }>> {
  return post("/tools/write_file", { path, content }, {});
}

async function runAgent(goal: string, roles: string[]): Promise<ApiResult<Record<string, unknown>>> {
  const result = await post<Record<string, unknown>>("/agents/api/run", { goal, roles }, {});
  if (!result.ok) {
    throw new Error(result.error || `Agent run failed with HTTP ${result.status}`);
  }
  return result;
}

async function downloadWorkspaceFile(path: string, filename: string): Promise<{ ok: boolean; error?: string }> {
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/tools/download?path=${encodeURIComponent(path)}`, {
      credentials: "include",
      headers: workspaceHeaders(),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      return { ok: false, error: friendlyError(payload, res.statusText || "Download failed") };
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename || path.split("/").pop() || "download";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: friendlyCaughtError(err, "Download failed") };
  }
}

export const latticeApi = {
  raw: get,
  selectFolder,
  desktopBackendStatus: async (): Promise<ApiResult<Record<string, unknown>>> => {
    const status = await tauriInvoke<Record<string, unknown>>("backend_status");
    if (status) return { ok: true, status: 200, data: status, source: "live" };
    return { ok: false, status: 0, data: {}, source: "unavailable", error: "Desktop backend status is available only inside the Tauri shell." };
  },
  health: () => get("/health", {}),
  workspaceOs: () => get("/workspace/os", { counts: {}, models: {}, workspace_registry: { workspaces: [] } }),
  workspaceVscodeStatus: () => get("/workspace/vscode/status", {
    connected: false,
    last_seen_ms: 0,
    status: "offline",
    index_status: "unknown",
  }),
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
  ingestNote: (content: string, title = "Brain note") => post(
    "/knowledge-graph/ingest",
    { type: "note", content, title, source: "brain_home" },
    {},
  ),
  memoryManager: () => get("/api/memory/manager", { sources: [], tiers: [], usage: {} }),
  memoryBrainQuality: () => get("/api/memory/brain-quality", {}),
  memoryBrainBrief: (query = "", limit = 3) => get("/api/memory/brain-brief", { focus: {}, next_actions: [], proactive_actions: [], evidence: [] }, { q: query, limit }),
  memoryBrainProof: (query = "", limit = 3) => get("/api/memory/brain-proof", { proofs: {}, recall: { items: [] }, model_continuity: {}, claims: {} }, { q: query, limit }),
  memoryRecall: (query: string, limit = 20) => post("/api/memory/recall", { query, limit }, { matches: [] }),
  memoryCompact: () => post("/api/memory/compact", {}, {}),
  memoryRebuild: () => post("/api/memory/rebuild", { target: "vector" }, {}),
  chatHistory: () => get("/history/conversations", []),
  conversation: (id: string) => get(`/history/conversations/${encodeURIComponent(id)}`, { messages: [] }),
  deleteConversation: (id: string) => del(`/history/conversations/${encodeURIComponent(id)}`, {}),
  streamChat,
  saveChatFile,
  downloadWorkspaceFile,
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
  agentRunPreview: (goal: string, roles: string[] = []) => post("/agents/api/run/preview", { goal, roles }, { ready: false, blocking_reasons: [] }),
  runAgent,
  toolRegistryDiagnostics: () => get("/tools/registry/diagnostics", { diagnostics: { ready: false } }),
  toolRegistry: () => get("/tools/registry", { status: "unavailable", diagnostics: {}, tools: [] }),
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
  automationRecipes: () => get("/workflows/api/automation/recipes", { recipes: [], principles: {} }),
  installAutomationRecipe: (recipeId: string, enabled = false) => post(`/workflows/api/automation/recipes/${encodeURIComponent(recipeId)}`, { enabled }, {}),
  createWorkflow: (body: { name: string; nodes: Array<Record<string, unknown>>; metadata?: Record<string, unknown> }) => post("/workflows/api/definitions", body, {}),
  importWorkflow: (data: Record<string, unknown>) => post("/workflows/api/import", { data }, {}),
  exportWorkflow: (id: string) => get(`/workflows/api/export/${encodeURIComponent(id)}`, {}),
  runWorkflow: (id: string) => post(`/workflows/api/definitions/${encodeURIComponent(id)}/run`, {}, {}),
  updateWorkflow: (id: string, body: unknown) => patch(`/workflows/api/definitions/${encodeURIComponent(id)}`, body, {}),
  stopWorkflowRun: (id: string) => post(`/workflows/api/runs/${encodeURIComponent(id)}/stop`, {}, {}),
  resumeWorkflowRun: (id: string, approved: boolean) => post(`/workflows/api/runs/${encodeURIComponent(id)}/resume`, { approved }, {}),
  hooks: () => get("/api/hooks", { hooks: [] }),
  hookRuns: () => get("/api/hooks/runs", { runs: [] }, { limit: 50 }),
  hookRun: (body: Record<string, unknown> = {}) => post("/api/hooks/run", { kind: "pre_run", event: "manual", ...body }, {}),
  automationReviews: reviewList,
  createReviewItem: (body: CreateReviewItemBody) => post("/automation/reviews", body, reviewItemShape()),
  approveReviewItem: (id: string) => reviewAction(id, "approve"),
  dismissReviewItem: (id: string) => reviewAction(id, "dismiss"),
  snoozeReviewItem: snoozeReview,
  unsnoozeReviewItem: (id: string) => reviewAction(id, "unsnooze"),
  runNowReviewItem: (id: string) => reviewAction(id, "run_now"),
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
