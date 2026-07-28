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

/** One selectable autonomy mode, as described by `/api/permission-mode/catalog`. */
export type PermissionModeOption = {
  id: string;
  label: string;
  label_ko: string;
  summary: string;
  summary_ko: string;
  risk: "low" | "medium" | "high" | string;
  requires_ack: boolean;
  warning?: string;
  warning_ko?: string;
};

/** Active dial plus the catalog the selector renders. */
export type PermissionModeState = {
  mode: string;
  label: string;
  label_ko: string;
  risk: string;
  requires_ack: boolean;
  proposal_first: boolean;
  workspace_writes_auto: boolean;
  knowledge_reads_auto: boolean;
  exec_auto: boolean;
  computer_observation_auto: boolean;
  computer_control_auto: boolean;
  circuit_breakers: boolean;
  catalog: PermissionModeOption[];
};

/** One selectable network boundary, as described by `/api/network-boundary`. */
export type NetworkBoundaryOption = {
  id: string;
  label: string;
  label_ko: string;
  summary: string;
  summary_ko: string;
  risk: "low" | "medium" | "high" | string;
  requires_ack: boolean;
  warning?: string;
  warning_ko?: string;
};

/** What the hybrid path is allowed to do once the boundary permits cloud. */
export type HybridPolicy = {
  blocked_node_types: string[];
  blocked_metadata_flags: string[];
  auto_commit: boolean;
  allow_multimodal: boolean;
  min_extraction_confidence: number;
};

export type CloudTokenBudget = {
  turn_limit?: number | null;
  session_limit?: number | null;
  session_used?: number | null;
  [key: string]: unknown;
};

/** Active boundary plus the catalog the selector renders (`/ui-state`). */
export type NetworkBoundaryState = {
  mode: string;
  label: string;
  label_ko: string;
  allows_cloud: boolean;
  requires_ack: boolean;
  warning_ko?: string | null;
  policy: Partial<HybridPolicy>;
  token_budget: CloudTokenBudget;
  catalog: NetworkBoundaryOption[];
};

/**
 * Exactly what would leave the machine for one message — shown *before*
 * anything is sent. `would_block` is the token guard's verdict, so the panel
 * can say "this turn would be refused" rather than discovering it mid-stream.
 */
export type CloudContextPreview = {
  mode: string;
  allows_cloud: boolean;
  node_ids: string[];
  keywords: string[];
  titles: string[];
  types: string[];
  token_estimate: number;
  quality: string;
  compact_preview: string;
  token_budget: CloudTokenBudget;
  would_block: string | null;
};

export type ReviewItem = components["schemas"]["ReviewItem"];
export type ReviewItemList = components["schemas"]["ReviewItemList"];
export type ReviewStatusFilter = "pending" | "snoozed" | "approved" | "dismissed" | "all";
export type ReviewSourceFilter = "workflow_run" | "trigger" | "kg_change_digest" | "chat_followup" | "agent_followup" | "change_proposal" | "all";
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
  // Terminal loop state (DONE | NEEDS_REVIEW | FAILED | ...): NEEDS_REVIEW and
  // FAILED must never render like success.
  final_state?: string;
  // Honesty meta from the file-generation pipeline; `repaired` marks a
  // deterministic fallback scaffold rather than clean model output.
  generation?: { repaired?: boolean; attempts?: unknown[] };
  artifacts?: Array<Record<string, unknown>>;
  // status === "awaiting_approval": the run paused server-side behind a
  // short-TTL single-use token until the user approves/edits/cancels the plan.
  run_id?: string;
  approval?: { token?: string; expires_at?: string; plan_summary?: string };
  plan?: Record<string, unknown>;
  // Loop transcript (parsed defensively into the step timeline for runs that
  // did not stream `event: agent_step` frames, e.g. approval resumes).
  steps?: unknown[];
  // Loop honesty meta: deterministic repairs applied to model output.
  loop?: {
    repairs?: Record<string, number>;
    parse_errors?: number;
    parse_recovered?: number;
  };
  // Plain-language outcome (v9.9.6): why the run ended this way, localized
  // server-side into {ko, en}. `ok` is true only for a verified DONE.
  explanation?: {
    code?: string;
    ok?: boolean;
    headline?: Record<string, string>;
    details?: Array<Record<string, string>>;
    model_strain?: Record<string, unknown>;
  };
  // Payload-level "Brain remembered" verdict for generated files: a single
  // {status,...} dict on the one-file path, or a list of {path, status, ...}
  // entries on the project-bundle path. Absent → unknown (no chip).
  brain_ingest?: Record<string, unknown> | Array<Record<string, unknown>>;
};

export type ChatEventHandlers = {
  onChunk?: (delta: string, fullText: string) => void;
  onTrace?: (trace: unknown) => void;
  onAgent?: (agent: ChatAgentPayload) => void;
  // Live `event: agent_step` frames emitted before the final payload frames.
  // Raw records — callers parse defensively and ignore unknown fields.
  onAgentStep?: (step: Record<string, unknown>) => void;
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
  // Additive answer meta ("context_quality" + "grounding") rides the same
  // trailer as the trace; keep the raw values so the UI parses defensively.
  let contextQuality: unknown = null;
  let grounding: unknown = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const lines = part.split("\n");
      const line = lines.find((item) => item.startsWith("data:"));
      if (!line) continue;
      const raw = line.slice(5).trim();
      // Named frames (same `event:` convention as streamModelPrepare). The
      // agent loop emits `event: agent_step` progress frames before the final
      // plain data frames; unknown named events are ignored gracefully.
      const eventName = lines.find((item) => item.startsWith("event:"))?.slice(6).trim() || "message";
      if (eventName === "agent_step") {
        try {
          const step = raw ? JSON.parse(raw) : null;
          if (step && typeof step === "object" && !Array.isArray(step)) {
            handlers.onAgentStep?.(step as Record<string, unknown>);
          }
        } catch {
          // A malformed progress frame must never break the answer stream.
        }
        continue;
      }
      if (eventName !== "message") continue;
      if (raw === "[DONE]") return { source: "live", text, trace, agent, contextQuality, grounding };
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
      if (data.context_quality && typeof data.context_quality === "object") {
        contextQuality = data.context_quality;
      }
      if (data.grounding && typeof data.grounding === "object") {
        grounding = data.grounding;
      }
      if (data.agent && typeof data.agent === "object") {
        agent = data.agent as ChatAgentPayload;
        handlers.onAgent?.(agent);
      }
    }
  }
  return { source: "live", text, trace, agent, contextQuality, grounding };
}

async function saveChatFile(path: string, content: string): Promise<ApiResult<{ path?: string; bytes?: number }>> {
  return post("/tools/write_file", { path, content }, {});
}

export type AgentResumeBody = {
  run_id: string;
  approval_token: string;
  approve: boolean;
  edited_plan?: Record<string, unknown>;
};

// Resumes an awaiting_approval agent run. Uses a raw fetch (no client
// timeout): approving executes the full plan, which can legitimately take
// minutes on local models. The HTTP status matters to callers (410 = token
// expired, 404 = run lost, 403 = wrong token/user), so it is passed through.
async function resumeAgentApproval(body: AgentResumeBody): Promise<ApiResult<Record<string, unknown>>> {
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/agent/resume`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...workspaceHeaders(),
      } satisfies HeadersInit,
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => null);
    return {
      ok: res.ok,
      status: res.status,
      data: (data && typeof data === "object" ? data : {}) as Record<string, unknown>,
      source: res.ok ? "live" : "unavailable",
      error: res.ok ? undefined : friendlyError(data, res.statusText || `HTTP ${res.status}`),
    };
  } catch (err) {
    return { ok: false, status: 0, data: {}, source: "unavailable", error: friendlyCaughtError(err, "unreachable") };
  }
}

export type DemoCorpusDocument = {
  demo_id?: string;
  title?: string;
  source_uri?: string;
  status?: string;
  node_id?: string | null;
  duplicate?: boolean;
  chunk_count?: number;
};

export type DemoCorpusQuestion = {
  question?: string;
  expected_source_uri?: string;
  expected_title?: string;
};

export type DemoCorpusStatus = {
  installed: boolean;
  documents: DemoCorpusDocument[];
  document_count: number;
  suggested_questions: DemoCorpusQuestion[];
};

export type DemoCorpusInstallResult = {
  status: string;
  ingested: number;
  duplicates: number;
  failed?: number;
  documents: DemoCorpusDocument[];
  suggested_questions: DemoCorpusQuestion[];
};

export type EvidenceAction = {
  id: string;
  kind: string;
  label: { ko: string; en: string };
  prompt: string;
  source_ids: string[];
  suggested_path?: string;
};

export type EvidenceActionsPayload = {
  sources: Array<{ id: string; title: string; type: string; origin: string; excerpt: string; truncated: boolean }>;
  missing: string[];
  actions: EvidenceAction[];
  reason: string;
};

// One-click First Value Loop corpus install. Raw fetch without the 10s client
// timeout: the three documents run through the real ingestion pipeline
// (chunking + embedding), which can exceed it on first use.
async function installDemoCorpus(): Promise<ApiResult<DemoCorpusInstallResult>> {
  const base = await apiBase();
  const shape: DemoCorpusInstallResult = {
    status: "", ingested: 0, duplicates: 0, documents: [], suggested_questions: [],
  };
  try {
    const res = await fetch(`${base}/api/setup/demo-corpus`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...workspaceHeaders(),
      } satisfies HeadersInit,
      body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        data: shape,
        source: "unavailable",
        error: friendlyError(data, res.statusText || `HTTP ${res.status}`),
      };
    }
    return {
      ok: true,
      status: res.status,
      data: { ...shape, ...(data && typeof data === "object" ? data : {}) },
      source: "live",
    };
  } catch (err) {
    return { ok: false, status: 0, data: shape, source: "unavailable", error: friendlyCaughtError(err, "unreachable") };
  }
}

async function runAgent(goal: string, roles: string[]): Promise<ApiResult<Record<string, unknown>>> {
  const result = await post<Record<string, unknown>>("/agents/api/run", { goal, roles }, {});
  if (!result.ok) {
    throw new Error(result.error || `Agent run failed with HTTP ${result.status}`);
  }
  return result;
}

// Reads a generated workspace file as text (same endpoint the download action
// uses) so file cards can offer an inline preview without a second backend
// surface. 404 keeps its status so callers can distinguish "file is gone".
async function readWorkspaceFile(path: string): Promise<ApiResult<{ content: string }>> {
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/tools/download?path=${encodeURIComponent(path)}`, {
      credentials: "include",
      headers: workspaceHeaders(),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      return {
        ok: false,
        status: res.status,
        data: { content: "" },
        source: "unavailable",
        error: friendlyError(payload, res.statusText || `HTTP ${res.status}`),
      };
    }
    const content = await res.text();
    return { ok: true, status: res.status, data: { content }, source: "live" };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      data: { content: "" },
      source: "unavailable",
      error: friendlyCaughtError(err, "unreachable"),
    };
  }
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
  graphPreview: (limit = 48) => get("/knowledge-graph/graph", { nodes: [], edges: [] }, { limit }),
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
  automationOverview: () => get("/api/automation/overview", { suggestions: [], installed: [], questions_scanned: 0 }),
  automationPatterns: () => get("/api/automation/patterns", { patterns: [], questions_scanned: 0 }),
  installAutomationSuggestion: (suggestionId: string, enabled = false) =>
    post("/api/automation/install", { suggestion_id: suggestionId, enabled }, {}),
  runAutomationNow: (workflowId: string, dryRun = true) =>
    post("/api/automation/run-now", { workflow_id: workflowId, dry_run: dryRun }, {}),
  commandBriefing: () => get("/api/command/briefing", { sections: {}, quick_actions: [] }),
  // Permission mode dial (v9.9.8). ``catalog`` carries the localized selector
  // copy so the UI never hardcodes mode labels; ``requires_ack`` marks the mode
  // the server refuses to set without an explicit risk acknowledgement.
  permissionMode: () => get<PermissionModeState>("/api/permission-mode", {
    mode: "strict", label: "Strict", label_ko: "엄격", risk: "low",
    requires_ack: false, proposal_first: true, workspace_writes_auto: false,
    knowledge_reads_auto: false, exec_auto: false,
    computer_observation_auto: false, computer_control_auto: false,
    circuit_breakers: true, catalog: [],
  }),
  setPermissionMode: (mode: string, acknowledgeRisk = false) =>
    post<PermissionModeState>("/api/permission-mode", {
      mode, acknowledge_risk: acknowledgeRisk,
    }, {} as PermissionModeState),
  // Network boundary dial (v10.1.0). Separate from the autonomy dial above:
  // this one answers "may knowledge leave this machine", not "may this tool
  // run". The fallback is the safe mode, so a failed read never renders the
  // panel as if cloud were already permitted.
  networkBoundary: () => get<NetworkBoundaryState>("/api/network-boundary/ui-state", {
    mode: "local_only", label: "Local only", label_ko: "로컬만",
    allows_cloud: false, requires_ack: false, warning_ko: null,
    policy: {}, token_budget: {}, catalog: [],
  }),
  setNetworkBoundary: (mode: string, acknowledgeRisk = false) =>
    post<{ mode: string }>("/api/network-boundary", {
      mode, acknowledge_risk: acknowledgeRisk,
    }, { mode: "local_only" }),
  // Marking one memory as never-leaving. The cloud filter has always looked
  // for this flag; before 10.2.0 nothing could set it, so the guard could not
  // fire. Ingestion stamps secret-bearing paths; this covers content a path
  // cannot reveal.
  setNodeSensitivity: (nodeId: string, localOnly: boolean, reason?: string) =>
    post<{ ok: boolean; node_id: string; local_only: boolean }>(
      "/api/network-boundary/node-sensitivity",
      { node_id: nodeId, local_only: localOnly, reason },
      { ok: false, node_id: nodeId, local_only: localOnly },
    ),
  setHybridPolicy: (patch: Partial<HybridPolicy>) =>
    post<HybridPolicy>("/api/network-boundary/policy", patch, {} as HybridPolicy),
  previewCloudContext: (message: string, topK = 6) =>
    post<CloudContextPreview>("/api/network-boundary/preview", { message, top_k: topK }, {
      mode: "local_only", allows_cloud: false, node_ids: [], keywords: [],
      titles: [], types: [], token_estimate: 0, quality: "", compact_preview: "",
      token_budget: {}, would_block: null,
    }),
  proposals: () => get("/api/proposals", { items: [], count: 0, contract: {} }),
  proposalCounts: () => get("/api/proposals/counts", { pending: 0 }),
  proposalDetail: (itemId: string) => get(`/api/proposals/${encodeURIComponent(itemId)}`, { payload: {}, provenance: {} }),
  approveProposal: (itemId: string) => post(`/api/proposals/${encodeURIComponent(itemId)}/approve`, {}, {}),
  rejectProposal: (itemId: string, reason = "") =>
    post(`/api/proposals/${encodeURIComponent(itemId)}/reject`, reason ? { reason } : {}, {}),
  commandSearch: (q: string, limit = 8) =>
    get("/api/command/search", { query: q, groups: [], total: 0 }, { q, limit }),
  brainHealth: () => get("/api/brain/health", { overall_score: null, grade: null, dimensions: {}, recommended_actions: [] }),
  brainVectorFreshness: () => get("/api/brain/vector-freshness", { status: "unavailable", pending_items: 0, total_items: 0, detail: "" }),
  ingestionJobs: () => get("/api/ingestion/jobs", { jobs: [] }),
  resumeIngestionJob: (jobId: string) => post(`/api/ingestion/jobs/${encodeURIComponent(jobId)}/resume`, {}, {}),
  brainInsights: () => get("/api/brain/insights", { activity: {}, attention: {}, suggested_questions: [] }),
  brainContradictions: () => get("/api/brain/contradictions", { items: [], count: 0 }),
  // Knowledge garden overview (v9.9.7): recent / contradictions / stale /
  // frequent in one read-only call.
  brainGarden: (limit = 8) => get<Record<string, unknown>>(
    "/api/brain/garden",
    { available: false, beds: {} },
    { limit },
  ),
  brainConsolidate: (apply = false) => post("/api/brain/consolidate", { apply }, {}),
  memoryCompact: () => post("/api/memory/compact", {}, {}),
  memoryRebuild: () => post("/api/memory/rebuild", { target: "vector" }, {}),
  chatHistory: () => get("/history/conversations", []),
  conversation: (id: string) => get(`/history/conversations/${encodeURIComponent(id)}`, { messages: [] }),
  deleteConversation: (id: string) => del(`/history/conversations/${encodeURIComponent(id)}`, {}),
  streamChat,
  resumeAgentApproval,
  // Pending paused approvals for the current user (GET /agent/approvals is
  // not in the generated OpenAPI spec yet; the plain path-based wrapper keeps
  // the same ApiResult contract until the next regeneration).
  agentApprovals: () => get<{ pending: Array<Record<string, unknown>> }>("/agent/approvals", { pending: [] }),
  // Evidence → action (v9.9.6): the citations an answer actually used become
  // ready-to-send, evidence-scoped follow-up prompts. Composition only — the
  // prompt still runs through the normal chat path.
  evidenceActions: (question: string, sourceIds: string[], language: string) =>
    post<EvidenceActionsPayload>(
      "/api/evidence/actions",
      { question, source_ids: sourceIds, language },
      { sources: [], missing: [], actions: [], reason: "" },
    ),
  // One graph node with its stored text/summary + provenance metadata, for
  // the citation "원문 보기" modal. Neighbors are skipped — this is a read of
  // one chunk, not an exploration.
  graphNode: (nodeId: string) => get<Record<string, unknown>>(
    "/api/graph/node",
    { node: {} },
    { node_id: nodeId, include_neighbors: false },
  ),
  // Folder watch-mode health (opt-in poller): stored watches + last scan
  // results, so the home can show whether connected folders actually flow.
  ingestionWatchStatus: () => get<Record<string, unknown>>(
    "/api/ingestion/watch",
    { enabled_count: 0, polling: false, interval_seconds: 0, watches: [] },
  ),
  demoCorpusStatus: () => get<DemoCorpusStatus>(
    "/api/setup/demo-corpus",
    { installed: false, documents: [], document_count: 0, suggested_questions: [] },
  ),
  installDemoCorpus,
  removeDemoCorpus: () => del<{ status: string; removed_count: number; removed: Array<Record<string, unknown>> }>(
    "/api/setup/demo-corpus",
    { status: "", removed_count: 0, removed: [] },
  ),
  saveChatFile,
  downloadWorkspaceFile,
  readWorkspaceFile,
  uploadDocument,
  documents: (limit = 200) => get("/knowledge-graph/documents", { documents: [] }, { limit }),
  localSources: () => get("/knowledge-graph/local/sources", { sources: [], watch: { available: false, active: {} } }),
  // Per-folder memory state (v9.9.7): indexing coverage, failures with their
  // stored reasons, and a single explicitly-global vector freshness figure.
  localFolderHealth: () => get<Record<string, unknown>>(
    "/knowledge-graph/local/health",
    { folders: [], count: 0 },
  ),
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
