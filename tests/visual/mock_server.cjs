const http = require("http");
const fs = require("fs");
const path = require("path");

const repoRoot = path.resolve(__dirname, "../..");
const port = Number(process.env.LTCAI_VISUAL_PORT || 4927);

const graphNodes = [
  { id: "entity:lattice", type: "Topic", title: "Lattice AI", summary: "Local-first workspace graph", importance_norm: 0.96, metadata: { graph_metrics: { degree: 4, importance_norm: 0.96, mention_count: 14, conversation_count: 5 } } },
  { id: "entity:workspace", type: "Concept", title: "Workspace Health", summary: "Operational workspace overview", importance_norm: 0.82, metadata: { graph_metrics: { degree: 3, importance_norm: 0.82 } } },
  { id: "entity:skills", type: "Task", title: "Skill Marketplace", summary: "Install, validate, and update skills", importance_norm: 0.72, metadata: { graph_metrics: { degree: 2, importance_norm: 0.72 } } },
  { id: "entity:enterprise", type: "Decision", title: "Enterprise Admin", summary: "Capability status without Community lockouts", importance_norm: 0.68, metadata: { graph_metrics: { degree: 2, importance_norm: 0.68 } } },
  { id: "file:readme", type: "File", title: "README.md", summary: "Release documentation", importance_norm: 0.58, metadata: { filename: "README.md", relative_path: "README.md", graph_metrics: { degree: 2, importance_norm: 0.58 } } },
];

const graphEdges = [
  { from: "entity:lattice", to: "entity:workspace", type: "discusses", weight: 1.4 },
  { from: "entity:lattice", to: "entity:skills", type: "mentions", weight: 1.1 },
  { from: "entity:lattice", to: "entity:enterprise", type: "mentions", weight: 1.0 },
  { from: "entity:workspace", to: "file:readme", type: "based_on", weight: 0.8 },
  { from: "entity:skills", to: "file:readme", type: "based_on", weight: 0.7 },
];

function json(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(body);
}

function text(res, value, contentType = "text/plain; charset=utf-8") {
  res.writeHead(200, { "content-type": contentType, "cache-control": "no-store" });
  res.end(value);
}

function serveFile(res, filePath) {
  if (!filePath.startsWith(repoRoot) || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    json(res, { detail: "not found" }, 404);
    return;
  }
  const ext = path.extname(filePath).toLowerCase();
  const types = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
  };
  res.writeHead(200, { "content-type": types[ext] || "application/octet-stream", "cache-control": "no-store" });
  fs.createReadStream(filePath).pipe(res);
}

function shortestPath(start, target) {
  if (!start || !target) return [];
  const adjacency = new Map();
  for (const edge of graphEdges) {
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, []);
    if (!adjacency.has(edge.to)) adjacency.set(edge.to, []);
    adjacency.get(edge.from).push(edge.to);
    adjacency.get(edge.to).push(edge.from);
  }
  const queue = [[start]];
  const seen = new Set([start]);
  while (queue.length) {
    const path = queue.shift();
    const node = path[path.length - 1];
    if (node === target) return path;
    for (const next of adjacency.get(node) || []) {
      if (!seen.has(next)) {
        seen.add(next);
        queue.push([...path, next]);
      }
    }
  }
  return [];
}

const workspaceOs = {
  version: "1.7.0",
  updated_at: "2026-06-01T12:00:00",
  counts: { snapshots: 2, traces: 3, memories: 7, agent_runs: 4, workflows: 2, skills: 3, timeline: 8 },
  graph: { nodes: { Topic: 1, Concept: 1, Task: 1, Decision: 1, File: 1 }, edges: { discusses: 1, mentions: 2, based_on: 2 } },
  models: { current_model: "mlx-community/gemma-4-12b-it-4bit", loaded_models: ["mlx-community/gemma-4-12b-it-4bit"], local_model: "mlx-community/gemma-4-12b-it-4bit" },
  workspace_registry: {
    active_workspace: "personal",
    workspaces: [
      { workspace_id: "personal", name: "Personal Workspace", type: "personal", your_role: "owner", member_count: 1, status: "active", members: [] },
      { workspace_id: "org-demo", name: "Design Org", type: "organization", your_role: "owner", member_count: 2, status: "active", members: [{ user_id: "admin@example.com", role: "owner" }, { user_id: "teammate@example.com", role: "member" }] },
    ],
  },
  edition: {
    edition: "community",
    is_enterprise: false,
    community_notice: "Community edition: Enterprise capabilities are extension points and do not gate Community features.",
    capabilities: {
      sso_advanced: false,
      idp_provisioning: false,
      scim: false,
      rbac_abac_advanced: false,
      tenant_isolation: false,
      compliance_retention: false,
      siem_export: false,
      private_vpc: false,
      dlp_policy: false,
      admin_policy_packs: false,
    },
  },
};

const enterpriseOverview = {
  edition: workspaceOs.edition,
  admin_policies: {
    capability: "admin_policy_packs",
    enabled: false,
    enforced: false,
    effective_policy: {
      base_roles: ["owner", "admin", "member", "viewer"],
      local_file_access: "approval-token gated (per path/user/action)",
      package_install: "admin-only with audit trail",
    },
    note: "Community features remain available.",
  },
  audit_export: {
    local_export: { available: true, endpoint: "/admin/security/export", formats: ["json", "csv", "xlsx", "txt", "pdf"] },
    siem_streaming: { enabled: false },
    compliance_retention: { enabled: false },
  },
  siem_export: {
    capability: "siem_export",
    enabled: false,
    streamed: false,
    destination: null,
    preview_envelope: { format: "ltcai.siem.v1", encoding: "ndjson", records: [{ ts: "2026-06-01T12:00:00", actor: "admin@example.com", act: "visual_smoke", sev: "informational" }] },
  },
  organization_settings: {
    community_baseline: { workspaces: ["personal", "organization"], roles: ["owner", "admin", "member", "viewer"], data_isolation: "single-tenant local storage (~/.ltcai)" },
    governance_capabilities: workspaceOs.edition.capabilities,
    note: "Enterprise governance is disabled in Community.",
  },
};

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  const pathname = decodeURIComponent(url.pathname);

  if (pathname === "/app" || pathname === "/v3") return serveFile(res, path.join(repoRoot, "static/v3/index.html"));
  if (pathname === "/" || pathname === "/workspace" || pathname === "/onboarding") return serveFile(res, path.join(repoRoot, "static/workspace.html"));
  if (pathname === "/graph" || pathname === "/knowledge-graph") return serveFile(res, path.join(repoRoot, "static/graph.html"));
  if (pathname === "/admin") return serveFile(res, path.join(repoRoot, "static/admin.html"));
  // v3 native Chat: POST /chat streams SSE; GET /chat still serves the legacy page.
  if (pathname === "/chat" && req.method === "POST") {
    res.writeHead(200, { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-store", connection: "keep-alive" });
    const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
    send({ chunk: "Hybrid retrieval ", model: "mock-local-model" });
    send({ chunk: "fuses the knowledge graph with the vector index, then reconciles the two ranked lists.", model: "mock-local-model" });
    send({ chunk: "", model: "mock-local-model", trace_id: "trace-mock", trace: {
      question: "", confidence: 0.9,
      graph_nodes: graphNodes.slice(0, 3).map((n) => ({ id: n.id, title: n.title, type: n.type })),
      source_files: [{ source: "notes/retrieval.md" }, { source: "config/index.yaml" }],
      vector_matches: [{ path: "notes/retrieval.md", score: 0.91 }, { path: "config/index.yaml", score: 0.74 }],
    } });
    res.write("data: [DONE]\n\n");
    return res.end();
  }
  if (pathname === "/history/conversations") return json(res, [
    { id: "conv-hybrid", title: "How hybrid search ranks", updated_at: "2026-06-06T13:20:00" },
    { id: "conv-reindex", title: "Reindex the workspace", updated_at: "2026-06-06T11:05:00" },
  ]);
  if (pathname.startsWith("/history/conversations/")) {
    if (req.method === "DELETE") return json(res, { removed: 1, kept: 0 });
    const id = pathname.slice("/history/conversations/".length);
    return json(res, { id, messages: [
      { role: "user", content: "How does hybrid search rank results?", timestamp: "2026-06-06T13:19:00" },
      { role: "assistant", content: "It fuses the vector index and the knowledge graph with reciprocal-rank fusion, so a strong hit in either modality surfaces.", timestamp: "2026-06-06T13:20:00" },
    ] });
  }
  if (pathname === "/chat") return serveFile(res, path.join(repoRoot, "static/chat.html"));
  if (pathname === "/account" || pathname === "/login") return serveFile(res, path.join(repoRoot, "static/account.html"));
  if (pathname === "/onboarding-fixture") return serveFile(res, path.join(repoRoot, "tests/visual/fixtures/onboarding.html"));
  if (pathname.startsWith("/static/")) return serveFile(res, path.join(repoRoot, pathname.slice(1)));
  if (pathname.startsWith("/icons/")) return serveFile(res, path.join(repoRoot, "static", pathname));
  if (pathname === "/manifest.json") return serveFile(res, path.join(repoRoot, "static/manifest.json"));
  if (pathname === "/favicon.ico") return serveFile(res, path.join(repoRoot, "static/favicon.ico"));
  if (pathname === "/sw.js") return serveFile(res, path.join(repoRoot, "static/sw.js"));

  // Keep the login page on /account: account.js redirects to /chat when
  // /account/profile is ok, so the visual mock returns 401 to stay on login.
  if (pathname === "/account/profile") return json(res, { detail: "unauthorized" }, 401);
  if (pathname === "/auth/sso/config") return json(res, { enabled: false, providers: [] });

  if (pathname === "/health") return json(res, { status: "ok", version: "3.4.0", mode: "visual" });
  if (pathname === "/vpc/status") return json(res, { provider: "local", region: "visual", vpn_status: "standby", peering_status: "not_configured", private_subnets: [] });
  if (pathname === "/workspace/os") return json(res, workspaceOs);
  if (pathname === "/workspace/onboarding/status") return json(res, { current_step: "complete", steps: ["account", "admin", "hardware", "model_recommendation", "folder_connection", "complete"].map((id) => ({ id, status: "complete" })) });
  if (pathname === "/workspace/traces") return json(res, { traces: [{ question: "What changed in v1.7.0?", confidence: 0.92, created_at: "2026-06-01T12:00:00", graph_nodes: graphNodes.slice(0, 2), source_files: [{ source: "README.md" }] }] });
  if (pathname === "/workspace/indexing") return json(res, { sources: [{ id: "source-demo", label: "Demo Repo", root_path: repoRoot, status: "indexed", success_count: 128, failure_count: 0, last_run_at: "2026-06-01T12:00:00", watch_active: true, file_status: { indexed: 128 } }] });
  if (pathname === "/workspace/snapshots") return json(res, { snapshots: [{ id: "snapshot-demo", name: "v1.7.0 checkpoint", created_at: "2026-06-01T12:00:00", node_count: 5 }] });
  if (pathname === "/workspace/memories") return json(res, { memories: [{ id: "mem-demo", kind: "decisions", content: "Ship graph and collaboration UX", updated_at: "2026-06-01T12:00:00", tags: ["release"] }] });
  if (pathname === "/workspace/computer-memory") return json(res, { enabled: false, approved: false, scopes: [], activities: [], notice: "disabled" });
  if (pathname === "/workspace/agents") return json(res, { agents: [{ id: "agent:planner", name: "Planner", role: "Plans release work", status: "available", relationships: ["agent:reviewer"] }] });
  if (pathname === "/workspace/workflows") return json(res, { workflows: [{ id: "wf-demo", name: "Validate -> Build -> Release", created_at: "2026-06-01T12:00:00", steps: [{ action: "validate" }, { action: "build" }] }] });
  if (pathname === "/workspace/skills") return json(res, {
    installed: [{ name: "code_review", description: "Review code changes", version: "1.0.0", enabled: true, installed: true, install_status: "ready", validation_status: "ready", source: "local" }],
    available: [
      { skill: "visual_regression", name: "visual_regression", description: "Capture and compare workspace UI", version: "1.2.0", category: "test", downloads: 2400, install_status: "available", validation_status: "not_installed", source: "marketplace" },
      { skill: "security_report", name: "security_report", description: "Summarize audit and policy risk", version: "1.1.0", category: "security", downloads: 1900, install_status: "available", validation_status: "not_installed", source: "marketplace" },
    ],
    total_installed: 1,
    total_available: 2,
  });
  if (pathname === "/workspace/time-machine") return json(res, { events: [{ event_type: "release_ready", area: "workspace", timestamp: "2026-06-01T12:00:00" }] });
  if (pathname.startsWith("/workspace/relationships/")) {
    const id = pathname.replace("/workspace/relationships/", "");
    return json(res, { node_id: id, node: graphNodes.find((node) => node.id === id) || { id }, inbound: graphEdges.filter((edge) => edge.to === id), outbound: graphEdges.filter((edge) => edge.from === id), related_entities: graphNodes, shortest_path: shortestPath(id, url.searchParams.get("target_id")) });
  }
  // ── v3 future API surfaces (integration targets) ──────────────────────────
  if (pathname === "/api/index/status") return json(res, {
    generated_at: "2026-06-06T12:00:00",
    pipelines: {
      knowledge_graph: { state: "ready", entities: graphNodes.length, relations: graphEdges.length, coverage: 0.9 },
      vector_index: { state: "ready", vectors: 48230, dimensions: 1024, model: "bge-local", coverage: 0.87 },
      hybrid: { state: "ready", strategy: "reciprocal-rank-fusion", alpha: 0.5 },
    },
    sources: [
      { id: "src-notes", label: "Workspace Notes", files: 312, state: "indexed", progress: 1 },
      { id: "src-repo", label: "Connected Repo", files: 1840, state: "indexing", progress: 0.62 },
    ],
  });
  if (pathname === "/api/graph") return json(res, { nodes: graphNodes, edges: graphEdges });
  if (pathname === "/api/search/hybrid") return json(res, {
    query: (url.searchParams.get("query") || "retrieval"),
    results: graphNodes.slice(0, 4).map((n, i) => ({
      id: n.id, title: n.title, path: (n.metadata && n.metadata.relative_path) || `graph://${n.id}`,
      snippet: n.summary, vector: 0.9 - i * 0.1, lexical: 0.6 - i * 0.08, graph: 0.8 - i * 0.05,
      score: 0.85 - i * 0.09,
    })),
  });

  if (pathname === "/api/embeddings/status") return json(res, {
    provider: "ollama", requested_provider: "ollama", active_provider: "ollama",
    model: "nomic-embed-text", model_id: "ollama:nomic-embed-text:768", dimensions: 768,
    grade: "production", state: "production", fell_back: false,
    health: { status: "ok", detail: "Ollama reachable" },
    last_indexed_at: "2026-06-06T12:30:00", index: { status: "ready", indexed_items: 48230 },
  });
  if (pathname === "/api/embeddings/providers") return json(res, { active: "ollama", requested: "ollama", providers: [
    { id: "hash", label: "Local hash (fallback)", grade: "fallback" },
    { id: "mlx", label: "MLX (Apple Silicon)", grade: "production" },
    { id: "ollama", label: "Ollama", grade: "production" },
    { id: "openai", label: "OpenAI-compatible", grade: "production" },
    { id: "custom", label: "Custom", grade: "production" },
  ] });
  // ── v3.4.0 Platform Completion surfaces ───────────────────────────────────
  if (pathname === "/models") return json(res, {
    recommended: [
      { id: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", name: "Qwen2.5-VL 7B", display_name: "Qwen2.5-VL 7B", family: "qwen-vl", modality: "multimodal", capabilities: ["vision", "text"], state: "loaded" },
      { id: "mlx-community/gemma-4-12b-it-4bit", name: "Gemma 4 12B", display_name: "Gemma 4 12B", family: "gemma", capabilities: ["text"], state: "available" },
    ],
    cloud: [],
    engines: [{ id: "local_mlx", name: "MLX", kind: "local", installed: true }],
    loaded: ["mlx-community/Qwen2.5-VL-7B-Instruct-4bit"],
    current: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
    compat_profiles: [],
    vision: { current_model: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", current_supports_vision: true, engine_available: true, enabled: true },
  });
  if (pathname === "/local/sysinfo") return json(res, { cpu_pct: 34, ram_pct: 61, gpu_mem_pct: 48, gpu_mem_gb: 9.4 });
  if (pathname === "/knowledge-graph/documents") return json(res, {
    documents: [
      { id: "file:a1b2c3", filename: "retrieval-design.pdf", ext: ".pdf", mime_type: "application/pdf", bytes: 184320, sha256: "a1b2c3d4e5f6", uploader: "you@local", chars: 18240, chunks: 24, indexed: true, ingest_state: "indexed", created_at: "2026-06-07T10:00:00", updated_at: "2026-06-07T10:00:05" },
      { id: "file:d4e5f6", filename: "meeting-notes.md", ext: ".md", mime_type: "text/markdown", bytes: 4096, sha256: "d4e5f6a1b2c3", uploader: "you@local", chars: 3200, chunks: 4, indexed: true, ingest_state: "indexed", created_at: "2026-06-07T09:30:00", updated_at: "2026-06-07T09:30:02" },
      { id: "file:g7h8i9", filename: "q3-budget.xlsx", ext: ".xlsx", mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", bytes: 20480, sha256: "g7h8i9j0k1l2", uploader: "you@local", chars: 980, chunks: 2, indexed: true, ingest_state: "indexed", created_at: "2026-06-06T16:10:00", updated_at: "2026-06-06T16:10:01" },
      { id: "file:m3n4o5", filename: "onboarding.docx", ext: ".docx", mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", bytes: 51200, sha256: "m3n4o5p6q7r8", uploader: "you@local", chars: 0, chunks: 0, indexed: false, ingest_state: "ingested", created_at: "2026-06-07T10:01:00", updated_at: "2026-06-07T10:01:00" },
    ],
    total: 4,
    generated_at: "2026-06-07T10:00:10",
  });
  {
    const localSources = {
      sources: [
        { id: "src-docs", label: "Documents", root_path: "/Users/you/Documents", success_count: 312, failure_count: 0, status: "indexed", last_run_at: "2026-06-07T09:00:00", watch_enabled: true, watch_active: true, watch_status: { last_event_at: 1717740000, last_indexed_at: 1717740300, last_error: null } },
        { id: "src-proj", label: "lattice (project)", root_path: "/Users/you/code/lattice", success_count: 1840, failure_count: 2, status: "indexed", last_run_at: "2026-06-07T08:30:00", watch_enabled: false, watch_active: false, watch_status: null },
      ],
      watch: { available: true, error: "", debounce_seconds: 5, active: { "src-docs": { root_path: "/Users/you/Documents", last_event_at: 1717740000, last_indexed_at: 1717740300, last_error: null } } },
    };
    if (pathname === "/knowledge-graph/local/sources") return json(res, localSources);
    if (pathname === "/knowledge-graph/local/roots") return json(res, { roots: [{ path: "/Users/you/Documents", label: "Documents" }, { path: "/Users/you/Desktop", label: "Desktop" }, { path: "/Users/you/code", label: "code" }] });
    if (pathname === "/knowledge-graph/local/watch/status") return json(res, localSources.watch);
    if (pathname === "/api/local-agent/status") return json(res, {
      agent: { id: "lattice-local-runtime", name: "Lattice Local Agent", kind: "on-device-runtime", online: true, platform: "macOS-15.5-arm64-arm-64bit", machine: "arm64", python: "3.12.4" },
      handshake: { ok: true, transport: "in-process", detail: "The local Lattice runtime serves this workspace on-device; no external agent is required." },
      health: { status: "ok", filesystem_access: true, watcher_available: true },
      folders: { connected: 2, watching: 1 },
      watch: localSources.watch,
      sources: localSources.sources,
    });
  }
  if (pathname === "/api/hooks/runs") return json(res, {
    runs: [
      { hook_id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run", status: "ok", detail: "", output: "redacted 1 field(s)", duration_ms: 1, blocked: false, target_event: "agent.run", target_kind: "pre_run", started_at: "2026-06-07T10:00:01" },
      { hook_id: "builtin:audit-agent-run", name: "Audit agent run", kind: "post_run", status: "ok", detail: "", output: "audited run agent-run-9", duration_ms: 2, blocked: false, target_event: "agent.run", target_kind: "post_run", started_at: "2026-06-07T10:00:02" },
      { hook_id: "builtin:pipeline-index-status", name: "Pipeline index status", kind: "pipeline", status: "ok", detail: "", output: "pipeline document.ingested: indexed=true", duration_ms: 0, blocked: false, target_event: "document.ingested", target_kind: "pipeline", started_at: "2026-06-07T09:58:00" },
      { hook_id: "user:notify-slack", name: "Notify Slack on release", kind: "post_run", status: "ok", detail: "", output: "posted to #releases", duration_ms: 142, blocked: false, target_event: "agent.run", target_kind: "post_run", started_at: "2026-06-07T09:55:00" },
      { hook_id: "user:policy-gate", name: "Policy gate", kind: "pre_tool", status: "blocked", detail: "write to /etc denied by policy", output: "", duration_ms: 8, blocked: true, target_event: "tool.write_file", target_kind: "pre_tool", started_at: "2026-06-07T09:50:00" },
    ],
    total: 5,
    generated_at: "2026-06-07T10:00:10",
  });
  if (pathname === "/agents/api/runtime/status") return json(res, {
    runtime: { ready: true, version: "2.2.0", execution_mode: "synchronous", default_pipeline: ["planner", "executor", "reviewer"], total_runs: 3, active_runs: 0 },
    health: { status: "ok", checks: { run_store: { status: "ok" }, orchestrator: { status: "ok" } } },
    roles: [{ role: "planner", agent_id: "agent:planner" }, { role: "executor", agent_id: "agent:executor" }, { role: "reviewer", agent_id: "agent:reviewer" }],
    agents: [
      { id: "agent:planner", name: "Planner", role: "Decomposes the goal into an ordered plan.", state: "available", runs: 2, handoffs: ["agent:executor"] },
      { id: "agent:executor", name: "Executor", role: "Executes each planned step.", state: "available", runs: 1, handoffs: ["agent:reviewer"] },
      { id: "agent:reviewer", name: "Reviewer", role: "Reviews and approves the work.", state: "available", runs: 1, handoffs: [] },
    ],
    runs: [
      { id: "agent-run-1", agent_id: "agent:executor", status: "ok", input: "Summarize release", output: "Completed 3/3 steps", created_at: "2026-06-06T12:30:00" },
      { id: "agent-run-2", agent_id: "agent:executor", status: "retried_ok", input: "Build index", output: "Completed after 1 retry", created_at: "2026-06-06T11:05:00" },
    ],
  });
  if (pathname === "/agents/api/runtime/health") return json(res, { status: "ok", checks: { run_store: { status: "ok" }, orchestrator: { status: "ok" } } });
  if (pathname === "/agents/api/runtime/config") return json(res, { version: "2.2.0", roles: ["researcher", "planner", "executor", "reviewer", "release"], default_pipeline: ["planner", "executor", "reviewer"], max_retries_cap: 5, execution_mode: "synchronous" });
  if (pathname === "/agents/api/registry") return json(res, {
    agents: [
      { id: "agent:researcher", name: "Researcher", type: "researcher", version: "3.3.1", description: "Gathers workspace context.", capabilities: ["context-retrieval", "hybrid-search"], source: "builtin", enabled: true, removable: false, config: {} },
      { id: "agent:planner", name: "Planner", type: "planner", version: "3.3.1", description: "Builds bounded plans.", capabilities: ["task-decomposition", "delegation"], source: "builtin", enabled: true, removable: false, config: {} },
      { id: "agent:executor", name: "Executor", type: "executor", version: "3.3.1", description: "Executes tools and workflows.", capabilities: ["tool-use", "workflow-run"], source: "builtin", enabled: true, removable: false, config: {} },
      { id: "agent:reviewer", name: "Reviewer", type: "reviewer", version: "3.3.1", description: "Reviews execution.", capabilities: ["verification", "approval"], source: "builtin", enabled: true, removable: false, config: {} },
      { id: "agent:release", name: "Release", type: "release", version: "3.3.1", description: "Finalizes approved outcomes.", capabilities: ["summarize"], source: "builtin", enabled: true, removable: false, config: {} },
    ],
    types: ["planner", "researcher", "executor", "reviewer", "release", "custom"],
    counts: { planner: 1, researcher: 1, executor: 1, reviewer: 1, release: 1 },
    total: 5,
    version: "3.3.1",
    default_pipeline: ["planner", "executor", "reviewer"],
  });
  if (pathname === "/agents/api/registry/capabilities") return json(res, {
    capabilities: {
      "tool-use": ["agent:executor"],
      "workflow-run": ["agent:executor"],
      "verification": ["agent:reviewer"],
      "task-decomposition": ["agent:planner"],
      "hybrid-search": ["agent:researcher"],
    },
  });

  if (pathname === "/marketplace/templates") return json(res, {
    marketplace_version: "3.3.1",
    kinds: ["plugin", "workflow", "agent"],
    templates: [
      { id: "agent-research-assistant", kind: "agent", name: "Research Assistant", version: "1.0.0", description: "Retrieves workspace context and synthesizes a reviewed answer.", metadata: { category: "research" }, definition: { roles: ["researcher", "planner", "reviewer"], capabilities: ["hybrid-search", "memory-recall"] } },
      { id: "agent-coding-assistant", kind: "agent", name: "Coding Assistant", version: "1.0.0", description: "Plans a code change, executes it, and reviews the result.", metadata: { category: "coding" }, definition: { roles: ["planner", "executor", "reviewer"], capabilities: ["tool-use", "verification"] } },
      { id: "workflow-agent-plugin-review", kind: "workflow", name: "Agent Plugin Review Workflow", version: "1.0.0", description: "Trigger into agent chain, plugin, and output.", metadata: { category: "agent-ops" }, definition: { roles: ["planner", "executor", "reviewer"] } },
    ],
    total: 3,
  });
  if (pathname === "/marketplace/templates/registry") return json(res, { registry: {} });
  if (pathname === "/plugins/registry") return json(res, { plugins: [{ id: "hello-world", name: "Hello World", version: "1.0.0", description: "Demo plugin", installed: true, enabled: true }] });
  if (pathname === "/plugins/directory") return json(res, { plugins: [{ id: "git-insights", name: "Git Insights", description: "Repository summary plugin", version: "1.0.0", author: "Lattice" }], categories: ["dev"] });
  if (pathname === "/skills/marketplace") return json(res, { skills: [{ skill: "visual_regression", name: "visual_regression", description: "Capture and compare workspace UI", version: "1.2.0", author: "Lattice", category: "test", installed: false }], categories: ["test"] });
  if (pathname === "/workflows/api/definitions") return json(res, { workflows: [{ id: "wf-agent-review", name: "Agent Review Workflow", nodes: [
    { id: "trigger", type: "trigger", name: "Trigger", next: "agent" },
    { id: "agent", type: "agent", name: "Agent chain", next: "tool" },
    { id: "tool", type: "tool", name: "Tool", next: "output" },
    { id: "output", type: "output", name: "Result", next: null },
  ] }] });
  if (pathname === "/workflows/api/runs") return json(res, { runs: [{ id: "wf-run-1", workflow_id: "wf-agent-review", workflow_name: "Agent Review Workflow", status: "ok", created_at: "2026-06-06T12:00:00" }] });
  if (pathname === "/api/memory/manager") return json(res, {
    sources: [
      { id: "workspace", type: "workspace", label: "Workspace Memory", count: 3, size_bytes: 2048, health: "ok", detail: "Personal workspace memory." },
      { id: "project", type: "project", label: "Project Memory", count: 1, size_bytes: 0, health: "ok", detail: "Organization memory." },
      { id: "agent", type: "agent", label: "Agent Memory", count: 2, size_bytes: 0, health: "ok", detail: "Agent memory snapshots." },
      { id: "conversation", type: "conversation", label: "Conversation Memory", count: 2, size_bytes: 1024, health: "ok", detail: "Chat history." },
      { id: "graph", type: "graph", label: "Graph Memory", count: graphNodes.length, size_bytes: 4096, health: "ok", detail: "Knowledge graph entities.", edges: graphEdges.length },
      { id: "vector", type: "vector", label: "Vector Memory", count: 8, size_bytes: 0, health: "ok", detail: "Vector index." },
    ],
    tiers: ["workspace", "project", "agent", "conversation", "graph", "vector"],
    usage: { total_items: 21, total_bytes: 7168, sources: 6 },
    health: "ok",
  });
  if (pathname === "/api/memory/inspect") return json(res, { source: url.searchParams.get("source"), items: [{ id: "mem-demo", kind: "workspace", title: "Demo memory", content: "Release memory" }], count: 1, available: true, stats: workspaceOs.graph, index: { status: "ready" } });
  if (pathname === "/api/hooks/run" && req.method === "POST") return json(res, {
    hook_id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run",
    status: "ok", detail: "", output: "redacted 1 field(s)", duration_ms: 1, blocked: false,
    source: "builtin", binding: "multi_agent._redact", started_at: "2026-06-07T10:05:00",
  });
  if (pathname === "/api/hooks/fire" && req.method === "POST") return json(res, { kind: "pre_run", event: "manual", ran: 1, blocked: false, block_reason: "", results: [], generated_at: "2026-06-07T10:05:00" });
  if (pathname === "/api/hooks") return json(res, { hooks: [
    { id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run", order: 10, description: "Strip secret-like fields from agent context before a run.", binding: "multi_agent._redact", managed: "platform", source: "builtin", enabled: true, removable: false },
    { id: "builtin:research-memory-snapshot", name: "Research memory snapshot", kind: "agent", order: 20, description: "Capture a short-term memory snapshot after the researcher stage.", binding: "multi_agent.default_role_runner", managed: "platform", source: "builtin", enabled: true, removable: false },
    { id: "builtin:tool-permission-gate", name: "Tool permission gate", kind: "pre_tool", order: 10, description: "Require approval for tools whose policy is not auto-approve.", binding: "tool_registry.permission", managed: "platform", source: "builtin", enabled: true, removable: false },
    { id: "builtin:sensitive-data-guard", name: "Sensitive-data guard", kind: "pre_tool", order: 20, description: "Classify outgoing content for sensitive data before tool execution.", binding: "server_app.classify_sensitive_message", managed: "platform", source: "builtin", enabled: true, removable: false },
    { id: "builtin:audit-agent-run", name: "Audit agent run", kind: "post_run", order: 10, description: "Append every completed agent run to the workspace audit log.", binding: "AgentRuntime.start", managed: "platform", source: "builtin", enabled: true, removable: false },
    { id: "builtin:pipeline-index-status", name: "Pipeline index status", kind: "pipeline", order: 10, description: "Publish ingest / embed / graph-build pipeline state.", binding: "api.search", managed: "platform", source: "builtin", enabled: true, removable: false },
    { id: "builtin:workflow-replay-log", name: "Workflow replay log", kind: "workflow", order: 10, description: "Record each workflow run's timeline so it can be replayed.", binding: "api.workflow_designer", managed: "platform", source: "builtin", enabled: true, removable: false },
    { id: "user:notify-slack", name: "Notify Slack on release", kind: "post_run", order: 100, description: "Posts a message to #releases after an agent run.", command: "python3 scripts/notify.py", managed: "user", source: "user", enabled: true, removable: true },
  ], kinds: ["pre_run", "post_run", "pre_tool", "post_tool", "agent", "pipeline", "workflow"], counts: { pre_run: { total: 1, enabled: 1 }, post_run: { total: 2, enabled: 2 }, pre_tool: { total: 2, enabled: 2 }, agent: { total: 1, enabled: 1 }, pipeline: { total: 1, enabled: 1 }, workflow: { total: 1, enabled: 1 } }, total: 8, enabled: 8 });
  if (pathname === "/tools/permissions") return json(res, { status: "ok", permissions: [
    { tool: "read_file", risk: "low", requires_approval: false, network: false },
    { tool: "write_file", risk: "medium", requires_approval: true, network: false },
    { tool: "run_command", risk: "high", requires_approval: true, network: false },
  ] });
  if (pathname === "/mcp/tools") return json(res, { status: "ok", installed_mcps: [{ id: "mcp-files", name: "Files", description: "File MCP", category: "local", installed: true }], tools: [
    { name: "read_file", description: "Read workspace files.", permission: { tool: "read_file", risk: "low", requires_approval: false, network: false }, governance: { risk: "read", destructive: false, shell: false, network: false, auto_approve: true, sandbox: "workspace", rollback: "none" } },
    { name: "write_file", description: "Write workspace files.", permission: { tool: "write_file", risk: "medium", requires_approval: true, network: false }, governance: { risk: "write", destructive: false, shell: false, network: false, auto_approve: false, sandbox: "workspace", rollback: "git" } },
    { name: "run_command", description: "Run allowlisted commands.", permission: { tool: "run_command", risk: "high", requires_approval: true, network: false }, governance: { risk: "exec", destructive: false, shell: true, network: false, auto_approve: false, sandbox: "workspace", rollback: "none" } },
  ] });
  if (pathname === "/mcp/installed") return json(res, { installed: [{ id: "mcp-files", name: "Files", installed: true }] });
  if (pathname === "/mcp/claude-code-servers") return json(res, { servers: [{ id: "claude-code:filesystem", name: "filesystem", description: "Claude Code MCP", package: "npx filesystem", category: "Claude Code", source: "claude-code", installed: true, env_vars: [] }] });
  if (pathname === "/mcp/custom") return json(res, { custom: [{ id: "custom:docs", name: "Docs", description: "Docs MCP", package: "npx docs", category: "custom", source: "custom", installed: false, env_vars: [] }] });

  if (pathname === "/knowledge-graph/graph") return json(res, { nodes: graphNodes, edges: graphEdges });
  if (pathname === "/knowledge-graph/stats") return json(res, workspaceOs.graph);
  if (pathname === "/knowledge-graph/search") return json(res, { query: url.searchParams.get("q"), matches: graphNodes });
  if (pathname.startsWith("/knowledge-graph/neighbors/")) return json(res, { node_id: pathname.replace("/knowledge-graph/neighbors/", ""), neighbors: graphNodes, edges: graphEdges });

  if (pathname === "/admin/summary") return json(res, { total_users: 2, active_users: 2, admin_users: 1, total_messages: 42, user_messages: 21, assistant_messages: 21 });
  if (pathname === "/admin/users") return json(res, [{ email: "admin@example.com", nickname: "Admin", role: "admin", disabled: false }, { email: "member@example.com", nickname: "Member", role: "user", disabled: false }]);
  if (pathname === "/admin/sensitivity") return json(res, { summary: { risky_messages: 1, compliant_messages: 41, risk_rate: 2, severity_counts: { high: 0 }, field_counts: {}, user_counts: {} }, risk_fields: [], compliance_fields: [] });
  if (pathname === "/admin/invite-link") return json(res, { invite_url: `http://127.0.0.1:${port}/`, invite_code: "visual", gate_enabled: false });
  if (pathname === "/admin/stats") return json(res, { daily: [{ date: "2026-06-01", user: 8, assistant: 8 }] });
  if (pathname === "/admin/audit") return json(res, { summary: { total_events: 12, chat_events: 6, user_messages: 3, assistant_messages: 3, document_uploads: 2, clear_events: 1, sensitive_events: 1, high_sensitive_events: 0 }, graph: workspaceOs.graph, per_user: [], recent_events: [
    { ts: "2026-06-06T09:12:00", actor: "admin@example.com", action: "policy.update", target: "local_file_access", severity: "notice" },
    { ts: "2026-06-06T10:40:00", actor: "member@example.com", action: "search.hybrid", target: "q: retrieval design", severity: "informational" },
    { ts: "2026-06-06T11:05:00", actor: "admin@example.com", action: "user.invite", target: "guest@example.com", severity: "notice" },
    { ts: "2026-06-06T12:30:00", actor: "system", action: "index.rebuild", target: "vector_index", severity: "informational" },
    { ts: "2026-06-06T13:15:00", actor: "member@example.com", action: "file.access.denied", target: "secrets/.env", severity: "warning" },
  ] });
  if (pathname === "/admin/sso") return json(res, { enabled: false, provider_name: "Okta", discovery_url: "", client_id: "", redirect_uri: "", scopes: "openid email profile" });
  if (pathname === "/admin/enterprise") return json(res, enterpriseOverview);
  if (pathname === "/admin/enterprise/siem-export") return json(res, enterpriseOverview.siem_export);
  if (pathname === "/admin/roles") return json(res, { roles: [
    { role: "admin", members: 1, caps: ["users", "policies", "audit", "security", "chat", "search", "files", "pipeline"] },
    { role: "user", members: 1, caps: ["chat", "search", "files", "pipeline"] },
  ] });
  if (pathname === "/admin/policies") return json(res, { policies: [
    { id: "local_file_access", label: "Local file access", value: "Approval-token gated (per path/user/action)", enforced: true },
    { id: "package_install", label: "Package install", value: "Admin-only with audit trail", enforced: true },
    { id: "data_residency", label: "Data residency", value: "Single-tenant local storage (~/.ltcai)", enforced: true },
    { id: "model_egress", label: "Model egress", value: "Local-only by default", enforced: true },
  ] });
  if (pathname === "/admin/security/overview") return json(res, {
    generated_at: "2026-06-06T12:00:00", risk_rate: 2,
    cards: { events_today: 5, high_risk_events: 0, risky_chats: 1, review_required: 1 },
    severity_counts: { high: 0, medium: 1, low: 2 }, field_counts: { email: 4, api_key: 1 },
  });
  if (pathname.startsWith("/admin/security/")) return json(res, { cards: {}, users: [], events: [], files: [], field_counts: {} });

  if (req.method === "POST" || req.method === "PATCH" || req.method === "DELETE") return json(res, { status: "ok" });
  text(res, "not found", "text/plain; charset=utf-8");
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Lattice AI visual mock server listening on http://127.0.0.1:${port}`);
});
