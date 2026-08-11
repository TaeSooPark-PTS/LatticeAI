/**
 * The knowledge graph itself: graph, stats, pipeline journey, the unified Act
 * run timeline, portability/archive, provenance and ingestion.
 *
 * Returns true when this module answered the request; false lets the entry
 * try the next module, in the same order the original if-chain ran.
 */
const { repoRoot, json } = require("./http.cjs");
const { graphNodes, graphEdges, workspaceOs } = require("./fixtures.cjs");

module.exports = function handleKnowledge({ req, res, url, pathname }) {
  if (pathname === "/knowledge-graph/graph") return json(res, { nodes: graphNodes, edges: graphEdges });
  if (pathname === "/knowledge-graph/stats") return json(res, workspaceOs.graph);
  // Capture 3-step journey ribbon (layout rebuild screen 11).
  // Counts: received=12, extracted=11 (1 still pending), connected=10.
  // stages.*.status + pending are the single source of truth (B4) — never
  // return pending=0 with status=waiting when count>0.
  // Intentionally leave extracted.pending=1 / status=working so the capture
  // gates on stages (not count-only inference): if the UI ignores stages,
  // screen 11 still looks fully done and the bug stays invisible.
  if (pathname === "/knowledge-graph/pipeline/status") return json(res, {
    received: graphNodes.length,
    extracted: graphNodes.length - 1,
    connected: graphEdges.length,
    updated_at: "2026-06-06T12:00:00",
    stages: {
      received: { count: graphNodes.length, pending: 0, status: "done" },
      extracted: { count: graphNodes.length - 1, pending: 1, status: "working" },
      connected: { count: graphEdges.length, pending: 0, status: "done" },
    },
  });
  // Unified Act run timeline (layout rebuild screen 09). Includes an
  // awaiting_approval row so the approval badge is always capturable.
  // Inline object (no bare `runs` identifier) so mock↔real shape tests can
  // parse this payload as JSON without evaluating JS.
  if (pathname === "/api/activity/runs" || pathname === "/automations/runs/combined") {
    return json(res, {
      runs: [
        {
          id: "wf-run-approval",
          source: "workflow",
          title: "Agent Review Workflow",
          status: "awaiting_approval",
          started_at: "2026-06-06T12:05:00",
          finished_at: null,
          can_stop: false,
          can_resume: true,
          workflow_id: "wf-agent-review",
        },
        {
          id: "agent-run-1",
          source: "agent",
          title: "Summarize release",
          status: "ok",
          started_at: "2026-06-06T12:30:00",
          finished_at: "2026-06-06T12:31:00",
          can_stop: false,
          can_resume: false,
          agent_id: "agent:executor",
        },
        {
          id: "wf-run-1",
          source: "workflow",
          title: "Agent Review Workflow",
          status: "ok",
          started_at: "2026-06-06T12:00:00",
          finished_at: "2026-06-06T12:01:00",
          can_stop: false,
          can_resume: false,
          workflow_id: "wf-agent-review",
        },
      ],
      total: 3,
      truncated: false,
    });
  }
  if (pathname === "/api/knowledge-graph/portability") return json(res, {
    available: true,
    graph_schema_version: 3,
    stats: workspaceOs.graph,
    provenance: { total: 8 },
    storage: { engine: "sqlite", available: true },
  });
  if (pathname === "/api/brain/storage") return json(res, {
    available: true,
    active: { engine: "sqlite", available: true, vector_search: "sqlite-vec-or-bruteforce" },
    postgres: { engine: "postgres", available: false, reason: "Postgres DSN not configured" },
    backup_health: { available: true, count: 1, latest: "~/.ltcai/workspace_exports/brain-demo.latticebrain" },
  });
  if (pathname === "/api/knowledge-graph/backup-health") return json(res, {
    available: true,
    directory: "~/.ltcai/workspace_exports",
    count: 1,
    latest: "~/.ltcai/workspace_exports/brain-demo.latticebrain",
    encrypted_archives: 1,
    zip_backups: 0,
  });
  if (pathname === "/api/knowledge-graph/archive" && req.method === "POST") return json(res, {
    path: "~/.ltcai/workspace_exports/brain-demo.latticebrain",
    bytes: 4096,
    encrypted: true,
    format_version: 2,
  });
  if (pathname === "/api/knowledge-graph/archive/inspect" && req.method === "POST") return json(res, {
    valid_envelope: true,
    encrypted: true,
    format_version: 2,
    manifest_summary: { sections: { graph: true, workspace_state: true, signed_bundles: true } },
  });
  if (pathname === "/api/knowledge-graph/archive/verify" && req.method === "POST") return json(res, {
    ok: true,
    encrypted: true,
    entries: 6,
    errors: [],
  });
  if (pathname === "/api/knowledge-graph/archive/restore" && req.method === "POST") return json(res, {
    restored: true,
    encrypted: true,
    verified: true,
  });
  if (pathname === "/api/knowledge-graph/archive/import" && req.method === "POST") return json(res, {
    operation: "import",
    restored: true,
    encrypted: true,
    verified: true,
  });
  if (pathname === "/knowledge-graph/provenance/coverage") return json(res, {
    total_nodes: graphNodes.length,
    nodes_with_provenance: graphNodes.length - 1,
    coverage_ratio: (graphNodes.length - 1) / graphNodes.length,
    provenance_by_source_type: { upload: 4, note: 3, conversation: 4 },
    uncovered_by_type: { Concept: 1 },
  });
  if (pathname === "/knowledge-graph/search") return json(res, { query: url.searchParams.get("q"), matches: graphNodes });
  if (pathname === "/knowledge-graph/ingest" && req.method === "POST") return json(res, { status: "ok", source_type: "note", node_id: "note:visual" });
  if (pathname === "/knowledge-graph/local/index" && req.method === "POST") return json(res, { status: "ok", source: { id: "source-visual", root_path: repoRoot, status: "indexed" } });
  if (pathname === "/upload/document" && req.method === "POST") return json(res, { status: "ok", source_type: "upload", node_id: "doc:visual" });
  if (pathname === "/api/browser/read-url" && req.method === "POST") return json(res, { status: "ok", source_type: "web_url", node_id: "web:visual" });
  if (pathname.startsWith("/knowledge-graph/neighbors/")) return json(res, { node_id: pathname.replace("/knowledge-graph/neighbors/", ""), neighbors: graphNodes, edges: graphEdges });
  return false;
};
