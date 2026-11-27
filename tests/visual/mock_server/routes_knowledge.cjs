const { repoRoot, json } = require("./http.cjs");
const { graphNodes, graphEdges, workspaceOs } = require("./fixtures.cjs");

module.exports = function handleKnowledge({ req, res, url, pathname }) {
  if (pathname === "/api/ingestion/interop" && req.method === "GET") return json(res, {
    sources: { notion: { available: true }, git: { available: true }, mail: { available: true } },
  });
  if ((pathname === "/api/ingestion/obsidian" || pathname === "/api/ingestion/interop") && req.method === "POST") {
    let raw = "";
    req.on("data", (chunk) => { raw += chunk; });
    req.on("end", () => {
      const body = JSON.parse(raw || "{}");
      if (!body.approved) return json(res, { permission_required: true, path: body.path, action: "read", approval_token: "mock-import-token", expires_in: 300 });
      const dryRun = Boolean(body.dry_run);
      return json(res, {
        path: body.path,
        dry_run: dryRun,
        files_scanned: 3,
        notes_found: 2,
        items_found: 2,
        wikilinks_detected: 1,
        ingested: dryRun ? 0 : 2,
        duplicates: 0,
        failed: 0,
        sample_files: ["notes/retrieval.md", "notes/index.md"],
        errors: [],
      });
    });
    return true;
  }
  if (pathname === "/knowledge-graph/graph") return json(res, { nodes: graphNodes, edges: graphEdges });
  if (pathname === "/knowledge-graph/stats") return json(res, workspaceOs.graph);
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
  if (pathname === "/api/knowledge-graph/export-vault" && req.method === "POST") {
    res.writeHead(200, {
      "content-type": "application/zip",
      "content-disposition": 'attachment; filename="lattice-brain-vault-demo.zip"',
      "x-lattice-vault-notes": "8",
      "x-lattice-vault-history": "1",
      "x-lattice-vault-withheld": "0",
      "x-lattice-vault-omitted": "0",
      "x-lattice-vault-truncated": "0",
    });
    return res.end(Buffer.from("504b0506000000000000000000000000000000000000", "hex"));
  }
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
