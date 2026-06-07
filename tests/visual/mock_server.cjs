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

  if (pathname === "/health") return json(res, { status: "ok", version: "1.7.0", mode: "visual" });
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
