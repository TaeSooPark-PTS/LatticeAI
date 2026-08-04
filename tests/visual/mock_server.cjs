const http = require("http");
const fs = require("fs");
const path = require("path");

const repoRoot = path.resolve(__dirname, "../..");
const port = Number(process.env.LTCAI_VISUAL_PORT || 4927);
const appVersion = require(path.join(repoRoot, "package.json")).version;
const releaseRunId = `run-${appVersion.replace(/\D/g, "")}-product`;

// One coherent personal brain for release captures 05 + 11: pipeline counts
// (received/extracted/connected) must match this graph's scale so the two
// screenshots look like the same computer.
//
// Capture 05 types "workspace" into the graph search. Client-side filter is
// substring match on id/title/type/summary — at least 4 nodes and 3 edges
// among those nodes must survive that filter (B1). Labels below intentionally
// share the token "workspace" so the filtered subgraph stays a real map.
const graphNodes = [
  { id: "entity:lattice", type: "Topic", title: "Lattice Workspace", summary: "Local-first personal memory system for this workspace", importance_norm: 0.96, metadata: { graph_metrics: { degree: 6, importance_norm: 0.96, mention_count: 14, conversation_count: 5 } } },
  { id: "entity:workspace", type: "Concept", title: "Workspace Health", summary: "How this computer's Brain is doing in the workspace", importance_norm: 0.82, metadata: { graph_metrics: { degree: 4, importance_norm: 0.82 } } },
  { id: "entity:skills", type: "Task", title: "Skill Marketplace", summary: "Install, validate, and update skills", importance_norm: 0.72, metadata: { graph_metrics: { degree: 2, importance_norm: 0.72 } } },
  { id: "entity:enterprise", type: "Decision", title: "Enterprise Admin", summary: "Capability status without Community lockouts", importance_norm: 0.68, metadata: { graph_metrics: { degree: 2, importance_norm: 0.68 } } },
  { id: "entity:release", type: "Task", title: "릴리스 절차", summary: "How this release gets out the door", importance_norm: 0.88, metadata: { graph_metrics: { degree: 4, importance_norm: 0.88, mention_count: 9 } } },
  { id: "entity:memory", type: "Concept", title: "Workspace 개인 기억", summary: "Things kept only on this machine's workspace memory", importance_norm: 0.9, metadata: { graph_metrics: { degree: 3, importance_norm: 0.9 } } },
  { id: "entity:review", type: "Decision", title: "검토함", summary: "Changes waiting for a human yes", importance_norm: 0.7, metadata: { graph_metrics: { degree: 2, importance_norm: 0.7 } } },
  { id: "file:readme", type: "File", title: "workspace-README.md", summary: "Release documentation for this workspace", importance_norm: 0.58, metadata: { filename: "workspace-README.md", relative_path: "README.md", graph_metrics: { degree: 3, importance_norm: 0.58 } } },
  { id: "file:retrieval", type: "File", title: "retrieval-design.pdf", summary: "How search finds the right memory", importance_norm: 0.64, metadata: { filename: "retrieval-design.pdf", relative_path: "docs/retrieval-design.pdf", graph_metrics: { degree: 2, importance_norm: 0.64 } } },
  { id: "file:meeting", type: "File", title: "meeting-notes.md", summary: "Notes from last planning pass", importance_norm: 0.55, metadata: { filename: "meeting-notes.md", relative_path: "notes/meeting-notes.md", graph_metrics: { degree: 2, importance_norm: 0.55 } } },
  { id: "file:onboarding", type: "File", title: "onboarding.docx", summary: "How a new person starts with Lattice", importance_norm: 0.5, metadata: { filename: "onboarding.docx", relative_path: "docs/onboarding.docx", graph_metrics: { degree: 1, importance_norm: 0.5 } } },
  { id: "note:budget", type: "Note", title: "Q3 예산 메모", summary: "Personal note kept in the Brain", importance_norm: 0.48, metadata: { graph_metrics: { degree: 1, importance_norm: 0.48 } } },
];

// Edges among the four "workspace" search hits (lattice, workspace, memory,
// readme): lattice↔workspace, lattice↔memory, workspace→readme, memory→readme.
const graphEdges = [
  { from: "entity:lattice", to: "entity:workspace", type: "discusses", weight: 1.4 },
  { from: "entity:lattice", to: "entity:skills", type: "mentions", weight: 1.1 },
  { from: "entity:lattice", to: "entity:enterprise", type: "mentions", weight: 1.0 },
  { from: "entity:lattice", to: "entity:memory", type: "discusses", weight: 1.3 },
  { from: "entity:lattice", to: "entity:release", type: "mentions", weight: 1.2 },
  { from: "entity:workspace", to: "file:readme", type: "based_on", weight: 0.8 },
  { from: "entity:memory", to: "file:readme", type: "based_on", weight: 0.85 },
  { from: "entity:skills", to: "file:readme", type: "based_on", weight: 0.7 },
  { from: "entity:release", to: "file:retrieval", type: "based_on", weight: 0.9 },
  { from: "entity:review", to: "file:meeting", type: "based_on", weight: 0.75 },
];

let installedRecipeWorkflow = null;

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

function redirect(res, target) {
  res.writeHead(308, { location: target, "cache-control": "no-store" });
  res.end();
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
  // Matches graphNodes (12) + graphEdges (10) used by capture 05 / pipeline 11.
  graph: {
    nodes: { Topic: 1, Concept: 2, Task: 2, Decision: 2, File: 4, Note: 1 },
    edges: { discusses: 2, mentions: 3, based_on: 5 },
  },
  models: { current_model: "mlx-community/Qwen3-VL-8B-Instruct-4bit", loaded_models: ["mlx-community/Qwen3-VL-8B-Instruct-4bit"], local_model: "mlx-community/Qwen3-VL-8B-Instruct-4bit" },
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

const snapshots = [
  { id: "snapshot-demo", name: "v4 checkpoint", created_at: "2026-06-01T12:00:00", node_count: 5, edge_count: 5, chat_count: 2, workspace_id: "personal" },
  { id: "snapshot-prev", name: "previous checkpoint", created_at: "2026-05-30T12:00:00", node_count: 3, edge_count: 2, chat_count: 1, workspace_id: "personal" },
];

const peers = [
  { peer_id: "peer-studio", name: "Studio Mac", base_url: "http://studio.local:8765", fingerprint: "sha256:VISUAL", public_key: "-----BEGIN PUBLIC KEY-----\\nvisual\\n-----END PUBLIC KEY-----" },
];

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

  if (pathname === "/app" || pathname === "/v3") return serveFile(res, path.join(repoRoot, "static/app/index.html"));
  if (pathname === "/") return redirect(res, "/app#/account");
  if (pathname === "/workspace" || pathname === "/onboarding") return redirect(res, "/app#/workspace-admin");
  if (pathname === "/graph" || pathname === "/knowledge-graph") return redirect(res, "/app#/knowledge-graph");
  if (pathname === "/admin") return redirect(res, "/app#/admin/users");
  if (pathname === "/agents") return redirect(res, "/app#/agents");
  if (pathname === "/workflows") return redirect(res, "/app#/workflows");
  if (pathname === "/activity") return redirect(res, "/app#/activity");
  if (pathname === "/plugins/sdk") return redirect(res, "/app#/marketplace");
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
      { role: "assistant", content: "It fuses the vector index and the knowledge graph with **reciprocal-rank fusion**, so a strong hit in either modality surfaces.", timestamp: "2026-06-06T13:20:00" },
    ] });
  }
  if (pathname === "/chat") return redirect(res, "/app#/chat");
  if (pathname === "/account") return redirect(res, "/app#/account");
  if (pathname.startsWith("/static/")) return serveFile(res, path.join(repoRoot, pathname.slice(1)));
  if (pathname.startsWith("/icons/")) return serveFile(res, path.join(repoRoot, "static", pathname));
  if (pathname === "/manifest.json") return serveFile(res, path.join(repoRoot, "static/manifest.json"));
  if (pathname === "/favicon.ico") return serveFile(res, path.join(repoRoot, "static/favicon.ico"));
  if (pathname === "/sw.js") return serveFile(res, path.join(repoRoot, "static/sw.js"));

  if (pathname === "/account/profile") {
    if (req.method === "PATCH") return json(res, { email: "admin@example.com", nickname: "Admin", name: "Admin", role: "admin" });
    return json(res, { email: "admin@example.com", nickname: "Admin", name: "Admin", role: "admin" });
  }
  if (pathname === "/login" && req.method === "POST") return json(res, { status: "ok", email: "admin@example.com", nickname: "Admin", role: "admin" });
  if (pathname === "/register" && req.method === "POST") return json(res, { status: "ok", message: "registered", role: "user" });
  if (pathname === "/logout" && req.method === "POST") return json(res, { status: "ok" });
  if (pathname === "/account/change-password" && req.method === "POST") return json(res, { status: "ok" });
  if (pathname === "/auth/sso/config") return json(res, { enabled: false, providers: [] });

  if (pathname === "/health") return json(res, { status: "ok", version: appVersion, mode: "visual" });
  if (pathname === "/vpc/status") return json(res, { provider: "local", region: "visual", vpn_status: "standby", peering_status: "not_configured", private_subnets: [] });
  if (pathname === "/workspace/os") return json(res, workspaceOs);
  if (pathname === "/workspace/registry") return json(res, workspaceOs.workspace_registry);
  if (pathname === "/workspace/activate" && req.method === "POST") return json(res, { workspace: workspaceOs.workspace_registry.workspaces[0] });
  if (pathname.startsWith("/workspace/orgs/") && pathname.endsWith("/archive") && req.method === "POST") return json(res, { workspace: { workspace_id: pathname.split("/")[3], status: "archived" } });
  if (pathname.startsWith("/workspace/orgs/") && pathname.endsWith("/members") && req.method === "POST") return json(res, { workspace: workspaceOs.workspace_registry.workspaces[1] });
  if (pathname.startsWith("/workspace/orgs/") && (req.method === "PATCH" || req.method === "DELETE")) return json(res, { workspace: workspaceOs.workspace_registry.workspaces[1] });
  if (pathname === "/workspace/onboarding/status") return json(res, { current_step: "complete", steps: ["account", "admin", "hardware", "model_recommendation", "folder_connection", "complete"].map((id) => ({ id, status: "complete" })) });
  if (pathname === "/setup/scan") return json(res, {
    environment: {
      os: "darwin",
      arch: "arm64",
      ram_mb: 65536,
      gpu: { vendor: "apple", vram_mb: 65536 },
      installed_runtimes: ["local acceleration"],
      local_models: ["mlx-community/Qwen3-VL-8B-Instruct-4bit"],
    },
    recommendations: {
      summary: {
        zero_config: {
          model_id: "mlx-community/gemma-4-26b-a4b-it-4bit",
          rationale: ["Apple Silicon and 64 GB memory detected."],
        },
      },
    },
    zero_config: {
      recommend: { model_id: "mlx-community/gemma-4-26b-a4b-it-4bit" },
    },
  });
  if (pathname === "/workspace/traces") return json(res, { traces: [{ question: "What changed in v1.7.0?", confidence: 0.92, created_at: "2026-06-01T12:00:00", graph_nodes: graphNodes.slice(0, 2), source_files: [{ source: "README.md" }] }] });
  if (pathname === "/workspace/indexing") return json(res, { sources: [{ id: "source-demo", label: "Demo Repo", root_path: repoRoot, status: "indexed", success_count: 128, failure_count: 0, last_run_at: "2026-06-01T12:00:00", watch_active: true, file_status: { indexed: 128 } }] });
  if (pathname === "/workspace/snapshots") {
    if (req.method === "POST") return json(res, { snapshot: snapshots[0] });
    return json(res, { snapshots });
  }
  if (pathname === "/workspace/snapshots/compare" && req.method === "POST") return json(res, { summary: { nodes_added: 2, nodes_removed: 0, edges_added: 3, edges_removed: 0, decisions_changed: 1 } });
  if (pathname.startsWith("/workspace/snapshots/") && pathname.endsWith("/export") && req.method === "POST") return json(res, { snapshot_id: pathname.split("/")[3], export_path: "/tmp/snapshot.zip", bytes: 4096 });
  if (pathname.startsWith("/workspace/snapshots/") && pathname.endsWith("/restore") && req.method === "POST") return json(res, { restored: true, restore: { id: "restore-demo", mode: "merge", graph: { imported: true, nodes: 5, edges: 5 } } });
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
  if (pathname === "/invitations") {
    if (req.method === "POST") return json(res, { invitation: { id: "invite-demo", token: "invite-token-demo", email: "new@example.com", role: "member", status: "pending" } });
    return json(res, { invitations: [{ id: "invite-demo", token: "invite-token-demo", email: "new@example.com", role: "member", status: "pending" }] });
  }
  if (pathname.startsWith("/invitations/") && pathname.endsWith("/accept") && req.method === "POST") return json(res, { invitation: { id: "invite-demo", status: "accepted" } });
  if (pathname === "/realtime/feed") return json(res, { events: [{ id: "evt-1", area: "workflow", event_type: "workflow_started", timestamp: "2026-06-01T12:00:00", payload: { run_id: "wf-run-approval" } }], stats: { events: 1 } });
  if (pathname === "/realtime/presence") return json(res, { presence: [{ client_id: "visual-client", user: "admin@example.com", workspace_id: "personal", last_seen: "2026-06-01T12:00:00" }], stats: { subscribers: 1 } });
  // Autonomy dial (v9.9.8). The settings screenshot renders this panel, so the
  // mock must serve the same catalog shape the real API does — otherwise the
  // release evidence would show an "unavailable" state for a working feature.
  if (pathname === "/api/permission-mode" || pathname === "/api/permission-mode/catalog") {
    const catalog = [
      { id: "strict", label: "Strict", label_ko: "엄격", summary: "Reads auto; writes and exec need approval or review proposals.", summary_ko: "읽기는 자동, 쓰기·실행은 승인 또는 변경 제안.", risk: "low", requires_ack: false },
      { id: "trusted", label: "Trusted", label_ko: "신뢰", summary: "Workspace writes and knowledge reads auto-run; exec/desktop control still gated.", summary_ko: "워크스페이스 쓰기·지식 읽기 자동. 실행·데스크톱 제어는 승인 필요.", risk: "medium", requires_ack: false },
      { id: "bypass", label: "Bypass", label_ko: "바이패스", summary: "YOLO inside the agent workspace. Hard circuit breakers still apply.", summary_ko: "에이전트 워크스페이스 안에서 전부 자동. 하드 차단만 남음.", risk: "high", requires_ack: true, warning: "Bypass skips routine approval prompts. Destructive system paths, root/home wipes, and blocked prefixes remain denied.", warning_ko: "바이패스는 일상 승인 프롬프트를 건너뜁니다. 시스템 경로 파괴, 루트/홈 삭제, 차단 접두사는 계속 거부됩니다." },
    ];
    if (pathname.endsWith("/catalog")) return json(res, { modes: catalog });
    const mode = req.method === "POST" ? "trusted" : "strict";
    const entry = catalog.find((item) => item.id === mode);
    return json(res, {
      mode, label: entry.label, label_ko: entry.label_ko, risk: entry.risk,
      requires_ack: entry.requires_ack, proposal_first: mode === "strict",
      workspace_writes_auto: mode !== "strict", knowledge_reads_auto: mode !== "strict",
      exec_auto: mode === "bypass", computer_observation_auto: mode !== "strict",
      computer_control_auto: mode === "bypass", circuit_breakers: true,
      catalog, scope: { user_email: "admin@example.com", workspace_id: null },
    });
  }
  // Network boundary dial (v10.1.1). Same reason as the autonomy dial above:
  // the settings screenshot renders this panel, so the mock has to serve the
  // real catalog shape or the evidence would show a working feature as broken.
  if (pathname.startsWith("/api/network-boundary")) {
    const catalog = [
      { id: "local_only", label: "Local only", label_ko: "로컬만", summary: "Nothing leaves this machine. Answers use local models and the local Brain only.", summary_ko: "이 컴퓨터를 벗어나지 않습니다. 로컬 모델과 로컬 Brain만 사용합니다.", risk: "low", requires_ack: false },
      { id: "cloud_allowed", label: "Cloud streaming allowed", label_ko: "클라우드 스트리밍 허용", summary: "Minimal related Knowledge Graph nodes may be sent to a cloud LLM. The streamed answer is written back into the local Brain with provenance.", summary_ko: "관련된 최소 Knowledge Graph 노드만 클라우드 LLM으로 전송될 수 있습니다. 스트리밍 답변은 provenance와 함께 로컬 Brain에 다시 기록됩니다.", risk: "medium", requires_ack: true, warning: "Cloud mode sends a compact summary of selected local nodes to an external provider. Sensitive nodes remain blocked.", warning_ko: "클라우드 모드는 선택된 로컬 노드의 압축 요약을 외부 제공자에게 전송합니다. 민감 노드는 계속 차단됩니다." },
    ];
    const policy = {
      blocked_node_types: [], blocked_metadata_flags: ["do_not_share", "local_only", "private", "sensitive"],
      auto_commit: false, allow_multimodal: false, min_extraction_confidence: 0.55,
    };
    if (pathname.endsWith("/catalog")) return json(res, { modes: catalog });
    if (pathname.endsWith("/policy")) return json(res, policy);
    if (pathname.endsWith("/preview")) {
      return json(res, {
        mode: "local_only", allows_cloud: false,
        node_ids: ["node-release", "node-checklist"], keywords: ["release", "checklist"],
        titles: ["릴리스 절차 정리", "배포 전 확인 목록"], types: ["Document", "Note"],
        token_estimate: 412, quality: "ok",
        compact_preview: "릴리스 절차 정리 · 배포 전 확인 목록",
        token_budget: { turn_limit: 2500, session_limit: 50000, session_used: 0 },
        would_block: null,
      });
    }
    // POST /api/network-boundary is the mode switch; the panel re-reads state
    // afterwards, so returning the acknowledged mode is enough.
    const mode = req.method === "POST" ? "cloud_allowed" : "local_only";
    const entry = catalog.find((item) => item.id === mode);
    return json(res, {
      mode, label: entry.label, label_ko: entry.label_ko, risk: entry.risk,
      requires_ack: entry.requires_ack, allows_cloud: mode === "cloud_allowed",
      warning_ko: entry.warning_ko || null,
      policy, token_budget: { turn_limit: 2500, session_limit: 50000, session_used: 0 },
      catalog, scope: { user_email: "admin@example.com", workspace_id: null },
    });
  }
  if (pathname === "/permissions/pending") return json(res, { pending: { "perm-token": { path: "/tmp/report.md", action: "read", action_label: "read file", user_email: "admin@example.com", approved: false, expires_in: 300 } }, count: 1 });
  if (pathname.startsWith("/permissions/approve/") && req.method === "POST") return json(res, { ok: true, token: pathname.split("/").pop() });
  if (pathname.startsWith("/permissions/deny/") && req.method === "POST") return json(res, { ok: true, denied: true, token: pathname.split("/").pop() });
  if (pathname === "/network/identity") return json(res, { device_id: "device-visual", fingerprint: "sha256:LOCAL", public_key: "-----BEGIN PUBLIC KEY-----\\nlocal\\n-----END PUBLIC KEY-----" });
  if (pathname === "/network/peers") {
    if (req.method === "POST") return json(res, { status: "paired", peer: peers[0] });
    return json(res, { peers });
  }
  if (pathname.startsWith("/network/peers/") && req.method === "DELETE") return json(res, { removed: true, peer_id: pathname.split("/").pop() });
  if (pathname.startsWith("/network/push/") && req.method === "POST") return json(res, { status: "ok", pushed: true, peer_id: pathname.split("/").pop() });
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
  if (pathname === "/agents/api/run" && req.method === "POST") return json(res, {
    run: { id: "agent-run-live", agent_id: "agent:executor", status: "ok", created_at: "2026-06-07T10:06:00" },
    result: {
      agent_id: "agent:executor", status: "ok", retries: 0,
      output: "Completed the goal across planner -> executor -> reviewer. 3/3 steps approved.",
      roles_run: ["planner", "executor", "reviewer"],
      timeline: [
        { event: "start", role: "planner", status: "ok", timestamp: "2026-06-07T10:06:00" },
        { event: "role", role: "planner", status: "ok", result: "Decomposed the goal into 3 ordered steps.", timestamp: "2026-06-07T10:06:00" },
        { event: "handoff", role: "executor", status: "ok", timestamp: "2026-06-07T10:06:01" },
        { event: "role", role: "executor", status: "ok", result: "Executed 3/3 steps, invoking tools.", timestamp: "2026-06-07T10:06:01" },
        { event: "handoff", role: "reviewer", status: "ok", timestamp: "2026-06-07T10:06:02" },
        { event: "role", role: "reviewer", status: "ok", result: "Reviewed and approved the work.", timestamp: "2026-06-07T10:06:02" },
        { event: "end", status: "ok", retries: 0, timestamp: "2026-06-07T10:06:02" },
      ],
      plan: { steps: [{ step: "Plan" }, { step: "Execute" }, { step: "Review" }] },
      review: { verdict: "pass" }, handoffs: [],
    },
    pre_run_hooks: { ran: 1, blocked: false },
    post_run_hooks: { ran: 1, blocked: false },
  });
  // The one-click switcher on the model library calls this. Without it the
  // primary action of the screen's first card 404s in every captured frame.
  if (pathname === "/models/load" && req.method === "POST") return json(res, {
    status: "ok", loaded: true, model_id: "mlx-community/gemma-4-26b-a4b-it-4bit", engine: "local_mlx",
  });
  if (pathname === "/models") return json(res, {
    recommended: [
      {
        id: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        name: "Qwen3-VL 8B",
        display_name: "Qwen3-VL 8B",
        family: "Qwen3-VL",
        size: "4.8GB",
        modality: "multimodal",
        capabilities: ["vision", "text"],
        state: "loaded",
        pulled: true,
        download_required: false,
        load_available: true,
        load_status: "loaded",
        recommended_engine: "local_mlx",
        recommended_load_id: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        runtime_compatibility: { supported: true, status: "supported" },
      },
      {
        id: "mlx-community/gemma-4-12b-it-4bit",
        name: "Gemma 4 12B Instruct",
        display_name: "Gemma 4 12B Instruct",
        family: "Gemma 4",
        size: "7.6GB",
        capabilities: ["text"],
        state: "runtime_update_needed",
        pulled: true,
        download_required: false,
        load_available: false,
        load_status: "runtime_update_needed",
        unavailable_reason: "Gemma 4 12B uses the gemma4_unified MLX format. The installed MLX-VLM runtime does not include that loader, so this local model cannot load until MLX-VLM is updated.",
        recommended_engine: "local_mlx",
        recommended_load_id: "mlx-community/gemma-4-12b-it-4bit",
        runtime_label: "MLX-VLM",
        engine_options: [
          { engine: "local_mlx", model_id: "mlx-community/gemma-4-12b-it-4bit", load_id: "mlx-community/gemma-4-12b-it-4bit", runtime_label: "MLX-VLM", runtime_supported: false },
          { engine: "ollama", model_id: "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", load_id: "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", runtime_label: "Ollama GGUF" },
          { engine: "lmstudio", model_id: "ggml-org/gemma-4-12B-it-GGUF", load_id: "lmstudio:ggml-org/gemma-4-12B-it-GGUF", runtime_label: "LM Studio GGUF" },
        ],
        runtime_compatibility: {
          supported: false,
          status: "runtime_update_needed",
          action: "Runtime update needed",
          reason_code: "mlx_vlm_missing_gemma4_unified_model",
          model_type: "gemma4_unified",
          missing_components: ["mlx_vlm.models.gemma4_unified"],
          preferred_runtime: "MLX-VLM",
          user_message: "Gemma 4 12B uses the gemma4_unified MLX format. The installed MLX-VLM runtime does not include that loader, so this local model cannot load until MLX-VLM is updated.",
          recovery_guidance: ["Update MLX-VLM to 0.6.3 or newer.", "Use Gemma 4 26B A4B locally until then."],
          alternatives: [
            { id: "mlx-community/gemma-4-26b-a4b-it-4bit", name: "Gemma 4 26B A4B", engine: "local_mlx" },
            { id: "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", name: "Ollama GGUF", engine: "ollama" },
          ],
        },
      },
      {
        id: "mlx-community/gemma-4-26b-a4b-it-4bit",
        name: "Gemma 4 26B A4B Instruct",
        display_name: "Gemma 4 26B A4B Instruct",
        family: "Gemma 4",
        size: "15.6GB",
        capabilities: ["vision", "text"],
        state: "ready",
        pulled: true,
        download_required: false,
        load_available: true,
        load_status: "ready",
        recommended_engine: "local_mlx",
        recommended_load_id: "mlx-community/gemma-4-26b-a4b-it-4bit",
        runtime_label: "MLX-VLM",
        runtime_compatibility: { supported: true, status: "supported", model_type: "gemma4", preferred_runtime: "MLX-VLM" },
      },
    ],
    cloud: [],
    engines: [{ id: "local_mlx", name: "MLX", kind: "local", installed: true }],
    loaded: ["mlx-community/Qwen3-VL-8B-Instruct-4bit"],
    current: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    compat_profiles: [{ model_id: "mlx-community/Qwen3-VL-8B-Instruct-4bit", engine: "local_mlx", quality_status: "ok", chat_compatible: true }],
    vision: { current_model: "mlx-community/Qwen3-VL-8B-Instruct-4bit", current_supports_vision: true, engine_available: true, enabled: true },
  });
  if (pathname === "/models/recommendations") return json(res, {
    profile: { os: "darwin", arch: "arm64", ram_mb: 65536, gpu: { vendor: "apple", vram_mb: 65536 } },
    recommendations: {
      engine: "local_mlx",
      engine_available: true,
      apple_silicon: true,
      ram_gb: 64,
      counts: { recommended: 2, compatible: 0, not_recommended: 1 },
      top_pick: { id: "mlx-community/gemma-4-26b-a4b-it-4bit", name: "Gemma 4 26B A4B Instruct", family: "Gemma 4", status: "recommended", size: "15.6GB" },
      families: [],
      models: [
        { id: "mlx-community/Qwen3-VL-8B-Instruct-4bit", name: "Qwen3-VL 8B", family: "Qwen3-VL", status: "recommended", reason: "현재 메모리에서 안정적으로 사용할 가능성이 높습니다", size: "4.8GB", runtime_compatibility: { supported: true, status: "supported" } },
        { id: "mlx-community/gemma-4-12b-it-4bit", name: "Gemma 4 12B Instruct", family: "Gemma 4", status: "not_recommended", reason: "Runtime update needed", size: "7.6GB", runtime_compatibility: { supported: false, status: "runtime_update_needed", action: "Runtime update needed" } },
        { id: "mlx-community/gemma-4-26b-a4b-it-4bit", name: "Gemma 4 26B A4B Instruct", family: "Gemma 4", status: "recommended", reason: "현재 메모리에서 안정적으로 사용할 가능성이 높습니다", size: "15.6GB", runtime_compatibility: { supported: true, status: "supported", model_type: "gemma4" } },
      ],
    },
  });
  // Install SSE stage tokens MUST match latticeai/services/model_loading.py
  // prepare stream wire protocol (B2): engine → download → load → smoke_test → done.
  // Frontend maps these to UI steps install/download/validate/load via
  // friendlyInstallStage — never invent mock-only stage names.
  if (pathname === "/engines/prepare-model/stream" && req.method === "POST") {
    res.writeHead(200, { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-store", connection: "keep-alive" });
    const send = (event, obj) => res.write(`event: ${event}\ndata: ${JSON.stringify(obj)}\n\n`);
    send("progress", { stage: "engine", message: "Execution engine is ready.", percent: 10 });
    setTimeout(() => send("progress", { stage: "download", message: "Already downloaded model files.", percent: 55 }), 100);
    setTimeout(() => send("progress", { stage: "load", message: "Loading model into memory.", percent: 92 }), 200);
    setTimeout(() => send("progress", { stage: "smoke_test", message: "Validating chat compatibility.", percent: 98 }), 300);
    setTimeout(() => {
      send("done", { status: "ok", model: "mlx-community/Qwen3-VL-8B-Instruct-4bit", current: "mlx-community/Qwen3-VL-8B-Instruct-4bit", ready_to_chat: true, compatibility_status: "ok" });
      res.end();
    }, 420);
    return;
  }
  // readiness is backend-owned (roomy|tight|low). Mock max load is 61% → tight.
  if (pathname === "/local/sysinfo") return json(res, {
    cpu_pct: 34,
    ram_pct: 61,
    gpu_mem_pct: 48,
    gpu_mem_gb: 9.4,
    readiness: "tight",
  });
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
    // Per-folder memory state. The Sources screen now shows this card beside the
    // recent-documents panel, so it has to answer here or the second row of the
    // redesigned layout captures as a single half-empty column.
    if (pathname === "/knowledge-graph/local/health") return json(res, {
      count: 2,
      vector_freshness_global: { status: "fresh", pending_items: 0 },
      folders: [
        {
          id: "src-docs", label: "Documents", root_path: "/Users/you/Documents",
          status: "indexed", watch_active: true, coverage: 1,
          files: { total: 312, indexed: 312, failed: 0 }, recent_errors: [],
        },
        {
          id: "src-proj", label: "lattice (project)", root_path: "/Users/you/code/lattice",
          status: "indexed", watch_active: false, coverage: 0.9989,
          files: { total: 1842, indexed: 1840, failed: 2 },
          recent_errors: [
            { path: "/Users/you/code/lattice/assets/logo.psd", detail: "지원하지 않는 파일 형식이라 건너뛰었어요." },
          ],
        },
      ],
    });
    if (pathname === "/api/local-agent/status") return json(res, {
      agent: { id: "lattice-local-runtime", name: "Lattice Local Agent", kind: "on-device-runtime", online: true, platform: "macOS-15.5-arm64-arm-64bit", machine: "arm64", python: "3.12.4" },
      online: true, mode: "online", version: "3.4.1", pid: 31166,
      handshake: { ok: true, transport: "in-process", latency_ms: 0.7, detail: "Probed the in-process runtime (filesystem + graph); the local Lattice server is the on-device agent — no separate desktop process." },
      health: { status: "online", filesystem_access: true, graph_reachable: true, watcher_available: true },
      filesystem_access: true, watcher_available: true, connected_folders: 2, watched_folders: 1,
      folders: { connected: 2, watching: 1 },
      watch: localSources.watch,
      sources: localSources.sources,
      last_seen: "2026-06-08T21:21:34", error: null,
    });
  }
  if (pathname === "/api/hooks/runs") return json(res, {
    runs: [
      { hook_id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run", status: "ok", detail: "", output: "redacted 1 field(s)", duration_ms: 1, blocked: false, target_event: "agent.run", target_kind: "pre_run", started_at: "2026-06-07T10:00:01" },
      { hook_id: "builtin:audit-agent-run", name: "Audit agent run", kind: "post_run", status: "ok", detail: "", output: "audited run agent-run-9", duration_ms: 2, blocked: false, target_event: "agent.run", target_kind: "post_run", started_at: "2026-06-07T10:00:02" },
      { hook_id: "builtin:pipeline-index-status", name: "Pipeline index status", kind: "post_index", status: "ok", detail: "", output: "pipeline document.index: indexed=true", duration_ms: 0, blocked: false, target_event: "document.index", target_kind: "post_index", started_at: "2026-06-07T09:58:00" },
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
  if (pathname === "/workflows/api/definitions") return json(res, { workflows: [
    { id: "wf-agent-review", name: "Agent Review Workflow", nodes: [
      { id: "trigger", type: "trigger", name: "Trigger", config: { trigger: "manual" }, next: "agent" },
      { id: "agent", type: "agent", name: "Agent chain", next: "tool" },
      { id: "tool", type: "tool", name: "Tool", next: "output" },
      { id: "output", type: "output", name: "Result", next: null },
    ] },
    ...(installedRecipeWorkflow ? [installedRecipeWorkflow] : []),
  ] });
  if (pathname === "/workflows/api/automation/recipes") return json(res, {
    recipes: [{
      id: "follow-up-radar",
      name: "Follow-up Radar",
      summary: "Looks for follow-up candidates when new knowledge enters the Brain.",
      user_value: "Gentle reminders for loose ends without a noisy task system.",
      cadence: "when new memory is saved",
      creates: ["follow-up suggestions", "approval-ready task drafts"],
      consent: { requires_user_enable: true },
    }],
    principles: { local_first: true, drafts_before_automation: true },
  });
  if (pathname === "/workflows/api/automation/recipes/follow-up-radar" && req.method === "POST") {
    const enabled = Boolean(installedRecipeWorkflow);
    installedRecipeWorkflow = {
      id: "wf-follow-up-radar",
      name: "Follow-up Radar",
      nodes: [{ id: "trigger", type: "trigger", config: { trigger: "brain_event", enabled }, next: "draft" }],
      metadata: { created_from: "brain_automation_recipe", recipe_id: "follow-up-radar", automation_state: enabled ? "enabled" : "draft_disabled" },
    };
    return json(res, { workflow: installedRecipeWorkflow, recipe: installedRecipeWorkflow.metadata, enabled, already_installed: enabled });
  }
  if (pathname.startsWith("/workflows/api/definitions/") && req.method === "PATCH") return json(res, { workflow: { id: "wf-agent-review", name: "Agent Review Workflow" } });
  if (pathname === "/workflows/api/triggers") return json(res, { running: true, tick_seconds: 5, armed: [{ workflow_id: "wf-agent-review", name: "Agent Review Workflow", kind: "brain_event", config: { source_type: "upload" }, last_fired_at: 1780300800, recent_events: [{ type: "fired", trigger: "brain_event" }] }] });
  if (pathname === "/workflows/api/runs") return json(res, { runs: [
    { id: "wf-run-approval", workflow_id: "wf-agent-review", workflow_name: "Agent Review Workflow", status: "awaiting_approval", mode: "live", pause: { node: "tool" }, timeline: [{ event: "workflow_started", status: "running" }, { event: "approval_required", status: "awaiting_approval" }], created_at: "2026-06-06T12:05:00" },
    { id: "wf-run-1", workflow_id: "wf-agent-review", workflow_name: "Agent Review Workflow", status: "ok", mode: "live", created_at: "2026-06-06T12:00:00" },
  ] });
  if (pathname.startsWith("/workflows/api/runs/") && pathname.endsWith("/stop") && req.method === "POST") return json(res, { stopped: true, run_id: pathname.split("/")[4] });
  if (pathname.startsWith("/workflows/api/runs/") && pathname.endsWith("/resume") && req.method === "POST") return json(res, { run: { id: "wf-run-resumed", status: "ok" }, result: { status: "ok" }, resumed_from: pathname.split("/")[4] });
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
    brain_readiness: {
      score: 100,
      state: "alive",
      depth: 5,
      title_key: "brain.readiness.alive",
      action_key: "brain.readiness.map",
      source: "memory_service",
      signals: { memory_count: 8, concept_count: graphNodes.length, relationship_count: graphEdges.length, healthy_sources: 6 },
    },
    health: "ok",
  });
  if (pathname === "/api/memory/brain-quality") return json(res, {
    score: 100,
    state: "alive",
    depth: 5,
    title_key: "brain.readiness.alive",
    action_key: "brain.readiness.map",
    source: "memory_service",
    signals: { memory_count: 8, concept_count: graphNodes.length, relationship_count: graphEdges.length, healthy_sources: 6 },
  });
  if (pathname === "/api/memory/brain-proof") return json(res, {
    status: "alive",
    model_continuity: {
      active_model: workspaceOs.models.current_model,
      brain_owner: "lattice_brain",
      capability: true,
      survives_model_switch: true,
      proven: true,
      context_store: "workspace + conversation + graph + vector",
    },
    proofs: {
      durable_items: 13,
      has_durable_evidence: true,
      workspace_memories: 3,
      conversations: 2,
      graph_concepts: graphNodes.length,
      vector_items: 8,
      healthy_sources: 6,
    },
    recall: {
      query: url.searchParams.get("q") || "first Brain proof",
      count: 2,
      items: [
        { id: "mem:release", source: "workspace", title: "Release memory", snippet: "The Brain recalls saved release decisions with source evidence.", score: 0.94 },
        { id: "file:readme", source: "graph", title: "README.md", snippet: "Release documentation anchors the answer.", score: 0.88 },
      ],
    },
    claims: {
      can_recall_user_context: true,
      keeps_context_across_models: true,
      is_knowledge_store: true,
    },
  });
  if (pathname === "/api/memory/brain-brief") return json(res, {
    status: "alive",
    score: 100,
    headline_key: "brain.brief.headline.alive",
    body_key: "brain.brief.body.alive",
    focus: {
      kind: "graph",
      title: "Lattice AI",
      detail: "Local-first workspace graph grounded in saved release decisions.",
      source: "Knowledge Graph",
      score: 0.96,
      empty: false,
    },
    next_actions: [
      { id: "inspect_topics", label_key: "brain.brief.action.topics", detail_key: "brain.brief.action.topics.detail", route: "/knowledge-graph", priority: 9 },
    ],
    suggested_questions: [
      { id: "focus_next", label_key: "brain.suggestion.focus.label", detail_key: "brain.suggestion.focus.detail", prompt_key: "brain.suggestion.focus.prompt", params: { focus: "Lattice AI" }, priority: 10 },
      { id: "evidence_check", label_key: "brain.suggestion.evidence.label", detail_key: "brain.suggestion.evidence.detail", prompt_key: "brain.suggestion.evidence.prompt", params: { focus: "Lattice AI" }, priority: 9 },
    ],
    proactive_actions: [
      { id: "proactive_evidence_review", intent: "ask", label_key: "brain.proactive.evidence.label", detail_key: "brain.proactive.evidence.detail", prompt: "Review the evidence Brain has for Lattice AI.", route: "", priority: 100, context: { focus: "Lattice AI" } },
      { id: "proactive_delegate", intent: "delegate", label_key: "brain.proactive.delegate.label", detail_key: "brain.proactive.delegate.detail", prompt: "Turn Lattice AI into an evidence-backed execution plan.", route: "", priority: 95, context: { focus: "Lattice AI" } },
      { id: "proactive_review_draft", intent: "review", label_key: "brain.proactive.review.label", detail_key: "brain.proactive.review.detail", prompt: "Create a reviewable task from Lattice AI.", route: "", priority: 90, context: { focus: "Lattice AI" } },
    ],
    evidence: [
      { id: "durable", label_key: "brain.brief.evidence.durable", value: 13, detail_key: "brain.brief.evidence.durable.detail" },
      { id: "graph", label_key: "brain.brief.evidence.graph", value: graphNodes.length, detail_key: "brain.brief.evidence.graph.detail" },
      { id: "sources", label_key: "brain.brief.evidence.sources", value: 6, detail_key: "brain.brief.evidence.sources.detail" },
    ],
    generated_at: "2026-06-07T10:05:00Z",
  });
  if (pathname === "/api/memory/inspect") return json(res, { source: url.searchParams.get("source"), items: [{ id: "mem-demo", kind: "workspace", title: "Demo memory", content: "Release memory" }], count: 1, available: true, stats: workspaceOs.graph, index: { status: "ready" } });
  if (pathname === "/api/hooks/run" && req.method === "POST") return json(res, {
    hook_id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run",
    status: "ok", detail: "", output: "redacted 1 field(s)", duration_ms: 1, blocked: false,
    source: "builtin", binding: "multi_agent._redact", started_at: "2026-06-07T10:05:00",
  });
  if (pathname === "/api/hooks/fire" && req.method === "POST") return json(res, { kind: "pre_run", event: "manual", ran: 1, blocked: false, block_reason: "", results: [], generated_at: "2026-06-07T10:05:00" });
  if (pathname === "/api/hooks") return json(res, { hooks: [
    { id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run", order: 10, description: "Strip secret-like fields from agent context before a run.", binding: "multi_agent._redact", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:research-memory-snapshot", name: "Research memory snapshot", kind: "agent", order: 20, description: "Capture a short-term memory snapshot after the researcher stage.", binding: "multi_agent.default_role_runner", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:tool-permission-gate", name: "Tool permission gate", kind: "pre_tool", order: 10, description: "Evaluate + record the governance policy for each tool call.", binding: "tool_registry.permission", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:sensitive-data-guard", name: "Sensitive-data guard", kind: "pre_tool", order: 20, description: "Classify outgoing content for sensitive data before tool execution.", binding: "server_app.classify_sensitive_message", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:audit-agent-run", name: "Audit agent run", kind: "post_run", order: 10, description: "Append every completed agent run to the workspace audit log.", binding: "AgentRuntime.start", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:pipeline-index-status", name: "Pipeline index status", kind: "post_index", order: 10, description: "Publish ingest / embed / graph-build pipeline state.", binding: "api.search", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:workflow-replay-log", name: "Workflow replay log", kind: "post_workflow", order: 10, description: "Record each workflow run's timeline so it can be replayed.", binding: "api.workflow_designer", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "user:notify-slack", name: "Notify Slack on release", kind: "post_run", order: 100, description: "Posts a message to #releases after an agent run.", command: "python3 scripts/notify.py", managed: "user", source: "user", enabled: true, removable: true, executable: true, advisory: false },
  ], kinds: ["pre_run", "post_run", "pre_tool", "post_tool", "pre_workflow", "post_workflow", "pre_upload", "post_upload", "pre_index", "post_index", "agent"], counts: { pre_run: { total: 1, enabled: 1 }, post_run: { total: 2, enabled: 2 }, pre_tool: { total: 2, enabled: 2 }, post_index: { total: 1, enabled: 1 }, post_workflow: { total: 1, enabled: 1 }, agent: { total: 1, enabled: 1 } }, total: 8, enabled: 8 });
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

  if (pathname === "/admin/summary") return json(res, { total_users: 2, active_users: 2, admin_users: 1, total_messages: 42, user_messages: 21, assistant_messages: 21 });
  // Calm admin header (layout rebuild screen 10). ``attention`` so the
  // non-ok layout is what the release capture actually shows.
  if (pathname === "/admin/health-summary") return json(res, {
    status: "attention",
    issue_count: 1,
    issues: [
      { area: "security", severity: "warning", message: "1 medium-risk event awaiting review" },
    ],
  });
  if (pathname === "/admin/users") return json(res, [{ email: "admin@example.com", nickname: "Admin", role: "admin", disabled: false }, { email: "member@example.com", nickname: "Member", role: "user", disabled: false }]);
  if (pathname === "/admin/sensitivity") return json(res, { summary: { risky_messages: 1, compliant_messages: 41, risk_rate: 2, severity_counts: { high: 0 }, field_counts: {}, user_counts: {} }, risk_fields: [], compliance_fields: [] });
  if (pathname === "/admin/invite-link") return json(res, { invite_url: `http://127.0.0.1:${port}/`, invite_code: "visual", gate_enabled: false });
  if (pathname === "/admin/stats") return json(res, { daily: [{ date: "2026-06-01", user: 8, assistant: 8 }] });
  if (pathname === "/admin/audit") return json(res, { summary: { total_events: 12, chat_events: 6, user_messages: 3, assistant_messages: 3, document_uploads: 2, clear_events: 1, sensitive_events: 1, high_sensitive_events: 0 }, filters: { matched_events: 5, scoped_events: 12, limit: 50 }, graph: workspaceOs.graph, per_user: [], recent_events: [
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
    { id: "log_retention", label: "Log retention", value: "90 day local audit window", enforced: true },
  ] });
  if (pathname === "/admin/log-retention") return json(res, {
    mode: "local-first",
    retention_days: 90,
    total_events: 12,
    retained_events: 12,
    prune_candidates: 0,
    export_before_prune: true,
    editable: false,
  });
  if (pathname === "/admin/product-hardening") return json(res, {
    version: "4.3.3",
    startup: { local_only_default: true, host: "127.0.0.1", port: 4825, network_exposed: false },
    privacy: { local_only_default: true, integrations: { telegram: { enabled: false, credential_present: false, opt_in_required: true } } },
    storage: { active: { engine: "sqlite", available: true } },
    backup: { available: true, count: 1 },
    device_identity: { fingerprint: "sha256:LOCAL", algorithm: "ed25519", storage: "file" },
    permissions: { export_requires_admin: true, import_requires_admin: true, destructive_restore_requires_confirmation: true },
  });
  if (pathname === "/admin/security/overview") return json(res, {
    generated_at: "2026-06-06T12:00:00", risk_rate: 2,
    cards: { events_today: 5, high_risk_events: 0, risky_chats: 1, review_required: 1 },
    severity_counts: { high: 0, medium: 1, low: 2 }, field_counts: { email: 4, api_key: 1 },
  });
  if (pathname.startsWith("/admin/security/")) return json(res, { cards: {}, users: [], events: [], files: [], field_counts: {} });
  // Keeps the Review Center's pending-proposal badge consistent with the one
  // change_proposal in the reviews fixture below.
  if (pathname === "/api/proposals/counts") return json(res, { pending: 1 });
  // The runs tab promoted "설치된 자동화" to its second tier, between the
  // approval inbox and the run history. This route did not exist, so the client
  // fell back to its `installed: []` default and the promoted panel was
  // captured as an empty state — the screenshot showed the new hierarchy with
  // its middle tier blank. Two entries so the lg:grid-cols-2 layout is exercised,
  // one already dry-run and one never run, which is what the card's two-step
  // control is there to distinguish.
  if (pathname === "/api/automation/overview") {
    return json(res, {
      suggestions: [],
      questions_scanned: 12,
      installed: [
        {
          id: "wf-daily-digest",
          name: "매일 기억 요약",
          enabled: true,
          requires_user_enable: false,
          creates: ["note"],
          last_execution: {
            mode: "dry_run",
            status: "ok",
            summary: "3개 항목을 요약할 예정입니다",
            run_id: "run-digest-1",
            finished_at: "2026-06-22T09:00:00",
          },
        },
        {
          id: "wf-weekly-review",
          name: "주간 되돌아보기",
          enabled: false,
          requires_user_enable: true,
          creates: ["document"],
          last_execution: null,
        },
      ],
    });
  }
  if (pathname === "/automation/reviews") {
    const items = [
      {
        id: "rev-7-8-release",
        status: "pending",
        effective_status: "pending",
        title: `Approve ${appVersion} product readiness evidence`,
        summary: "Review generated screenshots, exact artifacts, and product readiness gates before release.",
        source: "workflow_run",
        kind: "release_review",
        payload: { last_run_id: releaseRunId },
        provenance: { workflow_id: "wf-release", run_id: releaseRunId, source_detail: `${appVersion} release workflow` },
        created_at: "2026-06-22T12:00:00Z",
        updated_at: "2026-06-22T12:05:00Z",
      },
      {
        // A change proposal with a real diff. The Review Center card puts the
        // evidence on the left and the approve/reject decision on the right,
        // and without a proposal in the fixture the left column is empty — the
        // release screenshot would show the layout with nothing in it.
        id: "rev-proposal-readme",
        status: "pending",
        effective_status: "pending",
        title: "README 릴리스 표를 최신 버전으로 고칩니다",
        summary: "릴리스 기록 표에 이번 버전 줄을 추가합니다. 승인하면 파일에 그대로 적용됩니다.",
        source: "change_proposal",
        kind: "file_write",
        payload: {
          path: "README.md",
          tier: "small",
          diff: [
            "--- a/README.md",
            "+++ b/README.md",
            "@@ -18,6 +18,7 @@",
            " ## Release History",
            " ",
            " | Version | Theme |",
            " | --- | --- |",
            `+| ${appVersion} | First Things |`,
            " | 10.6.0 | Promoted Panels |",
          ],
        },
        provenance: {
          risk: "low",
          change_class: "docs",
          tool: "write_file",
          proposed_by: "Brain",
          source_detail: "문서 정리 자동화",
        },
        created_at: "2026-06-22T12:01:00Z",
        updated_at: "2026-06-22T12:01:00Z",
      },
      {
        id: "rev-kg-digest",
        status: "pending",
        effective_status: "pending",
        title: "Review new Knowledge Graph digest",
        summary: "Three new project memories are ready to become durable context.",
        source: "kg_change_digest",
        kind: "memory_digest",
        payload: {},
        provenance: { source_detail: "Brain ingestion pipeline" },
        created_at: "2026-06-22T12:02:00Z",
        updated_at: "2026-06-22T12:02:00Z",
      },
    ];
    const status = url.searchParams.get("status");
    const source = url.searchParams.get("source");
    return json(res, {
      items: items.filter((item) => (!status || item.effective_status === status) && (!source || item.source === source)),
    });
  }
  if (pathname.startsWith("/automation/reviews/")) {
    return json(res, {
      id: "rev-7-8-release",
      status: "pending",
      effective_status: "pending",
      title: `Approve ${appVersion} product readiness evidence`,
      summary: "Action preview completed.",
      source: "workflow_run",
      kind: "release_review",
      payload: { last_run_id: releaseRunId },
      provenance: { workflow_id: "wf-release", run_id: releaseRunId, source_detail: `${appVersion} release workflow` },
    });
  }

  if (req.method === "POST" || req.method === "PATCH" || req.method === "DELETE") return json(res, { status: "ok" });
  text(res, "not found", "text/plain; charset=utf-8");
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Lattice AI visual mock server listening on http://127.0.0.1:${port}`);
});
