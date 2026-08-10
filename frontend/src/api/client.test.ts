import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "./client";
import { useAppStore } from "@/store/appStore";

// A real SSE body: the fetch stub returns a Response whose ReadableStream the
// parser consumes exactly like the live /chat endpoint.
function sseResponse(frames: string[]): Response {
  return new Response(frames.join(""), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamChat SSE parsing", () => {
  it("routes agent_step named frames to onAgentStep and keeps data frames intact", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: agent_step\ndata: {"phase":"plan","event":"planned","step":1}\n\n',
      'event: agent_step\ndata: {"phase":"execute","event":"tool","action":"write_file","path":"notes.html","ok":true,"future_field":123}\n\n',
      'data: {"chunk":"안녕"}\n\n',
      'data: {"agent":{"status":"ok","final_state":"DONE","loop":{"repairs":{"json_fence":2},"parse_recovered":1},"steps":[]}}\n\n',
      "data: [DONE]\n\n",
    ])));
    const onAgentStep = vi.fn();
    const onChunk = vi.fn();
    const onAgent = vi.fn();

    const result = await latticeApi.streamChat(
      { message: "hi" },
      { onAgentStep, onChunk, onAgent },
    );

    expect(onAgentStep).toHaveBeenCalledTimes(2);
    expect(onAgentStep.mock.calls[0][0]).toMatchObject({ phase: "plan", event: "planned" });
    expect(onAgentStep.mock.calls[1][0]).toMatchObject({
      phase: "execute",
      event: "tool",
      action: "write_file",
      path: "notes.html",
      ok: true,
    });
    expect(onChunk).toHaveBeenCalledWith("안녕", "안녕");
    expect(onAgent).toHaveBeenCalledTimes(1);
    expect(onAgent.mock.calls[0][0]).toMatchObject({
      final_state: "DONE",
      loop: { repairs: { json_fence: 2 }, parse_recovered: 1 },
    });
    expect(result.text).toBe("안녕");
  });

  it("ignores unknown named events and malformed agent_step frames without breaking the stream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: future_event\ndata: {"x":1}\n\n',
      "event: agent_step\ndata: {broken json}\n\n",
      'event: agent_step\ndata: ["arrays","are","not","steps"]\n\n',
      'data: {"chunk":"ok"}\n\n',
      "data: [DONE]\n\n",
    ])));
    const onAgentStep = vi.fn();
    const onChunk = vi.fn();

    const result = await latticeApi.streamChat({ message: "hi" }, { onAgentStep, onChunk });

    expect(onAgentStep).not.toHaveBeenCalled();
    expect(onChunk).toHaveBeenCalledWith("ok", "ok");
    expect(result.text).toBe("ok");
  });
});

/**
 * The rest of `latticeApi`: the request each method actually sends, and what a
 * caller is handed when the local service does not answer.
 *
 * These two properties are what the whole UI leans on. Every page renders from
 * `res.data` unconditionally — `data.matches.map(...)`, `data.nodes.length` —
 * so a failed request that returned `undefined`, or `{}` where a list was
 * declared, is a white screen rather than an empty state. The declared shape is
 * the contract, and it has to survive a 500, a network drop and a timeout.
 */

type Recorded = { url: URL; method: string; body: unknown; headers: Headers };

/**
 * `openapi-fetch` destructures `globalThis.fetch` when the client is *created*,
 * and `base.ts` caches one client per origin for the lifetime of the module. So
 * a `vi.stubGlobal("fetch", ...)` installed inside a test is never seen: the
 * cached client is still holding whichever function existed when the first
 * request went out.
 *
 * Installing one permanent dispatcher at module scope — before any client can
 * exist — and swapping only the responder behind it keeps the indirection the
 * tests need without reaching into `base.ts` to expose its cache.
 */
let respondWith:
  | ((url: URL, signal?: AbortSignal | null) => Response | Promise<Response>)
  | null = null;
const dispatcherCalls: Recorded[] = [];
const realFetch = globalThis.fetch;

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  if (!respondWith) return realFetch(input as RequestInfo, init);
  // `openapi-fetch` hands fetch a fully-built `Request`, so method, headers and
  // body are on the request rather than on `init`. Reading only `init` reported
  // every POST as a bodiless GET with no headers.
  const request = input instanceof Request ? input : null;
  const raw = request ? request.url : typeof input === "string" ? input : (input as URL).href;
  const url = new URL(raw, "http://localhost");
  const rawBody =
    request ? await request.clone().text() : typeof init?.body === "string" ? init.body : "";
  // JSON bodies are recorded parsed; a multipart upload keeps its FormData so
  // the test can look at the parts instead of a boundary string.
  let body: unknown = init?.body instanceof FormData ? init.body : undefined;
  if (rawBody) {
    try {
      body = JSON.parse(rawBody);
    } catch {
      body = rawBody;
    }
  }
  dispatcherCalls.push({
    url,
    method: (request?.method || init?.method || "GET").toUpperCase(),
    body,
    headers: request ? request.headers : new Headers(init?.headers as HeadersInit),
  });
  return respondWith(url, request?.signal ?? init?.signal);
}) as typeof globalThis.fetch;

/**
 * A base URL is required, not optional, in jsdom.
 *
 * In the browser `apiBase()` resolves to "" — same origin — and the relative
 * path is resolved against the document. jsdom's fetch has no such base, so
 * `openapi-fetch` throws "Failed to parse URL" while building the Request and
 * every call comes back as status 0 without a request ever being made. Naming
 * an absolute origin is what makes the request observable; `base.ts` keys its
 * client cache on this value, so the client for it is built after the
 * dispatcher above is installed.
 */
const TEST_ORIGIN = "http://localhost";

function recordFetch(respond: (url: URL) => Response) {
  useAppStore.setState({ apiBase: TEST_ORIGIN });
  dispatcherCalls.length = 0;
  respondWith = respond;
  return dispatcherCalls;
}

function failFetchWith(error: Error) {
  useAppStore.setState({ apiBase: TEST_ORIGIN });
  dispatcherCalls.length = 0;
  respondWith = () => {
    throw error;
  };
  return dispatcherCalls;
}

afterEach(() => {
  respondWith = null;
  dispatcherCalls.length = 0;
  useAppStore.setState({ apiBase: "" });
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("what the client sends", () => {
  it("puts list-shaped query parameters on the URL, not in a body", async () => {
    const calls = recordFetch(() => jsonResponse({ nodes: [], edges: [] }));

    await latticeApi.graphPreview(12);

    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("GET");
    expect(calls[0].url.pathname).toBe("/knowledge-graph/graph");
    expect(calls[0].url.searchParams.get("limit")).toBe("12");
    expect(calls[0].body).toBeUndefined();
  });

  it("percent-encodes an id that would otherwise change the path", async () => {
    // Proposal ids come from the server and are not guaranteed to be
    // path-safe; an unescaped "/" would silently address a different route.
    const calls = recordFetch(() => jsonResponse({}));

    await latticeApi.approveProposal("item/42 with space");

    expect(calls[0].url.pathname).toBe("/api/proposals/item%2F42%20with%20space/approve");
  });

  it("sends the language header on an ordinary API call", async () => {
    const calls = recordFetch(() => jsonResponse({}));

    await latticeApi.health();

    expect(calls[0].headers.get("X-Lattice-Language")).toBeTruthy();
  });
});

describe("what the caller is handed when the service does not answer", () => {
  it("returns the declared shape, emptied, on a server error", async () => {
    recordFetch(() => jsonResponse({ detail: "boom" }, 500));

    const res = await latticeApi.graph();

    expect(res.ok).toBe(false);
    expect(res.status).toBe(500);
    expect(res.source).toBe("unavailable");
    // Not `undefined`, and not `{}` — the page maps over these.
    expect(res.data).toEqual({ nodes: [], edges: [] });
    expect(res.error).toBeTruthy();
  });

  it("returns the declared shape, emptied, when the service is unreachable", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.memoryManager();

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data).toEqual({ sources: [], tiers: [], usage: {} });
    expect(res.error).toBeTruthy();
  });

  it("keeps nested list keys as lists rather than dropping them", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.memoryBrainBrief();

    expect(Array.isArray(res.data.next_actions)).toBe(true);
    expect(Array.isArray(res.data.evidence)).toBe(true);
  });

  it("surfaces a live payload untouched when the call succeeds", async () => {
    recordFetch(() => jsonResponse({ nodes: [{ id: "a" }], edges: [] }));

    const res = await latticeApi.graph();

    expect(res.ok).toBe(true);
    expect(res.source).toBe("live");
    expect(res.data).toEqual({ nodes: [{ id: "a" }], edges: [] });
  });
});

describe("hybridSearch", () => {
  it("normalises a `results` payload to the `matches` the UI reads", async () => {
    // The endpoint has answered under both names across versions; the UI only
    // knows `matches`, so a `results` payload rendered as "nothing found".
    recordFetch(() => jsonResponse({ results: [{ id: "n1" }] }));

    const res = await latticeApi.hybridSearch("workspace");

    expect(res.data.matches).toEqual([{ id: "n1" }]);
  });

  it("leaves a payload that already has matches alone", async () => {
    recordFetch(() => jsonResponse({ matches: [{ id: "n2" }], results: [{ id: "ignored" }] }));

    const res = await latticeApi.hybridSearch("workspace");

    expect(res.data.matches).toEqual([{ id: "n2" }]);
  });

  it("passes weights through only when the caller supplied them", async () => {
    const calls = recordFetch(() => jsonResponse({ matches: [] }));

    await latticeApi.hybridSearch("a");
    await latticeApi.hybridSearch("b", { vector: 0.7 });

    expect(calls[0].body).toEqual({ query: "a" });
    expect(calls[1].body).toEqual({ query: "b", weights: { vector: 0.7 } });
  });

  it("still returns an empty match list when the search fails", async () => {
    recordFetch(() => jsonResponse({ detail: "no index" }, 503));

    const res = await latticeApi.hybridSearch("anything");

    expect(res.ok).toBe(false);
    expect(res.data.matches).toEqual([]);
  });
});

describe("desktopBackendStatus", () => {
  it("says plainly that it is a desktop-only reading, outside the desktop shell", async () => {
    // A browser tab has no Tauri bridge. Reporting "unavailable" with a reason
    // is the honest answer; returning a fabricated ok would put a green dot on
    // a backend nobody asked.
    const res = await latticeApi.desktopBackendStatus();

    expect(res.ok).toBe(false);
    expect(res.source).toBe("unavailable");
    expect(res.error).toMatch(/Tauri/);
  });

  it("reports the status the desktop bridge returns", async () => {
    (window as unknown as { __TAURI__?: unknown }).__TAURI__ = {
      core: { invoke: vi.fn().mockResolvedValue({ running: true, port: 8765 }) },
    };
    try {
      const res = await latticeApi.desktopBackendStatus();
      expect(res.ok).toBe(true);
      expect(res.data).toEqual({ running: true, port: 8765 });
    } finally {
      delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
    }
  });
});

/**
 * Every remaining wrapper, table-driven: the method, path, query and body each
 * one puts on the wire. A wrapper is one line of glue, but a typo in that line
 * is a feature that silently reads the wrong endpoint — and nothing else in the
 * suite would ever notice.
 */

type EndpointCase = {
  name: keyof typeof latticeApi;
  invoke: () => Promise<unknown>;
  method: string;
  path: string;
  query?: Record<string, string>;
  body?: unknown;
};

const api = latticeApi;

const ENDPOINT_TABLE: EndpointCase[] = [
  { name: "raw", invoke: () => api.raw("/health", {}), method: "GET", path: "/health" },
  { name: "health", invoke: () => api.health(), method: "GET", path: "/health" },
  { name: "workspaceOs", invoke: () => api.workspaceOs(), method: "GET", path: "/workspace/os" },
  { name: "workspaceVscodeStatus", invoke: () => api.workspaceVscodeStatus(), method: "GET", path: "/workspace/vscode/status" },
  { name: "indexStatus", invoke: () => api.indexStatus(), method: "GET", path: "/api/index/status" },
  { name: "rebuildIndex", invoke: () => api.rebuildIndex(), method: "POST", path: "/api/index/rebuild", body: { full: false, include_nodes: true, include_chunks: true } },
  { name: "graph", invoke: () => api.graph(), method: "GET", path: "/knowledge-graph/graph" },
  { name: "graphPreview", invoke: () => api.graphPreview(), method: "GET", path: "/knowledge-graph/graph", query: { limit: "48" } },
  { name: "graphStats", invoke: () => api.graphStats(), method: "GET", path: "/knowledge-graph/stats" },
  { name: "graphPortability", invoke: () => api.graphPortability(), method: "GET", path: "/api/knowledge-graph/portability" },
  { name: "brainStorage", invoke: () => api.brainStorage(), method: "GET", path: "/api/brain/storage" },
  { name: "backupHealth", invoke: () => api.backupHealth(), method: "GET", path: "/api/knowledge-graph/backup-health" },
  { name: "dockerPostgres", invoke: () => api.dockerPostgres({ consent: true, dry_run: true, port: 5433 }), method: "POST", path: "/api/brain/storage/postgres/docker", body: { consent: true, dry_run: true, port: 5433 } },
  { name: "migratePostgres", invoke: () => api.migratePostgres({ dsn: "postgresql://x" }), method: "POST", path: "/api/brain/storage/migrate-postgres", body: { dsn: "postgresql://x" } },
  { name: "graphProvenance", invoke: () => api.graphProvenance(), method: "GET", path: "/api/knowledge-graph/provenance", query: { limit: "50" } },
  { name: "graphProvenance", invoke: () => api.graphProvenance(5), method: "GET", path: "/api/knowledge-graph/provenance", query: { limit: "5" } },
  { name: "graphCoverage", invoke: () => api.graphCoverage(), method: "GET", path: "/knowledge-graph/provenance/coverage" },
  { name: "graphExport", invoke: () => api.graphExport(), method: "POST", path: "/api/knowledge-graph/export", body: {} },
  { name: "graphBackup", invoke: () => api.graphBackup(), method: "POST", path: "/api/knowledge-graph/backup", body: {} },
  { name: "graphImport", invoke: () => api.graphImport({ nodes: [] }), method: "POST", path: "/api/knowledge-graph/import", body: { artifact: { nodes: [] }, mode: "merge", dry_run: true } },
  { name: "graphImport", invoke: () => api.graphImport({ nodes: [] }, false), method: "POST", path: "/api/knowledge-graph/import", body: { artifact: { nodes: [] }, mode: "merge", dry_run: false } },
  { name: "brainArchive", invoke: () => api.brainArchive({ path: null, passphrase: "pw" }), method: "POST", path: "/api/knowledge-graph/archive", body: { path: null, passphrase: "pw" } },
  { name: "brainArchiveInspect", invoke: () => api.brainArchiveInspect({ path: "a.brain" }), method: "POST", path: "/api/knowledge-graph/archive/inspect", body: { path: "a.brain" } },
  { name: "brainArchiveVerify", invoke: () => api.brainArchiveVerify({ path: "a.brain", passphrase: "pw" }), method: "POST", path: "/api/knowledge-graph/archive/verify", body: { path: "a.brain", passphrase: "pw" } },
  { name: "brainArchiveRestore", invoke: () => api.brainArchiveRestore({ path: "a.brain", passphrase: "pw", dry_run: true }), method: "POST", path: "/api/knowledge-graph/archive/restore", body: { path: "a.brain", passphrase: "pw", dry_run: true } },
  { name: "brainArchiveImport", invoke: () => api.brainArchiveImport({ path: "a.brain", passphrase: "pw", confirm: true }), method: "POST", path: "/api/knowledge-graph/archive/import", body: { path: "a.brain", passphrase: "pw", confirm: true } },
  { name: "browserReadUrl", invoke: () => api.browserReadUrl("https://example.com"), method: "POST", path: "/api/browser/read-url", body: { url: "https://example.com" } },
  { name: "ingestNote", invoke: () => api.ingestNote("메모"), method: "POST", path: "/knowledge-graph/ingest", body: { type: "note", content: "메모", title: "Brain note", source: "brain_home" } },
  { name: "ingestNote", invoke: () => api.ingestNote("메모", "제목"), method: "POST", path: "/knowledge-graph/ingest", body: { type: "note", content: "메모", title: "제목", source: "brain_home" } },
  { name: "memoryManager", invoke: () => api.memoryManager(), method: "GET", path: "/api/memory/manager" },
  { name: "memoryBrainQuality", invoke: () => api.memoryBrainQuality(), method: "GET", path: "/api/memory/brain-quality" },
  { name: "memoryBrainBrief", invoke: () => api.memoryBrainBrief(), method: "GET", path: "/api/memory/brain-brief", query: { limit: "3" } },
  { name: "memoryBrainProof", invoke: () => api.memoryBrainProof(), method: "GET", path: "/api/memory/brain-proof", query: { limit: "3" } },
  { name: "memoryRecall", invoke: () => api.memoryRecall("질문"), method: "POST", path: "/api/memory/recall", body: { query: "질문", limit: 20 } },
  { name: "memoryRecall", invoke: () => api.memoryRecall("질문", 5), method: "POST", path: "/api/memory/recall", body: { query: "질문", limit: 5 } },
  { name: "automationOverview", invoke: () => api.automationOverview(), method: "GET", path: "/api/automation/overview" },
  { name: "automationPatterns", invoke: () => api.automationPatterns(), method: "GET", path: "/api/automation/patterns" },
  { name: "installAutomationSuggestion", invoke: () => api.installAutomationSuggestion("s1"), method: "POST", path: "/api/automation/install", body: { suggestion_id: "s1", enabled: false } },
  { name: "installAutomationSuggestion", invoke: () => api.installAutomationSuggestion("s1", true), method: "POST", path: "/api/automation/install", body: { suggestion_id: "s1", enabled: true } },
  { name: "runAutomationNow", invoke: () => api.runAutomationNow("w1"), method: "POST", path: "/api/automation/run-now", body: { workflow_id: "w1", dry_run: true } },
  { name: "runAutomationNow", invoke: () => api.runAutomationNow("w1", false), method: "POST", path: "/api/automation/run-now", body: { workflow_id: "w1", dry_run: false } },
  { name: "commandBriefing", invoke: () => api.commandBriefing(), method: "GET", path: "/api/command/briefing" },
  { name: "features", invoke: () => api.features(), method: "GET", path: "/api/features" },
  { name: "setFeature", invoke: () => api.setFeature("vector_backend", "quantized"), method: "POST", path: "/api/features/vector_backend", body: { value: "quantized" } },
  // The id rides in the path, so it is encoded — a feature id is server data.
  { name: "setFeature", invoke: () => api.setFeature("a/b", true), method: "POST", path: "/api/features/a%2Fb", body: { value: true } },
  { name: "permissionMode", invoke: () => api.permissionMode(), method: "GET", path: "/api/permission-mode" },
  { name: "setPermissionMode", invoke: () => api.setPermissionMode("trusted"), method: "POST", path: "/api/permission-mode", body: { mode: "trusted", acknowledge_risk: false } },
  { name: "setPermissionMode", invoke: () => api.setPermissionMode("bypass", true), method: "POST", path: "/api/permission-mode", body: { mode: "bypass", acknowledge_risk: true } },
  { name: "networkBoundary", invoke: () => api.networkBoundary(), method: "GET", path: "/api/network-boundary/ui-state" },
  { name: "setNetworkBoundary", invoke: () => api.setNetworkBoundary("hybrid", true), method: "POST", path: "/api/network-boundary", body: { mode: "hybrid", acknowledge_risk: true } },
  { name: "setNodeSensitivity", invoke: () => api.setNodeSensitivity("n1", true, "비밀"), method: "POST", path: "/api/network-boundary/node-sensitivity", body: { node_id: "n1", local_only: true, reason: "비밀" } },
  { name: "setNodeSensitivity", invoke: () => api.setNodeSensitivity("n1", false), method: "POST", path: "/api/network-boundary/node-sensitivity", body: { node_id: "n1", local_only: false } },
  { name: "setHybridPolicy", invoke: () => api.setHybridPolicy({ auto_commit: true }), method: "POST", path: "/api/network-boundary/policy", body: { auto_commit: true } },
  { name: "previewCloudContext", invoke: () => api.previewCloudContext("질문"), method: "POST", path: "/api/network-boundary/preview", body: { message: "질문", top_k: 6 } },
  { name: "proposals", invoke: () => api.proposals(), method: "GET", path: "/api/proposals" },
  { name: "proposalCounts", invoke: () => api.proposalCounts(), method: "GET", path: "/api/proposals/counts" },
  { name: "proposalDetail", invoke: () => api.proposalDetail("p/1"), method: "GET", path: "/api/proposals/p%2F1" },
  { name: "approveProposal", invoke: () => api.approveProposal("p1"), method: "POST", path: "/api/proposals/p1/approve", body: {} },
  { name: "rejectProposal", invoke: () => api.rejectProposal("p1"), method: "POST", path: "/api/proposals/p1/reject", body: {} },
  { name: "rejectProposal", invoke: () => api.rejectProposal("p1", "아님"), method: "POST", path: "/api/proposals/p1/reject", body: { reason: "아님" } },
  { name: "commandSearch", invoke: () => api.commandSearch("찾기"), method: "GET", path: "/api/command/search", query: { q: "찾기", limit: "8" } },
  { name: "brainHealth", invoke: () => api.brainHealth(), method: "GET", path: "/api/brain/health" },
  { name: "brainVectorFreshness", invoke: () => api.brainVectorFreshness(), method: "GET", path: "/api/brain/vector-freshness" },
  { name: "ingestionJobs", invoke: () => api.ingestionJobs(), method: "GET", path: "/api/ingestion/jobs" },
  { name: "resumeIngestionJob", invoke: () => api.resumeIngestionJob("j 1"), method: "POST", path: "/api/ingestion/jobs/j%201/resume", body: {} },
  { name: "brainInsights", invoke: () => api.brainInsights(), method: "GET", path: "/api/brain/insights" },
  { name: "brainContradictions", invoke: () => api.brainContradictions(), method: "GET", path: "/api/brain/contradictions" },
  { name: "brainGarden", invoke: () => api.brainGarden(), method: "GET", path: "/api/brain/garden", query: { limit: "8" } },
  { name: "brainConsolidate", invoke: () => api.brainConsolidate(), method: "POST", path: "/api/brain/consolidate", body: { apply: false } },
  { name: "memoryCompact", invoke: () => api.memoryCompact(), method: "POST", path: "/api/memory/compact", body: {} },
  { name: "memoryRebuild", invoke: () => api.memoryRebuild(), method: "POST", path: "/api/memory/rebuild", body: { target: "vector" } },
  { name: "chatHistory", invoke: () => api.chatHistory(), method: "GET", path: "/history/conversations" },
  { name: "conversation", invoke: () => api.conversation("c1"), method: "GET", path: "/history/conversations/c1" },
  { name: "deleteConversation", invoke: () => api.deleteConversation("c1"), method: "DELETE", path: "/history/conversations/c1" },
  { name: "agentApprovals", invoke: () => api.agentApprovals(), method: "GET", path: "/agent/approvals" },
  { name: "evidenceActions", invoke: () => api.evidenceActions("왜?", ["s1"], "ko"), method: "POST", path: "/api/evidence/actions", body: { question: "왜?", source_ids: ["s1"], language: "ko" } },
  { name: "graphNode", invoke: () => api.graphNode("n1"), method: "GET", path: "/api/graph/node", query: { node_id: "n1", include_neighbors: "false" } },
  { name: "ingestionWatchStatus", invoke: () => api.ingestionWatchStatus(), method: "GET", path: "/api/ingestion/watch" },
  { name: "demoCorpusStatus", invoke: () => api.demoCorpusStatus(), method: "GET", path: "/api/setup/demo-corpus" },
  { name: "removeDemoCorpus", invoke: () => api.removeDemoCorpus(), method: "DELETE", path: "/api/setup/demo-corpus" },
  { name: "saveChatFile", invoke: () => api.saveChatFile("notes.html", "<p>hi</p>"), method: "POST", path: "/tools/write_file", body: { path: "notes.html", content: "<p>hi</p>" } },
  { name: "documents", invoke: () => api.documents(), method: "GET", path: "/knowledge-graph/documents", query: { limit: "200" } },
  { name: "localSources", invoke: () => api.localSources(), method: "GET", path: "/knowledge-graph/local/sources" },
  { name: "localFolderHealth", invoke: () => api.localFolderHealth(), method: "GET", path: "/knowledge-graph/local/health" },
  { name: "localAgent", invoke: () => api.localAgent(), method: "GET", path: "/api/local-agent/status" },
  { name: "connectFolder", invoke: () => api.connectFolder("/Users/me/Notes"), method: "POST", path: "/knowledge-graph/local/index", body: { path: "/Users/me/Notes", approved: true, watch_enabled: true, consent: { approved: true, source: "desktop-spa" } } },
  { name: "localWatchStop", invoke: () => api.localWatchStop("src1"), method: "POST", path: "/knowledge-graph/local/watch/stop", body: { source_id: "src1" } },
  { name: "models", invoke: () => api.models(), method: "GET", path: "/models" },
  { name: "setupScan", invoke: () => api.setupScan(), method: "GET", path: "/setup/scan" },
  { name: "modelRecommendations", invoke: () => api.modelRecommendations(), method: "GET", path: "/models/recommendations", query: { engine: "local_mlx" } },
  { name: "installEngine", invoke: () => api.installEngine("local_mlx"), method: "POST", path: "/engines/install", body: { engine: "local_mlx" } },
  { name: "prepareModel", invoke: () => api.prepareModel("m1"), method: "POST", path: "/engines/prepare-model", body: { model: "m1", engine: null, allow_download: false } },
  { name: "prepareModel", invoke: () => api.prepareModel("m1", "ollama", true), method: "POST", path: "/engines/prepare-model", body: { model: "m1", engine: "ollama", allow_download: true } },
  { name: "loadModel", invoke: () => api.loadModel("m1"), method: "POST", path: "/models/load", body: { model_id: "m1", engine: null, allow_download: false } },
  { name: "loadModel", invoke: () => api.loadModel("m1", "local_mlx", true), method: "POST", path: "/models/load", body: { model_id: "m1", engine: "local_mlx", allow_download: true } },
  { name: "unloadModel", invoke: () => api.unloadModel("m/1"), method: "DELETE", path: "/models/unload/m%2F1" },
  { name: "embeddingsStatus", invoke: () => api.embeddingsStatus(), method: "GET", path: "/api/embeddings/status" },
  { name: "agentRuntime", invoke: () => api.agentRuntime(), method: "GET", path: "/agents/api/runtime/status" },
  { name: "agentRunPreview", invoke: () => api.agentRunPreview("정리해줘"), method: "POST", path: "/agents/api/run/preview", body: { goal: "정리해줘", roles: [] } },
  { name: "agentRunPreview", invoke: () => api.agentRunPreview("정리해줘", ["writer"]), method: "POST", path: "/agents/api/run/preview", body: { goal: "정리해줘", roles: ["writer"] } },
  { name: "toolRegistryDiagnostics", invoke: () => api.toolRegistryDiagnostics(), method: "GET", path: "/tools/registry/diagnostics" },
  { name: "toolRegistry", invoke: () => api.toolRegistry(), method: "GET", path: "/tools/registry" },
  { name: "agentRun", invoke: () => api.agentRun("r1"), method: "GET", path: "/agents/api/runs/r1" },
  { name: "stopAgentRun", invoke: () => api.stopAgentRun("r1"), method: "POST", path: "/agents/api/runs/r1/stop", body: {} },
  { name: "agentRegistry", invoke: () => api.agentRegistry(), method: "GET", path: "/agents/api/registry" },
  { name: "agentCapabilities", invoke: () => api.agentCapabilities(), method: "GET", path: "/agents/api/registry/capabilities" },
  { name: "registerAgent", invoke: () => api.registerAgent({ name: "요약가" }), method: "POST", path: "/agents/api/registry", body: { name: "요약가" } },
  { name: "updateAgent", invoke: () => api.updateAgent("a1", { name: "요약가2" }), method: "PATCH", path: "/agents/api/registry/a1", body: { name: "요약가2" } },
  { name: "removeAgent", invoke: () => api.removeAgent("a1"), method: "DELETE", path: "/agents/api/registry/a1" },
  { name: "workflowDefinitions", invoke: () => api.workflowDefinitions(), method: "GET", path: "/workflows/api/definitions" },
  { name: "workflowRuns", invoke: () => api.workflowRuns(), method: "GET", path: "/workflows/api/runs" },
  { name: "workflowTriggers", invoke: () => api.workflowTriggers(), method: "GET", path: "/workflows/api/triggers" },
  { name: "automationRecipes", invoke: () => api.automationRecipes(), method: "GET", path: "/workflows/api/automation/recipes" },
  { name: "installAutomationRecipe", invoke: () => api.installAutomationRecipe("r1"), method: "POST", path: "/workflows/api/automation/recipes/r1", body: { enabled: false } },
  { name: "createWorkflow", invoke: () => api.createWorkflow({ name: "wf", nodes: [] }), method: "POST", path: "/workflows/api/definitions", body: { name: "wf", nodes: [] } },
  { name: "importWorkflow", invoke: () => api.importWorkflow({ name: "wf" }), method: "POST", path: "/workflows/api/import", body: { data: { name: "wf" } } },
  { name: "exportWorkflow", invoke: () => api.exportWorkflow("w1"), method: "GET", path: "/workflows/api/export/w1" },
  { name: "runWorkflow", invoke: () => api.runWorkflow("w1"), method: "POST", path: "/workflows/api/definitions/w1/run", body: {} },
  { name: "updateWorkflow", invoke: () => api.updateWorkflow("w1", { name: "wf2" }), method: "PATCH", path: "/workflows/api/definitions/w1", body: { name: "wf2" } },
  { name: "stopWorkflowRun", invoke: () => api.stopWorkflowRun("run1"), method: "POST", path: "/workflows/api/runs/run1/stop", body: {} },
  { name: "resumeWorkflowRun", invoke: () => api.resumeWorkflowRun("run1", true), method: "POST", path: "/workflows/api/runs/run1/resume", body: { approved: true } },
  { name: "hooks", invoke: () => api.hooks(), method: "GET", path: "/api/hooks" },
  { name: "hookRuns", invoke: () => api.hookRuns(), method: "GET", path: "/api/hooks/runs", query: { limit: "50" } },
  { name: "hookRun", invoke: () => api.hookRun(), method: "POST", path: "/api/hooks/run", body: { kind: "pre_run", event: "manual" } },
  { name: "hookRun", invoke: () => api.hookRun({ kind: "post_run", event: "deploy" }), method: "POST", path: "/api/hooks/run", body: { kind: "post_run", event: "deploy" } },
  { name: "automationReviews", invoke: () => api.automationReviews(), method: "GET", path: "/automation/reviews" },
  { name: "automationReviews", invoke: () => api.automationReviews({ status: "pending", source: "trigger" }), method: "GET", path: "/automation/reviews", query: { status: "pending", source: "trigger" } },
  { name: "createReviewItem", invoke: () => api.createReviewItem({ title: "확인 필요" }), method: "POST", path: "/automation/reviews", body: { title: "확인 필요" } },
  { name: "approveReviewItem", invoke: () => api.approveReviewItem("i1"), method: "POST", path: "/automation/reviews/i1/approve" },
  { name: "dismissReviewItem", invoke: () => api.dismissReviewItem("i1"), method: "POST", path: "/automation/reviews/i1/dismiss" },
  { name: "snoozeReviewItem", invoke: () => api.snoozeReviewItem("i1", "2026-08-07T00:00:00Z"), method: "POST", path: "/automation/reviews/i1/snooze", body: { until: "2026-08-07T00:00:00Z" } },
  { name: "unsnoozeReviewItem", invoke: () => api.unsnoozeReviewItem("i1"), method: "POST", path: "/automation/reviews/i1/unsnooze" },
  { name: "runNowReviewItem", invoke: () => api.runNowReviewItem("i1"), method: "POST", path: "/automation/reviews/i1/run_now" },
  { name: "permissionsPending", invoke: () => api.permissionsPending(), method: "GET", path: "/permissions/pending" },
  { name: "approvePermission", invoke: () => api.approvePermission("tok/1"), method: "POST", path: "/permissions/approve/tok%2F1", body: {} },
  { name: "denyPermission", invoke: () => api.denyPermission("tok1"), method: "POST", path: "/permissions/deny/tok1", body: {} },
  { name: "skills", invoke: () => api.skills(), method: "GET", path: "/workspace/skills" },
  { name: "skillToggle", invoke: () => api.skillToggle("web-search", true), method: "POST", path: "/workspace/skills/disable", body: { skill: "web-search" } },
  { name: "skillToggle", invoke: () => api.skillToggle("web-search", false), method: "POST", path: "/workspace/skills/enable", body: { skill: "web-search" } },
  { name: "skillsMarketplace", invoke: () => api.skillsMarketplace(), method: "GET", path: "/skills/marketplace" },
  { name: "skillInstall", invoke: () => api.skillInstall("web-search"), method: "POST", path: "/workspace/skills/install", body: { skill: "web-search", plugin: "" } },
  { name: "skillInstall", invoke: () => api.skillInstall("web-search", "core"), method: "POST", path: "/workspace/skills/install", body: { skill: "web-search", plugin: "core" } },
  { name: "mcpTools", invoke: () => api.mcpTools(), method: "GET", path: "/mcp/tools" },
  { name: "mcpRecommend", invoke: () => api.mcpRecommend("검색"), method: "POST", path: "/mcp/recommend", body: { query: "검색", limit: 6 } },
  { name: "templates", invoke: () => api.templates(), method: "GET", path: "/marketplace/templates" },
  { name: "installTemplate", invoke: () => api.installTemplate({ id: "t1" }), method: "POST", path: "/marketplace/templates/install", body: { data: { id: "t1" } } },
  { name: "pluginsRegistry", invoke: () => api.pluginsRegistry(), method: "GET", path: "/plugins/registry" },
  { name: "pluginsDirectory", invoke: () => api.pluginsDirectory(), method: "GET", path: "/plugins/directory" },
  { name: "profile", invoke: () => api.profile(), method: "GET", path: "/account/profile" },
  { name: "login", invoke: () => api.login("a@b.c", "pw"), method: "POST", path: "/login", body: { email: "a@b.c", password: "pw" } },
  { name: "register", invoke: () => api.register({ email: "a@b.c" }), method: "POST", path: "/register", body: { email: "a@b.c" } },
  { name: "logout", invoke: () => api.logout(), method: "POST", path: "/logout", body: {} },
  { name: "updateProfile", invoke: () => api.updateProfile({ name: "나" }), method: "PATCH", path: "/account/profile", body: { name: "나" } },
  { name: "changePassword", invoke: () => api.changePassword("old", "new"), method: "POST", path: "/account/change-password", body: { current_password: "old", new_password: "new" } },
  { name: "ssoConfig", invoke: () => api.ssoConfig(), method: "GET", path: "/auth/sso/config" },
  { name: "workspaceRegistry", invoke: () => api.workspaceRegistry(), method: "GET", path: "/workspace/registry" },
  { name: "createOrg", invoke: () => api.createOrg("팀"), method: "POST", path: "/workspace/orgs", body: { name: "팀" } },
  { name: "activateWorkspace", invoke: () => api.activateWorkspace("ws1"), method: "POST", path: "/workspace/activate", body: { workspace_id: "ws1" } },
  { name: "archiveWorkspace", invoke: () => api.archiveWorkspace("ws1"), method: "POST", path: "/workspace/orgs/ws1/archive", body: {} },
  { name: "addWorkspaceMember", invoke: () => api.addWorkspaceMember("ws1", "u1", "admin"), method: "POST", path: "/workspace/orgs/ws1/members", body: { user_id: "u1", role: "admin" } },
  { name: "removeWorkspaceMember", invoke: () => api.removeWorkspaceMember("ws1", "u1"), method: "DELETE", path: "/workspace/orgs/ws1/members/u1" },
  { name: "invitations", invoke: () => api.invitations(), method: "GET", path: "/invitations" },
  { name: "createInvitation", invoke: () => api.createInvitation({ email: "a@b.c" }), method: "POST", path: "/invitations", body: { email: "a@b.c" } },
  { name: "acceptInvitation", invoke: () => api.acceptInvitation("tok1"), method: "POST", path: "/invitations/tok1/accept", body: {} },
  { name: "snapshots", invoke: () => api.snapshots(), method: "GET", path: "/workspace/snapshots" },
  { name: "createSnapshot", invoke: () => api.createSnapshot("백업"), method: "POST", path: "/workspace/snapshots", body: { name: "백업" } },
  { name: "compareSnapshots", invoke: () => api.compareSnapshots("s1", "s2"), method: "POST", path: "/workspace/snapshots/compare", body: { before_id: "s1", after_id: "s2" } },
  { name: "restoreSnapshot", invoke: () => api.restoreSnapshot("s1"), method: "POST", path: "/workspace/snapshots/s1/restore", body: {} },
  { name: "exportSnapshot", invoke: () => api.exportSnapshot("s1"), method: "POST", path: "/workspace/snapshots/s1/export", body: {} },
  { name: "timeMachine", invoke: () => api.timeMachine(), method: "GET", path: "/workspace/time-machine", query: { limit: "100" } },
  { name: "realtimeFeed", invoke: () => api.realtimeFeed(), method: "GET", path: "/realtime/feed", query: { limit: "80" } },
  { name: "presence", invoke: () => api.presence(), method: "GET", path: "/realtime/presence" },
  { name: "networkIdentity", invoke: () => api.networkIdentity(), method: "GET", path: "/network/identity" },
  { name: "networkPeers", invoke: () => api.networkPeers(), method: "GET", path: "/network/peers" },
  { name: "pairPeer", invoke: () => api.pairPeer({ code: "1234" }), method: "POST", path: "/network/peers", body: { code: "1234" } },
  { name: "unpairPeer", invoke: () => api.unpairPeer("peer1"), method: "DELETE", path: "/network/peers/peer1" },
  { name: "pushPeer", invoke: () => api.pushPeer("peer1", "ws1"), method: "POST", path: "/network/push/peer1", body: { workspace_id: "ws1" } },
  { name: "pushPeer", invoke: () => api.pushPeer("peer1"), method: "POST", path: "/network/push/peer1", body: {} },
  { name: "sysinfo", invoke: () => api.sysinfo(), method: "GET", path: "/local/sysinfo" },
  { name: "computerMemory", invoke: () => api.computerMemory(), method: "GET", path: "/workspace/computer-memory" },
  { name: "setComputerMemory", invoke: () => api.setComputerMemory(true), method: "POST", path: "/workspace/computer-memory", body: { enabled: true, consent: { approved: true } } },
  { name: "adminSummary", invoke: () => api.adminSummary(), method: "GET", path: "/admin/summary" },
  { name: "adminStats", invoke: () => api.adminStats(), method: "GET", path: "/admin/stats" },
  { name: "adminUsers", invoke: () => api.adminUsers(), method: "GET", path: "/admin/users" },
  { name: "adminAudit", invoke: () => api.adminAudit(), method: "GET", path: "/admin/audit" },
  { name: "adminAudit", invoke: () => api.adminAudit({ q: "login", limit: 10 }), method: "GET", path: "/admin/audit", query: { q: "login", limit: "10" } },
  { name: "adminRoles", invoke: () => api.adminRoles(), method: "GET", path: "/admin/roles" },
  { name: "adminPolicies", invoke: () => api.adminPolicies(), method: "GET", path: "/admin/policies" },
  { name: "adminLogRetention", invoke: () => api.adminLogRetention(), method: "GET", path: "/admin/log-retention" },
  { name: "adminProductHardening", invoke: () => api.adminProductHardening(), method: "GET", path: "/admin/product-hardening" },
  { name: "adminSecurity", invoke: () => api.adminSecurity(), method: "GET", path: "/admin/security/overview" },
  { name: "adminSecurityEvents", invoke: () => api.adminSecurityEvents(), method: "GET", path: "/admin/security/events", query: { limit: "50" } },
  { name: "vpcStatus", invoke: () => api.vpcStatus(), method: "GET", path: "/vpc/status" },
  { name: "toolPermissions", invoke: () => api.toolPermissions(), method: "GET", path: "/tools/permissions" },
  { name: "pipelineStatus", invoke: () => api.pipelineStatus(), method: "GET", path: "/knowledge-graph/pipeline/status" },
  { name: "activityRuns", invoke: () => api.activityRuns(), method: "GET", path: "/api/activity/runs", query: { limit: "20" } },
  { name: "adminHealthSummary", invoke: () => api.adminHealthSummary(), method: "GET", path: "/admin/health-summary" },
];

// Wrappers whose behavior is not "one request, one envelope": each has its own
// describe below (or above, for the SSE pair) instead of a table row.
const SPECIAL_CASES = new Set([
  "selectFolder",
  "desktopBackendStatus",
  "hybridSearch",
  "streamChat",
  "streamModelPrepare",
  "resumeAgentApproval",
  "installDemoCorpus",
  "runAgent",
  "saveChatFile",
  "readWorkspaceFile",
  "downloadWorkspaceFile",
  "uploadDocument",
]);

describe("every endpoint wrapper", () => {
  it("appears in the request table or has a dedicated suite", () => {
    const tabled = new Set<string>(ENDPOINT_TABLE.map((entry) => entry.name));
    for (const key of Object.keys(latticeApi)) {
      expect(
        tabled.has(key) || SPECIAL_CASES.has(key),
        `latticeApi.${key} is not covered by the endpoint table or a special-case suite`,
      ).toBe(true);
    }
  });

  it("sends the documented method, path, query and body", async () => {
    for (const entry of ENDPOINT_TABLE) {
      const calls = recordFetch(() => jsonResponse({}));
      await entry.invoke();
      expect(calls.length, `${entry.name} request count`).toBe(1);
      const call = calls[0];
      expect(call.method, `${entry.name} method`).toBe(entry.method);
      expect(call.url.pathname, `${entry.name} path`).toBe(entry.path);
      for (const [key, value] of Object.entries(entry.query || {})) {
        expect(call.url.searchParams.get(key), `${entry.name} query ${key}`).toBe(value);
      }
      if (entry.body !== undefined) {
        expect(call.body, `${entry.name} body`).toEqual(entry.body);
      }
      expect(call.headers.get("X-Lattice-Language"), `${entry.name} language header`).toBeTruthy();
    }
  });
});

describe("review queue wrappers (spec-generated client)", () => {
  it("returns the declared list shape, emptied, when the queue answers 500", async () => {
    recordFetch(() => jsonResponse({ detail: "boom" }, 500));

    const res = await latticeApi.automationReviews();

    expect(res.ok).toBe(false);
    expect(res.status).toBe(500);
    expect(res.data).toEqual({ items: [] });
    expect(res.error).toBe("boom");
  });

  it("returns the declared item shape, emptied, when the network drops", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.approveReviewItem("i1");

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data).toMatchObject({ id: "", status: "pending", payload: {} });
    expect(res.error).toBeTruthy();
  });

  it("passes a live review item through untouched", async () => {
    recordFetch(() => jsonResponse({
      id: "i1", status: "approved", effective_status: "approved", title: "t",
      summary: "", source: "workflow_run", kind: "suggestion", payload: {}, provenance: {},
    }));

    const res = await latticeApi.runNowReviewItem("i1");

    expect(res.ok).toBe(true);
    expect(res.data.id).toBe("i1");
  });
});

describe("the 10 second request budget", () => {
  it("turns a hung plain request into a friendly timeout", async () => {
    vi.useFakeTimers();
    try {
      useAppStore.setState({ apiBase: TEST_ORIGIN });
      dispatcherCalls.length = 0;
      respondWith = (_url, signal) =>
        new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        });

      const pending = latticeApi.health();
      await vi.advanceTimersByTimeAsync(10_100);
      const res = await pending;

      expect(res.ok).toBe(false);
      expect(res.status).toBe(0);
      expect(res.error).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("applies the same budget to spec-generated calls", async () => {
    vi.useFakeTimers();
    try {
      useAppStore.setState({ apiBase: TEST_ORIGIN });
      dispatcherCalls.length = 0;
      respondWith = (_url, signal) =>
        new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        });

      const pending = latticeApi.automationReviews();
      await vi.advanceTimersByTimeAsync(10_100);
      const res = await pending;

      expect(res.ok).toBe(false);
      expect(res.status).toBe(0);
      expect(res.data).toEqual({ items: [] });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("streamChat error and edge paths", () => {
  it("reports a JSON error payload instead of pretending to stream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ error: "no model" }, 503)));

    const result = await latticeApi.streamChat({ message: "hi" });

    expect(result.text).toBe("");
    expect(result.error).toBe("no model");
    expect(result.malformedFrames).toBe(0);
  });

  it("falls back to detail, then statusText, when the error field is missing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "gone" }, 404)));
    const withDetail = await latticeApi.streamChat({ message: "hi" });
    expect(withDetail.error).toBe("gone");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("plain text", { status: 200, statusText: "OK", headers: { "Content-Type": "text/plain" } }),
    ));
    const nonJson = await latticeApi.streamChat({ message: "hi" });
    expect(nonJson.error).toBe("OK");
  });

  it("treats an event-stream response without a body as an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(null, { status: 200, statusText: "Empty", headers: { "Content-Type": "text/event-stream" } }),
    ));

    const result = await latticeApi.streamChat({ message: "hi" });

    expect(result.text).toBe("");
    expect(result.error).toBe("Empty");
  });

  it("treats an OK answer with no content-type header as a non-stream", async () => {
    // A raw byte body keeps undici from inventing a content-type, so the
    // header lookup really does come back null here.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(new TextEncoder().encode("raw"), { status: 200, statusText: "OK" }),
    ));

    const result = await latticeApi.streamChat({ message: "hi" });

    expect(result.text).toBe("");
    expect(result.error).toBe("OK");
  });

  it("collects trace, quality, grounding and agent without any handlers wired", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"chunk":"부분"}\n\n',
      'data: {"text":"추가"}\n\n',
      'data: {"nothing_here":1}\n\n',
      "data: [1,2,3]\n\n",
      'data: {"trace":{"nodes":2},"context_quality":{"level":"strong"},"grounding":{"used":true}}\n\n',
      'data: {"context_quality":"not-an-object","grounding":7,"agent":"not-an-object"}\n\n',
      'event: agent_step\ndata: {"phase":"plan"}\n\n',
      'data: {"agent":{"status":"ok","final_state":"DONE"}}\n\n',
    ])));

    // No [DONE] sentinel: the loop must end with the stream and still return.
    const result = await latticeApi.streamChat({ message: "hi" });

    expect(result.text).toBe("부분추가");
    expect(result.trace).toEqual({ nodes: 2 });
    expect(result.contextQuality).toEqual({ level: "strong" });
    expect(result.grounding).toEqual({ used: true });
    expect(result.agent).toMatchObject({ final_state: "DONE" });
    expect(result.malformedFrames).toBe(0);
  });
});

describe("streamModelPrepare error and edge paths", () => {
  it("hands a structured refusal to onError before any stream exists", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      detail: { status: "needs_download", user_message: "내려받기가 필요해요" },
    }, 409)));
    const onError = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "m1" }, { onError });

    expect(result.ok).toBe(false);
    expect(result.status).toBe(409);
    expect(result.error).toBe("내려받기가 필요해요");
    // The structured detail is spread over the base shape, so its own status
    // wins while the friendly message is preserved.
    expect(onError).toHaveBeenCalledWith({
      status: "needs_download",
      user_message: "내려받기가 필요해요",
    });
  });

  it("survives a refusal with no handlers and no structured detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ message: "flat" }, 500)));
    const flat = await latticeApi.streamModelPrepare({ model: "m1" });
    expect(flat.ok).toBe(false);
    expect(flat.error).toBe("flat");
    expect(flat.data).toEqual({ message: "flat" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("not json", { status: 502, statusText: "Bad Gateway", headers: { "Content-Type": "text/plain" } }),
    ));
    const nonJson = await latticeApi.streamModelPrepare({ model: "m1" });
    expect(nonJson.error).toBe("Bad Gateway");
    expect(nonJson.data).toEqual({});
  });

  it("treats an event-stream response without a body as a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(null, { status: 200, statusText: "Empty", headers: { "Content-Type": "text/event-stream" } }),
    ));

    const result = await latticeApi.streamModelPrepare({ model: "m1" });

    expect(result.ok).toBe(false);
    expect(result.error).toBe("Empty");
  });

  it("still calls onError when an OK answer carries no content-type header", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(new TextEncoder().encode("raw"), { status: 200, statusText: "Down" }),
    ));
    const onError = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "m1" }, { onError });

    expect(result.ok).toBe(false);
    expect(result.data).toEqual({});
    expect(onError).toHaveBeenCalledWith({ status: "error", user_message: "Down" });
  });

  it("ignores a progress frame whose payload is valid JSON but not an object", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      "event: progress\ndata: [10]\n\n",
      'event: done\ndata: {"status":"ready"}\n\n',
    ])));
    const onProgress = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "m1" }, { onProgress });

    expect(onProgress).toHaveBeenCalledWith({});
    expect(result.ok).toBe(true);
  });

  it("stops on an in-stream error frame and unwraps its detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: progress\ndata: {"pct":10}\n\n',
      'event: error\ndata: {"detail":{"user_message":"디스크가 가득 찼어요"},"status_code":507}\n\n',
    ])));
    const onError = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "m1" }, { onError });

    expect(result.ok).toBe(false);
    expect(result.status).toBe(507);
    expect(result.error).toBe("디스크가 가득 찼어요");
    expect(onError).toHaveBeenCalledWith({ user_message: "디스크가 가득 찼어요" });
  });

  it("uses the frame itself when the error carries no detail object, defaulting to 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: error\ndata: {"user_message":"준비 실패"}\n\n',
    ])));

    // No handlers at all: onError?./onProgress?./onDone?. must tolerate it.
    const result = await latticeApi.streamModelPrepare({ model: "m1" });

    expect(result.ok).toBe(false);
    expect(result.status).toBe(500);
    expect(result.data).toEqual({ user_message: "준비 실패" });
  });

  it("finishes a handler-less progress stream, including empty data frames", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: progress\ndata: {"pct":50}\n\n',
      "event: progress\ndata: \n\n",
      'event: done\ndata: {"status":"ready"}\n\n',
    ])));

    const result = await latticeApi.streamModelPrepare({ model: "m1" });

    expect(result.ok).toBe(true);
    expect(result.data).toEqual({ status: "ready" });
  });
});

describe("uploadDocument", () => {
  const file = () => new File(["안녕"], "메모.txt", { type: "text/plain" });

  it("posts the file as multipart form data", async () => {
    const calls = recordFetch(() => jsonResponse({ status: "ingested" }));

    const res = await latticeApi.uploadDocument(file());

    expect(res.ok).toBe(true);
    expect(res.data).toEqual({ status: "ingested" });
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url.pathname).toBe("/upload/document");
    const body = calls[0].body as FormData;
    expect(body).toBeInstanceOf(FormData);
    const part = body.get("file") as File;
    expect(part.name).toBe("메모.txt");
  });

  it("reads the server's explanation out of a rejected upload", async () => {
    recordFetch(() => jsonResponse({ detail: "너무 큽니다" }, 413));

    const res = await latticeApi.uploadDocument(file());

    expect(res.ok).toBe(false);
    expect(res.status).toBe(413);
    expect(res.error).toBe("너무 큽니다");
  });

  it("falls back to a generic message when the failure body is not JSON", async () => {
    recordFetch(() => new Response("boom", { status: 500, statusText: "", headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.uploadDocument(file());

    expect(res.ok).toBe(false);
    expect(res.data).toBeNull();
    expect(res.error).toBe("Upload failed");
  });

  it("reports a network drop without throwing", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.uploadDocument(file());

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.error).toContain("Failed to fetch");
  });
});

describe("resumeAgentApproval", () => {
  const body = { run_id: "r1", approval_token: "tok", approve: true };

  it("posts the resume request and passes the payload through", async () => {
    const calls = recordFetch(() => jsonResponse({ status: "resumed" }));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(calls[0].method).toBe("POST");
    expect(calls[0].url.pathname).toBe("/agent/resume");
    expect(calls[0].body).toEqual(body);
    expect(res.ok).toBe(true);
    expect(res.data).toEqual({ status: "resumed" });
  });

  it("normalises a non-object success payload to an empty record", async () => {
    recordFetch(() => jsonResponse("done"));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(res.ok).toBe(true);
    expect(res.data).toEqual({});
  });

  it("keeps the HTTP status so callers can tell an expired token from a lost run", async () => {
    recordFetch(() => jsonResponse({ detail: { user_message: "승인이 만료되었어요" } }, 410));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(res.ok).toBe(false);
    expect(res.status).toBe(410);
    expect(res.error).toBe("승인이 만료되었어요");
  });

  it("labels a bodiless failure with its HTTP status", async () => {
    recordFetch(() => new Response("nope", { status: 500, statusText: "", headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(res.ok).toBe(false);
    expect(res.error).toBe("HTTP 500");
  });

  it("reports a network drop as unreachable", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data).toEqual({});
    expect(res.error).toBeTruthy();
  });
});

describe("installDemoCorpus", () => {
  it("merges the install report over the declared shape", async () => {
    const calls = recordFetch(() => jsonResponse({ status: "installed", ingested: 3 }));

    const res = await latticeApi.installDemoCorpus();

    expect(calls[0].method).toBe("POST");
    expect(calls[0].url.pathname).toBe("/api/setup/demo-corpus");
    expect(res.ok).toBe(true);
    expect(res.data).toMatchObject({ status: "installed", ingested: 3, duplicates: 0, documents: [] });
  });

  it("keeps the empty shape when the success body is not an object", async () => {
    recordFetch(() => new Response("ok!", { status: 200, headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.installDemoCorpus();

    expect(res.ok).toBe(true);
    expect(res.data).toEqual({ status: "", ingested: 0, duplicates: 0, documents: [], suggested_questions: [] });
  });

  it("reports a refusal with the declared shape intact", async () => {
    recordFetch(() => new Response(null, { status: 503, statusText: "" }));

    const res = await latticeApi.installDemoCorpus();

    expect(res.ok).toBe(false);
    expect(res.error).toBe("HTTP 503");
    expect(res.data.documents).toEqual([]);
  });

  it("reports a network drop without throwing", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.installDemoCorpus();

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data.suggested_questions).toEqual([]);
  });
});

describe("readWorkspaceFile", () => {
  it("returns the file body as text", async () => {
    const calls = recordFetch(() => new Response("<h1>안녕</h1>", { status: 200 }));

    const res = await latticeApi.readWorkspaceFile("out/페이지.html");

    expect(calls[0].url.pathname).toBe("/tools/download");
    expect(calls[0].url.searchParams.get("path")).toBe("out/페이지.html");
    expect(res.ok).toBe(true);
    expect(res.data.content).toBe("<h1>안녕</h1>");
  });

  it("keeps a 404 distinguishable and its message readable", async () => {
    recordFetch(() => jsonResponse({ detail: "파일이 없습니다" }, 404));

    const res = await latticeApi.readWorkspaceFile("gone.html");

    expect(res.ok).toBe(false);
    expect(res.status).toBe(404);
    expect(res.data.content).toBe("");
    expect(res.error).toBe("파일이 없습니다");
  });

  it("labels a bodiless failure with its HTTP status", async () => {
    recordFetch(() => new Response("x", { status: 500, statusText: "", headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.readWorkspaceFile("f.txt");

    expect(res.error).toBe("HTTP 500");
  });

  it("reports a network drop as unreachable", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.readWorkspaceFile("f.txt");

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data.content).toBe("");
  });
});

describe("downloadWorkspaceFile", () => {
  let clickSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:lattice/1") as never;
    URL.revokeObjectURL = vi.fn() as never;
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("downloads through a temporary anchor named after the file", async () => {
    recordFetch(() => new Response("내용", { status: 200 }));

    const res = await latticeApi.downloadWorkspaceFile("out/보고서.md", "보고서.md");

    expect(res.ok).toBe(true);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("보고서.md");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:lattice/1");
  });

  it("names the download from the path when no filename is given", async () => {
    recordFetch(() => new Response("내용", { status: 200 }));

    await latticeApi.downloadWorkspaceFile("nested/dir/파일.txt", "");

    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("파일.txt");
  });

  it("falls back to a generic name when there is no path either", async () => {
    recordFetch(() => new Response("내용", { status: 200 }));

    await latticeApi.downloadWorkspaceFile("", "");

    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("download");
  });

  it("reports a refusal without creating an anchor", async () => {
    recordFetch(() => jsonResponse({ detail: "권한 없음" }, 403));

    const res = await latticeApi.downloadWorkspaceFile("f.txt", "f.txt");

    expect(res).toEqual({ ok: false, error: "권한 없음" });
    expect(clickSpy).not.toHaveBeenCalled();
  });

  it("falls back to a generic message when the refusal body is not JSON", async () => {
    recordFetch(() => new Response("boom", { status: 500, statusText: "", headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.downloadWorkspaceFile("f.txt", "f.txt");

    expect(res).toEqual({ ok: false, error: "Download failed" });
  });

  it("reports a network drop without throwing", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.downloadWorkspaceFile("f.txt", "f.txt");

    expect(res.ok).toBe(false);
    expect(res.error).toBeTruthy();
  });
});

describe("runAgent", () => {
  it("resolves with the envelope when the run is accepted", async () => {
    const calls = recordFetch(() => jsonResponse({ run_id: "r1" }));

    const res = await latticeApi.runAgent("정리해줘", ["writer"]);

    expect(calls[0].url.pathname).toBe("/agents/api/run");
    expect(calls[0].body).toEqual({ goal: "정리해줘", roles: ["writer"] });
    expect(res.ok).toBe(true);
  });

  it("throws the server's message so the caller's error path runs", async () => {
    recordFetch(() => jsonResponse({ detail: "런타임이 준비되지 않았어요" }, 503));

    await expect(latticeApi.runAgent("정리해줘", [])).rejects.toThrow("런타임이 준비되지 않았어요");
  });

  it("names the HTTP status when the server offers no message at all", async () => {
    // An empty-string detail survives friendlyError verbatim, which is the one
    // way a failed envelope can carry a falsy error.
    recordFetch(() => jsonResponse({ detail: "" }, 502));

    await expect(latticeApi.runAgent("정리해줘", [])).rejects.toThrow("Agent run failed with HTTP 502");
  });
});
