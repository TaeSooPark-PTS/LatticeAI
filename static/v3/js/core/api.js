/* ============================================================================
 * Lattice AI v3 — Integration adapter
 *
 * Every adapter call hits the REAL endpoint first (including the documented
 * future surfaces /api/index/status, /api/graph, /api/search/hybrid). If that
 * endpoint is missing/unavailable, it degrades to a clearly-labeled SAMPLE
 * payload from fixtures.js and reports `source: "placeholder"` so the UI can
 * badge it. No backend logic is implemented here — only transport + graceful
 * fallback, which is what keeps the v3 frontend integration-ready.
 *
 * Return shape (never throws): { ok, status, data, source, error }
 *   source: "live"        → returned by a real backend endpoint
 *           "placeholder" → fixture fallback (backend not yet available)
 * ========================================================================== */

import { store } from "./store.js";
import * as fx from "./fixtures.js";

const TIMEOUT_MS = 8000;

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

/** Try the live endpoint; on any non-2xx/transport failure, use the fixture. */
async function withFallback(path, opts, fixture) {
  const res = await raw(path, opts);
  if (res.ok && res.data && !res.data.raw) {
    return { ...res, source: "live" };
  }
  return { ok: true, status: res.status, data: typeof fixture === "function" ? fixture() : fixture, source: "placeholder", error: res.error };
}

export const api = {
  raw,

  /** Generic GET with fixture fallback. */
  async get(path, fixture = null) {
    return withFallback(path, {}, fixture);
  },

  /* ── Documented future surfaces ─────────────────────────────────────── */

  /** GET /api/index/status — KG + Vector + Hybrid pipeline state. */
  indexStatus() {
    return withFallback("/api/index/status", {}, fx.INDEX_STATUS);
  },

  /** GET /api/graph — knowledge graph (nodes + edges). Falls back through the
   *  current /knowledge-graph/graph route before the fixture. */
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
    return { ok: true, status: 200, data: fx.GRAPH, source: "placeholder" };
  },

  graphStats() {
    return withFallback("/knowledge-graph/stats", {}, fx.GRAPH_STATS);
  },

  /** POST /api/search/hybrid — fused KG + vector retrieval. */
  async hybridSearch(query, opts = {}) {
    const res = await raw("/api/search/hybrid", { method: "POST", body: { query, ...opts } });
    if (res.ok && res.data && Array.isArray(res.data.results)) {
      return { ...res, data: res.data.results, source: "live" };
    }
    return { ok: true, status: res.status, data: fx.hybridResults(query), source: "placeholder", error: res.error };
  },

  /* ── Existing surfaces (used where helpful, all fallback-safe) ──────── */
  workspaceOs() { return withFallback("/workspace/os", {}, fx.WORKSPACE_OS); },
  models() { return withFallback("/models", {}, fx.MODELS); },
  sysinfo() { return withFallback("/local/sysinfo", {}, fx.SYSINFO); },

  adminSummary() { return withFallback("/admin/summary", {}, fx.ADMIN.summary); },
  adminUsers() { return withFallback("/admin/users", {}, fx.ADMIN.users); },
  adminAudit() { return withFallback("/admin/audit", {}, { recent_events: fx.ADMIN.audit }); },
  vpcStatus() { return withFallback("/vpc/status", {}, fx.ADMIN.vpc); },

  /* ── Chat (real backend: SSE /chat + /history/*) ────────────────────── */

  /** GET /history/conversations — conversation list. */
  async chatHistory() {
    const res = await raw("/history/conversations");
    const list = res.ok && Array.isArray(res.data) ? res.data
      : res.ok && res.data && Array.isArray(res.data.conversations) ? res.data.conversations
      : null;
    if (list) return { ok: true, status: res.status, data: list, source: "live" };
    return { ok: true, status: res.status, data: fx.CHAT.conversations, source: "placeholder" };
  },

  /** GET /history/conversations/{id} — messages for one conversation. */
  async conversation(id) {
    const res = await raw(`/history/conversations/${encodeURIComponent(id)}`);
    if (res.ok && res.data && Array.isArray(res.data.messages)) {
      return { ok: true, status: res.status, data: res.data.messages, source: "live" };
    }
    const sample = (fx.CHAT.conversations.find((c) => c.id === id) || {}).messages || [];
    return { ok: true, status: res.status, data: sample, source: "placeholder" };
  },

  deleteConversation(id) {
    return raw(`/history/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  /**
   * POST /chat — streams the assistant reply over SSE.
   * Parses `data: {chunk, model, trace}` events (terminator `[DONE]`), calling
   * onChunk(delta, fullText) and onTrace(trace). If the endpoint is missing or
   * not an event-stream, degrades to a clearly-labeled SAMPLE stream (no real
   * generation is invented). Resolves { source, text, trace, model, aborted }.
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
    if (!res.ok || !res.body || !ctype.includes("text/event-stream")) {
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

/** Transparent SAMPLE stream — used only when no chat backend is available. */
async function simulateChat(body, { onChunk, onTrace, signal } = {}) {
  const q = (body && body.message) || "your question";
  const reply =
    `This is a sample response. Connect a local model and the v3 retrieval backend ` +
    `(/api/search/hybrid, /api/graph) to ground answers to “${q}” in your workspace. ` +
    `The context panel shows the knowledge-graph entities, vector matches, and indexed ` +
    `files that would be used to answer — this preview does not run a model.`;
  let text = "";
  for (const word of reply.split(" ")) {
    if (signal && signal.aborted) return { source: "placeholder", text, aborted: true };
    const delta = (text ? " " : "") + word;
    text += delta;
    onChunk && onChunk(delta, text);
    await sleep(16);
  }
  const trace = fx.sampleTrace(q);
  onTrace && onTrace(trace);
  return { source: "placeholder", text, trace };
}

export { fx };
