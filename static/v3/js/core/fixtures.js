/* ============================================================================
 * Lattice AI v3 — Sample fixtures
 * Clearly-labeled SAMPLE data used ONLY as a graceful fallback when a backend
 * endpoint is not yet available. The UI always renders a "Sample data" badge
 * when these are used, so nothing here is presented as real backend output.
 * No backend logic is implemented — these are static shapes that mirror the
 * documented future API contracts so views are integration-ready.
 * ========================================================================== */

export const INDEX_STATUS = {
  generated_at: null,
  pipelines: {
    knowledge_graph: { state: "ready", entities: 1284, relations: 3960, last_built: null, coverage: 0.91 },
    vector_index: { state: "ready", vectors: 48230, dimensions: 1024, model: "bge-local", coverage: 0.87 },
    hybrid: { state: "ready", strategy: "reciprocal-rank-fusion", alpha: 0.5, last_eval: null },
  },
  sources: [
    { id: "src-notes", label: "Workspace Notes", files: 312, state: "indexed", progress: 1 },
    { id: "src-repo", label: "Connected Repo", files: 1840, state: "indexed", progress: 1 },
    { id: "src-uploads", label: "Uploads", files: 96, state: "indexing", progress: 0.62 },
  ],
};

export const GRAPH = {
  nodes: [
    { id: "n:lattice", label: "Lattice AI", type: "Topic", weight: 0.98, x: 0.5, y: 0.46 },
    { id: "n:hybrid", label: "Hybrid Search", type: "Concept", weight: 0.86, x: 0.72, y: 0.30 },
    { id: "n:vector", label: "Vector Index", type: "Concept", weight: 0.84, x: 0.30, y: 0.28 },
    { id: "n:graph", label: "Knowledge Graph", type: "Concept", weight: 0.9, x: 0.5, y: 0.74 },
    { id: "n:embed", label: "bge-local", type: "Model", weight: 0.6, x: 0.16, y: 0.5 },
    { id: "n:rrf", label: "Rank Fusion", type: "Method", weight: 0.58, x: 0.86, y: 0.54 },
    { id: "n:notes", label: "Workspace Notes", type: "File", weight: 0.52, x: 0.36, y: 0.9 },
    { id: "n:repo", label: "Connected Repo", type: "File", weight: 0.55, x: 0.66, y: 0.9 },
  ],
  edges: [
    { from: "n:lattice", to: "n:hybrid", type: "provides", weight: 1.0 },
    { from: "n:lattice", to: "n:graph", type: "builds", weight: 1.2 },
    { from: "n:lattice", to: "n:vector", type: "builds", weight: 1.1 },
    { from: "n:hybrid", to: "n:vector", type: "fuses", weight: 0.9 },
    { from: "n:hybrid", to: "n:graph", type: "fuses", weight: 0.9 },
    { from: "n:hybrid", to: "n:rrf", type: "uses", weight: 0.7 },
    { from: "n:vector", to: "n:embed", type: "uses", weight: 0.8 },
    { from: "n:graph", to: "n:notes", type: "from", weight: 0.6 },
    { from: "n:graph", to: "n:repo", type: "from", weight: 0.6 },
  ],
};

export const GRAPH_STATS = {
  nodes: { Topic: 1, Concept: 3, Model: 1, Method: 1, File: 2 },
  edges: { provides: 1, builds: 2, fuses: 2, uses: 2, from: 2 },
  total_nodes: 8,
  total_edges: 9,
};

export function hybridResults(query) {
  const q = (query || "retrieval").trim() || "retrieval";
  const base = [
    { id: "doc-1", title: "Hybrid retrieval design notes", path: "notes/retrieval.md", snippet: `…blends the knowledge graph and the vector field for "${q}", reconciling structure with semantic proximity…`, vector: 0.91, lexical: 0.64, graph: 0.78 },
    { id: "doc-2", title: "Vector index configuration", path: "config/index.yaml", snippet: `…embedding model and dimensions used to build the dense field that answers "${q}"…`, vector: 0.88, lexical: 0.41, graph: 0.35 },
    { id: "doc-3", title: "Graph entity: Rank Fusion", path: "graph://method/rrf", snippet: `…reciprocal-rank fusion merges ranked lists so a strong "${q}" signal in either modality surfaces…`, vector: 0.54, lexical: 0.33, graph: 0.95 },
    { id: "doc-4", title: "Workspace memory — decisions", path: "memory/decisions.md", snippet: `…prior decision relevant to "${q}", retrieved via graph adjacency from the active answer…`, vector: 0.49, lexical: 0.58, graph: 0.71 },
    { id: "doc-5", title: "Connected repo: README", path: "repo/README.md", snippet: `…lexical hit on "${q}" reinforced by neighboring entities in the knowledge graph…`, vector: 0.4, lexical: 0.82, graph: 0.4 },
  ];
  return base.map((r) => ({ ...r, score: Number((0.5 * r.vector + 0.2 * r.lexical + 0.3 * r.graph).toFixed(3)) }))
    .sort((a, b) => b.score - a.score);
}

export const WORKSPACE_OS = {
  version: "3.0.0",
  counts: { snapshots: 4, traces: 18, memories: 36, agent_runs: 12, workflows: 5, skills: 7, timeline: 24 },
  models: { current_model: "mlx-community/local-model-4bit", loaded_models: ["mlx-community/local-model-4bit"] },
};

export const MODELS = {
  current: "mlx-community/local-model-4bit",
  catalog: [
    { id: "mlx-community/local-model-4bit", name: "Local Model 4bit", family: "local", params: "12B", quant: "4bit", state: "loaded", context: 32768, recommended: true },
    { id: "mlx-community/reasoner-8bit", name: "Reasoner 8bit", family: "local", params: "8B", quant: "8bit", state: "available", context: 16384 },
    { id: "mlx-community/embed-bge", name: "bge-local (embeddings)", family: "embedding", params: "335M", quant: "fp16", state: "loaded", context: 512 },
  ],
};

export const AGENTS = [
  { id: "agent:planner", name: "Planner", role: "Decomposes goals into steps", state: "available", runs: 42, handoffs: ["agent:builder"] },
  { id: "agent:builder", name: "Builder", role: "Implements and edits files", state: "available", runs: 38, handoffs: ["agent:reviewer"] },
  { id: "agent:reviewer", name: "Reviewer", role: "Reviews diffs for risk", state: "idle", runs: 31, handoffs: [] },
  { id: "agent:retriever", name: "Retriever", role: "Runs hybrid search over the workspace", state: "available", runs: 57, handoffs: ["agent:planner"] },
];

export const PIPELINES = [
  { id: "pl-ingest", name: "Ingest → Embed → Graph", state: "active", stages: ["Watch source", "Chunk", "Embed", "Extract entities", "Link graph"], last_run: null, throughput: "1.8k files/run" },
  { id: "pl-eval", name: "Retrieval Eval", state: "idle", stages: ["Sample queries", "Hybrid search", "Score fusion", "Report"], last_run: null, throughput: "120 q/run" },
];

export const FILES = [
  { name: "retrieval.md", kind: "markdown", size: 4821, path: "notes/retrieval.md", indexed: true, updated: null },
  { name: "index.yaml", kind: "config", size: 932, path: "config/index.yaml", indexed: true, updated: null },
  { name: "decisions.md", kind: "markdown", size: 6210, path: "memory/decisions.md", indexed: true, updated: null },
  { name: "diagram.png", kind: "image", size: 184320, path: "assets/diagram.png", indexed: false, updated: null },
  { name: "dataset.csv", kind: "data", size: 51200, path: "data/dataset.csv", indexed: true, updated: null },
];

export const SYSINFO = { cpu_pct: 28.4, ram_pct: 61.2, gpu_mem_pct: 44.0, gpu_mem_gb: 12.6 };

export const CHAT = {
  conversations: [
    { id: "conv-hybrid", title: "How hybrid search ranks", updated_at: "2026-06-06T13:20:00", messages: [
      { role: "user", content: "How does hybrid search rank results?", timestamp: "2026-06-06T13:19:00" },
      { role: "assistant", content: "It fuses two signals: the vector index scores semantic similarity, while the knowledge graph scores structural proximity. Reciprocal-rank fusion merges the two ranked lists so a strong hit in either modality surfaces.", timestamp: "2026-06-06T13:20:00" },
    ] },
    { id: "conv-reindex", title: "Reindex the workspace", updated_at: "2026-06-06T11:05:00", messages: [
      { role: "user", content: "How do I rebuild the vector index?", timestamp: "2026-06-06T11:04:00" },
      { role: "assistant", content: "Trigger a rebuild from the Pipeline view, or call the index rebuild endpoint. The embedding model re-encodes every chunk and the graph is relinked.", timestamp: "2026-06-06T11:05:00" },
    ] },
    { id: "conv-entities", title: "Entities in retrieval.md", updated_at: "2026-06-05T18:40:00", messages: [
      { role: "user", content: "What entities were extracted from retrieval.md?", timestamp: "2026-06-05T18:39:00" },
      { role: "assistant", content: "Hybrid Search, Vector Index, and Rank Fusion, each linked back to Lattice AI in the knowledge graph.", timestamp: "2026-06-05T18:40:00" },
    ] },
  ],
};

/** Build a sample graph-RAG trace (mirrors the backend /chat trace shape). */
export function sampleTrace(query) {
  return {
    question: query || "",
    confidence: 0.86,
    graph_nodes: GRAPH.nodes.slice(0, 4).map((n) => ({ id: n.id, title: n.label, type: n.type })),
    source_files: FILES.slice(0, 3).map((f) => ({ source: f.path })),
    vector_matches: [
      { path: "notes/retrieval.md", score: 0.91 },
      { path: "config/index.yaml", score: 0.78 },
      { path: "memory/decisions.md", score: 0.66 },
    ],
  };
}

export const ADMIN = {
  summary: { total_users: 6, active_users: 5, admin_users: 2, total_messages: 1284 },
  users: [
    { email: "owner@acme.dev", nickname: "Owner", role: "owner", disabled: false, last_seen: null },
    { email: "admin@acme.dev", nickname: "Admin", role: "admin", disabled: false, last_seen: null },
    { email: "ml@acme.dev", nickname: "ML Eng", role: "member", disabled: false, last_seen: null },
    { email: "guest@acme.dev", nickname: "Guest", role: "viewer", disabled: true, last_seen: null },
  ],
  roles: [
    { role: "owner", members: 1, caps: ["all"] },
    { role: "admin", members: 1, caps: ["users", "policies", "audit", "security"] },
    { role: "member", members: 3, caps: ["chat", "search", "files", "pipeline"] },
    { role: "viewer", members: 1, caps: ["chat", "search"] },
  ],
  audit: [
    { ts: null, actor: "admin@acme.dev", action: "policy.update", target: "local_file_access", severity: "informational" },
    { ts: null, actor: "ml@acme.dev", action: "search.hybrid", target: "q: retrieval design", severity: "informational" },
    { ts: null, actor: "owner@acme.dev", action: "user.invite", target: "guest@acme.dev", severity: "notice" },
    { ts: null, actor: "system", action: "index.rebuild", target: "vector_index", severity: "informational" },
  ],
  security: {
    risk_rate: 1.2,
    risky_messages: 3,
    compliant_messages: 1281,
    severity_counts: { high: 0, medium: 1, low: 2 },
    dlp_fields: [{ field: "email", hits: 4 }, { field: "api_key", hits: 1 }],
  },
  policies: [
    { id: "local_file_access", label: "Local file access", value: "Approval-token gated (per path/user/action)", enforced: true },
    { id: "package_install", label: "Package install", value: "Admin-only with audit trail", enforced: true },
    { id: "data_residency", label: "Data residency", value: "Single-tenant local storage (~/.ltcai)", enforced: true },
    { id: "model_egress", label: "Model egress", value: "Local-only (no external inference)", enforced: true },
  ],
  vpc: { provider: "local", region: "on-prem", vpn_status: "standby", peering_status: "not_configured", private_subnets: [], enabled: false },
};
