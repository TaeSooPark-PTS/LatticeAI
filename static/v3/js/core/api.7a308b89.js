/* ============================================================================
 * Lattice AI v3 — Integration adapter
 *
 * Every adapter call hits the real endpoint first (including /api/index/status,
 * /api/graph, /api/search/hybrid, and /chat). If that endpoint is
 * missing/unavailable, it returns an unavailable source with empty data so the
 * UI can render a clear unavailable state without inventing counters or health.
 *
 * Return shape (never throws): { ok, status, data, source, error }
 *   source: "live"        → returned by a real backend endpoint
 *           "unavailable" → endpoint missing/down; no fake payload
 * ========================================================================== */

import { store } from "./store.204a08b2.js";

const TIMEOUT_MS = 8000;
const EMPTY_INDEX_STATUS = { generated_at: null, pipelines: {}, sources: [] };
const EMPTY_GRAPH_STATS = { nodes: {}, edges: {}, total_nodes: 0, total_edges: 0 };
const EMPTY_WORKSPACE_OS = { counts: {}, models: {} };
const EMPTY_SYSINFO = { cpu_pct: null, ram_pct: null, gpu_mem_pct: null, gpu_mem_gb: null };
const EMPTY_ADMIN = {
  summary: { total_users: null, active_users: null, admin_users: null, total_messages: null },
  users: [],
  audit: { recent_events: [] },
  security: {},
  roles: { roles: [] },
  policies: { policies: [] },
  vpc: {},
};

async function raw(path, { method = "GET", body, headers } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const ws = store.get().workspaceId;
    const res = await fetch(path, {
      method,
      credentials: "same-origin",
      signal: ctrl.signal,
      headers: {
        "Accept": "application/json",
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(ws ? { "X-Workspace-Id": ws } : {}),
        ...headers,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch { data = { raw: text }; } }
    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    return { ok: false, status: 0, data: null, error: err && err.name === "AbortError" ? "timeout" : String(err) };
  } finally {
    clearTimeout(timer);
  }
}

function unavailableData(shape) {
  const value = typeof shape === "function" ? shape() : shape;
  if (Array.isArray(value)) return [];
  if (value && typeof value === "object") return {};
  return null;
}

/** Try the live endpoint; on any non-2xx/transport failure, return empty data. */
async function withFallback(path, opts, shape) {
  const res = await raw(path, opts);
  if (res.ok && res.data && !res.data.raw) {
    return { ...res, source: "live" };
  }
  return { ok: false, status: res.status, data: unavailableData(shape), source: "unavailable", error: res.error };
}

export const api = {
  raw,

  /** Generic GET with unavailable fallback. */
  async get(path, shape = null) {
    return withFallback(path, {}, shape);
  },

  /* ── Documented future surfaces ─────────────────────────────────────── */

  /** GET /api/index/status — KG + Vector + Hybrid pipeline state.
   *  The backend endpoint is vector-centric (status/storage/source_items/…); the
   *  home pillars + topbar chip want a `pipelines` view keyed by
   *  knowledge_graph / vector_index / hybrid. Synthesize that shape from the real
   *  index status (vectors) plus the KG stats endpoint (entities). Nothing is
   *  fabricated: if the index endpoint is unavailable we report unavailable (so
   *  the UI shows the honest empty state), and a missing graph-stats count yields
   *  an "unavailable" graph pillar rather than a fake number. */
  async indexStatus() {
    const res = await raw("/api/index/status");
    if (!(res.ok && res.data && !res.data.raw)) {
      return { ok: false, status: res.status, data: EMPTY_INDEX_STATUS, source: "unavailable", error: res.error };
    }
    const idx = res.data;
    let entities = null;
    const gs = await raw("/knowledge-graph/stats");
    if (gs.ok && gs.data && !gs.data.raw) {
      const g = gs.data;
      const n = g.total_nodes ?? g.nodes_total ?? (g.nodes && (g.nodes.total ?? g.nodes.count));
      if (n !== undefined && n !== null) entities = Number(n) || 0;
    }
    const vectors = Number(idx.indexed_items ?? idx.ready_items) || 0;
    const vstate = idx.status === "ready" ? "ready" : "pending";
    const pipelines = {
      knowledge_graph: { state: entities === null ? "unavailable" : "ready", entities: entities ?? 0 },
      vector_index: { state: vstate, vectors },
      hybrid: { state: vstate, strategy: vstate === "ready" ? "fused" : "pending" },
    };
    return { ok: true, status: res.status, data: { ...idx, pipelines }, source: "live" };
  },

  /** POST /api/index/rebuild — rebuild the derived vector index (real run). */
  rebuildIndex(opts = {}) {
    return raw("/api/index/rebuild", { method: "POST", body: { full: false, include_nodes: true, include_chunks: true, ...opts } });
  },

  /** GET /api/graph — knowledge graph (nodes + edges). Falls back through the
   *  current /knowledge-graph/graph route before reporting unavailable. */
  async graph(params = {}) {
    const qs = new URLSearchParams(params).toString();
    const primary = await raw(`/api/graph${qs ? "?" + qs : ""}`);
    if (primary.ok && primary.data && Array.isArray(primary.data.nodes)) {
      return { ...primary, source: "live" };
    }
    const legacy = await raw("/knowledge-graph/graph");
    if (legacy.ok && legacy.data && Array.isArray(legacy.data.nodes)) {
      return { ...legacy, source: "live" };
    }
    return { ok: false, status: primary.status || legacy.status || 0, data: { nodes: [], edges: [] }, source: "unavailable", error: primary.error || legacy.error };
  },

  graphStats() {
    return withFallback("/knowledge-graph/stats", {}, EMPTY_GRAPH_STATS);
  },

  /** POST /api/search/hybrid — fused KG + vector retrieval.
   *  The backend returns `{ matches: [...] }` where each match carries
   *  `source_scores: { keyword, vector, graph }`. Normalize that into the flat
   *  result shape the view renders (title/path/snippet/score + per-signal). A
   *  legacy `results` array is also accepted defensively. */
  async hybridSearch(query, opts = {}) {
    const res = await raw("/api/search/hybrid", { method: "POST", body: { query, ...opts } });
    const live = res.ok && res.data
      ? (Array.isArray(res.data.matches) ? res.data.matches
        : Array.isArray(res.data.results) ? res.data.results
        : null)
      : null;
    if (live) {
      const items = live.map((m) => {
        const ss = m.source_scores || {};
        const meta = m.metadata || {};
        return {
          id: m.id || m.node_id,
          title: m.title || m.id || "Untitled",
          path: meta.path || meta.source || m.path || m.type || "",
          snippet: m.snippet || m.summary || "",
          score: typeof m.score === "number" ? m.score : 0,
          vector: Number(ss.vector ?? m.vector) || 0,
          lexical: Number(ss.keyword ?? m.lexical) || 0,
          graph: Number(ss.graph ?? m.graph) || 0,
        };
      });
      return { ok: true, status: res.status, data: items, source: "live", weights: res.data.weights || null };
    }
    return { ok: false, status: res.status, data: [], source: "unavailable", error: res.error };
  },

  /* ── Existing surfaces (used where helpful, all fallback-safe) ──────── */
  workspaceOs() { return withFallback("/workspace/os", {}, EMPTY_WORKSPACE_OS); },
  async models() {
    const res = await raw("/models");
    if (res.ok && res.data && !res.data.raw) {
      const data = res.data;
      const loadedIds = Array.isArray(data.loaded) ? data.loaded : [];
      const recommended = Array.isArray(data.recommended) ? data.recommended.map((m) => ({
        ...m,
        name: m.name || m.display_name || m.id,
        family: m.family || m.modality || "local",
        state: loadedIds.includes(m.id) || data.current === m.id ? "loaded" : "available",
      })) : [];
      const loadedOnly = loadedIds
        .filter((id) => !recommended.some((m) => m.id === id))
        .map((id) => ({ id, name: id, family: "local", state: data.current === id ? "loaded" : "available" }));
      return {
        ok: true,
        status: res.status,
        source: "live",
        data: { ...data, catalog: Array.isArray(data.catalog) ? data.catalog : [...recommended, ...loadedOnly] },
      };
    }
    return { ok: false, status: res.status, data: { current: null, catalog: [] }, source: "unavailable", error: res.error };
  },
  loadModel(modelId, engine) {
    return raw("/models/load", { method: "POST", body: { model_id: modelId, engine: engine || null } });
  },
  unloadModel(modelId) {
    return raw(`/models/unload/${encodeURIComponent(modelId)}`, { method: "DELETE" });
  },
  sysinfo() { return withFallback("/local/sysinfo", {}, EMPTY_SYSINFO); },

  /** POST /upload/document — manual document ingest (multipart/form-data).
   *  Real backend path: parse → chunk → embed → knowledge-graph ingest
   *  (latticeai/api/tools.py:/upload/document). Returns { ok, status, data,
   *  source }; never throws. FormData must NOT carry a JSON Content-Type — the
   *  browser sets the multipart boundary itself. */
  async uploadDocument(file) {
    const ws = store.get().workspaceId;
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch("/upload/document", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Accept": "application/json", ...(ws ? { "X-Workspace-Id": ws } : {}) },
        body: form,
      });
      let data = null;
      const text = await res.text();
      if (text) { try { data = JSON.parse(text); } catch { data = { raw: text }; } }
      return { ok: res.ok, status: res.status, data, source: res.ok ? "live" : "unavailable" };
    } catch (err) {
      return { ok: false, status: 0, data: null, source: "unavailable", error: String(err) };
    }
  },

  adminSummary() { return withFallback("/admin/summary", {}, EMPTY_ADMIN.summary); },
  adminUsers() { return withFallback("/admin/users", {}, EMPTY_ADMIN.users); },
  adminAudit() { return withFallback("/admin/audit", {}, EMPTY_ADMIN.audit); },
  adminSecurity() { return withFallback("/admin/security/overview", {}, EMPTY_ADMIN.security); },
  adminRoles() { return withFallback("/admin/roles", {}, EMPTY_ADMIN.roles); },
  adminPolicies() { return withFallback("/admin/policies", {}, EMPTY_ADMIN.policies); },
  vpcStatus() { return withFallback("/vpc/status", {}, EMPTY_ADMIN.vpc); },

  /* ── Embeddings (real backend: /api/embeddings/*) ───────────────────── */
  /** GET /api/embeddings/status — active provider, grade, dimensions, last index. */
  async embeddingsStatus() {
    const res = await raw("/api/embeddings/status");
    if (res.ok && res.data && res.data.provider) {
      return { ok: true, status: res.status, data: res.data, source: "live" };
    }
    // No backend → report unavailable honestly (never fabricate a provider).
    return {
      ok: false, status: res.status, source: "unavailable",
      data: { provider: null, active_provider: null, model: null,
        model_id: null, dimensions: null, grade: "unavailable",
        state: "unavailable", fell_back: false, health: { status: "unavailable", detail: "backend unavailable" },
        last_indexed_at: null },
    };
  },
  embeddingsProviders() { return withFallback("/api/embeddings/providers", {}, { active: "hash", providers: [] }); },

  /* ── Agents (real backend: AgentRuntime /agents/api/runtime/*) ───────── */
  /** GET /agents/api/runtime/status — roles, roster, runs, health from the runtime. */
  async agentRuntime() {
    const res = await raw("/agents/api/runtime/status");
    if (res.ok && res.data && res.data.runtime && Array.isArray(res.data.agents)) {
      return { ok: true, status: res.status, data: res.data, source: "live" };
    }
    // Fallback: unavailable roster, no fabricated run ledger.
    return {
      ok: false, status: res.status, source: "unavailable",
      data: { runtime: { ready: false, total_runs: 0, active_runs: 0 },
        health: { status: "unknown", checks: {} }, roles: [],
        agents: [], runs: [] },
    };
  },
  /** POST /agents/api/run — execute the multi-agent pipeline for a goal. */
  runAgent(goal, roles) { return raw("/agents/api/run", { method: "POST", body: { goal, roles: roles || [] } }); },

  /* ── Local computer memory (real backend: /workspace/computer-memory) ── */
  computerMemory() { return raw("/workspace/computer-memory"); },
  setComputerMemory(enabled) {
    return raw("/workspace/computer-memory", { method: "POST", body: { enabled, consent: { approved: !!enabled } } });
  },

  /* ── Organization workspaces (real backend: /workspace/orgs) ────────── */
  createOrg(name) { return raw("/workspace/orgs", { method: "POST", body: { name } }); },

  /* ── Chat (real backend: SSE /chat + /history/*) ────────────────────── */

  /** GET /history/conversations — conversation list. */
  async chatHistory() {
    const res = await raw("/history/conversations");
    const list = res.ok && Array.isArray(res.data) ? res.data
      : res.ok && res.data && Array.isArray(res.data.conversations) ? res.data.conversations
      : null;
    if (list) return { ok: true, status: res.status, data: list, source: "live" };
    return { ok: false, status: res.status, data: [], source: "unavailable" };
  },

  /** GET /history/conversations/{id} — messages for one conversation. */
  async conversation(id) {
    const res = await raw(`/history/conversations/${encodeURIComponent(id)}`);
    if (res.ok && res.data && Array.isArray(res.data.messages)) {
      return { ok: true, status: res.status, data: res.data.messages, source: "live" };
    }
    return { ok: false, status: res.status, data: [], source: "unavailable" };
  },

  deleteConversation(id) {
    return raw(`/history/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  /**
   * POST /chat — streams the assistant reply over SSE.
   * Parses `data: {chunk, model, trace}` events (terminator `[DONE]`), calling
   * onChunk(delta, fullText) and onTrace(trace). If the endpoint is missing or
 * not an event-stream, reports that chat is unavailable (no generated answer is
 * invented). Resolves { source, text, trace, model, aborted }.
   */
  async streamChat(body, { onChunk, onTrace, signal } = {}) {
    const ws = store.get().workspaceId;
    let res;
    try {
      res = await fetch("/chat", {
        method: "POST",
        credentials: "same-origin",
        signal,
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
          ...(ws ? { "X-Workspace-Id": ws } : {}),
        },
        body: JSON.stringify({ stream: true, max_tokens: 2048, temperature: 0.2, ...body }),
      });
    } catch (err) {
      if (err && err.name === "AbortError") return { source: "live", text: "", aborted: true };
      return simulateChat(body, { onChunk, onTrace, signal });
    }
    const ctype = res.headers.get("content-type") || "";
    if (!res.ok) {
      let data = null;
      try { data = await res.clone().json(); } catch {}
      const detail = data && (data.detail || data.message || data.error);
      const noModel = data && (data.error === "no_model_loaded" || /no .*model .*loaded/i.test(String(detail || "")));
      if (noModel) {
        return { source: "live", text: "", error: "no_model_loaded", errorMessage: String(detail || "No local model is loaded.") };
      }
      return simulateChat(body, { onChunk, onTrace, signal });
    }
    if (!res.body || !ctype.includes("text/event-stream")) {
      return simulateChat(body, { onChunk, onTrace, signal });
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "", text = "", trace = null, model = null;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop();
        for (const part of parts) {
          const line = part.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const rawData = line.slice(5).trim();
          if (rawData === "[DONE]") return { source: "live", text, trace, model };
          let data; try { data = JSON.parse(rawData); } catch { continue; }
          // Standard chat streams `chunk`; the document-generation path streams
          // `text` (report body + footnotes). Accept both so doc requests render
          // instead of falsely reporting the backend as unreachable.
          const delta = data.chunk || data.text;
          if (delta) { text += delta; onChunk && onChunk(delta, text); }
          if (data.model) model = data.model;
          if (data.trace) { trace = data.trace; onTrace && onTrace(trace); }
        }
      }
    } catch (err) {
      if (err && err.name === "AbortError") return { source: "live", text, trace, model, aborted: true };
      if (!text) return simulateChat(body, { onChunk, onTrace, signal });
    }
    return { source: "live", text, trace, model };
  },

  /* ── v3.2 platform surfaces (all fallback-safe; never fabricate) ─────── */

  // Agent Registry (Part 2)
  agentRegistry(type) { return withFallback(`/agents/api/registry${type ? "?type=" + encodeURIComponent(type) : ""}`, {}, { agents: [], counts: {}, types: [] }); },
  agentCapabilities() { return withFallback("/agents/api/registry/capabilities", {}, { capabilities: {} }); },
  registerAgent(body) { return raw("/agents/api/registry", { method: "POST", body }); },
  updateAgent(id, body) { return raw(`/agents/api/registry/${encodeURIComponent(id)}`, { method: "PATCH", body }); },
  removeAgent(id) { return raw(`/agents/api/registry/${encodeURIComponent(id)}`, { method: "DELETE" }); },
  agentRunDetail(runId) { return raw(`/agents/api/runs/${encodeURIComponent(runId)}`); },
  agentRunReplay(runId) { return raw(`/agents/api/runs/${encodeURIComponent(runId)}/replay`); },
  stopAgentRun(runId) { return raw(`/agents/api/runs/${encodeURIComponent(runId)}/stop`, { method: "POST" }); },

  // Marketplace + Templates (Parts 3, 4)
  templates(kind) { return withFallback(`/marketplace/templates${kind ? "?kind=" + encodeURIComponent(kind) : ""}`, {}, { templates: [], kinds: [] }); },
  templateRegistry() { return withFallback("/marketplace/templates/registry", {}, { registry: [] }); },
  exportTemplate(kind, id) { return raw(`/marketplace/templates/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/export`); },
  importTemplate(data) { return raw("/marketplace/templates/import", { method: "POST", body: { data } }); },
  installTemplate(data) { return raw("/marketplace/templates/install", { method: "POST", body: { data } }); },
  cloneTemplate(kind, id, name) { return raw(`/marketplace/templates/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/clone`, { method: "POST", body: { name } }); },
  pluginsRegistry() { return withFallback("/plugins/registry", {}, { plugins: [] }); },
  pluginsDirectory() { return withFallback("/plugins/directory", {}, { plugins: [], categories: [] }); },

  // Workflow Agents (Part 5)
  workflowDefinitions() { return withFallback("/workflows/api/definitions", {}, { workflows: [] }); },
  createWorkflow(body) { return raw("/workflows/api/definitions", { method: "POST", body }); },
  runWorkflow(id, body = {}) { return raw(`/workflows/api/definitions/${encodeURIComponent(id)}/run`, { method: "POST", body }); },
  workflowRuns() { return withFallback("/workflows/api/runs", {}, { runs: [] }); },
  workflowReplay(runId) { return raw(`/workflows/api/runs/${encodeURIComponent(runId)}/replay`); },

  // Long-Term Memory + Memory Manager (Parts 7, 8)
  memoryManager() { return withFallback("/api/memory/manager", {}, { sources: [], tiers: [], usage: {} }); },
  memoryTiers() { return withFallback("/api/memory/tiers", {}, { tiers: [], workspace_kinds: [] }); },
  memoryInspect(source, limit = 50) { return withFallback(`/api/memory/inspect?source=${encodeURIComponent(source)}&limit=${limit}`, {}, { items: [] }); },
  memoryRecall(query, limit = 20) { return raw("/api/memory/recall", { method: "POST", body: { query, limit } }); },
  memoryPrune(body) { return raw("/api/memory/prune", { method: "POST", body }); },
  memoryCompact() { return raw("/api/memory/compact", { method: "POST", body: {} }); },
  memoryRebuild(target = "vector") { return raw("/api/memory/rebuild", { method: "POST", body: { target } }); },
  memoryClear(scope, confirm = true) { return raw("/api/memory/clear", { method: "POST", body: { scope, confirm } }); },
  workspaceMemories(kind) { return withFallback(`/workspace/memories${kind ? "?kind=" + encodeURIComponent(kind) : ""}`, {}, { memories: [] }); },

  // Skills Registry (Part 9)
  skills() { return withFallback("/workspace/skills", {}, { skills: [] }); },
  skillEnable(skill) { return raw("/workspace/skills/enable", { method: "POST", body: { skill } }); },
  skillDisable(skill) { return raw("/workspace/skills/disable", { method: "POST", body: { skill } }); },
  skillInstall(skill, plugin) { return raw("/workspace/skills/install", { method: "POST", body: { skill, plugin: plugin || "" } }); },
  skillUninstall(skill) { return raw("/workspace/skills/uninstall", { method: "POST", body: { skill } }); },
  skillsMarketplace() { return withFallback("/skills/marketplace", {}, { skills: [], categories: [] }); },

  // Hooks Registry (Part 10)
  hooks(kind) { return withFallback(`/api/hooks${kind ? "?kind=" + encodeURIComponent(kind) : ""}`, {}, { hooks: [], kinds: [], counts: {} }); },
  hookEnable(hook_id, enabled = true) { return raw("/api/hooks/enable", { method: "POST", body: { hook_id, enabled } }); },
  hookDisable(hook_id) { return raw("/api/hooks/disable", { method: "POST", body: { hook_id, enabled: false } }); },
  hookReorder(kind, ordered_ids) { return raw("/api/hooks/reorder", { method: "POST", body: { kind, ordered_ids } }); },
  hookRegister(body) { return raw("/api/hooks/register", { method: "POST", body }); },
  hookRemove(hook_id) { return raw(`/api/hooks/${encodeURIComponent(hook_id)}`, { method: "DELETE" }); },

  // Tool Registry + MCP (Parts 11, 12)
  toolPermissions() { return withFallback("/tools/permissions", {}, { permissions: [] }); },
  mcpTools() { return withFallback("/mcp/tools", {}, { tools: [], installed_mcps: [] }); },
  mcpInstalled() { return withFallback("/mcp/installed", {}, { installed: [] }); },
  mcpClaudeServers() { return withFallback("/mcp/claude-code-servers", {}, { servers: [] }); },
  mcpCustom() { return withFallback("/mcp/custom", {}, { custom: [] }); },
  mcpRecommend(query, limit = 6) { return raw("/mcp/recommend", { method: "POST", body: { query, limit } }); },

  /* ── v3.4 Platform Completion ───────────────────────────────────────────
   * Uploaded documents in Files, Connect Folder + Folder Watch over the real
   * on-device runtime, the Local Agent status, and Hooks dispatch/run-log.
   * All endpoints are real (latticeai/api + knowledge_graph_api); fallback-safe. */

  /** GET /knowledge-graph/documents — uploaded + indexed docs with index state. */
  async documents(limit = 200) {
    const res = await raw(`/knowledge-graph/documents?limit=${encodeURIComponent(limit)}`);
    if (res.ok && res.data && Array.isArray(res.data.documents)) {
      return { ok: true, status: res.status, data: res.data.documents, source: "live", total: res.data.total };
    }
    return { ok: false, status: res.status, data: [], source: "unavailable", error: res.error };
  },

  // Local Agent (the on-device Lattice runtime: real GET /api/local-agent/status)
  async localAgent() {
    const res = await raw("/api/local-agent/status");
    if (res.ok && res.data && res.data.agent) {
      return { ok: true, status: res.status, data: res.data, source: "live" };
    }
    return {
      ok: false, status: res.status, source: "unavailable",
      data: { agent: { online: false }, health: {}, folders: { connected: 0, watching: 0 }, watch: { available: false, active: {} }, sources: [] },
    };
  },

  // Connect Folder + Folder Watch (real backend: /knowledge-graph/local/*)
  localRoots() { return withFallback("/knowledge-graph/local/roots", {}, { roots: [] }); },
  async localSources() {
    const res = await raw("/knowledge-graph/local/sources");
    if (res.ok && res.data && Array.isArray(res.data.sources)) {
      return { ok: true, status: res.status, data: res.data, source: "live" };
    }
    return { ok: false, status: res.status, data: { sources: [], watch: { available: false, active: {} } }, source: "unavailable" };
  },
  localWatchStatus() { return raw("/knowledge-graph/local/watch/status"); },
  localWatchStop(source_id) { return raw("/knowledge-graph/local/watch/stop", { method: "POST", body: { source_id } }); },
  approvePermission(token) { return raw(`/permissions/approve/${encodeURIComponent(token)}`, { method: "POST" }); },
  indexFolder(path, opts = {}) {
    return raw("/knowledge-graph/local/index", { method: "POST", body: { path, ...opts } });
  },
  /** One-call Connect Folder: request → self-approve (the click is the consent)
   *  → index (+ optional watch). Returns { ok, data, error }. */
  async connectFolder(path, { watch = true, includeOcr = false } = {}) {
    const probe = await raw("/knowledge-graph/local/index", { method: "POST", body: { path, approved: false } });
    const token = probe.data && probe.data.approval_token;
    if (!token) {
      const detail = (probe.data && (probe.data.detail || probe.data.error)) || "the runtime did not return an approval token";
      return { ok: false, error: detail, status: probe.status };
    }
    const approved = await raw(`/permissions/approve/${encodeURIComponent(token)}`, { method: "POST" });
    if (!approved.ok) {
      const detail = (approved.data && (approved.data.detail || approved.data.error)) || "approval failed";
      return { ok: false, error: detail, status: approved.status };
    }
    const res = await raw("/knowledge-graph/local/index", {
      method: "POST",
      body: { path, approved: true, approval_token: token, watch_enabled: watch, include_ocr: includeOcr, consent: { approved: true, source: "files-ui" } },
    });
    if (res.ok && res.data && !res.data.detail) return { ok: true, data: res.data, status: res.status };
    return { ok: false, error: (res.data && (res.data.detail || res.data.error)) || "indexing failed", status: res.status, data: res.data };
  },

  // Hooks dispatch (real backend: POST /api/hooks/run + GET /api/hooks/runs)
  hookRun(body) { return raw("/api/hooks/run", { method: "POST", body }); },
  hookRuns(limit = 50, kind) { return withFallback(`/api/hooks/runs?limit=${encodeURIComponent(limit)}${kind ? "&kind=" + encodeURIComponent(kind) : ""}`, {}, { runs: [], total: 0 }); },

  /* ── v3.6 Knowledge Graph First: ingestion provenance + portability ─────
   * The graph is the durable asset; these surface its health, where every node
   * came from, and local export/import/backup. All fallback-safe; never fake. */

  /** GET /api/knowledge-graph/portability — schema versions + stats + provenance counts. */
  async kgPortability() {
    const res = await raw("/api/knowledge-graph/portability");
    if (res.ok && res.data && res.data.available) {
      return { ok: true, status: res.status, data: res.data, source: "live" };
    }
    return {
      ok: false, status: res.status, source: "unavailable",
      data: { available: false, graph_schema_version: null, embed_dim: null,
        stats: { nodes: {}, edges: {} },
        provenance: { total: 0, by_source_type: {}, embedded: 0, duplicates: 0, last_ingested_at: null } },
    };
  },

  /** GET /api/knowledge-graph/provenance — recent ingestions (newest first). */
  kgProvenance(limit = 50, sourceType) {
    const qs = `?limit=${encodeURIComponent(limit)}${sourceType ? "&source_type=" + encodeURIComponent(sourceType) : ""}`;
    return withFallback(`/api/knowledge-graph/provenance${qs}`, {}, { items: [], count: 0 });
  },

  /** POST /api/knowledge-graph/export — logical JSON export of the whole graph. */
  graphExport() { return raw("/api/knowledge-graph/export", { method: "POST", body: {} }); },

  /** POST /api/knowledge-graph/import — import an export artifact (merge|replace). */
  graphImport(artifact, mode = "merge", dryRun = false) {
    return raw("/api/knowledge-graph/import", { method: "POST", body: { artifact, mode, dry_run: dryRun } });
  },

  /** POST /api/knowledge-graph/backup — binary backup (sqlite + blobs) to a local zip. */
  graphBackup() { return raw("/api/knowledge-graph/backup", { method: "POST", body: {} }); },

  /** POST /api/browser/read-url — fetch a public URL locally into the graph. */
  browserReadUrl(url) { return raw("/api/browser/read-url", { method: "POST", body: { url } }); },
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Transparent unavailable stream — used only when no chat backend is available. */
async function simulateChat(body, { onChunk, onTrace, signal } = {}) {
  const q = (body && body.message) || "your question";
  const reply =
    `Chat is unavailable because the Lattice backend or active model is not reachable. ` +
    `Start the server, load a model, and rebuild retrieval before sending “${q}”.`;
  let text = "";
  for (const word of reply.split(" ")) {
    if (signal && signal.aborted) return { source: "unavailable", text, aborted: true };
    const delta = (text ? " " : "") + word;
    text += delta;
    onChunk && onChunk(delta, text);
    await sleep(16);
  }
  onTrace && onTrace(null);
  return { source: "unavailable", text, trace: null };
}
