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

import { store } from "./store.js";

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

  /** GET /api/index/status — KG + Vector + Hybrid pipeline state. */
  indexStatus() {
    return withFallback("/api/index/status", {}, EMPTY_INDEX_STATUS);
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
          if (data.chunk) { text += data.chunk; onChunk && onChunk(data.chunk, text); }
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
