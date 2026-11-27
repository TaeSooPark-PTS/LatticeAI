import type { components, operations } from "./openapi";
import { chronicleApi } from "./chronicle";
import { importsApi } from "./imports";
import {
  type ApiResult,
  del,
  get,
  openApiJson,
  patch,
  post,
  selectFolder,
  tauriInvoke,
  workspaceHeaders,
} from "./base";
import { cloudStatus } from "./cloud";
import { selfModelApi } from "./selfModel";
import { memoryApi } from "./memory";
import { actReviewApi } from "./actReview";
import {
  resumeAgentApproval,
  streamChat,
  streamModelPrepare,
} from "./streaming";
import {
  type DemoCorpusStatus,
  downloadWorkspaceFile,
  exportBrainVault,
  installDemoCorpus,
  readWorkspaceFile,
  runAgent,
  saveChatFile,
  uploadDocument,
} from "./transfers";

export type {
  AgentResumeBody,
  ChatAgentPayload,
  ChatCreatedFile,
  ChatEventHandlers,
  ModelPrepareHandlers,
} from "./streaming";
export type {
  DemoCorpusDocument,
  DemoCorpusInstallResult,
  DemoCorpusQuestion,
  DemoCorpusStatus,
} from "./transfers";

export type { CloudProviderMode, CloudProviderStatus } from "./cloud";
export { normalizeCloudStatus, CLOUD_STATUS_UNCONFIGURED } from "./cloud";
export type { SelfModelFact, SelfModelKind, SelfModelSnapshot } from "./selfModel";

export type { ApiResult } from "./base";
export type {
  ChronicleAsOf,
  ChronicleChangeCard,
  ChronicleChangeKind,
  ChronicleConversationCard,
  ChronicleDay,
  ChronicleEntityCard,
  ChronicleLaneCounts,
  ChronicleOverview,
  ChronicleSeriesPoint,
  ChronicleSourceCard,
  ChronicleTopEntity,
} from "./chronicle";

export type SearchFilterBody = { since?: string; until?: string; include_historical?: boolean };

export type SearchSortBy = NonNullable<components["schemas"]["HybridSearchRequest"]["sort_by"]>;

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

export type FeatureChoice = {
  id: string;
  label: string;
  available: boolean;
  detail: string | null;
};

export type FeatureToggle = {
  id: string;
  kind: "toggle" | "choice" | string;
  label: string;
  summary: string;
  default: boolean | string;
  current: boolean | string;
  source: "default" | "env" | "user" | string;
  env_var: string;
  live: boolean;
  restart_required: boolean;
  caution: string | null;
  parent: string | null;
  choices: FeatureChoice[];
};

export type FeatureCatalog = { features: FeatureToggle[]; note: string };

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

export type ReasoningPathItem = {
  seed_id: string;
  seed_title: string;
  bridge_id?: string | null;
  bridge_title?: string | null;
  target_id: string;
  target_title: string;
  hops: number;
  relations: string[];
  path_score: number;
  is_contradiction: boolean;
};

export type BridgeConceptItem = {
  id: string;
  title: string;
  node_type: string;
  bridged_targets: number;
};

export type NeighborhoodConflictItem = {
  source_id: string;
  source_title: string;
  target_id: string;
  target_title: string;
  relation: string;
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

export const latticeApi = {
  ...chronicleApi,
  ...importsApi,
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
  hybridSearch: async (query: string, weights?: unknown, sortBy?: SearchSortBy, filters?: SearchFilterBody) => {
    const res = await post<Record<string, unknown>>(
      "/api/search/hybrid",
      { query, ...(weights ? { weights } : {}), ...(sortBy ? { sort_by: sortBy } : {}), ...filters },
      { matches: [] },
    );
    const data = res.data as Record<string, unknown>;
    if (res.ok && !Array.isArray(data.matches) && Array.isArray(data.results)) {
      return { ...res, data: { ...data, matches: data.results } };
    }
    return res;
  },
  hybridSearchGraphRag: (query: string, limit = 12, sortBy?: SearchSortBy, filters?: SearchFilterBody) =>
    post<Record<string, unknown>>(
      "/api/search/hybrid",
      { query, mode: "graphrag", limit, ...(sortBy ? { sort_by: sortBy } : {}), ...filters },
      { matches: [], reasoning_paths: [], synthesis: {} },
    ),
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
  networkBoundary: () => get<NetworkBoundaryState>("/api/network-boundary/ui-state", {
    mode: "local_only", label: "Local only", label_ko: "로컬만",
    allows_cloud: false, requires_ack: false, warning_ko: null,
    policy: {}, token_budget: {}, catalog: [],
  }),
  setNetworkBoundary: (mode: string, acknowledgeRisk = false) =>
    post<{ mode: string }>("/api/network-boundary", {
      mode, acknowledge_risk: acknowledgeRisk,
    }, { mode: "local_only" }),
  setNodeSensitivity: (nodeId: string, localOnly: boolean, reason?: string) =>
    post<{ ok: boolean; node_id: string; local_only: boolean }>(
      "/api/network-boundary/node-sensitivity",
      { node_id: nodeId, local_only: localOnly, reason },
      { ok: false, node_id: nodeId, local_only: localOnly },
    ),
  features: () => get<FeatureCatalog>("/api/features", { features: [], note: "" }),
  setFeature: (featureId: string, value: boolean | string) =>
    post<FeatureToggle>(
      `/api/features/${encodeURIComponent(featureId)}`,
      { value },
      {} as FeatureToggle,
    ),
  setHybridPolicy: (patch: Partial<HybridPolicy>) =>
    post<HybridPolicy>("/api/network-boundary/policy", patch, {} as HybridPolicy),
  previewCloudContext: (message: string, topK = 6) =>
    post<CloudContextPreview>("/api/network-boundary/preview", { message, top_k: topK }, {
      mode: "local_only", allows_cloud: false, node_ids: [], keywords: [],
      titles: [], types: [], token_estimate: 0, quality: "", compact_preview: "",
      token_budget: {}, would_block: null,
    }),
  cloudStatus,
  proposals: () => get("/api/proposals", { items: [], count: 0, contract: {} }),
  proposalCounts: () => get("/api/proposals/counts", { pending: 0 }),
  proposalDetail: (itemId: string) => get(`/api/proposals/${encodeURIComponent(itemId)}`, { payload: {}, provenance: {} }),
  approveProposal: (itemId: string) => post(`/api/proposals/${encodeURIComponent(itemId)}/approve`, {}, {}),
  rejectProposal: (itemId: string, reason = "") =>
    post(`/api/proposals/${encodeURIComponent(itemId)}/reject`, reason ? { reason } : {}, {}),
  commandSearch: (q: string, limit = 8, sortBy?: SearchSortBy) =>
    get(
      "/api/command/search",
      { query: q, groups: [], total: 0 },
      sortBy ? { q, limit, sort_by: sortBy } : { q, limit },
    ),
  brainHealth: () => get("/api/brain/health", { overall_score: null, grade: null, dimensions: {}, recommended_actions: [] }),
  brainVectorFreshness: () => get("/api/brain/vector-freshness", { status: "unavailable", pending_items: 0, total_items: 0, detail: "" }),
  ingestionJobs: () => get("/api/ingestion/jobs", { jobs: [] }),
  resumeIngestionJob: (jobId: string) => post(`/api/ingestion/jobs/${encodeURIComponent(jobId)}/resume`, {}, {}),
  brainInsights: () => get("/api/brain/insights", { activity: {}, attention: {}, suggested_questions: [] }),
  brainContradictions: () => get("/api/brain/contradictions", { items: [], count: 0 }),
  brainProposeContradictions: () => post<Record<string, unknown>>("/api/brain/contradictions/propose", {}, { proposed_count: 0, proposed: [] }),
  brainResolveContradiction: (itemId: string, resolution: "keep_old" | "replace" | "keep_both_temporal") =>
    post<Record<string, unknown>>("/api/brain/contradictions/resolve", { item_id: itemId, resolution }, {}),
  indexDrain: (limit = 50) => post<Record<string, unknown>>("/api/index/drain", { limit }, { status: "ok", processed: 0 }),
  brainGarden: (limit = 8) => get<Record<string, unknown>>(
    "/api/brain/garden",
    { available: false, beds: {} },
    { limit },
  ),
  brainConsolidate: (apply = false) => post("/api/brain/consolidate", { apply }, {}),
  memoryCompact: () => post("/api/memory/compact", {}, {}),
  memoryRebuild: () => post("/api/memory/rebuild", { target: "vector" }, {}),
  chatHistory: () => get("/history/conversations", []),
  searchHistory: (q: string) => get<Record<string, unknown>>("/history/search", { results: [], query: "" }, { q }),
  conversation: (id: string) => get(`/history/conversations/${encodeURIComponent(id)}`, { messages: [] }),
  deleteConversation: (id: string) => del(`/history/conversations/${encodeURIComponent(id)}`, {}),
  streamChat,
  resumeAgentApproval,
  agentApprovals: () => get<{ pending: Array<Record<string, unknown>> }>("/agent/approvals", { pending: [] }),
  evidenceActions: (question: string, sourceIds: string[], language: string) =>
    post<EvidenceActionsPayload>(
      "/api/evidence/actions",
      { question, source_ids: sourceIds, language },
      { sources: [], missing: [], actions: [], reason: "" },
    ),
  graphNode: (nodeId: string) => get<Record<string, unknown>>(
    "/api/graph/node",
    { node: {} },
    { node_id: nodeId, include_neighbors: false },
  ),
  graphNodeInspect: (nodeId: string, depth = 2, limit = 40) => get<Record<string, unknown>>(
    "/api/graph/node",
    { node: {}, neighborhood: { nodes: [], edges: [] }, reasoning: { reasoning_paths: [], synthesis: {} } },
    { node_id: nodeId, include_neighbors: true, include_reasoning: true, depth, limit },
  ),
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
  exportBrainVault,
  readWorkspaceFile,
  uploadDocument,
  documents: (limit = 200) => get("/knowledge-graph/documents", { documents: [] }, { limit }),
  localSources: () => get("/knowledge-graph/local/sources", { sources: [], watch: { available: false, active: {} } }),
  localFolderHealth: () => get<Record<string, unknown>>(
    "/knowledge-graph/local/health",
    { folders: [], count: 0 },
  ),
  pruneFolderDeleted: (path: string, confirm: boolean) => post<Record<string, unknown>>(
    "/api/ingestion/folder/prune",
    { path, confirm },
    { status: "", dry_run: !confirm, files: [], would_remove: {}, removed: {} },
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
  pipelineStatus: () => get("/knowledge-graph/pipeline/status", {}),
  activityRuns: (limit = 20) => get("/api/activity/runs", { runs: [] }, { limit }),
  adminHealthSummary: () => get("/admin/health-summary", { status: "ok", issue_count: 0, issues: [] }),
  ...selfModelApi,
  ...memoryApi,
  ...actReviewApi,
};
