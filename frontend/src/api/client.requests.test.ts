import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "./client";
import { useAppStore } from "@/store/appStore";
import {
  TEST_ORIGIN,
  dispatcherCalls,
  failFetchWith,
  jsonResponse,
  recordFetch,
  resetDispatcher,
  setResponder,
} from "@/test/apiClientHarness";

afterEach(() => {
  vi.unstubAllGlobals();
});

afterEach(resetDispatcher);

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
  { name: "cloudStatus", invoke: () => api.cloudStatus(), method: "GET", path: "/api/cloud/status" },
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
  { name: "pruneFolderDeleted", invoke: () => api.pruneFolderDeleted("/tmp/notes", false), method: "POST", path: "/api/ingestion/folder/prune", body: { path: "/tmp/notes", confirm: false } },
  { name: "pruneFolderDeleted", invoke: () => api.pruneFolderDeleted("/tmp/notes", true), method: "POST", path: "/api/ingestion/folder/prune", body: { path: "/tmp/notes", confirm: true } },
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
  { name: "chronicleOverview", invoke: () => api.chronicleOverview(), method: "GET", path: "/api/chronicle/overview" },
  { name: "chronicleDay", invoke: () => api.chronicleDay("2026-06-06"), method: "GET", path: "/api/chronicle/day/2026-06-06" },
  // The day is a path segment, so it is escaped on the way out: a value
  // carrying a slash must not be able to address a different route.
  { name: "chronicleDay", invoke: () => api.chronicleDay("../overview"), method: "GET", path: "/api/chronicle/day/..%2Foverview" },
  { name: "chronicleAsOf", invoke: () => api.chronicleAsOf("2026-06-06T23:59:59"), method: "GET", path: "/api/chronicle/as-of", query: { ts: "2026-06-06T23:59:59" } },
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
      setResponder((_url, signal) =>
        new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        }));

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
      setResponder((_url, signal) =>
        new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        }));

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

describe("the chronicle reads", () => {
  it("hands back an empty history rather than undefined when the read fails", async () => {
    // Every panel on the screen maps over these lists unconditionally, so a
    // failed read has to be an empty chronicle, not a missing one.
    failFetchWith(new TypeError("Failed to fetch"));

    const overview = await latticeApi.chronicleOverview();
    expect(overview.ok).toBe(false);
    expect(overview.data.series).toEqual([]);
    expect(overview.data.first_activity_at).toBeNull();
    expect(overview.data.totals).toEqual({ sources: 0, entities: 0, connections: 0, conversations: 0 });

    const day = await latticeApi.chronicleDay("2026-06-06");
    expect(day.data.date).toBe("2026-06-06");
    expect(day.data.groups.sources).toEqual([]);
    expect(day.data.counts.changes).toBe(0);

    const asOf = await latticeApi.chronicleAsOf("2026-06-06T23:59:59");
    expect(asOf.data.top_entities).toEqual([]);
    expect(asOf.data.stats).toEqual({ entities: 0, connections: 0 });
  });

  it("returns a live chronicle untouched", async () => {
    const payload = {
      first_activity_at: "2026-06-01T09:12:04",
      last_activity_at: "2026-06-06T18:40:11",
      totals: { sources: 3, entities: 7, connections: 5, conversations: 3 },
      series: [{ date: "2026-06-01", sources: 3, entities: 7, connections: 5, conversations: 3 }],
    };
    recordFetch(() => jsonResponse(payload));

    const res = await latticeApi.chronicleOverview();

    expect(res.ok).toBe(true);
    expect(res.source).toBe("live");
    expect(res.data).toEqual(payload);
  });
});
